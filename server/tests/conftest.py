"""Shared fixtures: a fake network of FDPs and webhook receivers, and wired services."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

# Never read a developer's .env or environment into tests.
for _key in [k for k in os.environ if k.startswith("FDPINDEX_")]:
    del os.environ[_key]
# The SPARQL store and its worker processes only where a test asks for them.
os.environ["FDPINDEX_SPARQL_ENABLED"] = "false"
# The search indexer runs a background loop; on the shared in-memory SQLite connection
# it would interleave with the test's own transactions. Search tests enable it.
os.environ["FDPINDEX_SEARCH_ENABLED"] = "false"
# Likewise the conformance loop; validation runs in a thread, not worker processes.
os.environ["FDPINDEX_CONFORMANCE_ENABLED"] = "false"
os.environ["FDPINDEX_CONFORMANCE_WORKERS"] = "0"

from fdpindex.config import Settings  # noqa: E402
from fdpindex.db import create_schema, make_engine, make_session_factory  # noqa: E402
from fdpindex.jobs import JobRunner  # noqa: E402
from fdpindex.outbound import OutboundClient  # noqa: E402
from fdpindex.service import IndexService  # noqa: E402
from fdpindex.settings import SettingsService  # noqa: E402
from fdpindex.webhooks import WebhookService  # noqa: E402

PUBLIC_IP = "93.184.216.34"
FDP = "https://fdp.example.org"

ROOT_TTL = f"""
@prefix dct: <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix fdp: <https://w3id.org/fdp/fdp-o#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix ldp: <http://www.w3.org/ns/ldp#> .
@prefix r3d: <http://www.re3data.org/schema/3-0#> .

<{FDP}> a fdp:MetadataService, ldp:DirectContainer ;
    dct:title "Demo FDP"@en, "Demo-FDP"@de ;
    dct:description "A FAIR Data Point for tests" ;
    dcat:version "1.0" ;
    r3d:country <http://lexvo.org/id/iso3166/NL> ;
    dct:publisher <{FDP}#publisher> ;
    ldp:hasMemberRelation fdp:metadataCatalog ;
    ldp:contains <{FDP}/catalog/c1> .
<{FDP}#publisher> foaf:name "Example University" .
"""

CATALOG_TTL = f"""
@prefix dct: <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
<{FDP}/catalog/c1> a dcat:Catalog ;
    dct:title "Catalog one" ;
    dcat:dataset <{FDP}/dataset/d1>, <https://elsewhere.example.com/dataset/x> .
"""

DATASET_TTL = f"""
@prefix dct: <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
<{FDP}/dataset/d1> a dcat:Dataset ;
    dct:title "Dataset one" ;
    dct:isPartOf <{FDP}/catalog/c1> .
"""


@dataclass
class FakeNetwork:
    """Every outbound request of the index lands here."""

    pages: dict[str, tuple[int, str, str]] = field(default_factory=lambda: {})
    failing: set[str] = field(default_factory=lambda: set())
    etags: dict[str, str] = field(default_factory=lambda: {})
    # Extra response headers per URL, such as Retry-After.
    headers: dict[str, dict[str, str]] = field(default_factory=lambda: {})
    requests: list[httpx.Request] = field(default_factory=lambda: [])
    webhook_calls: list[httpx.Request] = field(default_factory=lambda: [])

    def serve(
        self, url: str, body: str, *, status: int = 200, content_type: str = "text/turtle"
    ) -> None:
        self.pages[url] = (status, content_type, body)

    def standard_fdp(self) -> None:
        self.serve(FDP, ROOT_TTL)
        self.serve(f"{FDP}/catalog/c1", CATALOG_TTL)
        self.serve(f"{FDP}/dataset/d1", DATASET_TTL)

    def handler(self, request: httpx.Request) -> httpx.Response:
        # Requests keep the host name; pinning happens where PinnedTransport opens
        # the socket, and this fake stands in for that transport.
        self.requests.append(request)
        url = str(request.url)
        if url in self.failing:
            raise httpx.ConnectError("connection refused", request=request)
        if request.method == "POST" and "hooks" in url:
            self.webhook_calls.append(request)
            return httpx.Response(200, text="ok")
        page = self.pages.get(url) or self.pages.get(url.rstrip("/"))
        if page is None:
            return httpx.Response(404, text="not found")
        status, content_type, body = page
        headers = {"Content-Type": content_type, **self.headers.get(url, {})}
        etag = self.etags.get(url)
        if etag is not None:
            if request.headers.get("If-None-Match") == etag:
                return httpx.Response(304, headers={"ETag": etag})
            headers["ETag"] = etag
        return httpx.Response(status, text=body, headers=headers)


async def public_resolver(host: str, port: int) -> list[str]:
    if host in {"localhost", "internal.example"}:
        return ["127.0.0.1"] if host == "localhost" else ["10.0.0.5"]
    return [PUBLIC_IP]


@pytest.fixture
def network() -> FakeNetwork:
    net = FakeNetwork()
    net.standard_fdp()
    return net


@pytest.fixture
def config() -> Settings:
    return Settings(database_url="sqlite+aiosqlite://", auto_permit=True)


@dataclass
class Services:
    index: IndexService
    settings: SettingsService
    webhooks: WebhookService
    jobs: JobRunner
    outbound: OutboundClient
    session_factory: Any


@pytest.fixture
async def services(network: FakeNetwork, config: Settings) -> AsyncIterator[Services]:
    engine = make_engine(config.database_url)
    await create_schema(engine)
    factory = make_session_factory(engine)
    http = httpx.AsyncClient(transport=httpx.MockTransport(network.handler))
    outbound = OutboundClient(
        http,
        allow_private=False,
        max_bytes=config.outbound_max_bytes,
        max_redirects=config.outbound_max_redirects,
        user_agent="test",
        resolver=public_resolver,
    )
    # One job at a time: in-memory SQLite shares a single connection between sessions,
    # so concurrent jobs would interleave transactions (PostgreSQL has a pool).
    jobs = JobRunner(1)
    settings = SettingsService(factory, auto_permit_default=config.auto_permit)
    # Retries without waiting: three attempts in all.
    webhooks = WebhookService(
        factory, settings, outbound, jobs, body_limit=10_000, retry_delays=(0.0, 0.0)
    )
    index = IndexService(
        factory, settings, outbound, jobs, webhooks, body_limit=10_000, harvest_max_nodes=50
    )
    yield Services(index, settings, webhooks, jobs, outbound, factory)
    await jobs.close()
    await http.aclose()
    await engine.dispose()


async def ping(services: Services, client_url: str = FDP, remote: str = "203.0.113.7") -> Any:
    outcome = await services.index.accept_ping(
        client_url, remote_addr=remote, method="POST", path="/", headers={}, body="{}"
    )
    services.index.after_trigger(outcome.event_id)
    await services.jobs.drain()
    return outcome
