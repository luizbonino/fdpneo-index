"""Harvest provenance (TASKS 15.3)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from rdflib import RDF, Graph, Namespace, URIRef
from sqlalchemy import delete, select, update

from fdpindex.db import utcnow
from fdpindex.models import HarvestRecord, HarvestWalk, IndexEntry
from fdpindex.provenance import Harvests
from tests.conftest import FDP, FakeNetwork, Services, ping
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit

PROV = Namespace("http://www.w3.org/ns/prov#")
FDPO = Namespace("https://w3id.org/fdp/fdp-o#")
DCT = Namespace("http://purl.org/dc/terms/")


async def _entry_id(services: Services) -> Any:
    async with services.session_factory() as session:
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == FDP))
    assert entry is not None
    return entry.uuid


async def _records(services: Services) -> list[HarvestRecord]:
    async with services.session_factory() as session:
        return list(await session.scalars(select(HarvestRecord).order_by(HarvestRecord.started_at)))


async def _continue(services: Services) -> None:
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(HarvestWalk).values(continue_at=utcnow() - timedelta(seconds=1))
        )
    assert await services.index.schedule_due_walks() == 1
    await services.jobs.drain()


async def test_a_walk_is_kept_with_its_runs(services: Services) -> None:
    await ping(services)
    [first] = await _records(services)
    assert (first.outcome, first.pages_read, first.added, first.failed) == ("complete", 3, 3, 0)
    assert first.ended_at is not None and first.failure_reason is None
    assert [r["run"] for r in first.runs] == [1]

    entry_id = await _entry_id(services)
    await services.index.update_options(entry_id, budget={"run_pages": 1})
    await services.index.harvest_entry(entry_id)
    _first, second = await _records(services)
    # In progress until its last run, linked from the walk.
    assert (second.outcome, second.ended_at) == ("in progress", None)
    async with services.session_factory() as session:
        walk = await session.get(HarvestWalk, entry_id)
    assert walk is not None and walk.harvest_id == second.id
    await _continue(services)
    await _continue(services)
    _first, second = await _records(services)
    assert second.outcome == "complete"
    assert [r["run"] for r in second.runs] == [1, 2, 3]
    assert (second.pages_read, second.not_modified, second.changed) == (3, 3, 0)


async def test_a_walk_that_read_nothing_is_recorded_as_failed(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    network.failing.update({FDP, f"{FDP}/catalog/c1", f"{FDP}/dataset/d1"})
    await services.index.harvest_entry(await _entry_id(services))
    _first, failed = await _records(services)
    assert (failed.outcome, failed.pages_read, failed.failed) == ("failed", 0, 3)
    assert failed.failure_reason is not None and "IO error" in failed.failure_reason


async def test_walks_from_before_are_rebuilt_from_their_events(services: Services) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    await services.index.update_options(entry_id, budget={"run_pages": 2})
    await services.index.harvest_entry(entry_id)
    await _continue(services)
    before = [(r.outcome, r.pages_read, len(r.runs)) for r in await _records(services)]
    async with services.session_factory() as session, session.begin():
        await session.execute(delete(HarvestRecord))
    assert await services.index.backfill_harvests() == 2
    after = [(r.outcome, r.pages_read, len(r.runs)) for r in await _records(services)]
    assert after == before == [("complete", 3, 1), ("complete", 3, 2)]
    assert await services.index.backfill_harvests() == 0  # once


def test_which_harvest_fetched_a_copy() -> None:
    now = utcnow()
    harvests = Harvests([(now, "b"), (now - timedelta(days=1), "a")])
    assert harvests.at(now - timedelta(days=2)) is None
    assert harvests.at(now - timedelta(hours=1)) == "a"
    assert harvests.at(now) == "b"
    assert harvests.at(None) is None


def test_a_harvest_resolves_in_rdf_and_json(api: Api) -> None:  # noqa: F811
    assert api.ping(FDP).status_code == 204
    [entry] = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    page = api.client.get(f"/index/entries/{entry['uuid']}/harvests").json()
    assert page["total"] == 1
    [harvest] = page["items"]
    assert (harvest["outcome"], harvest["pagesRead"], harvest["clientUrl"]) == (
        "complete",
        3,
        FDP,
    )
    url = f"/index/harvests/{harvest['id']}"
    assert harvest["iri"] == f"http://testserver{url}"
    assert api.client.get(url).json()["runs"][0]["pagesRead"] == 3

    response = api.client.get(url, headers={"Accept": "text/turtle"})
    assert response.headers["content-type"].startswith("text/turtle")
    graph = Graph().parse(data=response.text, format="turtle")
    node = URIRef(harvest["iri"])
    assert (node, RDF.type, FDPO.Harvest) in graph
    assert (node, PROV.used, URIRef(FDP)) in graph
    assert (node, FDPO.harvestOutcome, FDPO.HarvestComplete) in graph
    index = graph.value(node, PROV.wasAssociatedWith)
    assert (index, RDF.type, FDPO.FAIRDataPointIndex) in graph
    run = URIRef(f"{harvest['iri']}#run-1")
    assert (run, DCT.isPartOf, node) in graph
    assert int(graph.value(run, FDPO.pagesRead)) == 3  # pyright: ignore[reportArgumentType]

    jsonld = api.client.get(url, params={"format": "jsonld"})
    assert jsonld.headers["content-type"].startswith("application/ld+json")
    browser = api.client.get(
        url, headers={"Accept": "text/html,application/xhtml+xml"}, follow_redirects=False
    )
    assert (browser.status_code, browser.headers["location"]) == (
        303,
        f"http://testserver/harvests/{harvest['id']}",
    )


def test_harvests_of_fdps_not_in_the_list_are_not_public(api: Api) -> None:  # noqa: F811
    assert api.ping(FDP).status_code == 204
    [entry] = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    [harvest] = api.client.get(f"/index/entries/{entry['uuid']}/harvests").json()["items"]
    api.client.put(f"/index/entries/{entry['uuid']}", json={"permit": "REJECTED"}, headers=ADMIN)
    assert api.client.get(f"/index/harvests/{harvest['id']}").status_code == 404
    assert api.client.get(f"/index/entries/{entry['uuid']}/harvests").status_code == 404
    assert api.client.get(f"/index/harvests/{harvest['id']}", headers=ADMIN).status_code == 200
