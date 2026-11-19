"""Saved and featured searches (TASKS 10.3).

Administrators keep searches under a title: the query string of the search
page (``q=registry&theme=…``), which is the whole search, since the page's
state lives in its URL. A featured search is listed on the search page for
everyone, before they type anything. Saved searches are shared by the
index's administrators; each records who saved it.

Stored as the fourth row of ``index_settings``; at most :data:`MAX_SAVED`.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fdpindex.db import utcnow
from fdpindex.errors import BadRequest, NotFound
from fdpindex.models import IndexSettingsRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_ROW_ID = 4
MAX_SAVED = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SavedSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    query: str = Field(
        max_length=2000,
        description="The search page's query string, such as q=registry&theme=…",
    )
    description: str | None = Field(default=None, max_length=500)
    featured: bool = False
    # The title and description in other languages (TASKS 13.4): language code → text.
    # ``title`` and ``description`` are the fallback shown when the reader's is missing.
    titles: dict[str, str] = Field(default_factory=dict, max_length=20)
    descriptions: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("titles", "descriptions")
    @classmethod
    def _languages(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for code, text in value.items():
            if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})?", code):
                raise ValueError(f"not a language code: {code}")
            cleaned = " ".join(text.split())
            if len(cleaned) > 500:
                raise ValueError(f"{code}: at most 500 characters")
            if cleaned:
                out[code] = cleaned
        return out

    @field_validator("title", "description")
    @classmethod
    def _plain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            return None
        return value

    @field_validator("query")
    @classmethod
    def _query(cls, value: str) -> str:
        value = value.strip().removeprefix("?")
        if _CONTROL.search(value) or "://" in value.split("=", 1)[0]:
            raise ValueError("a query string of the search page, such as q=registry")
        try:
            parse_qsl(value, strict_parsing=bool(value))
        except ValueError as err:
            raise ValueError("not a query string") from err
        return value


class SavedSearch(SavedSearchRequest):
    id: str
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SavedSearches:
    """The saved searches of the index; ``version`` changes with every write."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self.version = 0

    async def all(self) -> list[SavedSearch]:
        async with self._session_factory() as session:
            row = await session.get(IndexSettingsRow, _ROW_ID)
        items: list[Any] = (row.value or {}).get("searches", []) if row is not None else []
        return [SavedSearch.model_validate(item) for item in items]

    async def featured(self) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "title": s.title,
                "description": s.description,
                "titles": s.titles,
                "descriptions": s.descriptions,
                "query": s.query,
            }
            for s in await self.all()
            if s.featured
        ]

    async def add(self, request: SavedSearchRequest, by: str) -> SavedSearch:
        now = utcnow()
        saved = SavedSearch(
            **request.model_dump(),
            id=secrets.token_hex(6),
            createdBy=by,
            createdAt=now,
            updatedAt=now,
        )
        searches = await self.all()
        if len(searches) >= MAX_SAVED:
            raise BadRequest(f"At most {MAX_SAVED} saved searches; delete some first")
        await self._store([*searches, saved])
        return saved

    async def update(self, search_id: str, request: SavedSearchRequest) -> SavedSearch:
        searches = await self.all()
        for index, item in enumerate(searches):
            if item.id == search_id:
                changed = item.model_copy(update={**request.model_dump(), "updated_at": utcnow()})
                searches[index] = changed
                await self._store(searches)
                return changed
        raise NotFound(f"No saved search {search_id}")

    async def delete(self, search_id: str) -> None:
        searches = await self.all()
        kept = [s for s in searches if s.id != search_id]
        if len(kept) == len(searches):
            raise NotFound(f"No saved search {search_id}")
        await self._store(kept)

    async def _store(self, searches: list[SavedSearch]) -> None:
        payload = {"searches": [s.model_dump(mode="json", by_alias=True) for s in searches]}
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is None:
                session.add(IndexSettingsRow(id=_ROW_ID, value=payload))
            else:
                row.value = payload
                row.updated_at = utcnow()
        self.version += 1
