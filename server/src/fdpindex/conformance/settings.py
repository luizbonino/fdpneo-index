"""Conformance settings an administrator changes at run time (TASKS 11.3; design §2, §8).

* **packs**: the packs this deployment validates against, in order; per pack
  the record kinds (narrower than the manifest's, never wider) and the files
  validated against (the manifest's defaults when unset).
* **reports**: who may read conformance reports, ``public`` (the default,
  like the metadata they describe) or ``admin``.

Stored as the fifth row of ``index_settings``. Without one, DCAT-AP 3.0.1 is
validated and reports are public.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from fdpindex.db import utcnow
from fdpindex.models import IndexSettingsRow
from fdpindex.search.profile import Kind

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.conformance.packs import Pack

_ROW_ID = 5
DEFAULT_PACKS = ("dcat-ap@3.0.1",)
MAX_PACKS = 10


class _Camel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        serialize_by_alias=True,
        json_schema_serialization_defaults_required=True,
    )


class ActivePack(_Camel):
    key: str = Field(description="id@version, as GET /index/conformance/packs lists it")
    kinds: list[Kind] | None = Field(
        default=None, description="Record kinds validated; null for the pack's own"
    )
    files: list[str] | None = Field(
        default=None, description="Files validated against; null for the pack's defaults"
    )


class Surfaces(_Camel):
    """The minimum each surface shows to visitors: a pack id (no violations against it), or
    null for everything (design §5). Administrators always see everything."""

    search: str | None = Field(default=None, description="Search hits and facets")
    sparql: str | None = Field(default=None, description="The SPARQL store's harvested graphs")
    feed: str | None = Field(default=None, description="The catalogue feed (TASKS 11.5)")
    directory: str | None = Field(
        default=None, description="The public FDP list: FDPs whose own description conforms"
    )

    def chosen(self) -> dict[str, str]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ConformanceSettings(_Camel):
    packs: list[ActivePack] = Field(
        default_factory=lambda: [ActivePack(key=k) for k in DEFAULT_PACKS], max_length=MAX_PACKS
    )
    reports: Literal["public", "admin"] = Field(
        default="public", description="Who may read conformance reports"
    )
    surfaces: Surfaces = Field(default_factory=Surfaces)

    def check(self, available: dict[str, Pack]) -> None:
        """Packs must exist; kinds and files must be the pack's own."""
        keys = [p.key for p in self.packs]
        if len(set(keys)) != len(keys):
            raise ValueError("a pack is listed twice")
        ids = [k.split("@")[0] for k in keys]
        if len(set(ids)) != len(ids):
            raise ValueError("two versions of one pack: results are kept per pack id")
        for active in self.packs:
            pack = available.get(active.key)
            if pack is None:
                raise ValueError(f"unknown pack: {active.key}")
            for kind in active.kinds or []:
                if kind not in pack.manifest.applies_to:
                    raise ValueError(f"{active.key} does not apply to {kind}")
            paths = {f.path for f in pack.manifest.files}
            for path in active.files or []:
                if path not in paths:
                    raise ValueError(f"{active.key} has no file {path}")
            if active.files is not None and not any(
                f.role == "shapes" and f.path in active.files for f in pack.manifest.files
            ):
                raise ValueError(f"{active.key}: no shapes file chosen")
        for surface, pack_id in self.surfaces.chosen().items():
            if pack_id not in ids:
                raise ValueError(f"surface {surface}: {pack_id} is not an active pack")


class ConformanceSettingsView(ConformanceSettings):
    is_default: bool = Field(alias="isDefault")


def pack_digest(pack: Pack, active: ActivePack) -> str:
    """The pack's files and the deployment's choice of them: results are stale when it changes."""
    chosen = sorted(active.files) if active.files is not None else None
    return hashlib.sha256(f"{pack.digest}:{json.dumps(chosen)}".encode()).hexdigest()


def kinds_of(pack: Pack, active: ActivePack) -> frozenset[str]:
    return frozenset(active.kinds if active.kinds is not None else pack.manifest.applies_to)


class ConformanceSettingsService:
    """Reads (cached for a few seconds) and writes the conformance settings row."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        packs: dict[str, Pack],
        defaults: ConformanceSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.packs = packs
        # The deployment profile's defaults (TASKS 11.6); "reset" returns to them.
        self.defaults = defaults if defaults is not None else ConformanceSettings()
        self._cached: tuple[float, ConformanceSettings] | None = None
        self.version = 0

    async def get(self) -> ConformanceSettings:
        cached = self._cached
        if cached is not None and time.monotonic() - cached[0] < 5:
            return cached[1]
        async with self._session_factory() as session:
            row = await session.get(IndexSettingsRow, _ROW_ID)
        settings = (
            self.defaults.model_copy(deep=True)
            if row is None
            else ConformanceSettings.model_validate(row.value)
        )
        # A pack an operator removed since is skipped, not an error.
        settings.packs = [p for p in settings.packs if p.key in self.packs]
        ids = {p.key.split("@")[0] for p in settings.packs}
        for surface, pack_id in settings.surfaces.chosen().items():
            if pack_id not in ids:
                setattr(settings.surfaces, surface, None)
        self._cached = (time.monotonic(), settings)
        return settings

    async def view(self) -> ConformanceSettingsView:
        return _view(await self.get(), self.defaults)

    async def update(self, settings: ConformanceSettings) -> ConformanceSettingsView:
        settings.check(self.packs)
        payload = settings.model_dump(mode="json", by_alias=True)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is None:
                session.add(IndexSettingsRow(id=_ROW_ID, value=payload))
            else:
                row.value = payload
                row.updated_at = utcnow()
        self._cached = (time.monotonic(), settings)
        self.version += 1
        return _view(settings, self.defaults)

    async def reset(self) -> ConformanceSettingsView:
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is not None:
                await session.delete(row)
        settings = self.defaults.model_copy(deep=True)
        self._cached = (time.monotonic(), settings)
        self.version += 1
        return _view(settings, self.defaults)


def _view(settings: ConformanceSettings, defaults: ConformanceSettings) -> ConformanceSettingsView:
    return ConformanceSettingsView(
        packs=settings.packs,
        reports=settings.reports,
        surfaces=settings.surfaces,
        isDefault=settings == defaults,
    )
