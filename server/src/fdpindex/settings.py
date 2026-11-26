"""Index settings: rate limits, durations, deny list, auto-permit.

One row, created on the first write; until then the defaults apply. The API
shape is the reference's ``IndexSettingsDTO``: ``retrieval``
(``rateLimitWait``, ``timeout``), ``ping`` (``validDuration``,
``rateLimitDuration``, ``rateLimitHits``, ``denyList``), ``autoPermit`` and
``isDefault``. As in the reference, ``isDefault`` compares retrieval and
ping settings only, not ``autoPermit``.

Addition: ``healthCheck`` configures the scheduled liveness checks of
ADR-0004 (``enabled``, ``interval``, ``failureThreshold``, ``permits``,
``reharvestOnChange``). Stored settings without it take its defaults, and it
counts towards ``isDefault``.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Literal

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
from fdpindex.models import EntryPermit, IndexSettingsRow

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
    # Responses always carry every field, so the output schema lists them as required.
    model_config = ConfigDict(
        populate_by_name=True, extra="forbid", json_schema_serialization_defaults_required=True
    )


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


class HealthCheckSettings(_Camel):
    enabled: bool = True
    interval: Duration = timedelta(hours=24)
    failure_threshold: int = Field(default=3, alias="failureThreshold", ge=1, le=100)
    permits: list[EntryPermit] = Field(default_factory=lambda: [EntryPermit.ACCEPTED])
    reharvest_on_change: bool = Field(default=True, alias="reharvestOnChange")

    @field_validator("interval")
    @classmethod
    def _at_least_a_minute(cls, value: timedelta) -> timedelta:
        if value < timedelta(minutes=1):
            raise ValueError("must be at least one minute")
        return value

    @field_validator("permits")
    @classmethod
    def _unique(cls, value: list[EntryPermit]) -> list[EntryPermit]:
        return [permit for permit in EntryPermit if permit in value]


class ExpirySettings(_Camel):
    """Entries without contact for a long time (TASKS 4.6).

    An accepted entry whose last successful retrieval (or, never retrieved, its
    registration) is older than ``after`` is marked expired once: an ``Expiry``
    event and an ``EntryExpired`` webhook. With ``hide``, expired entries leave
    the public directory and the search. A successful retrieval revives it.
    """

    enabled: bool = False
    after: Duration = timedelta(days=90)
    hide: bool = False

    @field_validator("after")
    @classmethod
    def _at_least_a_day(cls, value: timedelta) -> timedelta:
        if value < timedelta(days=1):
            raise ValueError("must be at least one day")
        return value


class IndexSettings(_Camel):
    retrieval: RetrievalSettings
    ping: PingSettings
    auto_permit: bool = Field(alias="autoPermit")
    health_check: HealthCheckSettings = Field(
        default_factory=HealthCheckSettings, alias="healthCheck"
    )
    # Without auto-permit: accept a new FDP once its root metadata is retrieved and
    # valid, instead of waiting for an administrator (TASKS 4.8).
    verified_permit: bool = Field(default=False, alias="verifiedPermit")
    expiry: ExpirySettings = Field(default_factory=ExpirySettings)
    # Who may check an FDP before registering it (TASKS 15.11): "policy" follows the
    # approval policy (anyone when FDPs are accepted without an administrator).
    check_before_registering: Literal["policy", "anyone", "admins"] = Field(
        default="policy", alias="checkBeforeRegistering"
    )

    def check_policy(self) -> Literal["anyone", "admins"]:
        """Who may check an FDP before registering it."""
        if self.check_before_registering != "policy":
            return self.check_before_registering
        return "anyone" if self.auto_permit or self.verified_permit else "admins"

    def matches_defaults(self, defaults: IndexSettings) -> bool:
        return (
            self.retrieval == defaults.retrieval
            and self.ping == defaults.ping
            and self.health_check == defaults.health_check
            and self.verified_permit == defaults.verified_permit
            and self.expiry == defaults.expiry
            and self.check_before_registering == defaults.check_before_registering
        )

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
            healthCheck=settings.health_check,
            verifiedPermit=settings.verified_permit,
            expiry=settings.expiry,
            checkBeforeRegistering=settings.check_before_registering,
            isDefault=settings.matches_defaults(self.defaults()),
        )
