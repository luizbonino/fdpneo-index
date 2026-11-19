"""Search settings an administrator changes at run time (TASKS 8.13).

* **Facets**: which facets the search page shows, in what order, and where:
  among the main facets, under "more facets", or hidden. Besides the
  profile's facet fields and ``fdp``, a facet may be any captured property
  (``prop:<IRI>``), with its own label and help.
* **Properties**: whether visitors may add facets on any property found in
  the harvested metadata, and properties (IRIs or namespaces) they may not.

Stored as the second row of ``index_settings``; without one, the profile's
facets apply in the profile's order and visitors may add property facets.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fdpindex.db import utcnow
from fdpindex.models import IndexSettingsRow

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.search.profile import Profile

_ROW_ID = 2
PROPERTY_FACET = "prop:"
MAX_SETTING_FACETS = 40
# A property IRI: absolute, without spaces or characters that break SPARQL or URLs.
_IRI = re.compile(r"^(https?://|urn:)[^\s<>\"{}|\\^`]{1,2040}$")

Placement = Literal["main", "more", "hidden"]


class _Camel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", json_schema_serialization_defaults_required=True
    )


class FacetSetting(_Camel):
    name: str = Field(description="A facet field of the profile, `fdp`, or `prop:<IRI>`")
    placement: Placement = "main"
    label: dict[str, str] = Field(default_factory=dict, description="Per language; overrides")
    help: dict[str, str] = Field(default_factory=dict)


class PropertySettings(_Camel):
    visitors: bool = Field(
        default=True, description="Visitors may add facets on any captured property"
    )
    hidden: list[str] = Field(
        default_factory=list,
        description="Property IRIs, or namespaces ending in / or #, visitors may not use",
    )

    @field_validator("hidden")
    @classmethod
    def _iris(cls, value: list[str]) -> list[str]:
        for iri in value:
            if not _IRI.match(iri):
                raise ValueError(f"not an IRI: {iri}")
        return list(dict.fromkeys(value))


class SearchSettings(_Camel):
    facets: list[FacetSetting] | None = Field(
        default=None, description="Ordered facets; null for the profile's"
    )
    properties: PropertySettings = Field(default_factory=PropertySettings)
    default_language: str = Field(
        default="en",
        alias="defaultLanguage",
        pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$",
        description="Language of titles and labels when the reader's browser states none",
    )

    @field_validator("facets")
    @classmethod
    def _unique(cls, value: list[FacetSetting] | None) -> list[FacetSetting] | None:
        if value is None:
            return None
        if len(value) > MAX_SETTING_FACETS:
            raise ValueError(f"at most {MAX_SETTING_FACETS} facets")
        names = [f.name for f in value]
        if len(set(names)) != len(names):
            raise ValueError("a facet is listed twice")
        for facet in value:
            if facet.name.startswith(PROPERTY_FACET) and not is_property_iri(
                facet.name.removeprefix(PROPERTY_FACET)
            ):
                raise ValueError(f"not a property IRI: {facet.name}")
        return value

    def check(self, profile: Profile) -> None:
        """Facet names must exist: a profile facet, ``fdp``, or a property."""
        allowed = {*profile.facets, "fdp"}
        for facet in self.facets or []:
            if facet.name not in allowed and not facet.name.startswith(PROPERTY_FACET):
                raise ValueError(f"unknown facet: {facet.name}")

    def hides(self, iri: str) -> bool:
        return any(
            iri == hidden or (hidden.endswith(("/", "#")) and iri.startswith(hidden))
            for hidden in self.properties.hidden
        )


class SearchSettingsView(SearchSettings):
    is_default: bool = Field(alias="isDefault")


def is_property_iri(iri: str) -> bool:
    return bool(_IRI.match(iri))


def effective_facets(settings: SearchSettings, profile: Profile) -> list[FacetSetting]:
    """The facets in order: the configured ones, then profile facets the setting leaves out.

    A profile facet added after the setting was saved shows where the profile puts it.
    """
    main = [n for n in profile.facets if profile.fields[n].facet == "main"]
    more = [n for n in profile.facets if profile.fields[n].facet != "main"]
    defaults = [
        *(FacetSetting(name=name, placement="main") for name in main),
        FacetSetting(name="fdp", placement="main"),
        *(FacetSetting(name=name, placement="more") for name in more),
    ]
    if settings.facets is None:
        return defaults
    listed = {f.name for f in settings.facets}
    return [*settings.facets, *(f for f in defaults if f.name not in listed)]


class SearchSettingsService:
    """Reads (cached for a few seconds) and writes the search settings row."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        profile: Callable[[], Profile],
    ) -> None:
        self._session_factory = session_factory
        self._profile = profile  # the current profile: an administrator may change it
        self._cached: tuple[float, SearchSettings] | None = None
        self.version = 0  # bumped on every change; part of cache keys
        # Kept at hand for picking a request's language without a database read.
        self.default_language = "en"

    async def get(self) -> SearchSettings:
        cached = self._cached
        if cached is not None and time.monotonic() - cached[0] < 5:
            return cached[1]
        async with self._session_factory() as session:
            row = await session.get(IndexSettingsRow, _ROW_ID)
        settings = SearchSettings() if row is None else SearchSettings.model_validate(row.value)
        self._cached = (time.monotonic(), settings)
        self.default_language = settings.default_language
        return settings

    async def view(self) -> SearchSettingsView:
        return _view(await self.get())

    async def update(self, settings: SearchSettings) -> SearchSettingsView:
        settings.check(self._profile())
        payload = settings.model_dump(mode="json", by_alias=True)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is None:
                session.add(IndexSettingsRow(id=_ROW_ID, value=payload))
            else:
                row.value = payload
                row.updated_at = utcnow()
        self._cached = (time.monotonic(), settings)
        self.default_language = settings.default_language
        self.version += 1
        return _view(settings)

    async def reset(self) -> SearchSettingsView:
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is not None:
                await session.delete(row)
        settings = SearchSettings()
        self._cached = (time.monotonic(), settings)
        self.default_language = settings.default_language
        self.version += 1
        return _view(settings)


def _view(settings: SearchSettings) -> SearchSettingsView:
    return SearchSettingsView(
        facets=settings.facets,
        properties=settings.properties,
        defaultLanguage=settings.default_language,
        isDefault=settings == SearchSettings(),
    )
