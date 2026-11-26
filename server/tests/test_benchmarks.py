"""Running a network's benchmarks (TASKS 15.10)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from rdflib import Graph
from sqlalchemy import select, update

from fdpindex.benchmarks import (
    Definition,
    champion_endpoint,
    ftr_logs,
    read_champion,
    read_definition,
    result_set_text,
)
from fdpindex.models import BenchmarkResult, HarvestedNode
from fdpindex.settings import BenchmarkSettings
from tests.conftest import FDP, FakeNetwork
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit

BENCH = "https://bench.example.org/onboard.ttl"
SHEET = "https://docs.google.com/spreadsheets/d/SHEET123/edit"
W3ID = "https://w3id.org/FAIR-Champion/assess/algorithm/d/SHEET123"
CHAMPION = "https://tools.ostrails.eu/champion/assess/algorithm/d/SHEET123"
DATASET = f"{FDP}/dataset/d1"

BENCH_TTL = f"""
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix ftr: <https://w3id.org/ftr#> .
@prefix dpv: <https://w3id.org/dpv#> .
<{BENCH}> a dcat:DataService, ftr:Benchmark ;
    dcterms:title "Onboarding Benchmark"^^<http://www.w3.org/2001/XMLSchema#string> ;
    dcat:landingPage "https://fairsharing.org/8166"^^<http://www.w3.org/2001/XMLSchema#anyURI> ;
    ftr:scoringFunction <{SHEET}> ;
    ftr:supportedBy <https://w3id.org/FAIR-Champion> ;
    dpv:IsApplicableFor <https://schema.org/Dataset> .
"""

RESULT_SET = {
    "@context": {"ftr": "https://w3id.org/ftr#"},
    "@graph": [
        {"@id": "urn:set", "@type": ["ftr:TestResultSet", "prov:Collection"]},
        {
            "@id": "urn:out1",
            "@type": "ftr:TestResult",
            "ftr:log": {"@value": "INFO: found a title\nFAILURE: no keyword"},
            "ftr:summary": {"@value": "Summary:"},
            "ftr:outputFromTest": {"@id": "https://tests.example.org/core"},
        },
    ],
}


def champion_answer(*, l1: bool = True) -> dict[str, Any]:
    """FAIR Champion's JSON, as `POST /assess/algorithm/…` gives it."""
    return {
        "metadata": "default",
        "testedguid": DATASET,
        "test_results": {
            "CORE": {"log": "…", "result": "pass", "weight": 1.0},
            "T1": {"log": "…", "result": "pass" if l1 else "fail", "weight": 1.0 if l1 else -1.0},
        },
        "narratives": [
            "Acceptable: core checks passed.",
            "Acceptable at Level 1" if l1 else "Unacceptable at Level 1",
        ],
        "guidances": [[], [] if l1 else [["https://example.org/schema", "The metadata schema"]]],
        "tests": [
            {
                "reference": "CORE",
                "testid": "https://tests.example.org/core",
                "title": "Core quality",
                "metric_url": "https://w3id.org/metric/core",
            },
            {
                "reference": "T1",
                "testid": "https://tests.example.org/l1",
                "title": "Level 1",
                "metric_url": "https://w3id.org/metric/l1",
            },
        ],
        "conditions": [
            {
                "condition": "C0",
                "description": "Core quality",
                "formula": "CORE > 0",
                "success": "Acceptable: core checks passed.",
                "failure": "Unacceptable: core checks failed.",
                "guidance": None,
            },
            {
                "condition": "C1",
                "description": "Level 1",
                "formula": "(CORE > 0) AND (T1 > 0)",
                "success": "Acceptable at Level 1",
                "failure": "Unacceptable at Level 1",
                "guidance": '[[https://example.org/schema, "The metadata schema"]]',
            },
        ],
        "has_errors": False,
        "resultset": json.dumps(RESULT_SET),
    }


def serve_champion(network: FakeNetwork, *, l1: bool = True) -> None:
    network.serve(BENCH, BENCH_TTL)
    # w3id.org redirects with a 307, which keeps the POST.
    network.serve(W3ID, "", status=307)
    network.headers[W3ID] = {"Location": CHAMPION}
    network.serve(CHAMPION, json.dumps(champion_answer(l1=l1)), content_type="application/json")


def champion_calls(network: FakeNetwork) -> list[str]:
    return [
        json.loads(r.content)["guid"]
        for r in network.requests
        if r.method == "POST" and str(r.url) == CHAMPION
    ]


