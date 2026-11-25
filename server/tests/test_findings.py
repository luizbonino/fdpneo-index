"""Metadata health: what the index found in an FDP's metadata (TASKS 15.8)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from fdpindex.findings import WalkSummary, analyse, failure_kind, read_page
from fdpindex.models import IndexEntry, MetadataHealth
from tests.conftest import FDP, Services, ping
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit

RDF_TYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
DCAT = "http://www.w3.org/ns/dcat#"
LDP = "http://www.w3.org/ns/ldp#"


def nt(*triples: tuple[str, str, str]) -> str:
    def term(value: str) -> str:
        return value if value.startswith(('"', "_:", "<")) else f"<{value}>"

    return "".join(f"{term(s)} {term(p)} {term(o)} .\n" for s, p, o in triples)


def a(subject: str, kind: str) -> tuple[str, str, str]:
    return (subject, RDF_TYPE, DCAT + kind)


def container(page: str, relation: str, *members: str) -> list[tuple[str, str, str]]:
    box = f"{page}#children"
    return [
        (box, RDF_TYPE, LDP + "DirectContainer"),
        (box, LDP + "hasMemberRelation", relation),
        *((box, LDP + "contains", m) for m in members),
    ]


CAT, DS, DIST, SVC = f"{FDP}/catalog/c", f"{FDP}/dataset/d", f"{FDP}/distribution/x", f"{FDP}/s"


def well_formed() -> dict[str, str]:
    """An FDP whose every level links its children both in LDP and in DCAT."""
    fdp_o = "https://w3id.org/fdp/fdp-o#"
    return {
        FDP: nt(
            (FDP, RDF_TYPE, fdp_o + "MetadataService"),
            (FDP, fdp_o + "metadataCatalog", CAT),
            *container(FDP, fdp_o + "metadataCatalog", CAT),
        ),
        CAT: nt(
            a(CAT, "Catalog"), (CAT, DCAT + "dataset", DS), *container(CAT, DCAT + "dataset", DS)
        ),
        DS: nt(
            a(DS, "Dataset"),
            (DS, DCAT + "distribution", DIST),
            *container(DS, DCAT + "distribution", DIST),
        ),
        DIST: nt(
            a(DIST, "Distribution"),
            (DIST, DCAT + "accessService", SVC),
            *container(DIST, DCAT + "accessService", SVC),
        ),
        SVC: nt(a(SVC, "DataService"), (SVC, DCAT + "endpointURL", "https://api.example.org")),
    }


def report(pages: dict[str, str], walk: WalkSummary | None = None) -> dict[str, Any]:
    read = [read_page(iri, text, root=iri == FDP) for iri, text in pages.items()]
    return analyse(FDP, read, walk or WalkSummary(complete=True))


def codes(result: dict[str, Any]) -> dict[str, int]:
    return {f["code"]: f["count"] for f in [*result["findings"], *result["notes"]]}


def test_a_well_formed_fdp_reaches_its_data_services_and_is_fine() -> None:
    result = report(well_formed())
    assert (result["reach"], result["status"], result["findings"]) == ("dataservice", "fine", [])
    assert result["levels"] == {
        "fdp": 1,
        "catalog": 1,
        "dataset": 1,
        "distribution": 1,
        "dataservice": 1,
        "other": 0,
    }


def test_a_child_listed_only_in_ldp_is_reported() -> None:
    pages = well_formed()
    pages[CAT] = nt(a(CAT, "Catalog"), *container(CAT, DCAT + "dataset", DS))
    result = report(pages)
    assert codes(result) == {"not-linked-in-dcat": 1}
    assert result["findings"][0]["examples"] == [DS]


def test_a_member_relation_of_the_fdps_own_is_accepted() -> None:
    """Containers may use any relation (adms:sample, SPHN's …): its triple is the DCAT link."""
    pages = well_formed()
    sample, relation = f"{FDP}/sample/1", "http://www.w3.org/ns/adms#sample"
    pages[DS] += nt((DS, relation, sample), *container(DS + "#s", relation, sample))
    pages[sample] = nt((sample, RDF_TYPE, "http://example.org/Sample"))
    result = report(pages)
    assert "not-linked-in-dcat" not in codes(result)


def test_a_data_service_linked_only_in_dcat_is_reported() -> None:
    pages = well_formed()
    pages[DIST] = nt(a(DIST, "Distribution"), (DIST, DCAT + "accessService", SVC))
    del pages[SVC]
    result = report(pages)
    assert codes(result) == {"linked-only-in-dcat": 1}
    assert result["reach"] == "distribution"


def test_a_service_described_inside_the_distribution_is_not_a_link() -> None:
    pages = well_formed()
    pages[DIST] = nt(
        a(DIST, "Distribution"),
        (DIST, DCAT + "accessService", SVC),
        (SVC, DCAT + "endpointURL", "https://api.example.org"),
    )
    del pages[SVC]
    assert "linked-only-in-dcat" not in codes(report(pages))


def test_dead_ends_and_distributions_without_access() -> None:
    pages = well_formed()
    pages[DS] = nt(a(DS, "Dataset"))
    pages[DIST] = nt(a(DIST, "Distribution"))
    del pages[SVC]
    other = f"{FDP}/catalog/empty"
    pages[FDP] += nt((FDP, "https://w3id.org/fdp/fdp-o#metadataCatalog", other))
    pages[other] = nt(a(other, "Catalog"))
    result = report(pages)
    assert codes(result) == {
        "catalog-without-datasets": 1,
        "dataset-without-distributions": 1,
        "distribution-without-access": 1,
    }
    # DCAT's advice is a note: on its own it leaves an FDP fine.
    assert [f["code"] for f in result["findings"]] == ["catalog-without-datasets"]
    del pages[other]
    pages[FDP] = well_formed()[FDP]
    assert report(pages)["status"] == "fine"


def test_failures_by_kind_and_the_index_own_stops() -> None:
    walk = WalkSummary(
        failed={
            f"{FDP}/a": "HTTP 404",
            f"{FDP}/b": "HTTP 503",
            f"{FDP}/c": "Timeout",
            f"{FDP}/d": "cannot parse: bad turtle",
            f"{FDP}/e": "internal.example resolves to a non-public address",
        },
        unread=12,
        off_site=3,
        at_ceiling=True,
        complete=False,
    )
    result = report(well_formed(), walk)
    assert {f["kind"]: f["count"] for f in result["failures"]} == {
        "not-found": 1,
        "http-error": 1,
        "timeout": 1,
        "unparsable": 1,
        "refused": 1,
    }
    assert result["status"] == "findings"
    assert result["stops"] == {"offSite": 3, "unread": 12, "atCeiling": True}
    assert report({}, walk)["status"] == "unreadable"


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("HTTP 410", "not-found"),
        ("HTTP 500", "http-error"),
        ("response too large", "too-large"),
        ("Host not allowed: x", "refused"),
        ("IO error: connection reset", "other"),
    ],
)
def test_failure_kinds(reason: str, kind: str) -> None:
    assert failure_kind(reason) == kind


