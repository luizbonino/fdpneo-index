"""Where FDPs are, and where their data is about (TASKS 15.4).

FDPs rarely say where they are, so an FDP's country comes from the first of:

1. ``admin``: a country an administrator set for the FDP (a per-FDP option);
2. ``stated``: the FDP's own ``r3d:country`` (an ISO code, an EU country
   authority IRI or an English country name);
3. ``publisher``: the country of its publisher's ROR record, when concept
   labels are resolved (``FDPINDEX_LABELS_RESOLVE``);
4. ``domain``: the country-code top-level domain of the host that answered,
   after redirects (``.nl``, ``.de``; not ``.org``, ``.eu``): the operator chose
   it, so it usually names the organisation's country;
5. ``server``: the country of the address that answered its last retrieval,
   after redirects (so a w3id.org redirect does not count, the server it points
   to does), from an IP geolocation database (DB-IP Lite, CC BY 4.0). It names
   the hosting company's country: Leiden FDPs on servers in Germany are placed
   in Germany. Last, for generic domains.

Where the data is about comes from records' ``dct:spatial``: EU country
authority IRIs and NUTS region codes, both of which name their country.
Countries are ISO 3166-1 alpha-2 codes throughout.
"""

from __future__ import annotations

import gzip
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCES = ("admin", "stated", "publisher", "domain", "server")

EU_COUNTRY = "http://publications.europa.eu/resource/authority/country/"
NUTS = re.compile(r"^https?://data\.europa\.eu/nuts/code/([A-Z]{2})[A-Z0-9]*$")
# NUTS and Eurostat codes that differ from ISO 3166-1.
EUROSTAT = {"EL": "GR", "UK": "GB"}
# Country-code domains that are not ISO 3166-1 alpha-2 codes.
CCTLD = {"uk": "GB"}
# Country-code domains used as generic ones (.io, .co, .ai …): they say nothing about
# where an FDP is. Google's list of ccTLDs it treats as generic, and a few more.
GENERIC_CCTLDS = frozenset(
    [
        "ad",
        "ag",
        "ai",
        "am",
        "as",
        "bz",
        "cc",
        "cd",
        "co",
        "dj",
        "fm",
        "gg",
        "io",
        "la",
        "ly",
        "me",
        "ms",
        "nu",
        "sc",
        "sh",
        "so",
        "sr",
        "su",
        "to",
        "tv",
        "vc",
        "ws",
    ]
)


@dataclass(frozen=True)
class Country:
    alpha2: str
    alpha3: str
    numeric: str
    name: str


@cache
def countries() -> dict[str, Country]:
    """ISO 3166-1 by alpha-2 code (``countries.json``, generated from pycountry)."""
    raw: list[dict[str, str]] = json.loads(
        resources.files("fdpindex").joinpath("countries.json").read_text("utf-8")
    )
    return {r["a2"]: Country(r["a2"], r["a3"], r["n"], r["name"]) for r in raw}


@cache
def _by_alpha3() -> dict[str, str]:
    return {c.alpha3: c.alpha2 for c in countries().values()}


@cache
def _by_name() -> dict[str, str]:
    return {c.name.casefold(): c.alpha2 for c in countries().values()}


def valid(code: str) -> bool:
    return code.upper() in countries()


def country_of(value: str | None) -> str | None:
    """A stated country (``r3d:country``) as an alpha-2 code, when it can be read."""
    if not value:
        return None
    text = value.strip()
    if text.startswith(EU_COUNTRY):
        return _by_alpha3().get(text.removeprefix(EU_COUNTRY).upper())
    if text.startswith(("http://", "https://")):
        return spatial_country(text)
    upper = text.upper()
    if len(upper) == 2 and upper in countries():
        return upper
    if len(upper) == 3 and upper in _by_alpha3():
        return _by_alpha3()[upper]
    return _by_name().get(text.casefold())


def spatial_country(iri: str) -> str | None:
    """The country a ``dct:spatial`` IRI names: an EU country authority or a NUTS code."""
    if iri.startswith(EU_COUNTRY):
        return _by_alpha3().get(iri.removeprefix(EU_COUNTRY).upper())
    match = NUTS.match(iri)
    if match:
        code = EUROSTAT.get(match.group(1), match.group(1))
        return code if code in countries() else None
    return None


def domain_country(host: str | None) -> str | None:
    """The country of a host's country-code top-level domain, if it has one."""
    if not host:
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        pass
    tld = host.rstrip(".").rsplit(".", 1)[-1].lower()
    if tld in GENERIC_CCTLDS:
        return None
    if tld in CCTLD:
        return CCTLD[tld]
    code = tld.upper()
    return code if len(code) == 2 and code in countries() else None


def locate(
    *,
    admin: str | None,
    stated: str | None,
    publisher: str | None,
    server: str | None,
    domain: str | None,
) -> tuple[str, str] | None:
    """The FDP's country and which source placed it (first in ``SOURCES`` order)."""
    for source, code in (
        ("admin", admin),
        ("stated", stated),
        ("publisher", publisher),
        ("domain", domain),
        ("server", server),
    ):
        if code:
            return code, source
    return None


# --- IP geolocation -------------------------------------------------------------------

DBIP_URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
DBIP_FILE = "dbip-country-lite.mmdb"


class GeoIp:
    """Country by IP address from a MaxMind-format database (DB-IP Lite); optional."""

    def __init__(self, path: Path) -> None:
        import maxminddb

        self.path = path
        self._reader = maxminddb.open_database(str(path))

    @classmethod
    def open(cls, directory: Path | None) -> GeoIp | None:
        """The database in ``directory``, or ``None`` when there is none."""
        if directory is None:
            return None
        path = directory / DBIP_FILE
        return cls(path) if path.is_file() else None

    def country(self, address: str | None) -> str | None:
        if not address:
            return None
        try:
            record: Any = self._reader.get(address)
        except ValueError:
            return None
        found = cast("dict[str, Any]", record) if isinstance(record, dict) else {}
        country = cast("dict[str, Any]", found.get("country") or {})
        code = str(country.get("iso_code") or "")
        return code.upper() if code.upper() in countries() else None

    def close(self) -> None:
        self._reader.close()


def download_geoip(
    directory: Path, fetch: Callable[[str], bytes], today: date | None = None
) -> tuple[str, int]:
    """Fetch this month's DB-IP Lite country database (last month's early in a month)."""
    today = today or date.today()
    months = [today.strftime("%Y-%m")]
    previous = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    months.append(previous.strftime("%Y-%m"))
    last_error: Exception | None = None
    for month in months:
        url = DBIP_URL.format(month=month)
        try:
            data = gzip.decompress(fetch(url))
        except Exception as err:  # a month not published yet: try the one before
            last_error = err
            continue
        directory.mkdir(parents=True, exist_ok=True)
        partial = directory / (DBIP_FILE + ".part")
        partial.write_bytes(data)
        partial.replace(directory / DBIP_FILE)
        return month, len(data)
    raise RuntimeError(f"no DB-IP Lite database could be fetched: {last_error}")
