"""Against PostgreSQL: migrations, and the index with concurrent jobs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any, ClassVar
from uuid import uuid4

import httpx
import pytest
import sqlalchemy
from alembic import command
from sqlalchemy import delete, func, select
from testcontainers.community.postgres import PostgresContainer

from fdpindex.cli import alembic_config
from fdpindex.db import make_engine, make_session_factory
from fdpindex.jobs import JobRunner
from fdpindex.models import EntryPermit, EntryState, Event, EventType, HarvestedNode, IndexEntry
from fdpindex.outbound import OutboundClient
from fdpindex.service import IndexService
from fdpindex.settings import SettingsService
from fdpindex.webhooks import WebhookService
from tests.conftest import FakeNetwork, public_resolver

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    # PostgreSQL 18 with pgvector, for the semantic search test (TASKS 10.1).
    with PostgresContainer("pgvector/pgvector:0.8.6-pg18", driver="asyncpg") as container:
        url = container.get_connection_url()
        config = alembic_config(url)
        config.attributes["configure_logger"] = False
        command.upgrade(config, "head")
        command.downgrade(config, "base")  # the downgrade path works too
        command.upgrade(config, "head")
        yield url


@pytest.fixture
async def index(
    database_url: str, network: FakeNetwork
) -> AsyncIterator[tuple[IndexService, JobRunner, FakeNetwork, object]]:
    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    async with engine.begin() as connection:
        for table in (
            "index_event",
            "harvested_node",
            "index_entry",
            "index_webhook",
            "index_settings",
        ):
            await connection.exec_driver_sql(f"DELETE FROM {table}")
    http = httpx.AsyncClient(transport=httpx.MockTransport(network.handler))
    outbound = OutboundClient(
        http,
        allow_private=False,
        max_bytes=1_000_000,
        max_redirects=3,
        user_agent="it",
        resolver=public_resolver,
    )
    jobs = JobRunner(4)
    settings = SettingsService(factory, auto_permit_default=True)
    webhooks = WebhookService(factory, settings, outbound, jobs, body_limit=10_000)
    service = IndexService(
        factory, settings, outbound, jobs, webhooks, body_limit=10_000, harvest_max_nodes=50
    )
    yield service, jobs, network, (factory, webhooks)
    await jobs.close()
    await http.aclose()
    await engine.dispose()


async def test_concurrent_pings_retrievals_harvests_and_webhooks(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    service, jobs, network, extra = index
    factory, webhooks = extra  # type: ignore[misc]
    await webhooks.save_webhook(  # type: ignore[attr-defined]
        None,
        {
            "payload_url": "https://hooks.example.net/all",
            "secret": "s3cret-value",
            "all_events": True,
            "events": [],
            "all_entries": True,
            "entries": [],
            "enabled": True,
        },
    )
    urls = [f"https://fdp{i}.example.org" for i in range(8)]
    for url in urls:
        network.serve(
            url,
            f"<{url}> a <https://w3id.org/fdp/fdp-o#MetadataService> ; "
            f'<http://purl.org/dc/terms/title> "FDP {url}" .',
        )

    async def ping(url: str) -> None:
        outcome = await service.accept_ping(
            url, remote_addr="203.0.113.9", method="POST", path="/", headers={}, body="{}"
        )
        service.after_trigger(outcome.event_id)

    await asyncio.gather(*(ping(url) for url in urls))
    await jobs.drain()

    async with factory() as session:  # type: ignore[operator]
        states = (await session.execute(select(IndexEntry.state))).scalars().all()
        nodes = await session.scalar(select(func.count()).select_from(HarvestedNode))
        deliveries = await session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.type == EventType.WEBHOOK_TRIGGER, Event.finished_at.is_not(None))
        )
    assert sorted(states) == [EntryState.VALID] * 8
    assert nodes == 8
    # Per FDP: IncomingPing, NewEntry, EntryValid.
    assert deliveries == 24
    events = sorted(json.loads(call.content)["event"] for call in network.webhook_calls)
    assert events.count("EntryValid") == 8

    # Search reads the title out of the JSONB metadata on PostgreSQL.
    found = await service.list_entries(permits=list(EntryPermit), query="FDP3")
    assert [entry.client_url for entry in found.items] == ["https://fdp3.example.org"]
    counts = await service.counts(list(EntryPermit))
    assert counts["ACTIVE"] == 8


async def test_simultaneous_first_pings_for_one_url_make_one_entry(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    service, _jobs, _network, extra = index
    factory, _ = extra  # type: ignore[misc]
    url = "https://fdp.example.org"
    await asyncio.gather(
        *(
            service.accept_ping(
                url, remote_addr=f"203.0.113.{i}", method="POST", path="/", headers={}, body="{}"
            )
            for i in range(5)
        )
    )
    async with factory() as session:  # type: ignore[operator]
        count = await session.scalar(select(func.count()).select_from(IndexEntry))
    assert count == 1


async def test_concurrent_sweeps_claim_each_due_entry_once(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    from datetime import timedelta

    from sqlalchemy import update

    from fdpindex.db import utcnow

    service, jobs, network, extra = index
    factory, _ = extra  # type: ignore[misc]
    urls = [f"https://due{i}.example.org" for i in range(6)]
    for url in urls:
        network.serve(url, f"<{url}> a <https://w3id.org/fdp/fdp-o#MetadataService> .")
        outcome = await service.accept_ping(
            url, remote_addr="203.0.113.50", method="POST", path="/", headers={}, body="{}"
        )
        service.after_trigger(outcome.event_id)
    await jobs.drain()
    async with factory() as session, session.begin():  # type: ignore[operator]
        await session.execute(
            update(IndexEntry).values(
                next_check_at=utcnow() - timedelta(minutes=1),
                last_retrieval_at=utcnow() - timedelta(hours=2),
            )
        )
    started = await asyncio.gather(*(service.schedule_due_checks() for _ in range(3)))
    assert sum(started) == 6
    await jobs.drain()
    async with factory() as session:  # type: ignore[operator]
        checks = await session.scalar(
            select(func.count()).select_from(Event).where(Event.type == EventType.HEALTH_CHECK)
        )
    assert checks == 6


# --- search (ADR-0005): vectors, stemming, accents, ranking -------------------------------


async def _search_index(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> Any:
    from fdpindex.search.indexer import SearchIndexer
    from fdpindex.search.profile import default_profile
    from fdpindex.search.query import SearchService
    from tests.fixtures.search_corpus import ALPHA, BETA, serve

    service, jobs, network, extra = index
    factory, _ = extra  # type: ignore[misc]
    serve(network)
    indexer = SearchIndexer(factory, default_profile(), debounce=0)  # type: ignore[arg-type]
    service.add_listener(indexer.notify)
    for url in (ALPHA, BETA):
        outcome = await service.accept_ping(
            url, remote_addr="203.0.113.60", method="POST", path="/", headers={}, body="{}"
        )
        service.after_trigger(outcome.event_id)
    await jobs.drain()
    await indexer.sync_once()
    return SearchService(factory, service._settings, indexer)  # type: ignore[arg-type]


async def test_search_stems_folds_accents_and_ranks(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    from fdpindex.search.query import SearchParams

    search = await _search_index(index)

    async def titles(**kwargs: Any) -> list[str]:
        result = await search.search(SearchParams(facets=[], **kwargs))
        return [hit["title"] for hit in result["hits"]]

    # Stemming: "registries" finds "registry"; accents fold both ways.
    assert await titles(q="registries") == ["Breast cancer registry"]
    assert await titles(q="cafe") == ["Genome cohort"]
    assert await titles(q="Gezondheidscatalogus") == ["Alpha health catalog"]
    # websearch syntax: phrases, or, exclusion.
    assert await titles(q='"lung function"') == ["Lung function tests"]
    # (the English stemmer makes "genome" and "genomics" one word)
    assert set(await titles(q="genome or lungs")) == {
        "Genome cohort",
        "Genomics catalogue",
        "Lung function tests",
    }
    assert await titles(q="cancer -lungs") == ["Breast cancer registry"]
    # A title match outranks keyword and description matches.
    ranked = await search.search(SearchParams(q="cancer", facets=[]))
    assert ranked["hits"][0]["title"] == "Breast cancer registry"
    assert ranked["hits"][0]["score"] == 1.0
    assert all(0 < hit["score"] <= 1 for hit in ranked["hits"])
    # Theme labels and publisher names are searchable.
    assert set(await titles(q="health", types=["dataset"])) == {
        "Breast cancer registry",
        "Lung function tests",
    }
    assert await titles(q="pulmonology") == ["Lung function tests"]
    # Partial words match (prefix), below whole words.
    assert await titles(q="regist") == ["Breast cancer registry"]
    assert set(await titles(q="pulmo lab")) == {"Lung function tests"}
    assert await titles(q="genom", types=["dataset"]) == ["Genome cohort"]
    # Input that is not valid tsquery syntax never fails.
    for odd in ("(( & | !! :*", "a:* & b", "'quote", "x\\y", "-", "or"):
        assert isinstance(await titles(q=odd), list)
    # Facets and filters run on PostgreSQL too.
    result = await search.search(
        SearchParams(filters={"keyword": ["cancer"]}, facets=["theme", "fdp", "type"])
    )
    assert result["totalResults"] == 2
    assert result["facets"]["fdp"]["buckets"][0]["count"] == 2
    detail = await search.resource(result["hits"][0]["id"])
    assert detail["parents"][0]["type"] == "fdp"
    # Temporal coverage overlaps on date columns (TASKS 8.3).
    from datetime import date

    assert set(await titles(temporal=(date(2022, 6, 1), None))) == {
        "Lung function tests",
        "Genome cohort",
    }
    assert await titles(temporal=(date(2012, 1, 1), date(2013, 12, 31))) == [
        "Breast cancer registry"
    ]
    # Number ranges on value_num (TASKS 8.5).
    assert await titles(numbers={"records": (1000.0, None)}) == ["Breast cancer registry"]
    assert await titles(numbers={"records": (None, 999.0)}) == []
    # Copies across FDPs collapse to one, ranked by relevance (TASKS 8.8).
    collapsed = await search.search(
        SearchParams(q="lung or genome", types=["dataset"], collapse=True, facets=[])
    )
    assert (collapsed["totalResults"], collapsed["resources"]) == (1, 2)
    assert collapsed["hits"][0]["duplicateCount"] == 1
    # title: matches the title only, stemmed (TASKS 9.1).
    assert await titles(q="title:registries") == ["Breast cancer registry"]
    assert await titles(q="title:biopsies") == []
    assert await titles(q="description:biopsy") == ["Breast cancer registry"]
    assert set(await titles(q="theme:Gezondheid", types=["dataset"])) == {
        "Breast cancer registry",
        "Lung function tests",
    }
    # Typed tests on values (TASKS 9.2).
    from fdpindex.search.query import ValueTest

    assert await titles(tests=[ValueTest("keyword", "prefix", "LUN")]) == ["Lung function tests"]
    # Keyset paging walks every hit once (TASKS 9.4).
    for sort in ("modified", "title", "issued,asc"):
        walked: list[str] = []
        cursor: str | None = "*"
        while cursor is not None:
            page = await search.search(SearchParams(sort=sort, size=3, facets=[], cursor=cursor))
            walked += [h["iri"] for h in page["hits"]]
            cursor = page["nextCursor"]
        assert len(walked) == len(set(walked)) == 8, sort
    # The area filter on the four columns (TASKS 9.8).
    assert await titles(bbox=(13.0, 52.0, 14.0, 53.0)) == ["Lung function tests"]
    # Did you mean: trigram candidates from titles (TASKS 10.2).
    misspelt = await search.search(SearchParams(q="brest cancr", facets=[]))
    assert misspelt["totalResults"] == 0
    assert misspelt["didYouMean"]["q"] == "breast cancer"
    # Highlighting marks stemmed matches (TASKS 8.1).
    marked = await search.search(SearchParams(q="registries biopsy", highlight=True, facets=[]))
    hit = marked["hits"][0]
    assert {"text": "registry", "match": True} in hit["highlight"]["title"]
    assert any(s["match"] and s["text"] == "Biopsies" for s in hit["highlight"]["description"])
    assert "fullDescription" not in hit
    # Suggestions use the trigram index and similarity (TASKS 8.2).
    suggested = await search.suggest("cancer reg", types=["dataset"])
    assert suggested["suggestions"][0]["title"] == "Breast cancer registry"


async def test_large_searches_count_facets_on_a_sample(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    from fdpindex.search.query import SearchParams, SearchService
    from tests.fixtures.search_corpus import EU

    exact: Any = await _search_index(index)

    def large(max_page_percent: float) -> SearchService:
        # Every search counts as large; a sample as big as the corpus takes every match,
        # through TABLESAMPLE SYSTEM (100) or every first id.
        return SearchService(
            exact._session_factory,
            exact._settings,
            exact.indexer,
            sample_above=0,
            sample_size=10_000,
            max_page_percent=max_page_percent,
        )

    heal = f"{EU}data-theme/HEAL"
    for sampled in (large(100.0), large(2.0)):
        cases: list[dict[str, Any]] = [
            {"q": "cancer"},
            {"q": "cancer", "filters": {"theme": [heal]}},
            {"q": "genome or lungs", "types": ["dataset"], "filters": {"keyword": ["!cancer"]}},
        ]
        for params in cases:
            want = await exact.search(SearchParams(**params))
            got = await sampled.search(SearchParams(**params))
            assert got["totalResults"] == want["totalResults"]
            assert [h["iri"] for h in got["hits"]] == [h["iri"] for h in want["hits"]]
            assert got["ranking"] == {"candidates": 5_000}
            for name, facet in got["facets"].items():
                assert facet.pop("approximate") is True
                assert facet == want["facets"][name], (params, name)
    sampled = large(2.0)
    dialog = await sampled.facet(SearchParams(q="cancer"), "keyword")
    assert dialog["approximate"] is True
    assert {b["value"] for b in dialog["buckets"]} == {"cancer", "registry", "lungs", "oncology"}
    # The words-free path (search_value alone) agrees with the join.
    light = await exact.search(SearchParams(types=["dataset", "distribution"]))
    joined = await exact.search(
        SearchParams(types=["dataset", "distribution"], ranges={"harvested": (None, None)})
    )
    assert light["facets"] == joined["facets"]


async def test_search_rows_follow_the_entry(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    from fdpindex.models import SearchResource, SearchValue

    search = await _search_index(index)
    service, _jobs, _network, extra = index
    factory, _ = extra  # type: ignore[misc]
    async with factory() as session:  # type: ignore[operator]
        entry = await session.scalar(
            select(SearchResource.entry_uuid).where(
                SearchResource.iri == "https://fdp.alpha.example"
            )
        )
    await service.delete_entry(entry)
    await search.indexer.sync_once()
    async with factory() as session:  # type: ignore[operator]
        resources = await session.scalar(select(func.count()).select_from(SearchResource))
        values = await session.scalar(select(func.count()).select_from(SearchValue))
        tsv = await session.scalar(
            select(func.count())
            .select_from(SearchResource)
            .where(SearchResource.tsv_lang.is_(None))
        )
    assert resources == 5
    assert values and values > 0
    assert tsv == 0


class _TopicEmbedder:
    """A stand-in for the sentence model: one direction per topic word."""

    name = "topics"
    dimensions = 384
    TOPICS: ClassVar[dict[str, int]] = {
        "cancer": 0,
        "tumour": 0,
        "tumor": 0,
        "oncology": 0,
        "lung": 1,
        "lungs": 1,
        "breathing": 1,
        "pulmonary": 1,
        "genome": 2,
        "genomics": 2,
        "dna": 2,
        "sequencing": 2,
    }

    def embed(self, texts: list[str]) -> list[list[float]]:
        import re

        out: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for word in re.findall(r"\w+", text.lower()):
                if word in self.TOPICS:
                    vector[self.TOPICS[word]] += 1.0
            norm = sum(v * v for v in vector) ** 0.5
            if norm == 0:
                vector[-1], norm = 1.0, 1.0
            out.append([v / norm for v in vector])
        return out


async def test_semantic_search_finds_meaning_the_words_miss(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    from fdpindex.search.query import SearchParams
    from fdpindex.search.semantic import SemanticIndex

    search = await _search_index(index)
    factory, _ = index[3]  # type: ignore[misc]
    semantic = SemanticIndex(factory, _TopicEmbedder())  # type: ignore[arg-type]
    await semantic.ensure_schema()
    await semantic.ensure_schema()  # idempotent
    report = await semantic.sync()
    assert report.embedded >= 8
    assert (await semantic.sync()).embedded == 0  # nothing changed
    search.semantic = semantic

    async def run(q: str, mode: str) -> dict[str, Any]:
        return await search.search(SearchParams(q=q, types=["dataset"], semantic=mode, facets=[]))

    # "tumour" is in no record: the words find nothing, the meaning finds the cancer data.
    assert (await run("tumour", "off"))["totalResults"] == 0
    hybrid = await run("tumour", "hybrid")
    assert hybrid["semantic"]["added"] >= 1
    assert {h["title"] for h in hybrid["hits"]} >= {"Breast cancer registry"}
    assert hybrid["totalResults"] == len(hybrid["hits"])
    only = await run("tumour", "only")
    assert "Breast cancer registry" in {h["title"] for h in only["hits"]}
    # Words that match keep their hits, fused with the nearest meaning.
    words = await run("genome", "hybrid")
    assert words["hits"][0]["title"] == "Genome cohort"
    # Filters hold for the meaning too.
    beta_only = await search.search(
        SearchParams(q="tumour", types=["catalog"], semantic="only", facets=[])
    )
    assert all(h["type"] == "catalog" for h in beta_only["hits"])


async def test_conformance_in_worker_processes(
    index: tuple[IndexService, JobRunner, FakeNetwork, object],
) -> None:
    """Validation in spawned workers, results and reports on PostgreSQL (TASKS 11.3)."""
    from fdpindex.conformance.packs import discover
    from fdpindex.conformance.reports import entry_report, record_page
    from fdpindex.conformance.service import ConformanceService
    from fdpindex.conformance.settings import ConformanceSettingsService
    from fdpindex.conformance.workers import ValidationPool
    from fdpindex.models import ConformanceResult, IndexEntry
    from fdpindex.search.profile_store import ProfileStore

    service, jobs, network, extra = index
    factory, _ = extra  # type: ignore[misc]
    async with factory() as session:  # type: ignore[operator]
        await session.execute(delete(ConformanceResult))
        await session.commit()
    network.standard_fdp()
    outcome = await service.accept_ping(
        "https://fdp.example.org",
        remote_addr="203.0.113.9",
        method="POST",
        path="/",
        headers={},
        body="{}",
    )
    service.after_trigger(outcome.event_id)
    await jobs.drain()
    profile = await ProfileStore(factory).effective()  # type: ignore[arg-type]
    settings = ConformanceSettingsService(factory, discover())  # type: ignore[arg-type]
    conformance = ConformanceService(factory, settings, lambda: profile, ValidationPool(2))  # type: ignore[arg-type]
    try:
        await conformance.sync_once()
    finally:
        await conformance.stop()
    async with factory() as session:  # type: ignore[operator]
        entry = await session.scalar(select(IndexEntry.uuid))
        assert entry is not None
        report = await entry_report(session, entry, [("dcat-ap", "3.0.1")])
        page = await record_page(
            session,
            entry_id=entry,
            pack="dcat-ap",
            status="violations",
            path=None,
            component=None,
            severity="violation",
            offset=0,
            limit=10,
        )
    assert report.records == 3  # root, catalogue, dataset
    assert report.packs[0].records == 3
    # The test FDP's catalogue and dataset lack descriptions (DCAT-AP mandatory).
    assert report.packs[0].violations >= 2
    assert report.problems and report.problems[0].severity == "violation"
    assert page.total == report.packs[0].violations


def test_the_url_key_migration_fills_existing_entries() -> None:
    """0017 adds url_key to entries that predate it (fdpindex.urls)."""
    urls = ("https://A.example.org/", "http://a.example.org", "https://b.example.org/x/")

    async def insert(database_url: str) -> None:
        engine = make_engine(database_url)
        async with engine.begin() as connection:
            for client_url in urls:
                await connection.execute(
                    sqlalchemy.text(
                        "INSERT INTO index_entry (uuid, client_url, state, permit, created_at,"
                        " updated_at, metadata_version, metadata, consecutive_failures,"
                        " harvest_enabled) VALUES (:uuid, :url, 'Unknown', 'ACCEPTED', now(),"
                        " now(), 1, '{}', 0, true)"
                    ),
                    {"uuid": uuid4(), "url": client_url},
                )
        await engine.dispose()

    async def read(database_url: str) -> dict[str, str]:
        engine = make_engine(database_url)
        async with engine.connect() as connection:
            rows = await connection.execute(
                sqlalchemy.text("SELECT client_url, url_key FROM index_entry")
            )
            result = {str(url): str(key) for url, key in rows.all()}
        await engine.dispose()
        return result

    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        config = alembic_config(url)
        config.attributes["configure_logger"] = False
        command.upgrade(config, "0016")
        asyncio.run(insert(url))
        command.upgrade(config, "head")
        keys = asyncio.run(read(url))
    assert keys == {
        "https://A.example.org/": "//a.example.org",
        "http://a.example.org": "//a.example.org",
        "https://b.example.org/x/": "//b.example.org/x",
    }