# --- reading a benchmark and an answer --------------------------------------------------


def test_a_benchmark_description_names_runner_algorithm_and_records() -> None:
    graph = Graph().parse(data=BENCH_TTL, format="turtle")
    definition = read_definition(graph, BENCH)
    assert definition.resolved
    assert definition.title == "Onboarding Benchmark"
    assert definition.landing_page == "https://fairsharing.org/8166"
    assert (definition.runner, definition.algorithm) == ("https://w3id.org/FAIR-Champion", SHEET)
    assert definition.applies_to == ("https://schema.org/Dataset",)
    # schema:Dataset is the FDP's datasets, whatever class the profile adds for them.
    assert definition.levels == {"dataset"}
    assert definition.applies(frozenset(), "dataset")
    assert not definition.applies(frozenset(), "fdp")


def test_a_benchmark_the_index_cannot_run_says_why() -> None:
    other = BENCH_TTL.replace("https://w3id.org/FAIR-Champion", "https://runner.example.org")
    definition = read_definition(Graph().parse(data=other, format="turtle"), BENCH)
    assert not definition.resolved
    assert definition.reason == "the index cannot call its runner, https://runner.example.org"
    no_sheet = BENCH_TTL.replace(SHEET, "https://example.org/algorithm")
    definition = read_definition(Graph().parse(data=no_sheet, format="turtle"), BENCH)
    assert definition.reason is not None and "Google Sheet" in definition.reason
    empty = read_definition(Graph(), BENCH)
    assert empty.reason == "not a benchmark description (no ftr:Benchmark)"


def test_which_records_a_benchmark_applies_to() -> None:
    everything = Definition(BENCH, True)
    assert everything.applies(frozenset(), "distribution")
    assert not everything.applies(frozenset({"http://example.org/Thing"}), None)
    biobank = Definition(BENCH, True, applies_to=("http://example.org/Biobank",))
    assert biobank.applies(frozenset({"http://example.org/Biobank"}), None)
    assert not biobank.applies(frozenset(), "dataset")


def test_champion_endpoint() -> None:
    assert champion_endpoint("https://w3id.org/FAIR-Champion/", SHEET) == W3ID
    assert champion_endpoint("https://w3id.org/FAIR-Champion", "https://example.org/x") is None


def test_reading_fair_champions_answer() -> None:
    outcome = read_champion(champion_answer(l1=False))
    assert [(c["id"], c["met"]) for c in outcome.conditions] == [("C0", True), ("C1", False)]
    assert outcome.conditions[1]["message"] == "Unacceptable at Level 1"
    assert outcome.conditions[1]["guidance"] == [
        {"url": "https://example.org/schema", "text": "The metadata schema"}
    ]
    assert [(t["reference"], t["result"]) for t in outcome.tests] == [
        ("CORE", "pass"),
        ("T1", "fail"),
    ]
    assert not outcome.incomplete
    text = result_set_text(outcome.result_set)
    assert text is not None and json.loads(text) == RESULT_SET
    logs = ftr_logs(text)
    assert logs["https://tests.example.org/core"]["log"] == (
        "INFO: found a title\nFAILURE: no keyword"
    )
    # A narrative that is neither message (a formula it could not evaluate): undecided.
    odd = champion_answer()
    odd["narratives"] = ["Invalid formula CORE > 0: …", "There was a problem"]
    assert [c["met"] for c in read_champion(odd).conditions] == [None, None]
    errored = champion_answer()
    errored["test_results"]["T1"]["result"] = "error"
    assert read_champion(errored).incomplete
    with pytest.raises(ValueError, match="not a FAIR Champion result"):
        read_champion({"error": "nope"})


# --- through the index --------------------------------------------------------------------


def _call(app: Api, fn: Any, *args: Any, **kwargs: Any) -> Any:
    portal = app.client.portal
    assert portal is not None
    return portal.call(lambda: fn(*args, **kwargs))


def _setup(app: Api, mode: str = "scheduled", **options: Any) -> str:
    """The test FDP harvested, a profile naming the benchmark, runs in ``mode``."""
    response = app.client.put(
        "/index/network-profile", json={"source": f"benchmarks: [{BENCH}]\n"}, headers=ADMIN
    )
    assert response.status_code == 200, response.text
    settings = app.app.state.settings_service
    current = _call(app, settings.get)
    benchmarks = BenchmarkSettings.model_validate({"mode": mode, "pauseSeconds": 0, **options})
    _call(app, settings.update, current.model_copy(update={"benchmarks": benchmarks}))
    assert app.ping(FDP).status_code == 204
    [entry] = app.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    return entry["uuid"]


