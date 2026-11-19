"""SPARQL over the harvested metadata (search design §2.2): gate, library, store, workers, API."""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.main import create_app
from fdpindex.sparql.examples import default_library, expand
from fdpindex.sparql.gate import QueryRejected, check_query
from fdpindex.sparql.prefixes import PREFIXES, with_prefixes
from fdpindex.sparql.service import Limits, SparqlService, negotiate
from fdpindex.sparql.store import EntryFacts, SparqlStore, fingerprint
from fdpindex.sparql.workers import QueryFailed, WorkerPool
from tests.conftest import FDP, FakeNetwork, public_resolver
from tests.fixtures.sparql_corpus import corpus

pytestmark = pytest.mark.unit

TOKEN = "fdpi_test-admin-token"
ADMIN = {"Authorization": f"Bearer {TOKEN}"}


# --- gate ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "form", "has_limit"),
    [
        ("SELECT * WHERE { ?s ?p ?o }", "SELECT", False),
        ("SELECT DISTINCT ?s WHERE { ?s ?p ?o } ORDER BY ?s LIMIT 5", "SELECT", True),
        ("ASK { ?s a dcat:Dataset }", "ASK", False),
        ("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } LIMIT 3", "CONSTRUCT", True),
        ("DESCRIBE <https://fdp.example.org>", "DESCRIBE", False),
    ],
)
def test_gate_accepts_query_forms(query: str, form: str, has_limit: bool) -> None:
    checked = check_query(query, max_chars=1000)
    assert (checked.form, checked.has_limit) == (form, has_limit)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("SELECT * WHERE { SERVICE <http://10.0.0.1/sparql> { ?s ?p ?o } }", "SERVICE"),
        (
            "SELECT * WHERE { ?s ?p ?o OPTIONAL { SERVICE SILENT <http://x> { ?s ?q ?r } } }",
            "SERVICE",
        ),
        ("LOAD <http://example.org/data.ttl>", "Syntax error"),
        ("INSERT DATA { <http://a> <http://b> <http://c> }", "Syntax error"),
        ("DROP ALL", "Syntax error"),
        ("", "empty"),
        ("SELECT * WHERE { ?s ?p ?o " + "x" * 2000, "longer than"),
    ],
)
def test_gate_rejects(query: str, message: str) -> None:
    with pytest.raises(QueryRejected, match=message):
        check_query(query, max_chars=1000)


def test_gate_reports_the_position_of_a_syntax_error() -> None:
    with pytest.raises(QueryRejected) as caught:
        check_query("SELECT ?s\nWHERE { ?s ?p }", max_chars=1000)
    assert caught.value.line == 2


def test_labels() -> None:
    from fdpindex.sparql.labels import humanize

    assert humanize("http://www.w3.org/ns/dcat#landingPage") == "Landing page"
    assert (
        humanize("https://example.org/org/overijssel-medical-research")
        == "Overijssel medical research"
    )
    assert humanize("http://localhost:8000/fdp-api/licenses/cc-by-4.0") == "cc-by-4.0"
    assert (
        humanize("http://publications.europa.eu/resource/authority/data-theme/ENVI")
        == "Environment"
    )
    assert humanize("http://example.org/codes/XYZ") == "XYZ"


def test_negotiation() -> None:
    assert negotiate("SELECT", None, "text/csv, */*") == "csv"
    assert negotiate("SELECT", None, None) == "json"
    assert negotiate("CONSTRUCT", None, "application/sparql-results+json, text/turtle") == "turtle"
    assert negotiate("CONSTRUCT", "nt", None) == "ntriples"
    with pytest.raises(QueryFailed):
        negotiate("ASK", "csv", None)


# --- library ---------------------------------------------------------------------


def _example(example_id: str) -> Any:
    return next(e for e in default_library().examples if e.id == example_id)


