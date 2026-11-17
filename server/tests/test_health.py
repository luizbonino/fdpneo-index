"""Scheduled liveness checks (ADR-0004)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from fdpindex.db import utcnow
from fdpindex.models import EntryPermit, EntryState, Event, EventType, HarvestedNode, IndexEntry
from fdpindex.service import api_state
from fdpindex.settings import IndexSettings
from tests.conftest import FDP, ROOT_TTL, FakeNetwork, Services, ping

pytestmark = pytest.mark.unit


async def _entry(services: Services) -> IndexEntry:
    async with services.session_factory() as session:
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == FDP))
    assert entry is not None
    return entry


async def _make_due(services: Services, *, retrieved_ago: timedelta = timedelta(hours=25)) -> None:
    """Pretend time passed: the next check is due and the retrieval rate limit is over."""
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(
                next_check_at=utcnow() - timedelta(minutes=1),
                last_retrieval_at=utcnow() - retrieved_ago,
            )
        )


async def _check(services: Services) -> int:
    started = await services.index.schedule_due_checks()
    await services.jobs.drain()
    return started


async def _configure(services: Services, **health: object) -> None:
    current = (await services.settings.get()).model_dump(mode="json", by_alias=True)
    current["healthCheck"].update(health)
    await services.settings.update(IndexSettings.model_validate(current))


def _webhook_events(network: FakeNetwork) -> list[str]:
    return [json.loads(call.content)["event"] for call in network.webhook_calls]


async def _hook(services: Services) -> None:
    await services.webhooks.save_webhook(
        None,
        {
            "payload_url": "https://hooks.example.net/state",
            "secret": "s3cret-value",
            "all_events": False,
            "events": ["EntryValid", "EntryInvalid", "EntryUnreachable"],
            "all_entries": True,
            "entries": [],
            "enabled": True,
        },
    )


async def test_a_ping_schedules_the_next_check(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    assert entry.next_check_at is not None
    delay = entry.next_check_at - utcnow()
    assert timedelta(hours=21) < delay < timedelta(hours=27)  # 24 h ± 10 %
    assert await _check(services) == 0  # nothing due yet


async def test_an_fdp_that_stopped_pinging_stays_active(services: Services) -> None:
    await ping(services)
    await _make_due(services, retrieved_ago=timedelta(days=8))
    stale = await _entry(services)
    assert api_state(stale, await services.index.valid_threshold()) == "INACTIVE"
    assert await _check(services) == 1
    entry = await _entry(services)
    assert api_state(entry, await services.index.valid_threshold()) == "ACTIVE"
    assert entry.last_success_at is not None
    events, _ = await services.index.events(entry.uuid, limit=10)
    assert events[0].type is EventType.HEALTH_CHECK


async def test_unreachable_only_after_the_failure_threshold(
    services: Services, network: FakeNetwork
) -> None:
    await _hook(services)
    await ping(services)
    network.webhook_calls.clear()
    network.failing.add(FDP)
    for expected_failures in (1, 2):
        await _make_due(services)
        await _check(services)
        entry = await _entry(services)
        assert entry.consecutive_failures == expected_failures
        assert entry.state is EntryState.VALID
    assert network.webhook_calls == []

    await _make_due(services)
    await _check(services)
    entry = await _entry(services)
    assert entry.state is EntryState.UNREACHABLE
    assert _webhook_events(network) == ["EntryUnreachable"]

    await _make_due(services)
    await _check(services)  # still down: no second notification
    assert _webhook_events(network) == ["EntryUnreachable"]

    network.failing.clear()
    await _make_due(services)
    await _check(services)
    entry = await _entry(services)
    assert entry.state is EntryState.VALID
    assert entry.consecutive_failures == 0
    assert _webhook_events(network) == ["EntryUnreachable", "EntryValid"]


async def test_conditional_requests_and_reharvest_on_change(
    services: Services, network: FakeNetwork
) -> None:
    network.etags[FDP] = '"v1"'
    await ping(services)
    first_harvest = await services.index.harvest_summary((await _entry(services)).uuid)

    await _make_due(services)
    await _check(services)
    check_request = network.requests[-1]
    assert check_request.headers["If-None-Match"] == '"v1"'
    events, _ = await services.index.events((await _entry(services)).uuid, limit=1)
    assert events[0].payload["healthCheck"]["notModified"] is True
    assert await services.index.harvest_summary((await _entry(services)).uuid) == first_harvest

    network.etags[FDP] = '"v2"'
    network.serve(FDP, ROOT_TTL.replace("Demo FDP", "Renamed FDP"))
    await _make_due(services)
    await _check(services)
    entry = await _entry(services)
    assert entry.repository_metadata["title"] == "Renamed FDP"
    nodes, harvested_at = await services.index.harvest_summary(entry.uuid)
    assert nodes == 3
    assert first_harvest[1] is not None and harvested_at is not None
    assert harvested_at > first_harvest[1]


async def test_checks_respect_settings_and_permits(services: Services) -> None:
    await ping(services)
    await _configure(services, enabled=False)
    await _make_due(services)
    assert await _check(services) == 0
    await _configure(services, enabled=True, permits=["PENDING"])
    assert await _check(services) == 0  # the entry is ACCEPTED
    await _configure(services, permits=["PENDING", "ACCEPTED"])
    assert await _check(services) == 1


async def test_a_running_check_is_not_started_twice(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    async with services.session_factory() as session, session.begin():
        session.add(Event(type=EventType.HEALTH_CHECK, related_to=entry.uuid, payload={}))
    await _make_due(services)
    assert await services.index.schedule_due_checks() == 0
    async with services.session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(HarvestedNode))
    assert count == 3


async def test_a_rate_limited_check_counts_from_the_last_retrieval(services: Services) -> None:
    await ping(services)
    retrieved = (await _entry(services)).last_retrieval_at
    assert retrieved is not None
    await _make_due(services, retrieved_ago=timedelta(minutes=2))  # inside the 10 min limit
    await _check(services)
    entry = await _entry(services)
    events, _ = await services.index.events(entry.uuid, limit=1)
    assert events[0].payload["healthCheck"]["error"] == "Rate limit reached (skipping)"
    assert entry.next_check_at is not None and entry.last_retrieval_at is not None
    delay = entry.next_check_at - entry.last_retrieval_at
    assert timedelta(hours=21) < delay < timedelta(hours=27)


async def test_rejected_entries_are_not_checked_by_default(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    await services.index.update_permit(
        entry.uuid, EntryPermit.REJECTED, actor="a", remote_addr=None
    )
    await services.jobs.drain()
    await _make_due(services)
    assert await _check(services) == 0
