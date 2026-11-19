"""Faceted search: profile, projection, indexer, queries and the HTTP API (on SQLite).

Ranking, stemming and accent folding need PostgreSQL and are tested in
``tests/integration/test_search_postgres.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.errors import BadRequest
from fdpindex.main import create_app
from fdpindex.models import EntryPermit, IndexEntry, SearchLabel, SearchResource, SearchValue
from fdpindex.search.indexer import SearchIndexer
from fdpindex.search.profile import Profile, default_profile, load_profile
from fdpindex.search.project import (
    bucket_label,
    canonical_iri,
    normalise,
    parse_datetime,
    pick_language,
    project_entry,
    public_id,
)
from fdpindex.search.query import SearchParams, SearchService
from tests.conftest import FakeNetwork, Services, ping, public_resolver
from tests.fixtures.search_corpus import ALPHA, BETA, EU, nodes, serve

pytestmark = pytest.mark.unit

TOKEN = "fdpi_test-admin-token"
ADMIN = {"Authorization": f"Bearer {TOKEN}"}


# --- profile -------------------------------------------------------------------


def test_default_profile_is_valid_and_hashed() -> None:
    profile = default_profile()
    assert {"theme", "keyword", "publisher", "format", "license", "accessRights"} <= set(
        profile.facets
    )
    assert profile.expand("dcat:theme") == "http://www.w3.org/ns/dcat#theme"
    assert profile.path_iris("dct:publisher/foaf:name") == (
        "http://purl.org/dc/terms/publisher",
        "http://xmlns.com/foaf/0.1/name",
    )
    assert len(profile.hash) == 64
    assert profile.hash == default_profile().hash


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("fields:\n  title: {kind: iri, paths: [dct:title]}", "text field 'title'"),
        ("fields:\n  x: {kind: iri, paths: [nope:x]}", "unknown prefix"),
        ("fields:\n  x: {kind: iri, paths: [dct:a/dct:b/dct:c]}", "longer than two steps"),
        ("fields:\n  x: {kind: number, paths: [dct:x]}", "needs buckets"),
        ("fields:\n  x: {kind: iri, paths: [dct:x], normalise: missing}", "no normalise map"),
        ("cards:\n  dataset: [nothing]", "unknown field"),
        ("fields:\n  x: {kind: literal, paths: [dct:x], exclude: ['dct:']}", "namespaces apply"),
        ("fields:\n  x: {kind: iri, paths: [dct:x], namespaces: ['nope:']}", "unknown prefix"),
        ("fields:\n  x: {kind: period, paths: [dct:x]}", "only temporal is a period"),
    ],
)
def test_profile_validation(change: str, message: str) -> None:
    base = default_profile().model_dump(mode="json")
    import yaml

    patch = yaml.safe_load(change)
    for key, value in patch.items():
        if key == "fields":
            base["fields"].update(value)
        else:
            base[key] = value
    with pytest.raises(ValueError, match=message):
        Profile.model_validate(base)


def test_a_changed_profile_has_another_hash() -> None:
    data = default_profile().model_dump(mode="json")
    data["fields"]["keyword"]["facet"] = None
    import yaml

    assert load_profile(yaml.safe_dump(data)).hash != default_profile().hash


# --- small functions -------------------------------------------------------------------


def test_helpers() -> None:
    assert normalise("CC-BY-4.0", {"cc-by-4.0": "http://cc/by"}) == "http://cc/by"
    iana = {"https://www.iana.org/assignments/media-types/": ""}
    assert normalise("https://www.iana.org/assignments/media-types/text/csv", iana) == "text/csv"
    assert normalise("something else", iana) == "something else"
    assert canonical_iri("https://publications.europa.eu/x") == "http://publications.europa.eu/x"
    assert canonical_iri("https://example.org/x") == "https://example.org/x"
    assert parse_datetime("2020") is not None
    assert parse_datetime("2020-05").month == 5  # type: ignore[union-attr]
    assert parse_datetime("2020-05-03T10:00:00Z").hour == 10  # type: ignore[union-attr]
    assert parse_datetime("yesterday") is None
    assert parse_datetime("0001-01-01") is None
    assert pick_language({"nl": "Hallo", "": "Hi"}, "en") == ("", "Hi")
    assert pick_language({"nl": "Hallo", "en": "Hello"}, "nl-BE") == ("nl", "Hallo")
    assert pick_language({"de": "Hallo"}, "en") == ("de", "Hallo")
    assert bucket_label("..100") == "under 100"
    assert bucket_label("1000..10000") == "1,000 to 10,000"
    assert bucket_label("100000..") == "100,000 or more"
    assert len(public_id("u", "https://x")) == 22
    assert public_id("u", "https://x") == public_id("u", "https://x")


# --- projection -------------------------------------------------------------------------


def _alpha() -> dict[str, Any]:
    projection = project_entry("alpha", nodes(ALPHA), default_profile())
    return {row.iri: row for row in projection.resources} | {"_labels": projection.labels}


def test_kinds_parents_and_counts() -> None:
    rows = _alpha()
    assert rows[ALPHA].kind == "fdp"
    assert rows[f"{ALPHA}/catalog/c1"].kind == "catalog"
    assert rows[f"{ALPHA}/catalog/c1"].parent_iri == ALPHA
    d1 = rows[f"{ALPHA}/dataset/d1"]
    assert (d1.kind, d1.parent_iri, d1.catalog_iri) == (
        "dataset",
        f"{ALPHA}/catalog/c1",
        f"{ALPHA}/catalog/c1",
    )
    x1 = rows[f"{ALPHA}/distribution/x1"]
    assert (x1.kind, x1.dataset_iri) == ("distribution", d1.iri)
    assert d1.facts["distributionCount"] == 2
    assert rows[f"{ALPHA}/catalog/c1"].facts["datasetCount"] == 2
    assert rows[ALPHA].facts["datasetCount"] == 2


def test_values_are_normalised_and_rolled_up() -> None:
    d1 = _alpha()[f"{ALPHA}/dataset/d1"]
    values = {(v.field, v.value) for v in d1.values}
    assert ("keyword", "cancer") in values  # case folded
    assert ("theme", f"{EU}data-theme/HEAL") in values
    assert ("subject", "http://id.nlm.nih.gov/mesh/D001943") in values
    assert ("keyword", "oncology") in values  # a literal subject is a keyword
    assert not {(f, v) for f, v in values if f in {"theme", "subject"} and v == "oncology"}
    assert ("license", "http://creativecommons.org/licenses/by/4.0/") in values  # from "CC-BY-4.0"
    assert ("accessRights", f"{EU}access-right/PUBLIC") in values  # https folded
    assert ("language", f"{EU}language/ENG") in values  # from "en"
    assert ("publisher", "https://ror.org/alpha") in values
    assert ("contactName", "Data desk") in values
    assert ("records", "1000..10000") in values
    # Rolled up from the distributions:
    assert ("mediaType", "text/csv") in values
    assert ("format", f"{EU}file-type/CSV") in values
    assert ("format", f"{EU}file-type/PARQUET") in values
    assert d1.issued is not None and d1.issued.year == 2020
    assert d1.modified is not None and d1.modified.month == 11


def test_titles_texts_and_completeness() -> None:
    rows = _alpha()
    catalog = rows[f"{ALPHA}/catalog/c1"]
    assert catalog.titles == {"en": "Alpha health catalog", "nl": "Alpha gezondheidscatalogus"}
    assert catalog.title == "Alpha health catalog"
    d1 = rows[f"{ALPHA}/dataset/d1"]
    weights = {(weight, text) for _, weight, text in d1.segments}
    assert ("A", "Breast cancer registry") in weights
    assert ("C", "Biopsies recorded nationwide since 2010.") in weights
    assert ("B", "Health") in weights  # the theme's bundled label
    assert ("B", "Alpha University") in weights  # the publisher's harvested name
    assert d1.completeness > rows[f"{ALPHA}/dataset/d2"].completeness
    labels = {(label.iri, label.label) for label in rows["_labels"]}
    assert ("https://ror.org/alpha", "Alpha University") in labels


def test_themes_and_subjects_are_split_by_vocabulary() -> None:
    projection = project_entry("beta", nodes(BETA), default_profile())
    g = next(r for r in projection.resources if r.iri == f"{BETA}/dataset/g")
    values = {(v.field, v.value) for v in g.values if v.field in {"theme", "subject"}}
    # Whatever the predicate: EU data themes are themes (https folded), the rest subjects.
    assert values == {
        ("theme", f"{EU}data-theme/TECH"),
        ("subject", "http://edamontology.org/topic_0622"),
    }
    # The literal subject of Alpha's d1 is searched once, as a keyword.
    d1 = _alpha()[f"{ALPHA}/dataset/d1"]
    assert [t for _, _, t in d1.segments].count("oncology") == 1


def test_namespaces_only_for_iri_fields() -> None:
    profile = default_profile()
    theme, subject = profile.fields["theme"], profile.fields["subject"]
    heal = "https://publications.europa.eu/resource/authority/data-theme/HEAL"
    assert profile.accepts(theme, heal) and not profile.accepts(subject, heal)
    assert profile.accepts(subject, "http://www.wikidata.org/entity/Q128581")
    assert not profile.accepts(theme, "http://www.wikidata.org/entity/Q128581")
    assert profile.accepts(theme, "a name, not an IRI")


def test_java_fdp_shapes() -> None:
    projection = project_entry("beta", nodes(BETA), default_profile())
    rows = {row.iri: row for row in projection.resources}
    assert rows[BETA].kind == "fdp"
    assert ("version", "1.17.2") in {(v.field, v.value) for v in rows[BETA].values}
    g = rows[f"{BETA}/dataset/g"]
    assert g.parent_iri == f"{BETA}/catalog/k"
    assert ("license", "http://creativecommons.org/licenses/by/4.0/") in {
        (v.field, v.value) for v in g.values
    }
    assert ("format", "VCF") in {(v.field, v.value) for v in g.values}
    vcf = rows[f"{BETA}/distribution/vcf"]
    assert vcf.title == "vcf" and vcf.facts["titleFallback"] is True
    assert rows[f"{BETA}/api"].kind == "service"
    assert rows[f"{BETA}/api"].parent_iri == f"{BETA}/catalog/k"


def test_ldp_containers_and_blank_node_hashes_are_stable() -> None:
    root = """