def test_every_bundled_example_parses_with_and_without_parameters() -> None:
    library = default_library()
    assert {e.group for e in library.examples} <= set(library.groups)
    for example in library.examples:
        values = {
            p.name: (
                p.choices[0].value
                if p.choices
                else {
                    "iri": "https://x.example/a",
                    "fdp": "https://x.example/",
                    "date": "2024-01-01",
                    "integer": "3",
                }.get(p.type, "text")
            )
            for p in example.parameters
        }
        check_query(with_prefixes(expand(example, values)), max_chars=20_000)


def test_parameters_are_bound_by_values_not_pasted() -> None:
    query = expand(_example("datasets-by-theme"), {"theme": "http://example.org/theme/HEAL"})
    assert "VALUES ?_theme { <http://example.org/theme/HEAL> }" in query
    with pytest.raises(QueryRejected, match="valid IRI"):
        expand(_example("datasets-by-theme"), {"theme": "http://x> } } DROP ALL #"})
    text = expand(_example("datasets-by-words"), {"text": 'a" } } DROP ALL #'})
    assert 'VALUES ?_text { "a\\" } } DROP ALL #" }' in text
    check_query(with_prefixes(text), max_chars=20_000)


def test_parameter_validation() -> None:
    with pytest.raises(QueryRejected, match="required"):
        expand(_example("datasets-by-theme"), {})
    with pytest.raises(QueryRejected, match="not a date"):
        expand(_example("recently-modified"), {"since": "yesterday"})
    with pytest.raises(QueryRejected, match="choices"):
        expand(_example("missing-property"), {"property": "http://evil.example/p"})
    with pytest.raises(QueryRejected, match="Unknown parameter"):
        expand(_example("datasets-by-theme"), {"theme": "http://a/b", "x": "1"})
    # An optional parameter left empty stays unbound.
    assert "?_since {" not in expand(_example("recently-modified"), {})


def test_with_prefixes_sees_prefixes_inside_property_paths() -> None:
    sequence = with_prefixes("SELECT * WHERE { ?d dcat:distribution/dct:license ?l }")
    assert "PREFIX dct:" in sequence and "PREFIX dcat:" in sequence
    inverse = with_prefixes("SELECT * WHERE { ?d ^dcat:dataset|^foaf:x ?c }")
    assert "PREFIX dcat:" in inverse and "PREFIX foaf:" in inverse
    check_query("SELECT * WHERE { ?d dcat:distribution/dct:license ?l }", max_chars=1000)


@pytest.mark.parametrize(
    ("query", "uses"),
    [
        ("SELECT * { SERVICE <http://x> { ?s ?p ?o } }", True),
        ("select * { service silent <http://x> {} }", True),
        ("SELECT * { \\u0053ERVICE <http://x> {} }", True),
        ("SELECT ?service { ?service a dcat:DataService }", False),
        ("SELECT * { ?s ex:service ?o }", False),
        ('SELECT * { ?s ?p \'service\' . ?s ?p """a SERVICE b""" }', False),
        ("SELECT * { ?s ?p <http://x/service> } # SERVICE", False),
    ],
)
def test_the_workers_own_service_check(query: str, uses: bool) -> None:
    from fdpindex.sparql.prefixes import mentions_service

    assert mentions_service(query) is uses


def test_with_prefixes_declares_only_what_is_used() -> None:
    query = with_prefixes('SELECT * WHERE { ?d a dcat:Dataset ; dct:title "x:y" } # foaf:name')
    assert "PREFIX dcat:" in query and "PREFIX dct:" in query
    assert "PREFIX foaf:" not in query
    assert with_prefixes("PREFIX dcat: <x#> SELECT * { ?d a dcat:D }").count("PREFIX dcat:") == 1
    assert "PREFIX fdpindex:" not in with_prefixes(
        "SELECT * { GRAPH <urn:fdpindex:entries> { ?s ?p ?o } }"
    )


# --- store and workers ---------------------------------------------------------------


