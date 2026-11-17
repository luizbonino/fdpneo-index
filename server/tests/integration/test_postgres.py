"""Against PostgreSQL: migrations, and the index with concurrent jobs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from alembic import command
from sqlalchemy import func, select
from testcontainers.community.postgres import PostgresContainer

from fdpindex.cli import alembic_config
from fdpindex.db import make_engine, make_session_factory
from fdpindex.jobs import JobRunner
from fdpindex.models import EntryState, Event, EventType, HarvestedNode, IndexEntry
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
