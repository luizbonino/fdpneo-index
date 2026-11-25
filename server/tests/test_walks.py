"""Walks that continue over budgeted runs, and per-FDP budgets (TASKS 15.7)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from fdpindex.api.schemas import event_outcome
from fdpindex.db import utcnow
from fdpindex.models import Event, EventType, HarvestedNode, HarvestWalk, IndexEntry
from tests.conftest import FDP, FakeNetwork, Services, ping
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit

CATALOG = f"{FDP}/catalog/c1"
DATASET = f"{FDP}/dataset/d1"


async def _entry_id(services: Services) -> Any:
    async with services.session_factory() as session:
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == FDP))
    assert entry is not None
    return entry.uuid


async def _report(services: Services, harvest_id: Any) -> dict[str, Any]:
    async with services.session_factory() as session:
        event = await session.get(Event, harvest_id)
        assert event is not None and event.finished_at is not None
        return dict(event.payload["harvest"])


async def _walk(services: Services, entry_id: Any) -> HarvestWalk | None:
    async with services.session_factory() as session:
        return await session.get(HarvestWalk, entry_id)


async def _nodes(services: Services) -> dict[str, HarvestedNode]:
    async with services.session_factory() as session:
        return {n.node_iri: n for n in await session.scalars(select(HarvestedNode))}


async def _continue(services: Services) -> dict[str, Any]:
    """Let the walk's wait pass, run the sweep, and return the run's report."""
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(HarvestWalk).values(continue_at=utcnow() - timedelta(seconds=1))
        )
    assert await services.index.schedule_due_walks() == 1
    await services.jobs.drain()
    async with services.session_factory() as session:
        event = await session.scalar(
            select(Event).where(Event.finished_at.is_not(None)).order_by(Event.finished_at.desc())
        )
    assert event is not None
    return dict(event.payload["harvest"])


async def test_a_walk_continues_over_runs_until_it_is_complete(services: Services) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    await services.index.update_options(entry_id, budget={"run_pages": 1})

    first_id = await services.index.harvest_entry(entry_id)
    first = await _report(services, first_id)
    assert first["outcome"] == "in progress"
    async with services.session_factory() as session:
        event = await session.get(Event, first_id)
    assert event is not None
    assert event_outcome(event) == "Run 1: 1 nodes, unchanged; 1 pages still to read"
    assert (first["nodes"], first["walk"]["run"], first["walk"]["queued"]) == (1, 1, 1)
    assert first["walk"]["continuesAt"] is not None
    walk = await _walk(services, entry_id)
    assert walk is not None and walk.queue == [CATALOG]
    detail = await services.index.harvest_walk(entry_id)
    assert detail is not None and detail.runs == 1
    # Not due yet: the sweep leaves it alone.
    assert await services.index.schedule_due_walks() == 0

    second = await _continue(services)
    assert (second["outcome"], second["walk"]["run"]) == ("in progress", 2)
    third = await _continue(services)
    assert third["outcome"] == "complete"
    assert (third["walk"]["run"], third["walk"]["pages"], third["walk"]["queued"]) == (3, 3, 0)
    assert await _walk(services, entry_id) is None
    assert set(await _nodes(services)) == {FDP, CATALOG, DATASET}


async def test_only_the_run_that_ends_the_walk_removes_what_is_no_longer_listed(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    await services.index.update_options(entry_id, budget={"run_pages": 1})
    # The catalogue no longer lists the dataset.
    network.serve(CATALOG, f"<{CATALOG}> a <http://www.w3.org/ns/dcat#Catalog> .")

    await services.index.harvest_entry(entry_id)
    assert DATASET in await _nodes(services)  # not reached yet: kept, and not stale
    assert (await _nodes(services))[DATASET].stale_since is None
    last = await _continue(services)
    assert (last["outcome"], last["removed"]) == ("complete", 1)
    assert set(await _nodes(services)) == {FDP, CATALOG}


async def test_a_walk_stops_at_its_ceiling_and_keeps_what_it_did_not_reach(
    services: Services,
) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    await services.index.update_options(entry_id, budget={"run_pages": 1, "walk_pages": 2})

    await services.index.harvest_entry(entry_id)
    last = await _continue(services)
    assert last["outcome"] == "partial"
    assert last["walk"]["atCeiling"] is True
    assert (last["removed"], last["stale"]) == (0, 1)
    nodes = await _nodes(services)
    assert nodes[DATASET].fetch_error == "not reached: the walk stopped at its ceiling"
    assert await _walk(services, entry_id) is None


async def test_an_fdp_asking_to_wait_sets_when_the_walk_continues(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    network.serve(CATALOG, "busy", status=429, content_type="text/plain")
    network.headers[CATALOG] = {"Retry-After": "7200"}
    report = await _report(services, await services.index.harvest_entry(entry_id))
    assert report["outcome"] == "in progress"
    assert report["walk"]["retryAfter"] == 7200
    walk = await _walk(services, entry_id)
    assert walk is not None and walk.continue_at is not None
    assert walk.continue_at > utcnow() + timedelta(hours=1, minutes=55)
    assert walk.queue == [CATALOG]


async def test_directory_only_drops_the_walk(services: Services) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    await services.index.update_options(entry_id, budget={"run_pages": 1})
    await services.index.harvest_entry(entry_id)
    assert await _walk(services, entry_id) is not None
    await services.index.update_options(entry_id, harvest_enabled=False)
    assert await _walk(services, entry_id) is None


def test_budget_options_through_the_api(api: Api) -> None:  # noqa: F811  # imported above
    assert api.ping(FDP).status_code == 204
    [entry] = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    url = f"/index/entries/{entry['uuid']}"
    options = api.client.get(url).json()["options"]
    assert (options["harvestPagesPerRun"], options["harvestPagesPerWalk"]) == (2000, 100_000)
    assert (options["harvestBudgetSet"], options["harvestOrigins"]) == (False, [])

    body = {
        "options": {
            "harvestPagesPerRun": 500,
            "harvestOrigins": ["HTTPS://Data.Example.org/files/", "https://data.example.org"],
        }
    }
    options = api.client.put(url, json=body, headers=ADMIN).json()["options"]
    assert (options["harvestPagesPerRun"], options["harvestBudgetSet"]) == (500, True)
    assert options["harvestOrigins"] == ["https://data.example.org"]
    # Leaving a field out keeps it; null returns it to the default.
    put = {"options": {"harvestPagesPerRun": None}}
    options = api.client.put(url, json=put, headers=ADMIN).json()["options"]
    assert (options["harvestPagesPerRun"], options["harvestBudgetSet"]) == (2000, False)
    assert options["harvestOrigins"] == ["https://data.example.org"]

    # A walk with runs to go shows on the FDP's page; while a run reads, without a time.
    entry_id = UUID(entry["uuid"])

    async def add(*rows: object) -> None:
        async with api.app.state.session_factory() as session, session.begin():
            session.add_all(rows)

    portal = api.client.portal
    assert portal is not None
    soon = utcnow() + timedelta(minutes=10)
    walk = HarvestWalk(
        entry_uuid=entry_id, runs=1, pages=2, queue=[DATASET], failed={}, continue_at=soon
    )
    walk.unreached, walk.skipped_off_origin = False, 0
    portal.call(add, walk)
    shown = api.client.get(url).json()["harvest"]["walk"]
    assert (shown["runs"], shown["pages"], shown["queued"], shown["running"]) == (1, 2, 1, False)
    assert shown["continuesAt"] is not None
    running = Event(
        uuid=uuid4(),
        type=EventType.HARVEST,
        related_to=entry_id,
        payload={"harvest": {}},
        created_at=utcnow(),
    )
    portal.call(add, running)
    shown = api.client.get(url).json()["harvest"]["walk"]
    assert (shown["running"], shown["continuesAt"]) == (True, None)

    bad = {"options": {"harvestOrigins": ["ftp://data.example.org"]}}
    assert api.client.put(url, json=bad, headers=ADMIN).status_code == 400
    assert api.client.put(url, json=body).status_code == 401
