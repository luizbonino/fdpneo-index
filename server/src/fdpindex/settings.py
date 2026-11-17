"""Index settings: rate limits, durations, deny list, auto-permit.

One row, created on the first write; until then the defaults apply. The API
shape is the reference's ``IndexSettingsDTO``: ``retrieval``
(``rateLimitWait``, ``timeout``), ``ping`` (``validDuration``,
``rateLimitDuration``, ``rateLimitHits``, ``denyList``), ``autoPermit`` and
``isDefault``. As in the reference, ``isDefault`` compares retrieval and
ping settings only, not ``autoPermit``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
)

from fdpindex.db import utcnow
from fdpindex.durations import format_duration, parse_duration
from fdpindex.models import IndexSettingsRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_ROW_ID = 1


def _to_timedelta(value: object) -> object:
    if isinstance(value, str):
        return parse_duration(value)
    return value


Duration = Annotated[
    timedelta,
    BeforeValidator(_to_timedelta),
    PlainSerializer(format_duration, return_type=str),
]


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RetrievalSettings(_Camel):
    rate_limit_wait: Duration = Field(alias="rateLimitWait")
    timeout: Duration

    @field_validator("rate_limit_wait")
    @classmethod
    def _not_negative(cls, value: timedelta) -> timedelta:
        if value < timedelta(0):
            raise ValueError("must not be negative")
        return value

    @field_validator("timeout")
    @classmethod
    def _positive(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("must be positive")
        return value


class PingSettings(_Camel):
    valid_duration: Duration = Field(alias="validDuration")
    rate_limit_duration: Duration = Field(alias="rateLimitDuration")
    rate_limit_hits: int = Field(alias="rateLimitHits", ge=0)
    deny_list: list[str] = Field(alias="denyList")

    @field_validator("valid_duration", "rate_limit_duration")
    @classmethod
    def _not_negative(cls, value: timedelta) -> timedelta:
        if value < timedelta(0):
            raise ValueError("must not be negative")
        return value

    @field_validator("deny_list")
    @classmethod
    def _patterns(cls, value: list[str]) -> list[str]:
        # A set in the reference (sorted, duplicates collapsed); kept sorted here too.
        patterns = sorted({pattern for pattern in value if pattern.strip()})
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as err:
                raise ValueError(f"invalid regular expression {pattern!r}: {err}") from err
        return patterns


class IndexSettings(_Camel):
    retrieval: RetrievalSettings
    ping: PingSettings
    auto_permit: bool = Field(alias="autoPermit")

    def matches_defaults(self, defaults: IndexSettings) -> bool:
        return self.retrieval == defaults.retrieval and self.ping == defaults.ping

    def denies(self, client_url: str) -> bool:
        """Whether ``client_url`` matches a deny-list pattern (whole-string match, as Java's)."""
        return any(re.fullmatch(pattern, client_url) for pattern in self.ping.deny_list)


class IndexSettingsView(IndexSettings):
    is_default: bool = Field(alias="isDefault")


def default_settings(*, auto_permit: bool) -> IndexSettings:
    return IndexSettings(
        retrieval=RetrievalSettings(
            rateLimitWait=timedelta(minutes=10), timeout=timedelta(minutes=1)
        ),
        ping=PingSettings(
            validDuration=timedelta(days=7),
            rateLimitDuration=timedelta(hours=6),
            rateLimitHits=10,
            denyList=[r"^(http|https)://localhost(:[0-9]+){0,1}.*$"],
        ),
        autoPermit=auto_permit,
    )


class SettingsService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, auto_permit_default: bool
    ) -> None:
        self._session_factory = session_factory
        self._auto_permit_default = auto_permit_default

    def defaults(self) -> IndexSettings:
        return default_settings(auto_permit=self._auto_permit_default)

    async def get(self) -> IndexSettings:
        async with self._session_factory() as session:
            row = await session.get(IndexSettingsRow, _ROW_ID)
        if row is None:
            return self.defaults()
        return IndexSettings.model_validate(row.value)

    async def view(self) -> IndexSettingsView:
        return self._view(await self.get())

    async def update(self, settings: IndexSettings) -> IndexSettingsView:
        payload = settings.model_dump(mode="json", by_alias=True)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IndexSettingsRow, _ROW_ID)
            if row is None:
                session.add(IndexSettingsRow(id=_ROW_ID, value=payload))
            else:
                row.value = payload
                row.updated_at = utcnow()
        return self._view(settings)

    async def reset(self) -> IndexSettingsView:
        # As in the reference: store the defaults rather than delete the row.
        return await self.update(self.defaults())

    def _view(self, settings: IndexSettings) -> IndexSettingsView:
        return IndexSettingsView(
            retrieval=settings.retrieval,
            ping=settings.ping,
            autoPermit=settings.auto_permit,
            isDefault=settings.matches_defaults(self.defaults()),
        )
