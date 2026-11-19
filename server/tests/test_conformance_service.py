"""Validating harvested records and the conformance reports (TASKS 11.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.conformance.records import assemble
from fdpindex.main import create_app
from fdpindex.search.profile_store import ProfileStore
from fdpindex.search.project import public_id
from tests.conftest import FDP, ROOT_TTL, FakeNetwork, public_resolver

if TYPE_CHECKING:
    from collections.abc import Iterator
    from uuid import UUID

    from fastapi import FastAPI

pytestmark = pytest.mark.unit

TOKEN = "fdpi_test-admin-token"
ADMIN = {"Authorization": f"Bearer {TOKEN}"}
EU = "http://publications.europa.eu/resource/authority/"
PREFIXES = """
@prefix dct: <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix vcard: <http://www.w3.org/2006/vcard/ns#> .
"""
CATALOG = f"""{PREFIXES}
<{FDP}/catalog/c1> a dcat:Catalog ;
    dct:title "Catalog one"@en ;
    dct:description "Datasets of the demo FDP."@en ;
    dct:publisher <{FDP}#publisher> ;
    dcat:dataset <{FDP}/dataset/good>, <{FDP}/dataset/bad>, <{FDP}/dataset/nodist> .
<{FDP}#publisher> a foaf:Agent ; foaf:name "Example University" .
"""


def dataset(name: str, *, description: bool = True, distribution: str | None = None) -> str:
    lines = [
        f"<{FDP}/dataset/{name}> a dcat:Dataset ;",
        f'    dct:title "Dataset {name}"@en ;',
        f"    dct:accessRights <{EU}access-right/PUBLIC> ;",
        f"    dct:isPartOf <{FDP}/catalog/c1> ;",
    ]
    if description:
        lines.append(f'    dct:description "About {name}."@en ;')
    if distribution:
        lines.append(f"    dcat:distribution <{FDP}/distribution/{distribution}> ;")
    lines.append(f"    dcat:theme <{EU}data-theme/HEAL> .")
    return PREFIXES + "\n".join(lines)


def distribution(name: str, *, access_url: bool) -> str:
    body = f"<{FDP}/distribution/{name}> a dcat:Distribution"
    if access_url:
        body += f" ;\n    dcat:accessURL <https://data.example.org/{name}.csv>"
    return PREFIXES + body + " ."


def serve(network: FakeNetwork) -> None:
    network.serve(FDP, ROOT_TTL)
    network.serve(f"{FDP}/catalog/c1", CATALOG)
    network.serve(f"{FDP}/dataset/good", dataset("good", distribution="g1"))
    network.serve(f"{FDP}/distribution/g1", distribution("g1", access_url=True))
    network.serve(f"{FDP}/dataset/bad", dataset("bad", description=False))
    network.serve(f"{FDP}/dataset/nodist", dataset("nodist", distribution="n1"))
    network.serve(f"{FDP}/distribution/n1", distribution("n1", access_url=False))


class Api:
    def __init__(self, client: TestClient, app: FastAPI) -> None:
        self.client = client
        self.app = app
        portal = client.portal
        assert portal is not None
        # Tests drive validation themselves: the loop shares SQLite's single connection.
        portal.call(app.state.conformance.stop)

    def call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        portal = self.client.portal
        assert portal is not None
        return portal.call(lambda: fn(*args, **kwargs))

    def settle(self) -> None:
        self.call(self.app.state.jobs.drain)
        self.call(self.app.state.conformance.sync_once)

    @property
    def entry(self) -> str:
        return self.client.get("/index/entries").json()["content"][0]["uuid"]


@pytest.fixture
def api(network: FakeNetwork) -> Iterator[Api]:
    serve(network)
    config = Settings(
        database_url="sqlite+aiosqlite://",
        admin_tokens=[token_digest(TOKEN)],
        worker_concurrency=1,
        conformance_enabled=True,
        conformance_workers=0,
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
        client.post("/", json={"clientUrl": FDP})
        api.settle()
        yield api


def rid(name: str, entry: str) -> str:
    return public_id(entry, f"{FDP}/dataset/{name}")


def test_report_per_fdp(api: Api) -> None:
    entry = api.entry
    report = api.client.get(f"/index/conformance/entries/{entry}").json()
    assert report["records"] == 5  # FDP root, catalogue, three datasets
    [dcat] = report["packs"]
    assert dcat["pack"] == "dcat-ap"
    assert dcat["version"] == "3.0.1"
    assert dcat["records"] == 5
    assert dcat["violations"] == 2  # "bad" lacks a description, "nodist" an access URL
    assert dcat["conforms"] == 3
    paths = {(p["severity"], p["path"]) for p in report["problems"]}
    assert ("violation", "http://purl.org/dc/terms/description") in paths
    assert ("violation", "http://www.w3.org/ns/dcat#accessURL") in paths
    assert report["problems"][0]["severity"] == "violation"


def test_records_and_findings(api: Api) -> None:
    entry = api.entry
    page = api.client.get(
        "/index/conformance/records", params={"entry": entry, "status": "violations"}
    ).json()
    assert page["total"] == 2
    assert {i["iri"] for i in page["items"]} == {f"{FDP}/dataset/bad", f"{FDP}/dataset/nodist"}
    problem = api.client.get(
        "/index/conformance/records",
        params={
            "entry": entry,
            "path": "http://purl.org/dc/terms/description",
            "severity": "violation",
        },
    ).json()
    assert [i["iri"] for i in problem["items"]] == [f"{FDP}/dataset/bad"]
    # The distribution's node is part of its dataset's record.
    nodist = api.client.get(f"/index/conformance/records/{rid('nodist', entry)}").json()
    [result] = nodist["results"]
    assert result["status"] == "violations"
    focus = {f["focusNode"] for f in result["findings"] if f["severity"] == "violation"}
    assert focus == {f"{FDP}/distribution/n1"}
    good = api.client.get(f"/index/conformance/records/{rid('good', entry)}").json()
    assert good["results"][0]["status"] == "conforms"
    assert good["kind"] == "dataset"
    assert api.client.get("/index/conformance/records/unknown").status_code == 404


def test_only_changed_records_are_validated_again(api: Api, network: FakeNetwork) -> None:
    from uuid import UUID

    service = api.app.state.conformance
    entry = UUID(api.entry)
    assert api.call(service.validate_entry, entry) == 0  # nothing changed
    network.serve(f"{FDP}/dataset/bad", dataset("bad"))  # fixed by its holder
    api.call(_forget_last_retrieval, api.app, entry)  # past the 10-minute retrieval limit
    api.client.post("/index/admin/trigger", json={"clientUrl": FDP}, headers=ADMIN)
    api.call(api.app.state.jobs.drain)
    assert api.call(service.validate_entry, entry) == 1
    report = api.client.get(f"/index/conformance/entries/{entry}").json()
    assert report["packs"][0]["violations"] == 1
    assert api.call(service.validate_entry, entry, force=True) == 5


def test_settings_add_a_pack_and_hide_reports(api: Api) -> None:
    entry = api.entry
    assert api.client.get("/index/conformance/settings").status_code == 401
    current = api.client.get("/index/conformance/settings", headers=ADMIN).json()
    assert current == {
        "packs": [{"key": "dcat-ap@3.0.1", "kinds": None, "files": None}],
        "reports": "public",
        "isDefault": True,
    }
    body = {
        "packs": [
            {"key": "dcat-ap@3.0.1"},
            {"key": "health-ri-core@2.0.3", "kinds": ["dataset"]},
        ],
        "reports": "admin",
    }
    response = api.client.put("/index/conformance/settings", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    api.settle()
    assert api.client.get(f"/index/conformance/entries/{entry}").status_code == 401
    report = api.client.get(f"/index/conformance/entries/{entry}", headers=ADMIN).json()
    assert [p["pack"] for p in report["packs"]] == ["dcat-ap", "health-ri-core"]
    assert report["packs"][1]["records"] == 3  # datasets only
    # Back to the defaults: the Health-RI results go.
    api.client.delete("/index/conformance/settings", headers=ADMIN)
    api.settle()
    report = api.client.get(f"/index/conformance/entries/{entry}").json()
    assert [p["pack"] for p in report["packs"]] == ["dcat-ap"]


@pytest.mark.parametrize(
    ("packs", "message"),
    [
        ([{"key": "nope@1"}], "unknown pack"),
        ([{"key": "healthdcat-ap@release-8", "kinds": ["catalog"]}], "does not apply"),
        ([{"key": "dcat-ap@3.0.1", "files": ["nope.ttl"]}], "has no file"),
        ([{"key": "dcat-ap@3.0.1", "files": ["range.ttl"]}], "no shapes file"),
        ([{"key": "dcat-ap@3.0.1"}, {"key": "dcat-ap@3.0.1"}], "twice"),
    ],
)
def test_settings_are_checked(api: Api, packs: list[dict[str, Any]], message: str) -> None:
    response = api.client.put("/index/conformance/settings", json={"packs": packs}, headers=ADMIN)
    assert response.status_code == 400
    assert message in response.text


def test_range_checks_can_be_switched_on(api: Api) -> None:
    files = ["shapes.ttl", "mdr-vocabularies.shape.ttl", "range.ttl"]
    body = {"packs": [{"key": "dcat-ap@3.0.1", "files": files}]}
    assert (
        api.client.put("/index/conformance/settings", json=body, headers=ADMIN).status_code == 200
    )
    api.settle()
    good = api.client.get(f"/index/conformance/records/{rid('good', api.entry)}").json()
    # dct:accessRights must be a dct:RightsStatement: the EU concept is a skos:Concept only.
    findings = good["results"][0]["findings"]
    assert any(
        f["path"] == "http://purl.org/dc/terms/accessRights"
        and f["component"] == "ClassConstraintComponent"
        for f in findings
    )


def test_removed_entries_lose_their_results(api: Api) -> None:
    entry = api.entry
    assert api.client.delete(f"/index/entries/{entry}", headers=ADMIN).status_code in {200, 204}
    api.settle()
    page = api.client.get("/index/conformance/records").json()
    assert page["total"] == 0


def test_validate_again(api: Api) -> None:
    response = api.client.post("/index/conformance/validate", json={}, headers=ADMIN)
    assert response.status_code == 202
    assert response.json() == {"scheduled": "all"}
    assert api.client.post("/index/conformance/validate", json={}).status_code == 401


def test_records_from_nodes() -> None:
    import asyncio

    from fdpindex.db import create_schema, make_engine, make_session_factory

    async def profile() -> Any:
        engine = make_engine("sqlite+aiosqlite://")
        await create_schema(engine)
        try:
            return await ProfileStore(make_session_factory(engine)).effective()
        finally:
            await engine.dispose()

    prof = asyncio.run(profile())
    nt = (
        f"<{FDP}/dataset/a> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<http://www.w3.org/ns/dcat#Dataset> .\n"
        f"<{FDP}/dataset/a> <http://www.w3.org/ns/dcat#distribution> <{FDP}/distribution/a1> .\n"
    )
    dist = (
        f"<{FDP}/distribution/a1> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
        "<http://www.w3.org/ns/dcat#Distribution> .\n"
    )
    nodes = [(f"{FDP}/dataset/a", nt, "h1"), (f"{FDP}/distribution/a1", dist, "h2")]
    [record] = assemble("e", nodes, prof)
    assert record.kind == "dataset"
    assert len(record.nodes) == 2
    [changed] = assemble("e", [nodes[0], (nodes[1][0], dist, "h3")], prof)
    assert changed.content_hash != record.content_hash


async def _forget_last_retrieval(app: FastAPI, entry: UUID) -> None:
    from fdpindex.models import IndexEntry

    async with app.state.session_factory() as session, session.begin():
        row = await session.get(IndexEntry, entry)
        assert row is not None
        row.last_retrieval_at = None
