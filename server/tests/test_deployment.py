"""Deployment profiles: the EHDS bundle of defaults (TASKS 11.6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi.testclient import TestClient

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.main import create_app
from tests.conftest import FDP, FakeNetwork, public_resolver
from tests.test_conformance_service import ADMIN, PREFIXES, TOKEN, SearchApi, dataset, serve

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

CERTIFICATE = "https://certificates.example.org/42"


def certified() -> str:
    return (
        dataset("good", distribution="g1").removesuffix(" .").rstrip()
        + f""" ;
    <http://www.w3.org/ns/dqv#hasQualityAnnotation> [
        a <http://www.w3.org/ns/dqv#QualityCertificate> ;
        <http://www.w3.org/ns/oa#hasBody> <{CERTIFICATE}> ;
        <http://www.w3.org/ns/oa#hasTarget> <{FDP}/dataset/good> ] .
"""
    )


def app_for(network: FakeNetwork, profile: str) -> Any:
    config = Settings(
        database_url="sqlite+aiosqlite://",
        admin_tokens=[token_digest(TOKEN)],
        worker_concurrency=1,
        search_enabled=True,
        conformance_enabled=True,
        conformance_workers=0,
        deployment_profile=profile,  # type: ignore[arg-type]
    )
    return create_app(
        config,
        transport=httpx.MockTransport(network.handler),
        resolver=public_resolver,
        create_tables=True,
        configure_logs=False,
        run_scheduler=False,
    )


@pytest.fixture
def ehds(network: FakeNetwork) -> Iterator[SearchApi]:
    serve(network)
    network.serve(f"{FDP}/dataset/good", PREFIXES + certified())
    with TestClient(app_for(network, "ehds")) as client:
        api = SearchApi(client, client.app)  # type: ignore[arg-type]
        client.post("/", json={"clientUrl": FDP})
        api.settle()
        yield api


def test_ehds_defaults(ehds: SearchApi) -> None:
    settings = ehds.client.get("/index/conformance/settings", headers=ADMIN).json()
    assert [p["key"] for p in settings["packs"]] == ["healthdcat-ap@release-8", "dcat-ap@3.0.1"]
    assert settings["surfaces"]["feed"] == "healthdcat-ap"
    assert settings["isDefault"] is True
    profile = ehds.client.get("/index/search/profile", headers=ADMIN).json()
    assert "healthCategory" in profile["overlay"]["fields"]
    # Changing and resetting returns to the profile's defaults, not the plain ones.
    body = {"packs": [{"key": "dcat-ap@3.0.1"}]}
    ehds.client.put("/index/conformance/settings", json=body, headers=ADMIN)
    reset = ehds.client.delete("/index/conformance/settings", headers=ADMIN).json()
    assert [p["key"] for p in reset["packs"]] == ["healthdcat-ap@release-8", "dcat-ap@3.0.1"]


def test_quality_certificates_are_shown(ehds: SearchApi) -> None:
    body = ehds.client.get(
        "/index/search", params={"facets": "qualityCertified", "type": "dataset"}
    ).json()
    buckets = {b["value"]: b["count"] for b in body["facets"]["qualityCertified"]["buckets"]}
    assert buckets["true"] == 1
    hit = ehds.client.get(
        "/index/search", params={"qualityCertified": "true", "type": "dataset"}
    ).json()["hits"]
    assert [h["iri"] for h in hit] == [f"{FDP}/dataset/good"]
    resource = ehds.client.get(f"/index/search/resources/{hit[0]['id']}").json()
    certificates = [v["value"] for v in resource["values"].get("qualityCertificate", [])]
    assert certificates == [CERTIFICATE]


def test_the_ehds_feed_is_strict(ehds: SearchApi) -> None:
    # No test dataset meets HealthDCAT-AP (its mandatory health properties are missing).
    feed = ehds.client.get("/index/catalog", headers={"Accept": "application/n-triples"})
    assert feed.status_code == 200
    assert "http://www.w3.org/ns/dcat#CatalogRecord" not in feed.text
    assert 'hydra/core#totalItems> "0"' in feed.text


def test_the_default_profile_has_no_overlay(network: FakeNetwork) -> None:
    serve(network)
    with TestClient(app_for(network, "default")) as client:
        profile = client.get("/index/search/profile", headers=ADMIN).json()
        assert profile["overlay"] is None
