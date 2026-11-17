"""Import the index of a Java FAIR Data Point (TASKS 3.4).

Reads the reference implementation's PostgreSQL tables (``index_entry``,
``index_event``, ``index_settings`` + ``index_settings_ping_deny``,
``index_webhook`` + ``index_webhook_event`` + ``index_webhook_entry``) and
writes them into this index, keeping identifiers, so links to entries keep
working. Event payloads already use the same JSON shape.

* Entries whose client URL is already here are skipped, and so are their events.
* Settings are imported only while this index still runs on its defaults.
* Events the Java index left unfinished are closed, so starting this index
  does not replay old webhook deliveries.
* Harvested metadata lives in the Java FDP's triple store and is not copied:
  the liveness checks (ADR-0004) see every imported entry as due and harvest
  accepted ones again.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from fdpindex.durations import format_duration, parse_duration
from fdpindex.models import (
    EntryPermit,
    EntryState,
    Event,
    EventType,
    IndexEntry,
    IndexSettingsRow,
    Webhook,
)
from fdpindex.settings import IndexSettings, default_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

BATCH = 1000


class _DryRun(Exception):  # noqa: N818 - control flow, not an error
    """Raised at the end of a dry run to roll the whole import back."""


@dataclass
class ImportReport:
    entries: int = 0
    entries_skipped: int = 0
    events: int = 0
    events_closed: int = 0
    webhooks: int = 0
    settings: bool = False
    warnings: list[str] = field(default_factory=lambda: [])


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if value is not None else {}


def _duration(value: str | None, fallback: str) -> str:
    """Java stored ``Duration.toString()``; normalise, and fall back on anything odd."""
    try:
        return format_duration(parse_duration(value)) if value else fallback
    except ValueError:
        return fallback


async def _rows(source: AsyncConnection, sql: str) -> list[dict[str, Any]]:
    result = await source.execute(text(sql))
    return [dict(row._mapping) for row in result]


async def import_java(
    source_url: str,
    target: async_sessionmaker[AsyncSession],
    *,
    events: bool = True,
    dry_run: bool = False,
    auto_permit_default: bool = True,
) -> ImportReport:
    report = ImportReport()
    engine = create_async_engine(source_url)
    try:
        async with engine.connect() as source:
            entries = await _rows(source, "SELECT * FROM index_entry ORDER BY created_at")
            settings_rows = await _rows(
                source, "SELECT * FROM index_settings ORDER BY created_at LIMIT 1"
            )
            deny = await _rows(source, "SELECT * FROM index_settings_ping_deny")
            webhooks = await _rows(source, "SELECT * FROM index_webhook")
            hook_events = await _rows(source, "SELECT * FROM index_webhook_event")
            hook_entries = await _rows(source, "SELECT * FROM index_webhook_entry")
            event_rows = (
                await _rows(source, "SELECT * FROM index_event ORDER BY created_at")
                if events
                else []
            )
    finally:
        await engine.dispose()

    with contextlib.suppress(_DryRun):
        await _write(
            target,
            report,
            entries,
            settings_rows,
            deny,
            webhooks,
            hook_events,
            hook_entries,
            event_rows,
            dry_run=dry_run,
            auto_permit_default=auto_permit_default,
        )
    return report


async def _write(
    target: async_sessionmaker[AsyncSession],
    report: ImportReport,
    entries: list[dict[str, Any]],
    settings_rows: list[dict[str, Any]],
    deny: list[dict[str, Any]],
    webhooks: list[dict[str, Any]],
    hook_events: list[dict[str, Any]],
    hook_entries: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    *,
    dry_run: bool,
    auto_permit_default: bool,
) -> None:
    async with target() as session, session.begin():
        known_urls = set((await session.execute(select(IndexEntry.client_url))).scalars())
        known_hooks = set((await session.execute(select(Webhook.uuid))).scalars())
        imported: set[UUID] = set()
        for row in entries:
            if row["client_url"] in known_urls:
                report.entries_skipped += 1
                continue
            metadata = _json(row["metadata"])
            session.add(
                IndexEntry(
                    uuid=UUID(str(row["uuid"])),
                    client_url=row["client_url"],
                    state=EntryState(row["state"]),
                    permit=EntryPermit(row["permit"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    last_retrieval_at=row["last_retrieval_at"],
                    metadata_version=row["metadata_version"],
                    repository_uri=row["repository_uri"],
                    repository_metadata={str(k): str(v) for k, v in dict(metadata).items()},
                )
            )
            imported.add(UUID(str(row["uuid"])))
            report.entries += 1
        await session.flush()

        if settings_rows and await session.get(IndexSettingsRow, 1) is None:
            report.settings = True
            session.add(
                IndexSettingsRow(id=1, value=_settings(settings_rows[0], deny, auto_permit_default))
            )

        for row in webhooks:
            uuid = UUID(str(row["uuid"]))
            if uuid in known_hooks:
                continue
            session.add(
                Webhook(
                    uuid=uuid,
                    payload_url=row["payload_url"],
                    secret=row["secret"],
                    all_events=bool(row["all_events"]),
                    events=sorted(
                        str(e["event"]) for e in hook_events if str(e["webhook_id"]) == str(uuid)
                    ),
                    all_entries=bool(row["all_entries"]),
                    entries=sorted(
                        str(e["entry"]) for e in hook_entries if str(e["webhook_id"]) == str(uuid)
                    ),
                    enabled=bool(row["enabled"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            )
            report.webhooks += 1

        seen: set[UUID] = set()
        pending: list[Event] = []
        for row in event_rows:
            related = UUID(str(row["related_to"])) if row["related_to"] else None
            if related is not None and related not in imported:
                continue
            uuid = UUID(str(row["uuid"]))
            trigger = UUID(str(row["triggered_by"])) if row["triggered_by"] else None
            finished: datetime | None = row["finished_at"]
            if finished is None:
                finished = row["executed_at"] or row["created_at"]
                report.events_closed += 1
            pending.append(
                Event(
                    uuid=uuid,
                    type=EventType(row["type"]),
                    version=row["version"],
                    triggered_by=trigger if trigger in seen else None,
                    related_to=related,
                    payload=_json(row["payload"]),
                    remote_addr=row["remote_addr"],
                    created_at=row["created_at"],
                    executed_at=row["executed_at"],
                    finished_at=finished,
                )
            )
            seen.add(uuid)
            if len(pending) >= BATCH:
                session.add_all(pending)
                await session.flush()
                report.events += len(pending)
                pending = []
        session.add_all(pending)
        report.events += len(pending)
        await session.flush()

        if dry_run:
            raise _DryRun


def _settings(
    row: dict[str, Any], deny: list[dict[str, Any]], auto_permit_default: bool
) -> dict[str, Any]:
    defaults = default_settings(auto_permit=auto_permit_default).model_dump(
        mode="json", by_alias=True
    )
    patterns = sorted(
        str(d["pattern"]) for d in deny if str(d["index_settings_id"]) == str(row["uuid"])
    )
    value = {
        "retrieval": {
            "rateLimitWait": _duration(
                row["retrieval_rate_limit_wait"], defaults["retrieval"]["rateLimitWait"]
            ),
            "timeout": _duration(row["retrieval_timeout"], defaults["retrieval"]["timeout"]),
        },
        "ping": {
            "validDuration": _duration(
                row["ping_valid_duration"], defaults["ping"]["validDuration"]
            ),
            "rateLimitDuration": _duration(
                row["ping_rate_limit_duration"], defaults["ping"]["rateLimitDuration"]
            ),
            "rateLimitHits": row["ping_rate_limit_hits"]
            if row["ping_rate_limit_hits"] is not None
            else defaults["ping"]["rateLimitHits"],
            "denyList": patterns,
        },
        "autoPermit": row["auto_permit"]
        if row["auto_permit"] is not None
        else defaults["autoPermit"],
        "healthCheck": defaults["healthCheck"],
    }
    # Validate, so an unusable value fails the import instead of the running index.
    return IndexSettings.model_validate(value).model_dump(mode="json", by_alias=True)


def async_url(url: str) -> str:
    """Accept a plain ``postgresql://`` URL and use the asyncpg driver for it."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url
