"""One FDP, one entry: URL variants and merging duplicates (fdpindex.urls)."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from fdpindex.db import utcnow
from fdpindex.models import EntryPermit, EntryState, IndexEntry
from fdpindex.urls import url_key
from tests.conftest import FDP
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit


@pytest.fixture
def index_api(api: Api) -> Api:  # noqa: F811  # the fixture imported above
    return api


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://fdp.example.org", "https://fdp.example.org/"),
        ("https://fdp.example.org", "http://fdp.example.org"),
        ("https://FDP.Example.org/", "https://fdp.example.org"),
        ("https://fdp.example.org:443/fdp/", "http://fdp.example.org:80/fdp"),
        ("https://fdp.example.org/fdp#top", " https://fdp.example.org/fdp "),
    ],
)
def test_variants_of_one_url_share_a_key(a: str, b: str) -> None:
    assert url_key(a) == url_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://fdp.example.org/a", "https://fdp.example.org/A"),  # paths are case-sensitive
        ("https://fdp.example.org", "https://fdp.example.org:8443"),
        ("https://fdp.example.org/?x=1", "https://fdp.example.org/?x=2"),
        ("https://fdp.example.org", "https://www.fdp.example.org"),
    ],
)
def test_different_fdps_keep_different_keys(a: str, b: str) -> None:
    assert url_key(a) != url_key(b)


def test_a_variant_of_a_known_url_updates_that_entry(index_api: Api) -> None:
    assert index_api.ping(FDP).status_code == 204
    assert index_api.ping(FDP + "/").status_code == 204
    assert index_api.ping(FDP.replace("https://", "http://")).status_code == 204
    entries = index_api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()
    assert [e["clientUrl"] for e in entries["content"]] == [FDP]


def _add(index_api: Api, url: str, **values: object) -> str:
    """An entry stored as an import from a reference index would: without matching."""
    entry_id = uuid4()

    async def add() -> None:
        async with index_api.app.state.session_factory() as session, session.begin():
            session.add(
                IndexEntry(
                    uuid=entry_id,
                    client_url=url,
                    permit=EntryPermit.ACCEPTED,
                    repository_metadata={},
                    **values,
                )
            )

    portal = index_api.client.portal
    assert portal is not None
    portal.call(add)
    return str(entry_id)


def test_duplicates_are_listed_and_merged(index_api: Api) -> None:
    now = utcnow()
    # A working http entry beats a broken https one; a trailing slash is no reason to differ.
    broken = _add(index_api, "https://a.example.org", state=EntryState.INVALID)
    working = _add(index_api, "http://a.example.org", state=EntryState.VALID, last_retrieval_at=now)
    older = _add(index_api, "https://b.example.org/", created_at=now - timedelta(days=2))
    newer = _add(index_api, "https://b.example.org", created_at=now - timedelta(days=1))
    _add(index_api, "https://c.example.org")  # no duplicate

    assert index_api.client.get("/index/admin/duplicates").status_code == 401
    groups = index_api.client.get("/index/admin/duplicates", headers=ADMIN).json()
    assert [(g["key"], g["keep"]["uuid"], [r["uuid"] for r in g["remove"]]) for g in groups] == [
        ("//a.example.org", working, [broken]),
        ("//b.example.org", older, [newer]),
    ]
    assert all(not g["merged"] and not g["conflicts"] for g in groups)

    merged = index_api.client.post("/index/admin/duplicates/merge", headers=ADMIN).json()
    assert [g["merged"] for g in merged] == [True, True]
    assert index_api.client.get("/index/admin/duplicates", headers=ADMIN).json() == []
    left = index_api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()
    assert sorted(e["clientUrl"] for e in left["content"]) == [
        "http://a.example.org",
        "https://b.example.org/",
        "https://c.example.org",
    ]


def test_a_group_with_conflicts_is_left_to_an_administrator(index_api: Api) -> None:
    _add(index_api, "https://d.example.org", state=EntryState.VALID)
    rejected = _add(index_api, "http://d.example.org")

    async def reject() -> None:
        async with index_api.app.state.session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, UUID(rejected))
            assert entry is not None
            entry.permit = EntryPermit.REJECTED
            entry.country = "NL"

    portal = index_api.client.portal
    assert portal is not None
    portal.call(reject)
    [group] = index_api.client.post("/index/admin/duplicates/merge", headers=ADMIN).json()
    assert not group["merged"]
    assert group["conflicts"] == [
        "approval differs: ACCEPTED, REJECTED",
        "http://d.example.org has a country set by an administrator",
    ]
    assert len(index_api.client.get("/index/admin/duplicates", headers=ADMIN).json()) == 1