def _rows(app: Api) -> list[BenchmarkResult]:
    async def read() -> list[BenchmarkResult]:
        async with app.app.state.session_factory() as session:
            return list(await session.scalars(select(BenchmarkResult)))

    return _call(app, read)


def test_runs_are_off_by_default(api: Api, network: FakeNetwork) -> None:  # noqa: F811
    serve_champion(network)
    api.client.put(
        "/index/network-profile", json={"source": f"benchmarks: [{BENCH}]\n"}, headers=ADMIN
    )
    assert api.ping(FDP).status_code == 204
    [entry] = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    view = api.client.get(f"/index/entries/{entry['uuid']}/benchmarks").json()
    assert view["mode"] == "off"
    [benchmark] = view["benchmarks"]
    assert (benchmark["runnable"], benchmark["reason"]) == (
        False,
        "not read: benchmark runs are off",
    )
    assert _call(api, api.app.state.benchmarks.schedule) == 0
    response = api.client.post(
        f"/index/entries/{entry['uuid']}/benchmarks/runs", json={}, headers=ADMIN
    )
    assert response.status_code == 409
    # Off: nothing left the index for the benchmark, not even its description.
    assert not [r for r in network.requests if "bench.example.org" in str(r.url)]
    assert api.client.get("/index/stats").json()["benchmarks"] is None


def test_scheduled_runs_assess_the_records_the_benchmark_applies_to(
    api: Api,  # noqa: F811
    network: FakeNetwork,
) -> None:
    serve_champion(network, l1=False)
    uuid = _setup(api)
    service = api.app.state.benchmarks
    assert _call(api, service.schedule) == 1
    api.drain()
    # ERDERA-like: schema:Dataset, so the dataset only, not the root or the catalogue.
    assert champion_calls(network) == [DATASET]

    view = api.client.get(f"/index/entries/{uuid}/benchmarks").json()
    assert view["mode"] == "scheduled"
    [benchmark] = view["benchmarks"]
    assert benchmark["title"] == "Onboarding Benchmark"
    assert benchmark["runnable"]
    assert benchmark["records"] == {"total": 1, "assessed": 1, "due": 0, "failed": 0}
    assert [(c["id"], c["met"], c["notMet"]) for c in benchmark["conditions"]] == [
        ("C0", 1, 0),
        ("C1", 0, 1),
    ]
    assert benchmark["root"] is None
    assert benchmark["lastAssessedAt"]

    page = api.client.get(
        f"/index/entries/{uuid}/benchmarks/results", params={"condition": "C1", "met": "false"}
    ).json()
    assert page["total"] == 1
    [item] = page["items"]
    assert (item["record"], item["state"]) == (DATASET, "done")
    assert item["conditions"][1]["guidance"][0]["url"] == "https://example.org/schema"
    met = api.client.get(
        f"/index/entries/{uuid}/benchmarks/results", params={"condition": "C1", "met": "true"}
    ).json()
    assert met["total"] == 0

    detail = api.client.get(f"/index/benchmark-results/{item['id']}").json()
    assert detail["entry"] == uuid
    assert detail["tests"][0]["log"].endswith("FAILURE: no keyword")
    response = api.client.get(detail["resultSet"])
    assert response.headers["content-type"].startswith("application/ld+json")
    assert response.json() == RESULT_SET

    stats = api.client.get("/index/stats").json()["benchmarks"]
    [summary] = stats
    assert summary["fdps"] == 1
    assert [(c["id"], c["fdps"], c["records"], c["assessed"]) for c in summary["conditions"]] == [
        ("C0", 1, 1, 1),
        ("C1", 0, 0, 1),
    ]

    # Planned again: nothing changed, so no call.
    assert _call(api, service.plan, _uuid(uuid)) == 0
    # The record changes: due again, and its last result stays visible meanwhile.
    _call(api, _change_hash, api, DATASET)
    assert _call(api, service.plan, _uuid(uuid)) == 1
    [row] = _rows(api)
    assert (row.state, row.assessed_at is not None) == ("due", True)


def _uuid(value: str) -> UUID:
    return UUID(value)