@pytest.fixture
async def sparql(tmp_path: Path) -> AsyncIterator[SparqlService]:
    store = SparqlStore(tmp_path / "sparql")
    for url, docs in corpus().items():
        store.replace_graph(url, docs, fingerprint(docs))
    store.write_entries(
        [
            EntryFacts(
                "https://fdp.alpha.example", "u1", "ACTIVE", "Alpha FDP", None, None, None, 5
            ),
            EntryFacts(
                "https://fdp.beta.example/", "u2", "UNREACHABLE", "Beta FDP", None, None, None, 5
            ),
        ]
    )
    store.write_void("urn:fdpindex:sparql")
    store.publish()
    pool = WorkerPool(size=1, prefixes=PREFIXES, max_mb=0)
    await pool.start()
    limits = Limits(
        timeout=5,
        admin_timeout=5,
        max_rows=100,
        max_bytes=1_000_000,
        max_query_chars=20_000,
        per_minute=0,
    )
    service = SparqlService(store, pool, sync=None, limits=limits)
    try:
        yield service
    finally:
        await pool.close()
        store.close()


def _bindings(body: bytes) -> list[dict[str, str]]:
    data = json.loads(body)
    return [{k: v["value"] for k, v in b.items()} for b in data["results"]["bindings"]]


async def test_every_bundled_example_runs(sparql: SparqlService) -> None:
    values: dict[str, dict[str, str]] = {
        "datasets-by-words": {"text": "cancer"},
        "datasets-by-theme": {
            "theme": "http://publications.europa.eu/resource/authority/data-theme/HEAL"
        },
        "datasets-by-publisher": {"publisher": "alpha"},
        "datasets-by-licence": {"licence": "http://creativecommons.org/licenses/by/4.0/"},
        "distributions-by-format": {"format": "csv"},
        "temporal-overlap": {"from": "2020-01-01", "to": "2021-01-01"},
        "conforms-to": {"schema": "https://example.org/profiles/catalog"},
        "describe-resource": {"resource": "https://fdp.alpha.example/dataset/d2"},
        "keyword-cooccurrence": {"keyword": "cancer"},
    }
    expected_rows = {
        "datasets-by-words": 2,
        "datasets-by-theme": 2,
        "datasets-by-licence": 1,
        "temporal-overlap": 1,
        "datasets-per-fdp": 2,
        "missing-property": 1,  # Lung function tests: no licence
        "data-services": 1,
        "keyword-cooccurrence": 2,
    }
    for example in sparql.library.examples:
        result = await sparql.query(sparql.expand_example(example.id, values.get(example.id, {})))
        if example.id in expected_rows:
            assert result.rows == expected_rows[example.id], example.id
        else:
            assert result.rows > 0, example.id


async def test_results_in_every_format(sparql: SparqlService) -> None:
    query = "SELECT ?d ?t WHERE { ?d a dcat:Dataset ; dct:title ?t } ORDER BY ?t"
    rows = _bindings((await sparql.query(query)).body)
    assert [r["t"] for r in rows] == [
        "Breast cancer registry",
        "Genome cohort",
        "Lung function tests",
    ]
    csv = (await sparql.query(query, fmt="csv")).body.decode()
    assert csv.splitlines()[0] == "d,t" and "Genome cohort" in csv
    tsv = (await sparql.query(query, fmt="tsv")).body.decode()
    assert tsv.startswith("?d\t?t") and '"Genome cohort"@en' in tsv
    xml = (await sparql.query(query, fmt="xml")).body.decode()
    assert '<literal xml:lang="en">Genome cohort</literal>' in xml
    ask = await sparql.query("ASK { ?s a dcat:DataService }")
    assert json.loads(ask.body)["boolean"] is True
    graph = await sparql.query("DESCRIBE <https://fdp.beta.example/dataset/g>")
    assert graph.media_type.startswith("text/turtle") and b"Genome cohort" in graph.body


