"""An administrator's changes to the search profile (TASKS 6.12, 9.3).

Stored as an *overlay* on the bundled profile rather than a full copy, so an
index that adds one facet still receives the bundled profile's improvements
in later releases. The overlay may hold:

* ``prefixes``: added to the bundled ones (or replacing one of the same name);
* ``fields``: a field by name, replacing the bundled field of that name or
  adding one (an extension facet); ``null`` removes a bundled field;
* ``types``: the classes of a kind, replacing the bundled list for that kind;
* ``normalise``: entries added to a table, or a new table;
* ``cards`` (per kind), ``landing`` and ``capture``: replaced.

The merged profile is validated as a whole before it is stored. A change
re-projects every entry in the background; search keeps answering from the
old rows meanwhile. Stored as the third row of ``index_settings``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Any

import structlog

from fdpindex.db import utcnow
from fdpindex.models import IndexSettingsRow
from fdpindex.search.profile import Profile, default_profile

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)

_ROW_ID = 3
OVERLAY_KEYS = frozenset(
    {"prefixes", "fields", "types", "normalise", "cards", "landing", "capture"}
)


class ProfileError(ValueError):
    """The overlay is not acceptable; the message says why."""


@dataclass(frozen=True)
class Example:
    """A bundled overlay to start from (``search/examples/<name>.yaml``, TASKS 8.5)."""

    name: str
    title: str
    text: str


EXAMPLE_TITLES = {"healthdcat-ap": "HealthDCAT-AP"}


def examples() -> list[Example]:
    folder = resources.files("fdpindex.search").joinpath("examples")
    found: list[Example] = []
    for item in sorted(folder.iterdir(), key=lambda i: i.name):
        if item.name.endswith(".yaml"):
            name = item.name.removesuffix(".yaml")
            found.append(Example(name, EXAMPLE_TITLES.get(name, name), item.read_text("utf-8")))
    return found


def merge(base: Profile, overlay: dict[str, Any]) -> Profile:
    """The profile with ``overlay`` applied; raises :class:`ProfileError` when invalid."""
    unknown = sorted(set(overlay) - OVERLAY_KEYS)
    if unknown:
        raise ProfileError(f"unknown profile section(s): {', '.join(unknown)}")
    data = base.model_dump(mode="json")
    for key in ("prefixes", "types", "cards"):
        section = overlay.get(key)
        if section is not None:
            if not isinstance(section, dict):
                raise ProfileError(f"{key}: an object is expected")
            data[key] = {**data[key], **copy.deepcopy(section)}
    fields = overlay.get("fields")
    if fields is not None:
        if not isinstance(fields, dict):
            raise ProfileError("fields: an object is expected")
        for name, spec in fields.items():  # pyright: ignore[reportUnknownVariableType]
            if spec is None:
                data["fields"].pop(name, None)
            else:
                data["fields"][name] = copy.deepcopy(spec)
    normalise = overlay.get("normalise")
    if normalise is not None:
        if not isinstance(normalise, dict):
            raise ProfileError("normalise: an object is expected")
        for table, entries in normalise.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(entries, dict):
                raise ProfileError(f"normalise.{table}: an object is expected")
            data["normalise"][table] = {**data["normalise"].get(table, {}), **entries}
    for key in ("landing", "capture"):
        if overlay.get(key) is not None:
            data[key] = copy.deepcopy(overlay[key])
    try:
        return Profile.model_validate(data)
    except ValueError as err:
        raise ProfileError(str(err)) from err


class ProfileStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def overlay(self) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            row = await session.get(IndexSettingsRow, _ROW_ID)
        return dict(row.value) if row is not None else None

    async def effective(self) -> Profile:
        """The bundled profile with the stored overlay; the bundled one if the overlay no
        longer validates (a release removed something it relied on)."""
        overlay = await self.overlay()
        if not overlay:
            return default_profile()
        try:
            return merge(default_profile(), overlay)
        except ProfileError as err:
            log.warning("search_profile_overlay_invalid", error=str(err))
            return default_profile()

    async def save(self, overlay: dict[str, Any]) -> Profile:
        profile = merge(default_profile(), overlay)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is None:
                session.add(IndexSettingsRow(id=_ROW_ID, value=overlay))
            else:
                row.value = overlay
                row.updated_at = utcnow()
        return profile

    async def delete(self) -> Profile:
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is not None:
                await session.delete(row)
        return default_profile()
