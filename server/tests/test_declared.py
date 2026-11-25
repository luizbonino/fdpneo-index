"""Checking records against what they declare (TASKS 15.12)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.conformance.declared import DeclaredResolver, declared
from fdpindex.conformance.packs import discover
from fdpindex.db import utcnow
from fdpindex.main import create_app
from fdpindex.models import DeclaredProfile
from tests.conftest import FDP, ROOT_TTL, FakeNetwork, public_resolver
from tests.test_conformance_service import (
    ADMIN,
    CATALOG,
    TOKEN,
    Api,
    SearchApi,
    dataset,
    rid,
    search_api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit

PROFILE = f"{FDP}/profile/p1"
CORE = f"{FDP}/profile/core"
SCHEMA = f"{FDP}/metadata-schemas/s1"
DCAT_AP = "https://semiceu.github.io/DCAT-AP/releases/3.0.1"

PROFILE_TTL = f"""
@prefix prof: <http://www.w3.org/ns/dx/prof/> .
@prefix dct: <http://purl.org/dc/terms/> .
<{PROFILE}> a prof:Profile ;
    prof:isProfileOf <{CORE}> ;
    prof:hasResource [
        a prof:ResourceDescriptor ;
        dct:conformsTo <https://www.w3.org/TR/shacl/> ;
        prof:hasRole <http://www.w3.org/ns/dx/prof/role/Validation> ;
        prof:hasArtifact <{SCHEMA}>
    ] .
"""
# The FDP's own rule: a dataset has a description.
SCHEMA_TTL = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
<https://example.org/shapes#Dataset> a sh:NodeShape ;
    sh:targetClass dcat:Dataset ;
    sh:property [ sh:path dct:description ; sh:minCount 1 ] .
"""


def declaring(name: str, profile: str, **kwargs: Any) -> str:
    body = dataset(name, **kwargs)
    return body.replace(
        f"<{FDP}/dataset/{name}> a dcat:Dataset ;",
        f"<{FDP}/dataset/{name}> a dcat:Dataset ;\n    dct:conformsTo <{profile}> ;",
    )


def serve(network: FakeNetwork) -> None:
    network.serve(FDP, ROOT_TTL)
    network.serve(f"{FDP}/catalog/c1", CATALOG)  # declares nothing
    network.serve(f"{FDP}/dataset/good", declaring("good", PROFILE))
    network.serve(f"{FDP}/dataset/bad", declaring("bad", PROFILE, description=False))
    network.serve(f"{FDP}/dataset/nodist", declaring("nodist", "https://profiles.example.com/x"))
    network.serve(PROFILE, PROFILE_TTL)
    network.serve(SCHEMA, SCHEMA_TTL)
    # CORE does not answer (404), as on many FDPs.


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


def test_what_a_record_declares() -> None:
    text = (
        f"<{FDP}/d> <http://purl.org/dc/terms/conformsTo> <{PROFILE}> .\n"
        f"<{FDP}/d> <http://purl.org/dc/terms/conformsTo> <{DCAT_AP}/> .\n"
        f"<{FDP}/other> <http://purl.org/dc/terms/conformsTo> <https://x.example/p> .\n"
    )
    assert declared(text, f"{FDP}/d") == [PROFILE, f"{DCAT_AP}/"]
    assert declared(text, f"{FDP}/none") == []


def test_by_default_records_are_checked_against_what_they_declare(api: Api) -> None:
    settings = api.client.get("/index/conformance/settings", headers=ADMIN).json()
    assert (settings["packs"], settings["declared"]) == ([], True)

    report = api.client.get(f"/index/conformance/entries/{api.entry}").json()
    [summary] = report["packs"]
    assert summary["pack"] == "declared"
    # The FDP root and the catalogue declare nothing; nodist declares a profile elsewhere.
    assert (summary["conforms"], summary["violations"]) == (1, 1)
    assert (summary["undeclared"], summary["unresolved"]) == (2, 1)

    bad = api.client.get(f"/index/conformance/records/{rid('bad', api.entry)}").json()
    [result] = bad["results"]
    assert (result["pack"], result["status"]) == ("declared", "violations")
    assert result["findings"][0]["path"] == "http://purl.org/dc/terms/description"
    nodist = api.client.get(f"/index/conformance/records/{rid('nodist', api.entry)}").json()
    [result] = nodist["results"]
    assert result["status"] == "unresolved"
    assert "another site" in result["error"]


def test_a_known_specification_uses_its_pack(api: Api, network: FakeNetwork) -> None:
    network.serve(f"{FDP}/dataset/good", declaring("good", f"{DCAT_AP}/"))
    api.client.post("/index/admin/trigger", json={"clientUrl": FDP}, headers=ADMIN)
    api.settle()
    good = api.client.get(f"/index/conformance/records/{rid('good', api.entry)}").json()
    [result] = good["results"]
    # Checked against DCAT-AP 3.0.1, which the record declares, without fetching anything.
    assert result["status"] in {"conforms", "violations"}
    assert not any(str(r.url).startswith("https://semiceu") for r in network.requests)


def test_declared_checks_can_be_switched_off(api: Api) -> None:
    body = {"packs": [], "declared": False}
    response = api.client.put("/index/conformance/settings", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    api.settle()
    report = api.client.get(f"/index/conformance/entries/{api.entry}").json()
    assert report["packs"] == []


def test_the_statistics_show_what_records_declare(search_api: SearchApi) -> None:  # noqa: F811
    body = {"packs": [], "declared": True}
    response = search_api.client.put("/index/conformance/settings", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    search_api.settle()
    [share] = search_api.client.get("/index/stats").json()["conformance"]
    # The records of this FDP declare nothing: none checked, all counted as undeclared.
    assert (share["pack"], share["title"]["en"]) == ("declared", "What it declares")
    assert (share["checked"], share["conforms"]) == (0, 0)
    assert share["undeclared"] > 0


async def test_resolved_profiles_are_kept_for_a_day(services: Any, network: FakeNetwork) -> None:
    serve(network)
    resolver = DeclaredResolver(services.session_factory, services.outbound, lambda: discover())
    origins = [FDP]
    first = await resolver.resolve([PROFILE, f"{DCAT_AP}/", "urn:x:y"], origins)
    assert first[PROFILE].kind == "shapes" and first[PROFILE].shapes
    assert first[f"{DCAT_AP}/"].kind == "pack"
    assert first["urn:x:y"].kind == "unresolved"
    asked = len(network.requests)
    await resolver.resolve([PROFILE], origins)
    assert len(network.requests) == asked  # from the cache
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(DeclaredProfile).values(fetched_at=utcnow() - timedelta(days=2))
        )
    await resolver.resolve([PROFILE], origins)
    assert len(network.requests) > asked
    # Not a profile and no shapes: unresolved, with the reason.
    network.serve(PROFILE, "@prefix dct: <http://purl.org/dc/terms/> . <x:a> dct:title 'x' .")
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(DeclaredProfile).values(fetched_at=utcnow() - timedelta(days=2))
        )
    again = await resolver.resolve([PROFILE], origins)
    assert again[PROFILE].kind == "unresolved"
    assert "no SHACL" in (again[PROFILE].reason or "")
