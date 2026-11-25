"""Statistics on the index (TASKS 15.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import update

from fdpindex.db import utcnow
from fdpindex.models import SearchResource
from tests.conftest import FDP
from tests.test_conformance_service import (  # the search_api fixture too
    ADMIN,
    SearchApi,
    search_api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


@pytest.fixture
def api(search_api: SearchApi) -> Iterator[SearchApi]:  # noqa: F811
    yield search_api


def stats(api: SearchApi, headers: dict[str, str] | None = None, **params: Any) -> dict[str, Any]:
    api.app.state.stats.invalidate()
    response = api.client.get("/index/stats", params=params, headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_what_the_index_holds_now(api: SearchApi) -> None:
    body = stats(api)
    assert body["fdps"]["total"] == 1
    assert body["fdps"]["byState"]["ACTIVE"] == 1
    kinds = body["records"]["byKind"]
    assert kinds["fdp"] == 1
    assert kinds["catalog"] == 1
    assert kinds["dataset"] == 3
    assert body["records"]["total"] == sum(kinds.values())
    # The FDP's own description is not one of its records.
    assert body["largest"] == [
        {
            "uuid": body["largest"][0]["uuid"],
            "clientUrl": FDP,
            "title": body["largest"][0]["title"],
            "records": body["records"]["total"] - 1,
        }
    ]
    assert body["largest"][0]["title"]
    (share,) = body["conformance"]
    assert share["pack"] == "dcat-ap"
    assert share["title"]["en"]
    assert share["conforms"] == 3  # the FDP, the catalogue, the "good" dataset
    assert share["byKind"]["dataset"] == {"checked": 3, "conforms": 1}
    assert share["checked"] >= 5


def test_records_are_counted_as_visitors_find_them(api: SearchApi) -> None:
    body = {
        "packs": [{"key": "dcat-ap@3.0.1"}],
        "declared": False,
        "surfaces": {"search": "dcat-ap"},
    }
    response = api.client.put("/index/conformance/settings", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    api.settle()
    # Visitors' searches find one dataset; administrators see the same statistics.
    assert stats(api)["records"]["byKind"]["dataset"] == 1
    assert stats(api, ADMIN)["records"]["byKind"]["dataset"] == 1


def test_conformance_shares_follow_who_may_read_reports(api: SearchApi) -> None:
    body = {"packs": [{"key": "dcat-ap@3.0.1"}], "declared": False, "reports": "admin"}
    response = api.client.put("/index/conformance/settings", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    api.call(api.app.state.stats.snapshot)
    anonymous = stats(api)
    assert anonymous["conformance"] is None
    assert not [m for m in anonymous["history"]["series"] if m.startswith("conformance.")]
    admin = stats(api, ADMIN)
    assert admin["conformance"][0]["pack"] == "dcat-ap"
    assert "conformance.dcat-ap.conforms" in admin["history"]["series"]


def test_history_from_daily_snapshots_with_gaps(api: SearchApi) -> None:
    assert stats(api)["history"] == {"days": [], "series": {}}
    today = utcnow().date()
    api.call(api.app.state.stats.snapshot, today - timedelta(days=2))
    api.call(api.app.state.stats.snapshot)
    api.call(api.app.state.stats.snapshot)  # again the same day: replaced, not added
    history = stats(api)["history"]
    assert history["days"] == [(today - timedelta(days=n)).isoformat() for n in (2, 1, 0)]
    assert history["series"]["fdps.total"] == [1, None, 1]
    assert history["series"]["fdps.state.ACTIVE"] == [1, None, 1]
    assert history["series"]["records.kind.dataset"] == [3, None, 3]
    # A shorter window leaves out what came before it.
    assert stats(api, days=1)["history"]["days"] == [today.isoformat()]


def test_registrations_by_month(api: SearchApi) -> None:
    months = stats(api)["registrations"]
    now = utcnow()
    assert months == [{"month": f"{now.year:04d}-{now.month:02d}", "count": 1}]


def test_datasets_by_year_of_issue(api: SearchApi) -> None:
    async def issue() -> None:
        async with api.app.state.session_factory() as session, session.begin():
            for name, year in (("good", 2019), ("bad", 2021), ("nodist", 1066)):
                await session.execute(
                    update(SearchResource)
                    .where(SearchResource.iri == f"{FDP}/dataset/{name}")
                    .values(issued=datetime(year, 6, 1, tzinfo=UTC))
                )

    api.call(issue)
    years = stats(api)["published"]
    # 1066 is a typing error, not history; every year from the first is there.
    assert years[:3] == [
        {"year": 2019, "count": 1},
        {"year": 2020, "count": 0},
        {"year": 2021, "count": 1},
    ]
    assert years[-1]["year"] == utcnow().year
    assert sum(y["count"] for y in years) == 2


def test_days_are_bounded(api: SearchApi) -> None:
    assert api.client.get("/index/stats", params={"days": 0}).status_code == 400
    assert api.client.get("/index/stats", params={"days": 4000}).status_code == 400