async def _change_hash(app: Api, iri: str) -> None:
    async with app.app.state.session_factory() as session, session.begin():
        await session.execute(
            update(HarvestedNode).where(HarvestedNode.node_iri == iri).values(content_hash="x")
        )


def test_an_administrator_runs_a_benchmark_on_one_record(
    api: Api,  # noqa: F811
    network: FakeNetwork,
) -> None:
    serve_champion(network)
    uuid = _setup(api, mode="onRequest")
    service = api.app.state.benchmarks
    # On request: no planning, no scheduled calls.
    assert _call(api, service.schedule) == 0
    api.drain()
    assert champion_calls(network) == []

    url = f"/index/entries/{uuid}/benchmarks/runs"
    assert api.client.post(url, json={}).status_code == 401
    bad = api.client.post(url, json={"record": "https://elsewhere.example.com/x"}, headers=ADMIN)
    assert bad.status_code == 400
    response = api.client.post(url, json={}, headers=ADMIN)
    assert (response.status_code, response.json()) == (202, {"queued": 1})
    api.drain()
    # The root, though the benchmark is about datasets: an administrator may run it anywhere.
    assert champion_calls(network) == [FDP]
    [benchmark] = api.client.get(f"/index/entries/{uuid}/benchmarks").json()["benchmarks"]
    assert benchmark["root"]["record"] == FDP
    assert [c["met"] for c in benchmark["root"]["conditions"]] == [True, True]


def test_a_call_that_failed_is_retried_and_never_the_records_result(
    api: Api,  # noqa: F811
    network: FakeNetwork,
) -> None:
    serve_champion(network)
    network.serve(CHAMPION, "down", status=503, content_type="text/plain")
    uuid = _setup(api)
    service = api.app.state.benchmarks
    _call(api, service.schedule)
    api.drain()
    [row] = _rows(api)
    assert (row.state, row.attempts, row.error) == ("due", 1, "HTTP 503")
    assert row.next_try_at is not None and row.assessed_at is None
    [benchmark] = api.client.get(f"/index/entries/{uuid}/benchmarks").json()["benchmarks"]
    assert benchmark["records"] == {"total": 1, "assessed": 0, "due": 1, "failed": 0}
    assert benchmark["conditions"] == []
    # Not due before its retry time.
    assert _call(api, service.call_next) is False


def test_the_daily_cap_and_another_runner(api: Api, network: FakeNetwork) -> None:  # noqa: F811
    serve_champion(network)
    own = "https://champion.example.org/assess/algorithm/d/SHEET123"
    network.serve(own, json.dumps(champion_answer()), content_type="application/json")
    _setup(api, dailyCalls=1, runner="https://champion.example.org/")
    service = api.app.state.benchmarks
    _call(api, service.schedule)
    api.drain()
    assert [str(r.url) for r in network.requests if r.method == "POST"] == [own]
    [row] = _rows(api)
    assert (row.state, row.runner) == ("done", "https://champion.example.org")
    _call(api, _change_hash, api, DATASET)
    _call(api, service.plan, row.entry_uuid)
    assert _call(api, service.call_next) is False  # one call a day


def test_results_follow_the_health_reports_visibility(
    api: Api,  # noqa: F811
    network: FakeNetwork,
) -> None:
    serve_champion(network)
    uuid = _setup(api)
    _call(api, api.app.state.benchmarks.schedule)
    api.drain()
    [row] = _rows(api)

    async def admin_only() -> Any:
        return SimpleNamespace(reports="admin")

    stats = api.app.state.stats
    stats._conformance = SimpleNamespace(settings=SimpleNamespace(get=admin_only))
    stats.invalidate()
    assert api.client.get(f"/index/entries/{uuid}/benchmarks").status_code == 401
    assert api.client.get(f"/index/benchmark-results/{row.id}").status_code == 401
    assert api.client.get(f"/index/benchmark-results/{row.id}", headers=ADMIN).status_code == 200
    assert api.client.get("/index/stats").json()["benchmarks"] is None


def test_results_go_with_the_harvested_metadata(api: Api, network: FakeNetwork) -> None:  # noqa: F811
    serve_champion(network)
    uuid = _setup(api)
    _call(api, api.app.state.benchmarks.schedule)
    api.drain()
    assert len(_rows(api)) == 1
    response = api.client.put(
        f"/index/entries/{uuid}", json={"options": {"harvest": False}}, headers=ADMIN
    )
    assert response.status_code == 200, response.text
    api.drain()
    assert _rows(api) == []
