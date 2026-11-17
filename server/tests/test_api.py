"""The HTTP surface: reference paths, status codes, shapes, authentication."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.main import create_app
from tests.conftest import FDP, FakeNetwork, public_resolver

pytestmark = pytest.mark.unit

TOKEN = "fdpi_test-admin-token"
ADMIN = {"Authorization": f"Bearer {TOKEN}"}


class Api:
    def __init__(self, client: TestClient, app: FastAPI) -> None:
        self.client = client
        self.app = app

    def drain(self) -> None:
        portal = self.client.portal
        assert portal is not None
        portal.call(self.app.state.jobs.drain)

    def ping(self, url: str = FDP, **kwargs: Any) -> Any:
        response = self.client.post("/", json={"clientUrl": url}, **kwargs)
        self.drain()
        return response


@pytest.fixture
def api(network: FakeNetwork) -> Iterator[Api]:
    config = Settings(
        database_url="sqlite+aiosqlite://",
        admin_tokens=[token_digest(TOKEN)],
        worker_concurrency=1,
    )
    app = create_app(
        config,
        transport=httpx.MockTransport(network.handler),
        resolver=public_resolver,
        create_tables=True,
        configure_logs=False,
    )
    with TestClient(app) as client:
        yield Api(client, app)


def test_ping_contract(api: Api) -> None:
    assert api.ping().status_code == 204
    invalid = api.client.post("/", json={"clientUrl": "not a url"})
    assert invalid.status_code == 400
    body = invalid.json()
    assert {"timestamp", "status", "error", "message", "path", "code"} <= set(body)
    assert api.client.post("/", json={}).status_code == 400
    denied = api.ping("http://localhost:8080")
    assert denied.status_code == 403
    assert denied.json()["message"] == "Client URL is denied: http://localhost:8080"
    # Unknown fields are ignored, as Spring does.
    extra = api.client.post("/", json={"clientUrl": FDP, "version": "1.22"})
    assert extra.status_code == 204


def test_ping_rate_limit_returns_429(api: Api) -> None:
    settings = api.client.get("/index/settings", headers=ADMIN).json()
    settings["ping"]["rateLimitHits"] = 0
    del settings["isDefault"]
    assert api.client.put("/index/settings", json=settings, headers=ADMIN).status_code == 200
    assert api.ping().status_code == 204
    limited = api.ping()
    assert limited.status_code == 429
    assert "Rate limit reached" in limited.json()["message"]


def test_entries_page_shape_and_visibility(api: Api) -> None:
    api.ping()
    page = api.client.get("/index/entries").json()
    assert page["totalElements"] == 1
    assert page["totalPages"] == 1
    assert page["number"] == 0
    entry = page["content"][0]
    assert set(entry) >= {
        "uuid",
        "clientUrl",
        "state",
        "permit",
        "registrationTime",
        "modificationTime",
    }
    assert entry["state"] == "ACTIVE"
    assert entry["permit"] == "ACCEPTED"
    assert entry["title"] == "Demo FDP"
    assert entry["registrationTime"].endswith("Z")

    # Rejected entries disappear for the public, not for administrators.
    api.client.put(f"/index/entries/{entry['uuid']}", json={"permit": "REJECTED"}, headers=ADMIN)
    assert api.client.get("/index/entries").json()["totalElements"] == 0
    assert api.client.get("/index/entries?permit=rejected").json()["totalElements"] == 0
    assert (
        api.client.get("/index/entries?permit=rejected", headers=ADMIN).json()["totalElements"] == 1
    )
    assert api.client.get("/index/entries?permit=all", headers=ADMIN).json()["totalElements"] == 1
    assert api.client.get(f"/index/entries/{entry['uuid']}").status_code == 404
    assert api.client.get(f"/index/entries/{entry['uuid']}", headers=ADMIN).status_code == 200


def test_entry_detail_info_and_all(api: Api) -> None:
    api.ping()
    uuid = api.client.get("/index/entries").json()["content"][0]["uuid"]
    detail = api.client.get(f"/index/entries/{uuid}").json()
    assert detail["currentMetadata"]["repositoryUri"] == FDP
    assert detail["currentMetadata"]["metadata"]["publisherName"] == "Example University"
    assert {e["type"] for e in detail["events"]} == {"IncomingPing", "MetadataRetrieval"}
    assert detail["harvest"]["nodes"] == 3
    assert api.client.get("/index/entries/info").json() == {
        "entriesCount": {
            "ALL": 1,
            "UNKNOWN": 0,
            "ACTIVE": 1,
            "INACTIVE": 0,
            "UNREACHABLE": 0,
            "INVALID": 0,
        }
    }
    assert [e["uuid"] for e in api.client.get("/index/entries/all").json()] == [uuid]
    assert api.client.get(f"/index/entries/{uuid}/events").status_code == 401
    events = api.client.get(f"/index/entries/{uuid}/events", headers=ADMIN).json()
    assert events["totalElements"] == 2
    assert "payload" in events["content"][0]


def test_harvested_data_negotiates_rdf(api: Api) -> None:
    api.ping()
    uuid = api.client.get("/index/entries").json()["content"][0]["uuid"]
    turtle = api.client.get(f"/index/entries/{uuid}/data")
    assert turtle.headers["content-type"].startswith("text/turtle")
    assert "Dataset one" in turtle.text
    jsonld = api.client.get(
        f"/index/entries/{uuid}/data", headers={"Accept": "application/ld+json"}
    )
    assert jsonld.headers["content-type"].startswith("application/ld+json")
    nt = api.client.get(f"/index/entries/{uuid}/data?format=nt")
    assert nt.headers["content-type"].startswith("application/n-triples")
    assert api.client.get(f"/index/entries/{uuid}/data?format=pdf").status_code == 400


def test_admin_endpoints_need_an_administrator(api: Api) -> None:
    for method, path in [
        ("GET", "/index/settings"),
        ("PUT", "/index/settings"),
        ("DELETE", "/index/settings"),
        ("POST", "/index/admin/trigger-all"),
        ("GET", "/index/webhooks"),
    ]:
        assert api.client.request(method, path).status_code == 401, path
    wrong = api.client.get("/index/settings", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert wrong.headers["www-authenticate"].startswith("Bearer")


def test_settings_round_trip(api: Api) -> None:
    current = api.client.get("/index/settings", headers=ADMIN).json()
    assert current["isDefault"] is True
    assert current["ping"]["validDuration"] == "PT168H"
    current["ping"]["validDuration"] = "P14D"
    del current["isDefault"]
    updated = api.client.put("/index/settings", json=current, headers=ADMIN).json()
    assert updated["ping"]["validDuration"] == "PT336H"
    assert updated["isDefault"] is False
    current["ping"]["denyList"] = ["([bad"]
    assert api.client.put("/index/settings", json=current, headers=ADMIN).status_code == 400
    assert api.client.delete("/index/settings", headers=ADMIN).json()["isDefault"] is True


def test_admin_trigger_and_delete(api: Api) -> None:
    assert (
        api.client.post("/index/admin/trigger", json={"clientUrl": FDP}, headers=ADMIN).status_code
        == 204
    )
    api.drain()
    page = api.client.get("/index/entries").json()
    assert page["content"][0]["state"] == "ACTIVE"
    uuid = page["content"][0]["uuid"]
    assert api.client.delete(f"/index/entries/{uuid}").status_code == 401
    assert api.client.delete(f"/index/entries/{uuid}", headers=ADMIN).status_code == 204
    assert api.client.get("/index/entries").json()["totalElements"] == 0


def test_webhook_management(api: Api, network: FakeNetwork) -> None:
    created = api.client.post(
        "/index/webhooks",
        json={
            "payloadUrl": "https://hooks.example.net/x",
            "secret": "s3cret-value",
            "allEvents": True,
        },
        headers=ADMIN,
    )
    assert created.status_code == 201
    hook = created.json()
    assert hook["hasSecret"] is True
    assert "secret" not in hook
    no_secret = api.client.post(
        "/index/webhooks", json={"payloadUrl": "https://hooks.example.net/y"}, headers=ADMIN
    )
    assert no_secret.status_code == 400
    updated = api.client.put(
        f"/index/webhooks/{hook['uuid']}",
        json={
            "payloadUrl": "https://hooks.example.net/x",
            "enabled": False,
            "events": ["NewEntry"],
        },
        headers=ADMIN,
    ).json()
    assert updated["enabled"] is False
    assert updated["events"] == ["NewEntry"]
    ping = api.client.post(f"/index/admin/ping-webhook?webhook={hook['uuid']}", headers=ADMIN)
    assert ping.status_code == 204
    api.drain()
    assert len(network.webhook_calls) == 1  # pings go out even to a disabled webhook: it is a test
    missing = api.client.post(
        "/index/admin/ping-webhook?webhook=00000000-0000-0000-0000-000000000000", headers=ADMIN
    )
    assert missing.status_code == 404
    assert api.client.delete(f"/index/webhooks/{hook['uuid']}", headers=ADMIN).status_code == 204
    assert api.client.get("/index/webhooks", headers=ADMIN).json() == []


def test_service_info_and_health(api: Api) -> None:
    info = api.client.get("/").json()
    assert info["name"] == "FDP Index"
    assert info["auth"] == ["token"]
    assert info["oidc"] is None
    assert api.client.get("/index/config").json() == info
    assert api.client.get("/healthz").json() == {"status": "ok"}
    assert api.client.get("/readyz").json()["status"] == "ok"
