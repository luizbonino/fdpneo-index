"""Registry policies: expiry, per-FDP options, verified approval, registration (TASKS 4.6 to 4.9)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from fdpindex.db import utcnow
from fdpindex.errors import PingDenied
from fdpindex.models import (
    EntryPermit,
    EntryState,
    Event,
    EventType,
    HarvestedNode,
    IndexEntry,
    WebhookEvent,
)
from fdpindex.settings import IndexSettings
from tests.conftest import FDP, FakeNetwork, Services, ping
from tests.test_api import (
    ADMIN,
    Api,
    api as api,
)

pytestmark = pytest.mark.unit


async def _entry(services: Services) -> IndexEntry:
    async with services.session_factory() as session:
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == FDP))
    assert entry is not None
    return entry


async def _nodes(services: Services) -> int:
    async with services.session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(HarvestedNode)) or 0)


async def _settings(services: Services, **changes: Any) -> None:
    current = (await services.settings.get()).model_dump(mode="json", by_alias=True)
    current.update(changes)
    await services.settings.update(IndexSettings.model_validate(current))


async def _age(services: Services, days: int) -> None:
    """Pretend the last contact (and the registration) were ``days`` ago."""
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(
                last_success_at=utcnow() - timedelta(days=days),
                last_retrieval_at=utcnow() - timedelta(days=days),
                created_at=utcnow() - timedelta(days=days),
            )
        )


# --- verified approval (4.8) ---------------------------------------------------------------


async def test_verified_approval_accepts_an_fdp_that_answers(services: Services) -> None:
    await _settings(services, autoPermit=False, verifiedPermit=True)
    await ping(services)
    entry = await _entry(services)
    assert entry.permit is EntryPermit.ACCEPTED
    assert await _nodes(services) == 3  # harvested at once, like an accepted entry
    events, _ = await services.index.events(entry.uuid, limit=20)
    retrieval = next(e for e in events if e.type is EventType.METADATA_RETRIEVAL)
    assert retrieval.payload["metadataRetrieval"]["approved"] == "verified"


async def test_verified_approval_waits_for_valid_metadata(
    services: Services, network: FakeNetwork
) -> None:
    await _settings(services, autoPermit=False, verifiedPermit=True)
    network.failing.add(FDP)
    await ping(services)
    assert (await _entry(services)).permit is EntryPermit.PENDING
    # A rejected FDP is never approved this way.
    network.failing.clear()
    entry = await _entry(services)
    await services.index.update_permit(
        entry.uuid, EntryPermit.REJECTED, actor="t", remote_addr=None
    )
    await _age(services, 1)
    await ping(services)
    assert (await _entry(services)).permit is EntryPermit.REJECTED


# --- expiry (4.6) ----------------------------------------------------------------------------


async def test_entries_without_contact_expire_once_and_revive(
    services: Services, network: FakeNetwork
) -> None:
    await services.webhooks.save_webhook(
        None,
        {
            "payload_url": "https://hooks.example.net/expiry",
            "secret": "s3cret-value",
            "all_events": False,
            "events": [WebhookEvent.ENTRY_EXPIRED.value],
            "all_entries": True,
            "entries": [],
            "enabled": True,
        },
    )
    await ping(services)
    await _age(services, 100)
    # Off by default.
    assert await services.index.mark_expired() == 0
    await _settings(services, expiry={"enabled": True, "after": "PT2160H", "hide": True})
    assert await services.index.mark_expired() == 1
    assert await services.index.mark_expired() == 0  # once
    await services.jobs.drain()
    assert [json.loads(c.content)["event"] for c in network.webhook_calls] == ["EntryExpired"]
    entry = await _entry(services)
    assert entry.expired_at is not None
    events, _ = await services.index.events(entry.uuid, limit=20)
    expiry = next(e for e in events if e.type is EventType.EXPIRY)
    assert expiry.payload["expiry"]["after"] == "PT2160H"
    # Hidden from the public directory, not from administrators.
    public = await services.index.list_entries(permits=[EntryPermit.ACCEPTED], public=True)
    assert public.items == []
    assert (await services.index.counts([EntryPermit.ACCEPTED], public=True))["ALL"] == 0
    admin = await services.index.list_entries(permits=[EntryPermit.ACCEPTED])
    assert [e.uuid for e in admin.items] == [entry.uuid]
    # A ping that reaches the FDP revives it.
    await ping(services)
    assert (await _entry(services)).expired_at is None
    public = await services.index.list_entries(permits=[EntryPermit.ACCEPTED], public=True)
    assert len(public.items) == 1


async def test_expiry_counts_from_registration_when_never_reached(
    services: Services, network: FakeNetwork
) -> None:
    network.failing.add(FDP)
    await ping(services)
    await _settings(services, expiry={"enabled": True, "after": "P30D", "hide": False})
    assert await services.index.mark_expired() == 0  # registered just now
    await _age(services, 31)
    async with services.session_factory() as session, session.begin():
        await session.execute(update(IndexEntry).values(last_success_at=None))
    assert await services.index.mark_expired() == 1
    # Not hidden: the policy only notifies.
    public = await services.index.list_entries(permits=[EntryPermit.ACCEPTED], public=True)
    assert len(public.items) == 1


def test_expiry_needs_at_least_a_day() -> None:
    from pydantic import ValidationError

    from fdpindex.settings import ExpirySettings

    with pytest.raises(ValidationError, match="at least one day"):
        ExpirySettings.model_validate({"after": "PT1H"})


# --- per-FDP options (4.7) -------------------------------------------------------------------


async def test_directory_only_removes_and_stops_the_harvest(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    assert await _nodes(services) == 3
    await services.index.update_options(entry.uuid, harvest_enabled=False)
    assert await _nodes(services) == 0
    await _age(services, 1)
    await ping(services)
    assert (await _entry(services)).state is EntryState.VALID  # still checked
    assert await _nodes(services) == 0
    await services.index.update_options(entry.uuid, harvest_enabled=True)
    await _age(services, 1)
    await ping(services)
    assert await _nodes(services) == 3


async def test_an_own_harvest_interval_harvests_unchanged_metadata(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    assert entry.last_harvest_at is not None

    async def scheduled_check() -> int:
        async with services.session_factory() as session, session.begin():
            await session.execute(
                update(IndexEntry).values(
                    next_check_at=utcnow() - timedelta(minutes=1),
                    last_retrieval_at=utcnow() - timedelta(days=2),
                )
            )
        await services.index.schedule_due_checks()
        await services.jobs.drain()
        async with services.session_factory() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(Event).where(Event.type == EventType.HARVEST)
                )
                or 0
            )

    # Unchanged metadata and no interval: the check does not harvest.
    assert await scheduled_check() == 1
    await services.index.update_options(entry.uuid, harvest_interval=timedelta(hours=12))
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(last_harvest_at=utcnow() - timedelta(hours=13))
        )
    assert await scheduled_check() == 2
    # Just harvested: not due again.
    assert await scheduled_check() == 2
    await services.index.update_options(entry.uuid, clear_interval=True)
    assert (await _entry(services)).harvest_interval_seconds is None


# --- registration (4.9) ----------------------------------------------------------------------


async def test_registration_checks_at_once(services: Services, network: FakeNetwork) -> None:
    outcome = await services.index.register(FDP, remote_addr="203.0.113.9")
    assert outcome.new_entry and outcome.retrieved and outcome.error is None
    assert outcome.entry.state is EntryState.VALID
    assert outcome.entry.permit is EntryPermit.ACCEPTED
    await services.jobs.drain()
    assert await _nodes(services) == 3
    # Again right away: the retrieval rate limit applies, the last state is reported.
    again = await services.index.register(FDP, remote_addr="203.0.113.9")
    assert not again.new_entry and not again.retrieved
    assert again.entry.state is EntryState.VALID


async def test_registration_reports_failures_and_refusals(
    services: Services, network: FakeNetwork
) -> None:
    other = "https://down.example.org"
    network.failing.add(other)
    outcome = await services.index.register(other, remote_addr="203.0.113.9")
    assert outcome.entry.state is EntryState.UNREACHABLE
    assert outcome.error is not None
    with pytest.raises(PingDenied):
        await services.index.register("http://localhost:8080", remote_addr="203.0.113.9")


def test_registration_and_options_over_http(api: Api) -> None:
    registered = api.client.post("/index/register", json={"clientUrl": FDP})
    api.drain()  # the harvest it started: in-memory SQLite has one connection
    assert registered.status_code == 200, registered.text
    body = registered.json()
    assert body["newEntry"] is True and body["state"] == "ACTIVE"
    assert body["permit"] == "ACCEPTED" and body["title"]
    assert api.client.post("/index/register", json={"clientUrl": "ftp://x"}).status_code == 400
    entry = body["uuid"]
    # Options: administrators only, validated.
    put = {"options": {"harvest": False, "harvestInterval": "PT24H"}}
    assert api.client.put(f"/index/entries/{entry}", json=put).status_code == 401
    updated = api.client.put(f"/index/entries/{entry}", json=put, headers=ADMIN)
    assert updated.status_code == 200, updated.text
    assert updated.json()["options"] == {
        "harvest": False,
        "harvestInterval": "PT24H",
        "country": None,
        "harvestPagesPerRun": 2000,
        "harvestPagesPerWalk": 100_000,
        "harvestBudgetSet": False,
        "harvestOrigins": [],
    }
    assert updated.json()["permit"] == "ACCEPTED"  # unchanged without permit
    short = {"options": {"harvestInterval": "PT5M"}}
    assert api.client.put(f"/index/entries/{entry}", json=short, headers=ADMIN).status_code == 400
    cleared = {"options": {"harvestInterval": ""}}
    response = api.client.put(f"/index/entries/{entry}", json=cleared, headers=ADMIN)
    assert response.json()["options"]["harvestInterval"] is None
    # The reference's body still works.
    permit = api.client.put(f"/index/entries/{entry}", json={"permit": "REJECTED"}, headers=ADMIN)
    assert permit.json()["permit"] == "REJECTED"


def test_administrators_add_an_fdp(api: Api, network: FakeNetwork) -> None:
    other = "https://other.example.org"
    network.serve(other, network.pages[FDP][2].replace(FDP, other))
    body = {"clientUrl": other, "permit": "PENDING", "options": {"harvest": False}}
    assert api.client.post("/index/entries", json=body).status_code == 401
    created = api.client.post("/index/entries", json=body, headers=ADMIN)
    assert created.status_code == 201, created.text
    detail = created.json()
    assert detail["clientUrl"] == other
    assert detail["permit"] == "PENDING"
    assert detail["state"] in {"ACTIVE", "VALID"}
    assert detail["options"]["harvest"] is False