<https://fdp.example/> a <https://w3id.org/fdp/fdp-o#MetadataService> ;
  <http://purl.org/dc/terms/title> "Root" .
<https://fdp.example/catalogs> a <http://www.w3.org/ns/ldp#DirectContainer> ;
  <http://www.w3.org/ns/ldp#contains> <https://fdp.example/catalog/1> .
"""
    catalog = """
<https://fdp.example/catalog/1> a <http://www.w3.org/ns/dcat#Catalog> ;
  <http://purl.org/dc/terms/title> "One" .
"""
    from rdflib import Graph

    def nt(text: str) -> str:
        return Graph().parse(data=text, format="turtle").serialize(format="nt")

    pages = [("https://fdp.example/", nt(root)), ("https://fdp.example/catalog/1", nt(catalog))]
    first = project_entry("e", pages, default_profile())
    second = project_entry("e", list(reversed(pages)), default_profile())
    rows = {r.iri: r for r in first.resources}
    assert rows["https://fdp.example/catalog/1"].parent_iri == "https://fdp.example/"
    assert [r.row_hash for r in sorted(first.resources, key=lambda r: r.iri)] == [
        r.row_hash for r in sorted(second.resources, key=lambda r: r.iri)
    ]


def test_temporal_coverage_in_every_form() -> None:
    rows = _alpha()
    d1, d2 = rows[f"{ALPHA}/dataset/d1"], rows[f"{ALPHA}/dataset/d2"]
    assert (d1.temporal_start, d1.temporal_end) == (date(2010, 1, 1), date(2015, 12, 31))
    # schema:startDate, a year, no end: open-ended from the first day of that year.
    assert (d2.temporal_start, d2.temporal_end) == (date(2018, 1, 1), None)
    beta = {r.iri: r for r in project_entry("beta", nodes(BETA), default_profile()).resources}
    g = beta[f"{BETA}/dataset/g"]
    # OWL-Time instants; an end year is its last day.
    assert (g.temporal_start, g.temporal_end) == (date(2020, 1, 1), date(2022, 12, 31))
    assert rows[f"{ALPHA}/catalog/c1"].temporal_start is None
    # The hash of a row without coverage is what it was before coverage existed.
    assert "temporal" not in str(rows[f"{ALPHA}/catalog/c1"].row_hash)


def test_a_node_without_its_subject_is_skipped() -> None:
    page = '<https://other.example/x> <http://purl.org/dc/terms/title> "X" .\n<https://other.example/y> <http://purl.org/dc/terms/title> "Y" .\n'
    assert project_entry("e", [("https://fdp.example/n", page)], default_profile()).resources == []


# --- indexer and queries on SQLite ---------------------------------------------------------


@pytest.fixture
async def search(services: Services, network: FakeNetwork) -> AsyncIterator[SearchService]:
    serve(network)
    indexer = SearchIndexer(services.session_factory, default_profile(), debounce=0)
    services.index.add_listener(indexer.notify)
    for url in (ALPHA, BETA):
        await ping(services, url)
    await indexer.sync_once()
    yield SearchService(services.session_factory, services.settings, indexer)


async def _count(services: Services, model: Any) -> int:
    async with services.session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _entry(services: Services, iri: str) -> Any:
    async with services.session_factory() as session:
        entry = await session.scalar(
            select(SearchResource.entry_uuid).where(SearchResource.iri == iri)
        )
    assert entry is not None
    return entry


async def test_everything_harvested_is_indexed(search: SearchService, services: Services) -> None:
    assert await _count(services, SearchResource) == 11
    result = await search.search(SearchParams())
    # Distributions are not listed by default.
    assert result["totalResults"] == 8
    types = {b["value"]: b["count"] for b in result["facets"]["type"]["buckets"]}
    assert types == {"dataset": 3, "catalog": 2, "fdp": 2, "service": 1, "distribution": 3}
    # Newest first without a query.
    assert result["hits"][0]["title"] == "Breast cancer registry"


async def test_filters_and_disjunctive_facets(search: SearchService) -> None:
    heal = f"{EU}data-theme/HEAL"
    result = await search.search(SearchParams(filters={"theme": [heal]}))
    assert {h["title"] for h in result["hits"]} == {"Breast cancer registry", "Lung function tests"}
    themes = {b["value"]: b for b in result["facets"]["theme"]["buckets"]}
    assert themes[heal]["label"] == "Health"
    assert themes[f"{EU}data-theme/TECH"]["count"] == 2  # d2, and g via dct:subject
    # The theme facet ignores its own filter; others count within it.
    keywords = {b["value"]: b["count"] for b in result["facets"]["keyword"]["buckets"]}
    assert keywords == {"cancer": 2, "registry": 1, "lungs": 1, "oncology": 1}
    subjects = {b["value"]: b["count"] for b in result["facets"]["subject"]["buckets"]}
    assert subjects == {"http://id.nlm.nih.gov/mesh/D001943": 1}
    both = await search.search(
        SearchParams(filters={"theme": [heal], "accessRights": [f"{EU}access-right/RESTRICTED"]})
    )
    assert [h["title"] for h in both["hits"]] == ["Lung function tests"]
    negated = await search.search(SearchParams(types=["dataset"], filters={"keyword": ["!cancer"]}))
    assert [h["title"] for h in negated["hits"]] == ["Genome cohort"]


async def test_text_types_fdp_dates_and_paging(search: SearchService) -> None:
    words = await search.search(SearchParams(q="cancer registry"))
    assert [h["title"] for h in words["hits"]] == ["Breast cancer registry"]
    excluded = await search.search(SearchParams(q="cancer -lungs", types=["dataset"]))
    assert [h["title"] for h in excluded["hits"]] == ["Breast cancer registry"]
    services = await search.search(SearchParams(types=["service"]))
    assert [h["title"] for h in services["hits"]] == ["Beta Beacon"]
    fdp_uuid = next(
        b["value"] for b in services["facets"]["fdp"]["buckets"] if b["label"] == "Beta FDP"
    )
    beta = await search.search(SearchParams(fdps=[__import__("uuid").UUID(fdp_uuid)]))
    assert {h["fdp"]["title"] for h in beta["hits"]} == {"Beta FDP"}
    recent = await search.search(
        SearchParams(ranges={"modified": (parse_datetime("2026-01-01"), None)})
    )
    assert [h["title"] for h in recent["hits"]] == ["Breast cancer registry"]
    first = await search.search(SearchParams(size=3, sort="title,asc"))
    second = await search.search(SearchParams(size=3, page=1, sort="title,asc"))
    titles = [h["title"] for h in first["hits"] + second["hits"]]
    assert titles == sorted(titles, key=str.lower)
    assert len(set(titles)) == 6


async def test_a_country_is_one_spatial_value(search: SearchService) -> None:
    nld = f"{EU}country/NLD"
    result = await search.search(SearchParams(facets=["spatial"], types=["dataset"]))
    buckets = {b["value"]: b for b in result["facets"]["spatial"]["buckets"]}
    # An EU IRI, a GeoNames IRI and an ISO code: one value, labelled from the pack.
    assert buckets[nld]["count"] == 3
    assert buckets[nld]["label"] == "Netherlands"
    assert buckets["https://sws.geonames.org/2759794/"]["count"] == 1
    found = await search.search(SearchParams(filters={"spatial": [nld]}, facets=[]))
    assert len(found["hits"]) == 3


async def test_temporal_coverage_overlaps(search: SearchService) -> None:
    async def titles(low: date | None, high: date | None) -> set[str]:
        result = await search.search(SearchParams(temporal=(low, high), facets=[]))
        return {h["title"] for h in result["hits"]}

    assert await titles(date(2012, 1, 1), date(2013, 12, 31)) == {"Breast cancer registry"}
    # An open end reaches every later period.
    assert await titles(date(2030, 1, 1), None) == {"Lung function tests"}
    assert await titles(date(2022, 6, 1), date(2022, 6, 30)) == {
        "Lung function tests",
        "Genome cohort",
    }
    assert await titles(None, date(2009, 12, 31)) == set()
    # Only resources with a coverage match.
    assert len(await titles(None, None)) == 3
    result = await search.search(SearchParams(q="genome", temporal=(date(2021, 1, 1), None)))
    assert result["hits"][0]["temporal"] == {"start": "2020-01-01", "end": "2022-12-31"}
    assert result["query"]["temporal"] == {"start": "2021-01-01", "end": None}


async def test_hits_carry_values_labels_and_distributions(search: SearchService) -> None:
    result = await search.search(SearchParams(q="breast"))
    hit = result["hits"][0]
    assert hit["fdp"]["title"] == "Alpha FDP"
    assert hit["catalog"]["title"] == "Alpha health catalog"
    assert hit["values"]["publisher"] == [
        {"value": "https://ror.org/alpha", "label": "Alpha University"}
    ]
    assert {v["label"] for v in hit["values"]["format"]} == {"CSV", "Parquet"}
    assert hit["values"]["records"][0]["label"] == "1,000 to 10,000"
    assert [d["title"] for d in hit["distributions"]] == ["CSV export", "Parquet export"]
    assert hit["facts"]["distributionCount"] == 2


async def test_selected_values_stay_in_their_facet(search: SearchService) -> None:
    result = await search.search(
        SearchParams(filters={"keyword": ["genomics"]}, facet_size=1, facets=["keyword"])
    )
    values = [b["value"] for b in result["facets"]["keyword"]["buckets"]]
    assert "genomics" in values and result["facets"]["keyword"]["more"] is True


async def test_facets_count_the_same_with_and_without_the_resource_join(
    search: SearchService,
) -> None:
    # Without words or dates the counts come from search_value alone; a date range
    # that excludes nothing forces the join with search_resource.
    always: dict[str, Any] = {"harvested": (parse_datetime("2000-01-01"), None)}
    cases: list[dict[str, Any]] = [
        {},
        {"types": ["dataset", "distribution"]},
        {"filters": {"theme": [f"{EU}data-theme/HEAL"]}},
        {"filters": {"keyword": ["!cancer"]}, "fdp_state": "ACTIVE"},
    ]
    for kwargs in cases:
        light = await search.search(SearchParams(**kwargs))
        joined = await search.search(SearchParams(**kwargs, ranges=always))
        assert light["totalResults"] == joined["totalResults"]
        assert light["facets"] == joined["facets"], kwargs


async def test_facet_counts_are_cached_until_the_index_changes(search: SearchService) -> None:
    first = await search.search(SearchParams())
    assert search._facet_cache
    first["facets"]["theme"]["buckets"].clear()  # callers get copies
    again = await search.search(SearchParams())
    assert again["facets"]["theme"]["buckets"]
    keys = set(search._facet_cache)
    await search.search(SearchParams(q="cancer"))  # words: not cached
    assert set(search._facet_cache) == keys
    generation = search.indexer.generation
    search.indexer.generation += 1  # as after a projection that wrote rows
    await search.search(SearchParams())
    assert {k[0] for k in search._facet_cache} == {generation, search.indexer.generation}


async def test_one_facet_in_full(search: SearchService) -> None:
    result = await search.facet(SearchParams(), "theme", contains="sci")
    assert [b["label"] for b in result["buckets"]] == ["Science and technology"]


async def test_resource_detail(search: SearchService) -> None:
    hit = (await search.search(SearchParams(q="breast")))["hits"][0]
    detail = await search.resource(hit["id"])
    assert [p["title"] for p in detail["parents"]] == ["Alpha FDP", "Alpha health catalog"]
    assert [c["title"] for c in detail["children"]] == ["CSV export", "Parquet export"]
    assert detail["values"]["contactEmail"][0]["value"] == "mailto:desk@alpha.example"
    graph = await search.resource_graph(hit["id"])
    assert len(graph) > 10
    catalog = await search.resource(hit["catalog"]["id"], lang="nl")
    assert catalog["title"] == "Alpha gezondheidscatalogus"


async def test_landing_and_labels(search: SearchService) -> None:
    landing = await search.landing()
    assert landing["counters"]["dataset"] == 3
    assert landing["fdps"]["total"] == 2
    assert landing["indexing"] == {"pending": 0}
    assert any(f["name"] == "theme" and f["help"]["en"] for f in landing["facets"])
    labels = await search.labels([f"{EU}data-theme/HEAL", "https://ror.org/alpha"], "en")
    assert labels == {f"{EU}data-theme/HEAL": "Health", "https://ror.org/alpha": "Alpha University"}


async def test_unchanged_harvests_write_nothing_and_rejection_clears(
    search: SearchService, services: Services
) -> None:
    indexer = search.indexer
    generation = indexer.generation
    alpha = await _entry(services, ALPHA)
    await services.index.harvest_entry(alpha)
    await indexer.sync_once()
    assert indexer.generation == generation  # nothing changed at the source
    await services.index.update_permit(alpha, EntryPermit.REJECTED, actor="test", remote_addr=None)
    await indexer.sync_once()
    assert await _count(services, SearchResource) == 5
    async with services.session_factory() as session:
        orphans = await session.scalar(
            select(func.count())
            .select_from(SearchValue)
            .where(~SearchValue.resource_id.in_(select(SearchResource.id)))
        )
        labels = await session.scalar(
            select(func.count()).select_from(SearchLabel).where(SearchLabel.entry_uuid == alpha)
        )
    assert (orphans, labels) == (0, 0)
    assert (await search.search(SearchParams(q="breast")))["totalResults"] == 0


async def test_a_changed_node_updates_its_rows(
    search: SearchService, services: Services, network: FakeNetwork
) -> None:
    from tests.fixtures.search_corpus import PAGES, PREFIXES

    page = PAGES[f"{BETA}/dataset/g"].replace("Genome cohort", "Exome cohort")
    network.serve(f"{BETA}/dataset/g", PREFIXES + page)
    await services.index.harvest_entry(await _entry(services, BETA))
    await search.indexer.sync_once()
    titles = {h["title"] for h in (await search.search(SearchParams(q="cohort")))["hits"]}
    assert titles == {"Exome cohort"}


async def test_reindex_forces_a_projection(search: SearchService) -> None:
    generation = search.indexer.generation
    async with search._session_factory() as session:  # pyright: ignore[reportPrivateUsage]
        await session.execute(update(SearchResource).values(row_hash="stale"))
        await session.commit()
    search.indexer.reindex()
    await search.indexer.sync_once()
    assert search.indexer.generation > generation


@pytest.mark.parametrize(
    "params",
    [
        SearchParams(types=["nope"]),  # pyright: ignore[reportArgumentType]
        SearchParams(filters={"nope": ["x"]}),
        SearchParams(size=0),
        SearchParams(size=100, page=100),
        SearchParams(sort="random"),
        SearchParams(fdp_state="UNREACHABLE"),
        SearchParams(q="x" * 513),
        SearchParams(facets=["nope"]),
    ],
)
def test_parameter_validation(params: SearchParams) -> None:
    from fdpindex.errors import BadRequest

    with pytest.raises(BadRequest):
        params.validate(default_profile())


# --- the search as SPARQL ---------------------------------------------------------------


def _sparql(query: str) -> list[str]:
    """``?resource`` of each row, over the fixture FDPs laid out as ``sparql.store`` does.

    pyoxigraph's result objects must be dropped on the thread that made them,
    so only strings leave this function.
    """
    import pyoxigraph as ox

    store = ox.Store()
    for fdp in (ALPHA, BETA):
        graph = ox.NamedNode(fdp)
        for _, ntriples in nodes(fdp):
            for triple in ox.parse(ntriples.encode(), ox.RdfFormat.N_TRIPLES):
                store.add(ox.Quad(triple.subject, triple.predicate, triple.object, graph))
        state = ox.Quad(
            ox.NamedNode(fdp),
            ox.NamedNode("urn:fdpindex:state"),
            ox.Literal("ACTIVE"),
            ox.NamedNode("urn:fdpindex:entries"),
        )
        store.add(state)
    solutions: Any = store.query(query)
    resources = [str(row["resource"].value) for row in solutions]
    del solutions, store
    return resources


def _equivalents() -> list[tuple[str, dict[str, Any]]]:
    heal = f"{EU}data-theme/HEAL"
    return [
        ("all", {}),
        ("one type", {"types": ["dataset"]}),
        ("services", {"types": ["service"]}),
        ("fdps", {"types": ["fdp"]}),
        ("distributions", {"types": ["distribution"]}),
        ("iri", {"filters": {"theme": [heal]}}),
        (
            "two fields",
            {"filters": {"theme": [heal], "accessRights": [f"{EU}access-right/RESTRICTED"]}},
        ),
        ("negated literal", {"types": ["dataset"], "filters": {"keyword": ["!cancer"]}}),
        ("rolled up", {"filters": {"format": [f"{EU}file-type/CSV"]}}),
        (
            "rolled up, both kinds",
            {"types": ["dataset", "distribution"], "filters": {"format": [f"{EU}file-type/CSV"]}},
        ),
        (
            "normalised strings",
            {
                "types": ["dataset", "distribution"],
                "filters": {"license": ["http://creativecommons.org/licenses/by/4.0/"]},
            },
        ),
        ("agent iri", {"filters": {"publisher": ["https://ror.org/alpha"]}}),
        ("theme from dct:subject", {"filters": {"theme": [f"{EU}data-theme/TECH"]}}),
        (
            "subject from dcat:theme",
            {"filters": {"subject": ["http://edamontology.org/topic_0622"]}},
        ),
        (
            "negated subject",
            {"types": ["dataset"], "filters": {"subject": ["!http://id.nlm.nih.gov/mesh/D001943"]}},
        ),
        ("agent name", {"filters": {"publisher": ["Beta Institute"]}}),
        ("number bucket", {"filters": {"records": ["1000..10000"]}}),
        ("language", {"filters": {"language": [f"{EU}language/ENG"]}}),
        ("words", {"q": "cancer registry"}),
        ("excluded word", {"q": "cancer -lungs", "types": ["dataset"]}),
        ("phrase", {"q": '"function tests"'}),
        ("dates", {"ranges": {"modified": (parse_datetime("2026-01-01"), None)}}),
        ("temporal", {"temporal": (date(2012, 1, 1), date(2013, 12, 31))}),
        # The EU IRI, a GeoNames IRI and an ISO code are one country.
        ("country", {"filters": {"spatial": [f"{EU}country/NLD"]}}),
        # An end year ("2022") still reaches a period starting inside that year.
        ("temporal open", {"temporal": (date(2022, 6, 1), None)}),
        ("active FDPs", {"fdp_state": "ACTIVE", "types": ["dataset"]}),
    ]


@pytest.mark.parametrize(("case", "kwargs"), _equivalents(), ids=[c for c, _ in _equivalents()])
async def test_the_search_as_sparql_finds_the_same_resources(
    search: SearchService, case: str, kwargs: dict[str, Any]
) -> None:
    from fdpindex.sparql.gate import check_query

    found = await search.search(SearchParams(size=100, facets=[], **kwargs))
    expected = {h["iri"] for h in found["hits"]}
    assert expected or case == "language", case
    translated = await search.sparql(SearchParams(**kwargs))
    check_query(translated["query"], max_chars=20_000)
    assert set(_sparql(translated["query"])) == expected


async def test_the_search_as_sparql_names_fdps_and_approximations(
    search: SearchService, services: Services
) -> None:
    beta = await _entry(services, f"{BETA}/dataset/g")
    translated = await search.sparql(SearchParams(fdps=[beta], q="genome", sort="title"))
    assert f"VALUES ?fdp {{ <{BETA}> }}" in translated["query"]
    assert translated["approximation"] == ["fulltext"]
    assert "# Where SPARQL cannot do the same" in translated["query"]
    assert _sparql(translated["query"]) == [f"{BETA}/dataset/g"]
    # "or" (which only the PostgreSQL search parses; SQLite matches words).
    either = await search.sparql(SearchParams(q="genome or breast", types=["dataset"]))
    assert set(_sparql(either["query"])) == {f"{BETA}/dataset/g", f"{ALPHA}/dataset/d1"}
    ranged = await search.sparql(
        SearchParams(q="x", ranges={"harvested": (parse_datetime("2026-01-01"), None)})
    )
    assert ranged["approximation"] == ["fulltext", "ranking", "harvested"]
    # Anonymous callers do not learn the address of an FDP that is not accepted.
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).where(IndexEntry.uuid == beta).values(permit=EntryPermit.PENDING)
        )
    hidden = await search.sparql(SearchParams(fdps=[beta]))
    assert BETA not in hidden["query"]
    assert "VALUES ?fdp {  }" in hidden["query"]
    assert BETA in (await search.sparql(SearchParams(fdps=[beta]), admin=True))["query"]


def test_sparql_values_cannot_inject() -> None:
    from fdpindex.search.query import DEFAULT_TYPES
    from fdpindex.search.to_sparql import raw_forms, to_sparql

    profile = default_profile()
    params = SearchParams(q='x" } DROP ALL #', filters={"keyword": ['a") } INSERT { ?s ?p ?o']})
    params.validate(profile)
    query = to_sparql(params, profile, {}, default_types=DEFAULT_TYPES)["query"]
    # Words become separate string literals; the gate sees one SELECT with no update.
    assert '"drop"' in query and '"}"' in query
    assert '"a\\") } insert { ?s ?p ?o"' in query
    from fdpindex.sparql.gate import check_query

    check_query(query, max_chars=20_000)
    forms = raw_forms(f"{EU}file-type/CSV", profile.normalise["format"])
    assert {"csv", "text/csv", f"{EU}file-type/csv".lower()} <= forms
    assert raw_forms("text/csv", profile.normalise["mediaType"]) >= {
        "text/csv",
        "https://www.iana.org/assignments/media-types/text/csv",
    }


# --- HTTP --------------------------------------------------------------------------------


class Api:
    def __init__(self, client: TestClient, app: FastAPI) -> None:
        self.client = client
        self.app = app
        portal = client.portal
        assert portal is not None
        # Tests drive the indexer themselves: the loop shares SQLite's single connection.
        portal.call(app.state.search.indexer.stop)

    def settle(self) -> None:
        portal = self.client.portal
        assert portal is not None
        portal.call(self.app.state.jobs.drain)
        portal.call(self.app.state.search.indexer.sync_once)


@pytest.fixture
def api(network: FakeNetwork) -> Iterator[Api]:
    serve(network)
    config = Settings(
        database_url="sqlite+aiosqlite://",
        admin_tokens=[token_digest(TOKEN)],
        worker_concurrency=1,
        search_enabled=True,
        search_rate_per_minute=20,
    )
    app = create_app(
        config,
        transport=httpx.MockTransport(network.handler),
        resolver=public_resolver,
        create_tables=True,
        configure_logs=False,
        run_scheduler=False,
    )
    with TestClient(app) as client:
        api = Api(client, app)
        for url in (ALPHA, BETA):
            client.post("/", json={"clientUrl": url})
            api.settle()  # one at a time: in-memory SQLite has a single shared connection
        yield api


def test_search_over_http(api: Api) -> None:
    response = api.client.get(
        "/index/search",
        params=[("q", "cancer"), ("theme", f"{EU}data-theme/HEAL"), ("facets", "theme,fdp")],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totalResults"] == 2
    assert set(body["facets"]) == {"theme", "fdp"}
    assert api.client.get("/index/search", params={"facets": "none"}).json()["facets"] == {}
    assert (
        api.client.get("/index/search", params={"modifiedFrom": "2026-01-01"}).json()[
            "totalResults"
        ]
        == 1
    )
    assert (
        api.client.get("/index/search", params={"type": "dataset,service"}).json()["totalResults"]
        == 4
    )
    temporal = api.client.get(
        "/index/search", params={"temporalFrom": "2012", "temporalTo": "2013-06"}
    ).json()
    assert [h["title"] for h in temporal["hits"]] == ["Breast cancer registry"]
    assert temporal["query"]["temporal"] == {"start": "2012-01-01", "end": "2013-06-30"}
    info = api.client.get("/index/config").json()
    assert info["search"]["endpoint"].endswith("/index/search")


@pytest.mark.parametrize(
    "params",
    [
        {"unknown": "x"},
        {"theme2": "x"},
        {"fdp": "not-a-uuid"},
        {"modifiedFrom": "soon"},
        {"temporalFrom": "soon"},
        {"temporalTo": "2010-13"},
        {"page": "one"},
        {"size": "1000"},
    ],
)
def test_bad_parameters_are_400(api: Api, params: dict[str, str]) -> None:
    response = api.client.get("/index/search", params=params)
    assert response.status_code == 400
    assert response.json()["code"] == "index.bad_request"


def test_resource_formats(api: Api) -> None:
    hit = api.client.get("/index/search", params={"q": "genome"}).json()["hits"][0]
    detail = api.client.get(f"/index/search/resources/{hit['id']}")
    assert detail.status_code == 200 and detail.json()["title"] == "Genome cohort"
    turtle = api.client.get(f"/index/search/resources/{hit['id']}", params={"format": "ttl"})
    assert turtle.headers["content-type"].startswith("text/turtle")
    assert "Genome cohort" in turtle.text
    jsonld = api.client.get(
        f"/index/search/resources/{hit['id']}", headers={"Accept": "application/ld+json"}
    )
    assert jsonld.headers["content-type"].startswith("application/ld+json")
    assert api.client.get("/index/search/resources/" + "A" * 22).status_code == 404
    assert api.client.get("/index/search/resources/short").status_code == 400


def test_landing_labels_and_facets_over_http(api: Api) -> None:
    landing = api.client.get("/index/search/landing").json()
    assert landing["counters"]["fdp"] == 2
    labels = api.client.get("/index/search/labels", params=[("iri", f"{EU}data-theme/HEAL")]).json()
    assert labels == {f"{EU}data-theme/HEAL": "Health"}
    facet = api.client.get("/index/search/facets", params={"field": "keyword"}).json()
    assert {b["value"] for b in facet["buckets"]} >= {"cancer", "genomics"}


def test_admin_routes(api: Api) -> None:
    assert api.client.get("/index/search/stats").status_code == 401
    assert api.client.post("/index/search/reindex").status_code == 401
    stats = api.client.get("/index/search/stats", headers=ADMIN).json()
    assert {e["resources"] for e in stats["entries"]} == {6, 5}
    assert api.client.get("/index/search/profile", headers=ADMIN).json()["hash"]
    assert api.client.post("/index/search/reindex", headers=ADMIN).status_code == 202


def test_anonymous_searches_are_rate_limited(api: Api) -> None:
    statuses = [
        api.client.get("/index/search", params={"facets": "none"}).status_code for _ in range(25)
    ]
    assert statuses.count(429) >= 5
    assert api.client.get("/index/search", headers=ADMIN).status_code == 200


def test_search_can_be_switched_off(network: FakeNetwork) -> None:
    app = create_app(
        Settings(database_url="sqlite+aiosqlite://", search_enabled=False),
        transport=httpx.MockTransport(network.handler),
        resolver=public_resolver,
        create_tables=True,
        configure_logs=False,
        run_scheduler=False,
    )
    with TestClient(app) as client:
        assert client.get("/index/search").status_code == 404
        assert client.get("/index/config").json()["search"] is None


def test_the_search_as_sparql_over_http(api: Api) -> None:
    response = api.client.get(
        "/index/search/sparql", params=[("q", "cancer"), ("theme", f"{EU}data-theme/HEAL")]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approximation"] == ["fulltext", "ranking"]
    assert body["prefixes"]["dcat"] == "http://www.w3.org/ns/dcat#"
    assert "SELECT ?resource" in body["query"]
    assert api.client.get("/index/search/sparql", params={"nope": "x"}).status_code == 400


# --- facets on any property, and the index's default facets (TASKS 8.13) ---------------

DCT = "http://purl.org/dc/terms/"
DCAT_NS = "http://www.w3.org/ns/dcat#"
ACCESS = f"prop:{DCT}accessRights"
KEYWORD = f"prop:{DCAT_NS}keyword"


def test_every_property_is_captured_with_short_values() -> None:
    from fdpindex.search.project import property_field

    projection = project_entry("alpha", nodes(ALPHA), default_profile())
    d1 = next(r for r in projection.resources if r.iri == f"{ALPHA}/dataset/d1")
    captured = {(v.field, v.value) for v in d1.values if v.field.startswith("p:")}
    assert (property_field(f"{DCT}language"), "en") in captured
    assert (property_field(f"{DCAT_NS}keyword"), "Cancer") in captured
    # https folded as everywhere; dates, numbers and blank nodes are not facet values.
    assert (property_field(f"{DCT}accessRights"), f"{EU}access-right/PUBLIC") in captured
    fields = {f for f, _ in captured}
    assert property_field(f"{DCT}issued") not in fields
    assert property_field(f"{DCAT_NS}contactPoint") not in fields
    assert property_field("http://www.w3.org/1999/02/22-rdf-syntax-ns#type") not in fields
    assert projection.properties[property_field(f"{DCT}language")] == f"{DCT}language"


def test_capture_can_be_switched_off() -> None:
    data = default_profile().model_dump(mode="json")
    data["capture"]["properties"] = False
    import yaml

    profile = load_profile(yaml.safe_dump(data))
    projection = project_entry("alpha", nodes(ALPHA), profile)
    assert not any(v.field.startswith("p:") for r in projection.resources for v in r.values)
    assert profile.hash != default_profile().hash


async def test_a_property_is_a_facet_and_a_filter(search: SearchService) -> None:
    result = await search.search(
        SearchParams(types=["dataset"], facets=[KEYWORD, ACCESS], filters={KEYWORD: ["cancer"]})
    )
    assert [h["title"] for h in result["hits"]] == ["Lung function tests"]
    keywords = {b["value"]: b["count"] for b in result["facets"][KEYWORD]["buckets"]}
    assert keywords == {"Cancer": 1, "cancer": 1, "registry": 1, "lungs": 1, "genomics": 1}
    access = result["facets"][ACCESS]["buckets"]
    assert {b["label"] for b in access} == {"Restricted"}  # labelled like any IRI
    assert result["query"]["filters"] == {KEYWORD: ["cancer"]}
    # Hits do not carry the captured values; the resource's triples show them.
    assert not any(k.startswith("p:") for h in result["hits"] for k in h["values"])
    more = await search.facet(SearchParams(types=["dataset"]), KEYWORD, contains="lung")
    assert [b["value"] for b in more["buckets"]] == ["lungs"]


async def test_properties_are_listed_with_their_counts(search: SearchService) -> None:
    listed = await search.properties(["dataset"])
    by_iri = {p["property"]: p for p in listed["properties"]}
    assert by_iri[f"{DCAT_NS}keyword"]["resources"] == 3
    assert by_iri[f"{DCAT_NS}keyword"]["curie"] == "dcat:keyword"
    assert by_iri[f"{DCAT_NS}keyword"]["label"] == "Keyword"
    assert by_iri[f"{DCAT_NS}keyword"]["name"] == KEYWORD
    found = await search.properties(["dataset"], contains="access")
    assert [p["curie"] for p in found["properties"]] == ["dct:accessRights"]


async def test_visitors_get_the_properties_the_index_offers(search: SearchService) -> None:
    from fdpindex.search.settings import FacetSetting, PropertySettings, SearchSettings

    await search.search_settings.update(
        SearchSettings(properties=PropertySettings(hidden=[DCAT_NS]))
    )
    with pytest.raises(BadRequest, match="not offered"):
        await search.search(SearchParams(facets=[KEYWORD]))
    assert (await search.search(SearchParams(facets=[KEYWORD]), admin=True))["facets"][KEYWORD]
    listed = {p["property"] for p in (await search.properties(["dataset"]))["properties"]}
    assert f"{DCT}language" in listed and f"{DCAT_NS}keyword" not in listed
    # Off for visitors, except the properties an administrator made facets.
    await search.search_settings.update(
        SearchSettings(
            facets=[FacetSetting(name=ACCESS, placement="more", label={"en": "Access"})],
            properties=PropertySettings(visitors=False),
        )
    )
    assert (await search.properties(["dataset"]))["properties"] == []
    await search.search(SearchParams(facets=[ACCESS]))
    with pytest.raises(BadRequest, match="not offered"):
        await search.search(SearchParams(filters={f"prop:{DCT}language": ["en"]}))
    with pytest.raises(BadRequest, match="Not a property IRI"):
        await search.search(SearchParams(facets=["prop:not an iri"]), admin=True)


async def test_administrators_set_the_default_facets(search: SearchService) -> None:
    from fdpindex.search.settings import FacetSetting, SearchSettings

    landing = await search.landing()
    names = [f["name"] for f in landing["facets"]]
    assert names[:3] == ["keyword", "theme", "subject"] and "fdp" in names
    assert landing["properties"] == {"visitors": True}
    await search.search_settings.update(
        SearchSettings(
            facets=[
                FacetSetting(name="publisher", placement="main", label={"en": "Who"}),
                FacetSetting(name=ACCESS, placement="main"),
                FacetSetting(name="theme", placement="hidden"),
                FacetSetting(name="keyword", placement="more"),
            ]
        )
    )
    landing = await search.landing()
    facets = {f["name"]: f for f in landing["facets"]}
    assert [f["name"] for f in landing["facets"]][:2] == ["publisher", ACCESS]
    assert "theme" not in facets and facets["keyword"]["group"] == "more"
    assert facets["publisher"]["label"]["en"] == "Who"
    assert facets[ACCESS]["kind"] == "property" and facets[ACCESS]["curie"] == "dct:accessRights"
    # Facets the setting leaves out keep the profile's place after the listed ones.
    assert {"subject", "format", "fdp"} <= set(facets)
    # A search without `facets` counts the main ones.
    result = await search.search(SearchParams())
    assert ACCESS in result["facets"] and "theme" not in result["facets"]
    assert "keyword" not in result["facets"] and "type" in result["facets"]
    with pytest.raises(ValueError, match="unknown facet"):
        await search.search_settings.update(SearchSettings(facets=[FacetSetting(name="nope")]))
    await search.search_settings.reset()
    assert next(f["name"] for f in (await search.landing())["facets"]) == "keyword"


async def test_a_property_filter_as_sparql_finds_the_same_resources(
    search: SearchService,
) -> None:
    params: dict[str, Any] = {
        "types": ["dataset"],
        "filters": {KEYWORD: ["lungs"], ACCESS: [f"!{EU}access-right/PUBLIC"]},
    }
    found = await search.search(SearchParams(size=100, facets=[], **params))
    expected = {h["iri"] for h in found["hits"]}
    assert expected == {f"{ALPHA}/dataset/d2"}
    translated = await search.sparql(SearchParams(**params))
    assert f"<{DCAT_NS}keyword>" in translated["query"] or "dcat:keyword" in translated["query"]
    assert set(_sparql(translated["query"])) == expected


def test_property_facets_and_settings_over_http(api: Api) -> None:
    response = api.client.get(
        "/index/search", params=[(KEYWORD, "lungs"), ("facets", f"theme,{KEYWORD}")]
    )
    assert response.status_code == 200, response.text
    assert response.json()["totalResults"] == 1
    assert KEYWORD in response.json()["facets"]
    listed = api.client.get("/index/search/properties", params={"type": "dataset"}).json()
    assert any(p["name"] == KEYWORD for p in listed["properties"])
    assert api.client.get("/index/search/properties", params={"type": "nope"}).status_code == 400
    assert api.client.get("/index/search/settings").status_code == 401
    assert api.client.put("/index/search/settings", json={}).status_code == 401
    body = {"facets": [{"name": "keyword", "placement": "main"}], "properties": {"visitors": False}}
    saved = api.client.put("/index/search/settings", json=body, headers=ADMIN)
    assert saved.status_code == 200, saved.text
    assert saved.json()["isDefault"] is False
    assert api.client.get("/index/search", params={"facets": KEYWORD}).status_code == 400
    landing = api.client.get("/index/search/landing").json()
    assert landing["facets"][0]["name"] == "keyword" and landing["properties"]["visitors"] is False
    bad = api.client.put(
        "/index/search/settings", json={"facets": [{"name": "nope"}]}, headers=ADMIN
    )
    assert bad.status_code == 400
    reset = api.client.delete("/index/search/settings", headers=ADMIN)
    assert reset.json()["isDefault"] is True


def test_titles_and_descriptions_are_not_captured() -> None:
    from fdpindex.search.project import property_field

    projection = project_entry("alpha", nodes(ALPHA), default_profile())
    fields = {v.field for r in projection.resources for v in r.values}
    assert property_field(f"{DCT}title") not in fields
    assert property_field(f"{DCT}description") not in fields


async def test_theme_names_in_other_languages_are_found(search: SearchService) -> None:
    result = await search.search(SearchParams(q="gezondheid", types=["dataset"]))
    assert {h["title"] for h in result["hits"]} == {"Breast cancer registry", "Lung function tests"}
    german = await search.search(SearchParams(types=["dataset"], lang="de", facets=["theme"]))
    labels = {b["label"] for b in german["facets"]["theme"]["buckets"]}
    assert "Gesundheit" in labels


# --- administrator's profile (TASKS 6.12, 9.3) ---------------------------------------------


def test_an_overlay_adds_replaces_and_removes_fields() -> None:
    from fdpindex.search.profile_store import ProfileError, merge

    base = default_profile()
    merged = merge(
        base,
        {
            "prefixes": {"ex": "https://example.org/ns#"},
            "fields": {
                "fdpVersion": {
                    "kind": "literal",
                    "paths": ["dct:hasVersion"],
                    "facet": "more",
                    "label": {"en": "FDP version"},
                },
                "records": None,
            },
            "normalise": {"format": {"vcf": "https://example.org/formats/VCF"}},
        },
    )
    assert merged.fields["fdpVersion"].facet == "more"
    assert "records" not in merged.fields
    assert merged.normalise["format"]["vcf"] == "https://example.org/formats/VCF"
    assert merged.normalise["format"]["csv"] == base.normalise["format"]["csv"]  # kept
    assert merged.hash != base.hash
    with pytest.raises(ProfileError, match="unknown profile section"):
        merge(base, {"extensions": {}})
    with pytest.raises(ProfileError, match="unknown prefix"):
        merge(base, {"fields": {"x": {"kind": "iri", "paths": ["nope:x"]}}})
    with pytest.raises(ProfileError, match="text field 'title'"):
        merge(base, {"fields": {"title": None}})


async def test_a_changed_profile_reprojects_and_offers_the_new_facet(
    search: SearchService,
) -> None:
    overlay = {
        "fields": {
            "fdpVersion": {
                "kind": "literal",
                "paths": ["dct:hasVersion"],
                "facet": "main",
                "label": {"en": "FDP version"},
            }
        }
    }
    with pytest.raises(BadRequest):
        await search.search(SearchParams(facets=["fdpVersion"]))
    await search.change_profile(overlay)
    assert "fdpVersion" in search.profile.facets
    await search.indexer.sync_once()
    result = await search.search(SearchParams(types=["fdp"], facets=["fdpVersion"]))
    assert {b["value"] for b in result["facets"]["fdpVersion"]["buckets"]} == {"1.17.2"}
    filtered = await search.search(SearchParams(filters={"fdpVersion": ["1.17.2"]}))
    assert [h["title"] for h in filtered["hits"]] == ["Beta FDP"]
    landing = await search.landing()
    assert any(f["name"] == "fdpVersion" for f in landing["facets"])
    assert await search.profiles.overlay() == overlay
    # Removing the overlay restores the bundled profile.
    await search.change_profile(None)
    assert "fdpVersion" not in search.profile.facets
    assert await search.profiles.overlay() is None


def test_the_profile_over_http(api: Api) -> None:
    assert api.client.put("/index/search/profile", json={}).status_code == 401
    yaml_body = (
        "fields:\n  fdpVersion:\n    kind: literal\n    paths: [dct:hasVersion]\n    facet: more\n"
    )
    saved = api.client.put(
        "/index/search/profile",
        content=yaml_body,
        headers={**ADMIN, "Content-Type": "application/yaml"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["overlay"]["fields"]["fdpVersion"]["facet"] == "more"
    assert "fdpVersion" in saved.json()["profile"]["fields"]
    bad = api.client.put(
        "/index/search/profile", json={"fields": {"x": {"kind": "nope"}}}, headers=ADMIN
    )
    assert bad.status_code == 400
    assert "Invalid profile" in bad.json()["message"]
    assert (
        api.client.put(
            "/index/search/profile",
            content=b"[1, 2]",
            headers={**ADMIN, "Content-Type": "application/json"},
        ).status_code
        == 400
    )
    restored = api.client.delete("/index/search/profile", headers=ADMIN)
    assert restored.json()["overlay"] is None
    assert "fdpVersion" not in restored.json()["profile"]["fields"]


async def test_highlights_are_segments_not_html(search: SearchService) -> None:
    result = await search.search(SearchParams(q="registry nationwide", highlight=True, facets=[]))
    hit = result["hits"][0]
    assert hit["highlight"]["title"] == [
        {"text": "Breast cancer ", "match": False},
        {"text": "registry", "match": True},
    ]
    assert {"text": "nationwide", "match": True} in hit["highlight"]["description"]
    assert "fullDescription" not in hit
    plain = await search.search(SearchParams(q="registry", facets=[]))
    assert "highlight" not in plain["hits"][0] and "fullDescription" not in plain["hits"][0]


def test_control_characters_cannot_forge_marks() -> None:
    from fdpindex.search.query import START, STOP, _mark_prefixes, _plain, _segments

    text = _plain(f"evil {START}fake{STOP} registry")
    segments = _segments(_mark_prefixes(text, ["registry"]))
    assert [s["text"] for s in segments if s["match"]] == ["registry"]
    assert START not in "".join(s["text"] for s in segments)


# --- suggestions (TASKS 8.2) -----------------------------------------------------------------


async def test_suggestions_offer_titles_and_filter_values(search: SearchService) -> None:
    result = await search.suggest("regis", types=["dataset"])
    kinds = {(s["kind"], s.get("title") or s.get("label")) for s in result["suggestions"]}
    assert ("resource", "Breast cancer registry") in kinds
    assert ("value", "registry") in kinds
    # Themes by their bundled names, in any language, labelled in the reader's.
    health = await search.suggest("gezond", types=["dataset"], lang="en")
    theme = next(s for s in health["suggestions"] if s.get("field") == "theme")
    assert theme == {
        "kind": "value",
        "field": "theme",
        "value": f"{EU}data-theme/HEAL",
        "label": "Health",
        "count": 2,
    }
    # Publishers by their harvested names.
    alpha = await search.suggest("alpha uni", types=["dataset"])
    assert any(
        s.get("field") == "publisher" and s["value"] == "https://ror.org/alpha"
        for s in alpha["suggestions"]
    )
    assert (await search.suggest("a", types=["dataset"]))["suggestions"] == []
    odd = await search.suggest("100%_\\", types=["dataset"])
    assert odd["suggestions"] == []


def test_suggestions_over_http(api: Api) -> None:
    response = api.client.get("/index/search/suggest", params={"q": "genom"})
    assert response.status_code == 200, response.text
    titles = [s["title"] for s in response.json()["suggestions"] if s["kind"] == "resource"]
    assert "Genome cohort" in titles
    assert (
        api.client.get("/index/search/suggest", params={"q": "x", "type": "nope"}).status_code
        == 400
    )


async def test_identical_titles_are_one_suggestion(
    search: SearchService, services: Services
) -> None:
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(SearchResource)
            .where(SearchResource.title.in_(["Lung function tests", "Genome cohort"]))
            .values(title="Cohort data")
        )
    result = await search.suggest("cohort da", types=["dataset"])
    assert {"kind": "query", "text": "Cohort data", "count": 2} in result["suggestions"]


def test_the_index_default_language_applies_without_a_browser_preference(api: Api) -> None:
    def theme_labels(headers: dict[str, str] | None = None) -> set[str]:
        body = api.client.get(
            "/index/search", params={"facets": "theme", "type": "dataset"}, headers=headers
        ).json()
        return {b["label"] for b in body["facets"]["theme"]["buckets"]}

    assert "Health" in theme_labels()
    saved = api.client.put("/index/search/settings", json={"defaultLanguage": "de"}, headers=ADMIN)
    assert saved.status_code == 200, saved.text
    assert "Gesundheit" in theme_labels()
    assert "Gezondheid" in theme_labels({"Accept-Language": "nl-NL,nl;q=0.9"})
    landing = api.client.get("/index/search/landing").json()
    assert landing["defaultLanguage"] == "de" and "nl" in landing["languages"]
    bad = api.client.put("/index/search/settings", json={"defaultLanguage": "x"}, headers=ADMIN)
    assert bad.status_code in {400, 422}


# --- number and yes/no fields: the HealthDCAT-AP example (TASKS 8.5) -------------------


def _healthdcat() -> dict[str, Any]:
    import yaml

    from fdpindex.search.profile_store import examples

    example = next(e for e in examples() if e.name == "healthdcat-ap")
    return yaml.safe_load(example.text)


def test_numbers_keep_their_value_and_absence_is_no() -> None:
    from fdpindex.search.profile_store import merge

    profile = merge(default_profile(), _healthdcat())
    rows = {r.iri: r for r in project_entry("alpha", nodes(ALPHA), profile).resources}
    d1, d2 = rows[f"{ALPHA}/dataset/d1"], rows[f"{ALPHA}/dataset/d2"]

    def values(row: Any, name: str) -> set[tuple[str, float | None]]:
        return {(v.value, v.num) for v in row.values if v.field == name}

    assert values(d1, "individuals") == {("1000..10000", 1200.0)}
    assert values(d2, "individuals") == {("..100", 40.0)}
    assert values(d1, "minAge") == {("18..65", 18.0)}
    assert values(d1, "hasAnalytics") == {("true", None)}
    # A presence field is "false" where the property is absent, so "No" counts.
    assert values(d2, "hasAnalytics") == {("false", None)}
    assert values(d2, "personalData") == {("true", None)}
    assert values(d1, "personalData") == {("false", None)}


async def test_number_ranges_and_yes_no_facets(search: SearchService) -> None:
    await search.change_profile(_healthdcat())
    await search.indexer.sync_once()
    result = await search.search(
        SearchParams(types=["dataset"], facets=["individuals", "personalData"])
    )
    individuals = result["facets"]["individuals"]
    assert individuals["kind"] == "number"
    # Buckets in the order of their ranges, not their counts.
    assert [b["label"] for b in individuals["buckets"]] == ["under 100", "1,000 to 10,000"]
    personal = result["facets"]["personalData"]
    assert personal["kind"] == "boolean"
    assert [(b["value"], b["label"], b["count"]) for b in personal["buckets"]] == [
        ("true", "Yes", 1),
        ("false", "No", 2),
    ]

    async def titles(**kwargs: Any) -> set[str]:
        found = await search.search(SearchParams(types=["dataset"], facets=[], **kwargs))
        return {h["title"] for h in found["hits"]}

    assert await titles(numbers={"individuals": (100.0, None)}) == {"Breast cancer registry"}
    assert await titles(numbers={"individuals": (None, 40.0)}) == {"Lung function tests"}
    assert await titles(numbers={"individuals": (41.0, 1199.0)}) == set()
    assert await titles(filters={"personalData": ["false"]}) == {
        "Breast cancer registry",
        "Genome cohort",
    }
    # The same as SPARQL: a range on the numbers, "No" as the property's absence.
    for kwargs in (
        {"numbers": {"individuals": (100.0, None)}},
        {"filters": {"personalData": ["false"]}},
        {"filters": {"hasAnalytics": ["true"]}},
    ):
        params: dict[str, Any] = {"types": ["dataset"], **kwargs}
        expected = {h["iri"] for h in (await search.search(SearchParams(**params)))["hits"]}
        translated = await search.sparql(SearchParams(**params))
        assert set(_sparql(translated["query"])) == expected, kwargs
    with pytest.raises(BadRequest):
        await search.search(SearchParams(numbers={"keyword": (1.0, None)}))
    with pytest.raises(BadRequest):
        await search.search(SearchParams(numbers={"individuals": (10.0, 1.0)}))


def test_number_ranges_and_examples_over_http(api: Api) -> None:
    found = api.client.get("/index/search", params={"recordsMin": "1000", "facets": "records"})
    assert [h["title"] for h in found.json()["hits"]] == ["Breast cancer registry"]
    assert found.json()["facets"]["records"]["kind"] == "number"
    assert api.client.get("/index/search", params={"recordsMax": "lots"}).status_code == 400
    assert api.client.get("/index/search", params={"keywordMin": "1"}).status_code == 400
    assert api.client.get("/index/search/profile/examples/healthdcat-ap").status_code == 401
    example = api.client.get("/index/search/profile/examples/healthdcat-ap", headers=ADMIN)
    assert example.status_code == 200
    assert "healthdcatap:numberOfUniqueIndividuals" in example.json()["yaml"]
    assert example.json()["overlay"]["fields"]["individuals"]["kind"] == "number"
    listed = api.client.get("/index/search/profile", headers=ADMIN).json()["examples"]
    assert listed == [{"name": "healthdcat-ap", "title": "HealthDCAT-AP"}]
    missing = api.client.get("/index/search/profile/examples/nope", headers=ADMIN)
    assert missing.status_code == 404
