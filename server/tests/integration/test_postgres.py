"""Against PostgreSQL: migrations, and the index with concurrent jobs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from alembic import command
from sqlalchemy import func, select
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
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as container:
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