async def test_named_graphs_and_the_entries_graph(sparql: SparqlService) -> None:
    rows = _bindings(
        (
            await sparql.query(
                "SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp ORDER BY ?fdp"
            )
        ).body
    )
    assert rows == [
        {"fdp": "https://fdp.alpha.example", "n": "2"},
        {"fdp": "https://fdp.beta.example/", "n": "1"},
    ]
    states = _bindings(
        (await sparql.query("SELECT ?s WHERE { ?fdp fdpindex:state ?s } ORDER BY ?s")).body
    )
    assert [r["s"] for r in states] == ["ACTIVE", "UNREACHABLE"]
    # The sync bookkeeping is not part of the default graph.
    assert (await sparql.query("SELECT * WHERE { ?s fdpindex:fingerprint ?f }")).rows == 0


async def test_rows_are_capped(sparql: SparqlService) -> None:
    result = await sparql.query("SELECT * WHERE { ?s ?p ?o }", max_rows=5)
    assert (result.rows, result.truncated) == (5, True)


async def test_a_slow_query_is_killed_and_the_pool_recovers(sparql: SparqlService) -> None:
    slow = "SELECT (COUNT(*) AS ?n) WHERE { ?a ?b ?c . ?d ?e ?f . ?g ?h ?i . ?j ?k ?l . ?m ?n2 ?o }"
    with pytest.raises(QueryFailed) as caught:
        await sparql.query(slow, timeout=0.5)
    assert caught.value.status == 408
    result = await sparql.query("ASK { ?s ?p ?o }")
    assert json.loads(result.body)["boolean"] is True


async def test_a_cancelled_query_does_not_lose_its_worker(sparql: SparqlService) -> None:
    slow = "SELECT (COUNT(*) AS ?n) WHERE { ?a ?b ?c . ?d ?e ?f . ?g ?h ?i . ?j ?k ?l . ?m ?n2 ?o }"
    for _ in range(2):  # more than the pool's single worker
        task = asyncio.create_task(sparql.query(slow, timeout=30))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    result = await asyncio.wait_for(sparql.query("ASK { ?s ?p ?o }"), 20)
    assert json.loads(result.body)["boolean"] is True


async def test_one_query_at_a_time_per_anonymous_address(sparql: SparqlService) -> None:
    slow = "SELECT (COUNT(*) AS ?n) WHERE { ?a ?b ?c . ?d ?e ?f . ?g ?h ?i . ?j ?k ?l . ?m ?n2 ?o }"
    first = asyncio.create_task(sparql.query_for("203.0.113.1", slow, timeout=1))
    await asyncio.sleep(0.2)
    with pytest.raises(QueryFailed) as caught:
        await sparql.query_for("203.0.113.1", "ASK { ?s ?p ?o }")
    assert caught.value.status == 429
    with pytest.raises(QueryFailed):
        await first  # times out
    ok = await sparql.query_for("203.0.113.1", "ASK { ?s ?p ?o }")
    assert json.loads(ok.body)["boolean"] is True


async def test_labels_for_many_long_iris(sparql: SparqlService) -> None:
    iris = [f"https://fdp.alpha.example/{'x' * 80}/{i}" for i in range(400)]
    labels = await sparql.labels([*iris, "https://fdp.alpha.example/dataset/d1"])
    assert labels == {"https://fdp.alpha.example/dataset/d1": "Breast cancer registry"}


async def test_unknown_dataset_graphs_are_refused(sparql: SparqlService) -> None:
    with pytest.raises(QueryRejected, match="Unknown graph"):
        await sparql.query("ASK { ?s ?p ?o }", default_graph_uri=["urn:fdpindex:sync"])
    one = await sparql.query(
        "SELECT * WHERE { ?d a dcat:Dataset }", default_graph_uri=["https://fdp.beta.example/"]
    )
    assert one.rows == 1


async def test_workers_see_new_data_after_a_change(sparql: SparqlService) -> None:
    query = "SELECT * WHERE { ?s a dcat:Dataset }"
    assert (await sparql.query(query)).rows == 3
    await asyncio.to_thread(sparql.store.drop_graph, "https://fdp.beta.example/")
    assert (await sparql.query(query)).rows == 3  # not published yet
    await asyncio.to_thread(sparql.store.publish)
    assert (await sparql.query(query)).rows == 2