# --- computed after a harvest, and served -------------------------------------------------


async def _entry_id(services: Services) -> Any:
    async with services.session_factory() as session:
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == FDP))
    assert entry is not None
    return entry.uuid


async def test_a_report_follows_each_walk(services: Services) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    assert await services.index.schedule_health() == 1
    await services.jobs.drain()
    health = await services.index.metadata_health(entry_id)
    assert health is not None and health.report is not None
    assert (health.reach, health.status) == ("dataset", "findings")
    assert health.walk["complete"] is True
    assert health.walk["offSite"] == 1
    # Done until the next walk ends.
    assert await services.index.schedule_health() == 0
    await services.index.harvest_entry(entry_id)
    assert await services.index.schedule_health() == 1
    await services.jobs.drain()
    # Directory only: the report goes with the harvested pages.
    await services.index.update_options(entry_id, harvest_enabled=False)
    assert await services.index.metadata_health(entry_id) is None


async def test_fdps_harvested_before_reports_existed_get_one(services: Services) -> None:
    await ping(services)
    entry_id = await _entry_id(services)
    async with services.session_factory() as session, session.begin():
        health = await session.get(MetadataHealth, entry_id)
        assert health is not None
        await session.delete(health)
    assert await services.index.schedule_health() == 1
    await services.jobs.drain()
    health = await services.index.metadata_health(entry_id)
    assert health is not None and health.report is not None
    assert health.walk == {}
    assert health.report["complete"] is None


def test_health_through_the_api(api: Api) -> None:  # noqa: F811  # imported above
    assert api.ping(FDP).status_code == 204
    [entry] = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    url = f"/index/entries/{entry['uuid']}/health"
    assert api.client.get(url).json() is None  # not computed yet
    portal = api.client.portal
    assert portal is not None
    portal.call(api.app.state.index.schedule_health)
    api.drain()

    health = api.client.get(url).json()
    assert (health["reach"], health["status"], health["pages"]) == ("dataset", "findings", 3)
    assert health["stops"] == {"offSite": 1, "unread": 0, "atCeiling": False}
    # The test FDP lists its catalogue only in LDP; its dataset has no distribution (a note).
    assert [f["code"] for f in health["findings"]] == ["not-linked-in-dcat"]
    assert [f["code"] for f in health["notes"]] == ["dataset-without-distributions"]

    [row] = api.client.get("/index/health").json()
    assert (row["uuid"], row["reach"], row["findings"]) == (
        entry["uuid"],
        "dataset",
        {"not-linked-in-dcat": 1},
    )
    assert row["notes"] == {"dataset-without-distributions": 1}
    summary = api.client.get("/index/stats").json()["health"]
    assert summary["fdps"] == 1
    assert summary["byReach"]["dataset"] == 1
    assert summary["byStatus"] == {"fine": 0, "findings": 1, "unreadable": 0}
    assert summary["findings"]["not-linked-in-dcat"] == 1
    assert summary["notes"]["dataset-without-distributions"] == 1

    # With conformance reports for administrators only, health is too.
    async def admin_only() -> Any:
        return SimpleNamespace(reports="admin")

    stats = api.app.state.stats
    stats._conformance = SimpleNamespace(settings=SimpleNamespace(get=admin_only))
    stats.invalidate()
    assert api.client.get(url).status_code == 401
    assert api.client.get("/index/health").status_code == 401
    assert api.client.get("/index/stats").json()["health"] is None
    assert api.client.get(url, headers=ADMIN).json()["reach"] == "dataset"
