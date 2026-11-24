"""Where FDPs are, and where their data is about (TASKS 15.4)."""

from __future__ import annotations

import gzip
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import update

from fdpindex import geo
from fdpindex.models import IndexEntry
from tests.conftest import FDP, PUBLIC_IP, FakeNetwork
from tests.test_conformance_service import (  # the search_api fixture too
    ADMIN,
    SearchApi,
    dataset,
    search_api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

NUTS = "http://data.europa.eu/nuts/code/"
EU = "http://publications.europa.eu/resource/authority/country/"


def test_stated_countries_are_read_in_their_usual_forms() -> None:
    assert geo.country_of(f"{EU}NLD") == "NL"
    assert geo.country_of("nl") == "NL"
    assert geo.country_of("DEU") == "DE"
    assert geo.country_of("Netherlands") == "NL"
    assert geo.country_of(f"{NUTS}BE21") == "BE"
    assert geo.country_of("Atlantis") is None
    assert geo.country_of(None) is None


def test_spatial_iris_name_their_country() -> None:
    assert geo.spatial_country(f"{EU}BEL") == "BE"
    assert geo.spatial_country(f"{NUTS}UKG3") == "GB"  # Eurostat's UK
    assert geo.spatial_country(f"{NUTS}EL30") == "GR"  # and its Greece
    assert geo.spatial_country(f"{NUTS}NL") == "NL"
    assert geo.spatial_country("https://www.geonames.org/2750405") is None


def test_country_code_domains() -> None:
    assert geo.domain_country("fdp.lumc.nl") == "NL"
    assert geo.domain_country("data.example.co.uk") == "GB"
    assert geo.domain_country("w3id.org") is None
    assert geo.domain_country("fdp.example.eu") is None
    assert geo.domain_country("192.0.2.1") is None
    assert geo.domain_country("fdp.example.io") is None  # used as a generic domain
    assert geo.domain_country("fdp.example.co") is None


def test_the_first_source_wins() -> None:
    # A country-code domain names the operator's country; a server, its host's.
    assert geo.locate(admin=None, stated=None, publisher=None, server="DE", domain="NL") == (
        "NL",
        "domain",
    )
    assert geo.locate(admin=None, stated=None, publisher=None, server="DE", domain=None) == (
        "DE",
        "server",
    )
    assert geo.locate(admin="BE", stated="NL", publisher=None, server="DE", domain=None) == (
        "BE",
        "admin",
    )
    assert geo.locate(admin=None, stated=None, publisher=None, server=None, domain=None) is None


def test_the_database_download_falls_back_to_last_month(tmp_path: Path) -> None:
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        asked.append(url)
        if "2026-12" in url:
            raise RuntimeError("404")
        return gzip.compress(b"mmdb")

    month, size = geo.download_geoip(tmp_path, fetch, today=date(2026, 12, 1))
    assert (month, size) == ("2026-11", 4)
    assert (tmp_path / geo.DBIP_FILE).read_bytes() == b"mmdb"
    assert [u.rsplit("-", 2)[-2:] for u in asked] == [
        ["2026", "12.mmdb.gz"],
        ["2026", "11.mmdb.gz"],
    ]
    # January's fallback is December of the year before.
    january = geo.download_geoip(tmp_path, lambda u: gzip.compress(b"x"), today=date(2027, 1, 9))
    assert january[0] == "2027-01"


class FakeGeoIp:
    def __init__(self, table: dict[str, str]) -> None:
        self.table = table
        self.asked: list[str | None] = []

    def country(self, address: str | None) -> str | None:
        self.asked.append(address)
        return self.table.get(address or "")


@pytest.fixture
def api(search_api: SearchApi) -> Iterator[SearchApi]:  # noqa: F811
    yield search_api


def _retrieve_again(api: SearchApi) -> None:
    async def forget_last_retrieval() -> None:  # the retrieval rate limit would skip it
        async with api.app.state.session_factory() as session, session.begin():
            await session.execute(update(IndexEntry).values(last_retrieval_at=None))

    api.call(forget_last_retrieval)
    response = api.client.post("/index/admin/trigger", json={"clientUrl": FDP}, headers=ADMIN)
    assert response.status_code == 204, response.text
    api.settle()


def _entry(api: SearchApi) -> dict[str, Any]:
    uuid = api.client.get("/index/entries").json()["content"][0]["uuid"]
    return api.client.get(f"/index/entries/{uuid}").json()


def test_an_fdp_is_placed_by_its_server_then_by_an_administrator(api: SearchApi) -> None:
    fake = FakeGeoIp({PUBLIC_IP: "DE"})
    api.app.state.index._geoip = fake
    _retrieve_again(api)
    assert PUBLIC_IP in fake.asked  # the address the index connected to
    entry = _entry(api)
    assert entry["location"] == {"country": "DE", "source": "server"}
    api.app.state.stats.invalidate()
    fdps = api.client.get("/index/stats").json()["fdps"]
    assert fdps["byCountry"] == [
        {
            "country": "DE",
            "fdps": 1,
            "bySource": {"admin": 0, "stated": 0, "publisher": 0, "domain": 0, "server": 1},
        }
    ]
    assert fdps["unplaced"] == 0

    url = f"/index/entries/{entry['uuid']}"
    bad = api.client.put(url, json={"options": {"country": "XX"}}, headers=ADMIN)
    assert bad.status_code == 400
    put = api.client.put(url, json={"options": {"country": "be"}}, headers=ADMIN)
    assert put.status_code == 200, put.text
    assert put.json()["options"]["country"] == "BE"
    assert put.json()["location"] == {"country": "BE", "source": "admin"}
    cleared = api.client.put(url, json={"options": {"country": ""}}, headers=ADMIN)
    assert cleared.json()["location"] == {"country": "DE", "source": "server"}


def test_an_fdp_nobody_can_place(api: SearchApi) -> None:
    # fdp.example.org: no stated country, no server database, a generic domain.
    assert _entry(api)["location"] is None
    api.app.state.stats.invalidate()
    fdps = api.client.get("/index/stats").json()["fdps"]
    assert (fdps["byCountry"], fdps["unplaced"]) == ([], 1)


def test_records_are_counted_by_the_country_their_spatial_coverage_names(
    api: SearchApi, network: FakeNetwork
) -> None:
    for name, places in (
        ("good", [f"{NUTS}UKG3", f"{NUTS}UKL"]),  # two regions of one country: once
        ("bad", [f"{EU}BEL"]),
    ):
        body = dataset(name, distribution="g1" if name == "good" else None)
        assert body.endswith(" .")
        spatial = "".join(f" ;\n    dct:spatial <{p}>" for p in places)
        network.serve(f"{FDP}/dataset/{name}", body[:-2] + spatial + " .")
    _retrieve_again(api)
    api.app.state.stats.invalidate()
    countries = api.client.get("/index/stats").json()["records"]["byCountry"]
    assert [(c["country"], c["records"]) for c in countries] == [("BE", 1), ("GB", 1)]
    assert countries[1]["values"] == [f"{NUTS}UKG3", f"{NUTS}UKL"]