def test_old_snapshots_are_removed_after_the_grace_period(tmp_path: Path) -> None:
    store = SparqlStore(tmp_path / "sparql", keep_seconds=0)
    first = store.snapshot
    assert store.publish() == first  # nothing changed: no new snapshot
    docs = corpus()["https://fdp.beta.example/"]
    store.replace_graph("https://fdp.beta.example/", docs, fingerprint(docs))
    second = store.publish()
    assert second != first and second.exists() and not first.exists()
    store.close()
    reopened = SparqlStore(tmp_path / "sparql")
    assert [p.name for p in (tmp_path / "sparql" / "snapshots").iterdir()] == [
        reopened.snapshot.name
    ]
    reopened.close()


async def test_schema_and_suggestions(sparql: SparqlService) -> None:
    schema = await sparql.schema()
    classes = {c["curie"]: c for c in schema["classes"]}
    assert classes["dcat:Dataset"]["count"] == 3 and classes["dcat:Dataset"]["label"] == "Dataset"
    assert any(
        p["curie"] == "dcat:landingPage" or p["curie"] == "dcat:keyword"
        for p in schema["properties"]
    )
    assert {g["title"] for g in schema["graphs"]} == {"Alpha FDP", "Beta FDP"}
    keywords = await sparql.suggest(
        predicate="http://www.w3.org/ns/dcat#keyword", type_iri=None, text="CAN"
    )
    assert [(k["value"], k["count"]) for k in keywords] == [("cancer", 2)]
    themes = await sparql.suggest(
        predicate="http://www.w3.org/ns/dcat#theme", type_iri=None, text=""
    )
    assert themes[0]["kind"] == "uri" and themes[0]["label"] == "Health"
    datasets = await sparql.suggest(
        predicate=None, type_iri="http://www.w3.org/ns/dcat#Dataset", text="genome"
    )
    assert [d["label"] for d in datasets] == ["Genome cohort"]


async def test_labels_from_vocabularies_and_data(sparql: SparqlService) -> None:
    labels = await sparql.labels(
        [
            "http://publications.europa.eu/resource/authority/data-theme/HEAL",
            "https://fdp.alpha.example/dataset/d1",
            "https://fdp.beta.example/api",
            "https://nowhere.example/x",
            "not an iri",
        ]
    )
    assert labels == {
        "http://publications.europa.eu/resource/authority/data-theme/HEAL": "Health",
        "https://fdp.alpha.example/dataset/d1": "Breast cancer registry",
        "https://fdp.beta.example/api": "Beta Beacon",
    }


async def test_void_counts_only_fdp_metadata(sparql: SparqlService) -> None:
    await asyncio.to_thread(sparql.store.write_void, "urn:fdpindex:sparql")  # a second pass
    classes, properties = sparql.store.void_partitions()
    assert not any(iri.startswith("http://rdfs.org/ns/void#") for iri, _ in classes + properties)
    assert dict(classes)["http://www.w3.org/ns/dcat#Dataset"] == 3


# --- store sync and the HTTP API ----------------------------------------------------


class Api:
    def __init__(self, client: TestClient, app: FastAPI) -> None:
        self.client = client
        self.app = app

    def settle(self) -> None:
        portal = self.client.portal
        assert portal is not None
        portal.call(self.app.state.jobs.drain)
        portal.call(functools.partial(self.app.state.sparql.sync.sync_once, force_void=True))


