"""Network profiles: what a network expects of the FDPs it lists, as data (TASKS 15.9).

Design note metadata-health 3.3. One YAML document with a fixed structure
(:class:`NetworkProfile`, whose JSON Schema ``GET /index/network-profile/schema``
serves), so every network states its requirements the same way:

* ``network``: its name, homepage, contact and description;
* ``reach``: the level an FDP's metadata must reach (FDP, catalogue, dataset,
  distribution, data service);
* ``levels``: per level, the types that count as it, the properties a record
  ``requires`` and ``recommends``;
* ``vocabularies``: where a property's values must come from (IRI prefixes);
* ``shapes``: SHACL documents, checked as a required conformance check;
* ``benchmarks``: FAIR Testing Resources benchmarks, the network's own
  verdict, run outside the index (TASKS 15.10);
* ``prefixes``: CURIE prefixes beyond the common ones.

The generic profile, the default, expects nothing beyond the FDP
specification: the index does not impose a level an FDP never promised. An
administrator edits the profile on the settings page (stored as the sixth row
of ``index_settings``, with the YAML as written) or installs a file with
``FDPINDEX_NETWORK_PROFILE``. ``network_profiles/`` bundles the generic
profile and ERDERA's as an example.

Reach, required and recommended properties and vocabularies become the
metadata health report's requirements (``fdpindex.findings``); shapes become
the conformance check ``network``.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fdpindex.db import utcnow
from fdpindex.models import IndexSettingsRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BUNDLED = Path(__file__).parent / "network_profiles"
EXAMPLES = ("generic", "erdera")
_ROW_ID = 6
MAX_SOURCE = 64 * 1024

Level = Literal["fdp", "catalog", "dataset", "distribution", "dataservice"]
LEVELS: tuple[Level, ...] = ("fdp", "catalog", "dataset", "distribution", "dataservice")

COMMON_PREFIXES = {
    "adms": "http://www.w3.org/ns/adms#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "fdp-o": "https://w3id.org/fdp/fdp-o#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "ldp": "http://www.w3.org/ns/ldp#",
    "prof": "http://www.w3.org/ns/dx/prof/",
    "r3d": "http://www.re3data.org/schema/3-0#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "schema": "http://schema.org/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _web(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"not an http(s) address: {value}")
    return value


class NetworkInfo(_Model):
    name: str = Field(default="", max_length=200, description="The network's name")
    homepage: str | None = Field(default=None, description="Where people read about it")
    contact: str | None = Field(default=None, description="A mailto: or web address")
    description: str = Field(default="", max_length=4000)

    @field_validator("homepage")
    @classmethod
    def _homepage(cls, value: str | None) -> str | None:
        return _web(value) if value else None

    @field_validator("contact")
    @classmethod
    def _contact(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith("mailto:"):
            return value
        return _web(value)


class LevelRules(_Model):
    types: list[str] = Field(
        default_factory=lambda: [],
        max_length=20,
        description="Classes (CURIEs or IRIs) that count as this level, besides DCAT's",
    )
    requires: list[str] = Field(
        default_factory=lambda: [],
        max_length=50,
        description="Properties every record at this level must have",
    )
    recommends: list[str] = Field(
        default_factory=lambda: [],
        max_length=50,
        description="Properties a record at this level should have",
    )


class NetworkProfile(_Model):
    """What a network expects of the FDPs it lists (TASKS 15.9)."""

    profile: Literal[1] = Field(default=1, description="The template's version")
    network: NetworkInfo = Field(default_factory=NetworkInfo)
    prefixes: dict[str, str] = Field(
        default_factory=lambda: {}, description="CURIE prefixes beyond the common ones"
    )
    reach: Level | None = Field(
        default=None, description="The level an FDP's metadata must reach; none: no level"
    )
    levels: dict[Level, LevelRules] = Field(default_factory=lambda: {})
    vocabularies: dict[str, list[str]] = Field(
        default_factory=lambda: {},
        description="Per property, the IRI prefixes its values must start with",
    )
    shapes: list[str] = Field(
        default_factory=lambda: [],
        max_length=10,
        description="SHACL documents every record is checked against (as the check 'network')",
    )
    benchmarks: list[str] = Field(
        default_factory=lambda: [],
        max_length=10,
        description="FAIR Testing Resources benchmarks, run outside the index (TASKS 15.10)",
    )

    @field_validator("shapes", "benchmarks")
    @classmethod
    def _addresses(cls, values: list[str]) -> list[str]:
        return [_web(v) for v in values]

    @model_validator(mode="after")
    def _terms_resolve(self) -> NetworkProfile:
        """Every CURIE must use a known prefix: a typing error would otherwise silently
        require a property nobody has."""
        terms = [t for rules in self.levels.values() for t in (*rules.types, *rules.requires)]
        terms += [t for rules in self.levels.values() for t in rules.recommends]
        terms += list(self.vocabularies)
        for term in terms:
            self.expand(term)
        for values in self.vocabularies.values():
            for value in values:
                _web(value)
        return self

    def expand(self, term: str) -> str:
        """A CURIE or an IRI as an IRI."""
        text = term.strip()
        if text.startswith(("http://", "https://", "urn:")):
            return text
        prefix, sep, local = text.partition(":")
        if not sep:
            raise ValueError(f"not a CURIE or an IRI: {term}")
        base = self.prefixes.get(prefix) or COMMON_PREFIXES.get(prefix)
        if base is None:
            raise ValueError(f"unknown prefix '{prefix}' in {term}: add it under prefixes")
        return base + local

    def expectations(self) -> Expectations:
        levels = {
            level: (
                frozenset(self.expand(t) for t in rules.types),
                tuple(self.expand(p) for p in rules.requires),
                tuple(self.expand(p) for p in rules.recommends),
            )
            for level, rules in self.levels.items()
        }
        return Expectations(
            reach=self.reach,
            types={level: v[0] for level, v in levels.items() if v[0]},
            requires={level: v[1] for level, v in levels.items() if v[1]},
            recommends={level: v[2] for level, v in levels.items() if v[2]},
            vocabularies={self.expand(p): tuple(values) for p, values in self.vocabularies.items()},
        )

    @property
    def empty(self) -> bool:
        e = self.expectations()
        return not (e.reach or e.requires or e.recommends or e.vocabularies or self.shapes)


@dataclass(frozen=True)
class Expectations:
    """A profile's requirements as IRIs, for ``fdpindex.findings``."""

    reach: str | None = None
    types: dict[str, frozenset[str]] = field(default_factory=lambda: {})
    requires: dict[str, tuple[str, ...]] = field(default_factory=lambda: {})
    recommends: dict[str, tuple[str, ...]] = field(default_factory=lambda: {})
    vocabularies: dict[str, tuple[str, ...]] = field(default_factory=lambda: {})

    @property
    def watched(self) -> frozenset[str]:
        """Predicates whose presence or values a page must report."""
        found = {p for ps in self.requires.values() for p in ps}
        found |= {p for ps in self.recommends.values() for p in ps}
        return frozenset(found | set(self.vocabularies))

    def digest(self) -> str:
        payload = repr(
            (
                self.reach,
                sorted((k, sorted(v)) for k, v in self.types.items()),
                sorted(self.requires.items()),
                sorted(self.recommends.items()),
                sorted(self.vocabularies.items()),
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ProfileError(ValueError):
    """A network profile that cannot be used, with the reason."""


def parse(source: str) -> NetworkProfile:
    if len(source) > MAX_SOURCE:
        raise ProfileError(f"the profile is longer than {MAX_SOURCE // 1024} KiB")
    try:
        data: Any = yaml.safe_load(source) if source.strip() else {}
    except yaml.YAMLError as err:
        raise ProfileError(f"not valid YAML: {err}") from err
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ProfileError("the profile must be a mapping (key: value)")
    try:
        return NetworkProfile.model_validate(data)
    except ValueError as err:
        raise ProfileError(str(err)) from err


def example(name: str) -> str:
    if name not in EXAMPLES:
        raise ProfileError(f"no bundled example '{name}'")
    return (BUNDLED / f"{name}.yaml").read_text(encoding="utf-8")


class NetworkProfileService:
    """Reads (cached for a few seconds) and writes the network profile."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], default_source: str
    ) -> None:
        self._session_factory = session_factory
        self.default_source = default_source
        parse(default_source)  # a broken installed file stops the start
        self._cached: tuple[float, str, NetworkProfile, bool] | None = None
        self.listeners: list[Any] = []

    async def get(self) -> tuple[str, NetworkProfile, bool]:
        """The YAML, the parsed profile, and whether it is the default."""
        cached = self._cached
        if cached is not None and time.monotonic() - cached[0] < 5:
            return cached[1], cached[2], cached[3]
        async with self._session_factory() as session:
            row = await session.get(IndexSettingsRow, _ROW_ID)
        source = self.default_source if row is None else str(row.value.get("source", ""))
        try:
            profile = parse(source)
        except ProfileError:
            source, profile = self.default_source, parse(self.default_source)
        self._cached = (time.monotonic(), source, profile, row is None)
        return source, profile, row is None

    async def profile(self) -> NetworkProfile:
        return (await self.get())[1]

    async def update(self, source: str) -> NetworkProfile:
        profile = parse(source)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is None:
                session.add(IndexSettingsRow(id=_ROW_ID, value={"source": source}))
            else:
                row.value = {"source": source}
                row.updated_at = utcnow()
        self._cached = None
        await self._changed()
        return profile

    async def reset(self) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is not None:
                await session.delete(row)
        self._cached = None
        await self._changed()

    async def _changed(self) -> None:
        for listener in self.listeners:
            await listener()


def default_source(path: Path | None) -> str:
    """The installed profile (``FDPINDEX_NETWORK_PROFILE``), or the generic one."""
    if path is None:
        return example("generic")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise ProfileError(f"FDPINDEX_NETWORK_PROFILE: {err}") from err