@pytest.fixture
def api(network: FakeNetwork, tmp_path: Path) -> Iterator[Api]:
    config = Settings(
        database_url="sqlite+aiosqlite://",
        admin_tokens=[token_digest(TOKEN)],
        worker_concurrency=1,
        sparql_enabled=True,
        sparql_dir=tmp_path / "sparql",
        sparql_workers=1,
        sparql_rate_per_minute=5,
        sparql_worker_max_mb=0,
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
        yield Api(client, app)


def test_harvested_metadata_is_queryable(api: Api) -> None:
    assert api.client.post("/", json={"clientUrl": FDP}).status_code == 204
    api.settle()
    response = api.client.get(
        "/index/sparql",
        params={"query": "SELECT ?t WHERE { GRAPH ?g { ?d a dcat:Dataset ; dct:title ?t } }"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/sparql-results+json"
    assert _bindings(response.content) == [{"t": "Dataset one"}]
    assert response.headers["x-fdpindex-rows"] == "1"
    entries = api.client.get(
        "/index/sparql",
        params={
            "query": "SELECT ?title WHERE { GRAPH fdpindex:entries "
            "{ ?f fdpindex:state 'ACTIVE' ; dct:title ?title } }"
        },
        headers=ADMIN,
    )
    assert _bindings(entries.content) == [{"title": "Demo FDP"}]


def test_rejecting_an_entry_removes_its_graph(api: Api) -> None:
    api.client.post("/", json={"clientUrl": FDP})
    api.settle()
    uuid = api.client.get("/index/entries").json()["content"][0]["uuid"]
    assert (
        api.client.put(
            f"/index/entries/{uuid}", json={"permit": "REJECTED"}, headers=ADMIN
        ).status_code
        == 200
    )
    api.settle()
    response = api.client.get(
        "/index/sparql", params={"query": "ASK { ?d a dcat:Dataset }"}, headers=ADMIN
    )
    assert response.status_code == 200, response.text
    assert json.loads(response.content)["boolean"] is False


def test_protocol_post_forms_and_errors(api: Api) -> None:
    api.client.post("/", json={"clientUrl": FDP})
    api.settle()
    body = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
    direct = api.client.post(
        "/index/sparql", content=body, headers={**ADMIN, "Content-Type": "application/sparql-query"}
    )
    form = api.client.post(
        "/index/sparql", data={"query": body}, headers={**ADMIN, "Accept": "text/csv"}
    )
    assert direct.status_code == form.status_code == 200
    assert form.text.startswith("n\r\n")
    bad = api.client.post("/index/sparql", data={"query": "SELECT * WHERE {"}, headers=ADMIN)
    assert bad.status_code == 400 and bad.json()["code"] == "index.sparql"
    service = api.client.post(
        "/index/sparql",
        data={"query": "SELECT * WHERE { SERVICE <http://internal.example/> { ?s ?p ?o } }"},
        headers=ADMIN,
    )
    assert service.status_code == 400 and "SERVICE" in service.json()["message"]
    wrong_type = api.client.post("/index/sparql", json={"query": body}, headers=ADMIN)
    assert wrong_type.status_code == 415


def test_protocol_details(api: Api) -> None:
    api.client.post("/", json={"clientUrl": FDP})
    api.settle()
    form = api.client.post(
        "/index/sparql", data={"query": "ASK { ?s ?p ?o }", "format": "xml"}, headers=ADMIN
    )
    assert form.headers["content-type"] == "application/sparql-results+xml"
    assert api.client.get("/index/sparql", params={"query": " "}).status_code == 400
    ask_csv = api.client.get(
        "/index/sparql", params={"query": "ASK { ?s ?p ?o }", "format": "csv"}, headers=ADMIN
    )
    assert ask_csv.status_code == 406
    xml = api.client.get(
        "/index/sparql",
        params={"query": "SELECT ?t WHERE { ?d dct:title ?t } LIMIT 1", "format": "xml"},
        headers=ADMIN,
    )
    assert "<literal>" in xml.text and "XMLSchema#string" not in xml.text


def test_advertised_urls_ignore_the_host_header(api: Api) -> None:
    api.app.state.config.public_url = "https://index.example.org"
    description = api.client.get("/index/sparql", headers={"Host": "attacker.example"})
    assert "attacker.example" not in description.text
    assert "<https://index.example.org/index/sparql>" in description.text
    info = api.client.get("/index/config", headers={"Host": "attacker.example"}).json()
    assert info["ping"] == "https://index.example.org/"


def test_large_request_bodies_are_refused(api: Api) -> None:
    big = "x" * (2 * 1024 * 1024)
    response = api.client.post(
        "/index/sparql", content=big, headers={"Content-Type": "application/sparql-query"}
    )
    assert response.status_code == 413


def test_service_description_validate_and_config(api: Api) -> None:
    description = api.client.get("/index/sparql")
    assert description.headers["content-type"].startswith("text/turtle")
    assert "sd:Service" in description.text
    ok = api.client.post("/index/sparql/validate", json={"query": "ASK { ?s ?p ?o }"}).json()
    assert ok == {"ok": True, "form": "ASK", "hasLimit": False}
    bad = api.client.post(
        "/index/sparql/validate", json={"query": "SELECT ?x\nWHERE { ?x }"}
    ).json()
    assert bad["ok"] is False and bad["line"] == 2
    info = api.client.get("/index/config").json()
    assert info["sparql"]["endpoint"].endswith("/index/sparql")


def test_library_over_http(api: Api) -> None:
    api.client.post("/", json={"clientUrl": FDP})
    api.settle()
    library = api.client.get("/index/sparql/examples").json()
    assert library["groups"] and any(e["id"] == "datasets-per-fdp" for e in library["examples"])
    expanded = api.client.post(
        "/index/sparql/examples/datasets-by-words/expand", json={"parameters": {"text": "one"}}
    ).json()["query"]
    assert expanded.startswith("PREFIX ") and 'VALUES ?_text { "one" }' in expanded
    run = api.client.post(
        "/index/sparql/examples/datasets-by-words/run",
        json={"parameters": {"text": "one"}},
        headers=ADMIN,
    )
    assert run.status_code == 200 and _bindings(run.content)[0]["title"] == "Dataset one"
    missing = api.client.post("/index/sparql/examples/nope/run", json={}, headers=ADMIN)
    assert missing.status_code == 404
    invalid = api.client.post(
        "/index/sparql/examples/datasets-by-theme/expand", json={"parameters": {}}
    )
    assert invalid.status_code == 400 and "required" in invalid.json()["message"]


def test_anonymous_callers_are_rate_limited(api: Api) -> None:
    statuses = [
        api.client.get("/index/sparql", params={"query": "ASK { ?s ?p ?o }"}).status_code
        for _ in range(7)
    ]
    assert statuses[:5] == [200] * 5 and statuses[5:] == [429, 429]
    admin = api.client.get("/index/sparql", params={"query": "ASK { ?s ?p ?o }"}, headers=ADMIN)
    assert admin.status_code == 200


def test_sparql_can_be_switched_off(network: FakeNetwork) -> None:
    app = create_app(
        Settings(database_url="sqlite+aiosqlite://", sparql_enabled=False),
        transport=httpx.MockTransport(network.handler),
        resolver=public_resolver,
        create_tables=True,
        configure_logs=False,
        run_scheduler=False,
    )
    with TestClient(app) as client:
        assert client.get("/index/sparql").status_code == 404
        assert client.get("/index/config").json()["sparql"] is None


def test_schema_names_properties_by_profile_fields(network: FakeNetwork, tmp_path: Path) -> None:
    config = Settings(
        database_url="sqlite+aiosqlite://",
        worker_concurrency=1,
        sparql_enabled=True,
        sparql_dir=tmp_path / "sparql",
        sparql_workers=1,
        sparql_worker_max_mb=0,
        search_enabled=True,
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
        api.client.post("/", json={"clientUrl": FDP})
        api.settle()
        schema = api.client.get("/index/sparql/schema").json()
    properties = {p["curie"]: p for p in schema["properties"]}
    title = properties["dct:title"]["fields"][0]
    assert (title["name"], title["label"]) == ("title", "Title")
    # dcat:theme feeds two fields of the bundled profile.
    if "dcat:theme" in properties:
        assert {f["name"] for f in properties["dcat:theme"]["fields"]} >= {"theme", "subject"}
    assert "fields" not in properties.get("rdf:type", {})
