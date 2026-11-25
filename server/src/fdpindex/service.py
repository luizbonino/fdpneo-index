"""The index: ping intake, metadata retrieval, harvest, entries.

Behaviour follows the reference's ``EventService`` and ``IndexEntryService``.
Deliberate differences, all small:

* A retrieval skipped by the retrieval rate limit does not move the entry's
  last retrieval time (the reference sets it anyway, which keeps an entry
  ``ACTIVE`` without anything having been fetched).
* HTTP error responses are classified: ``5xx`` makes the entry
  ``Unreachable``, other non-``2xx`` statuses ``Invalid`` (the reference
  tries to parse any response and so reports every error page as
  ``Invalid``).
* An accepted entry is harvested after a retrieval found it valid, not in
  parallel with the retrieval; a harvest that reaches no node at all keeps
  the previously harvested data instead of wiping it.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import structlog
from rdflib import Graph
from sqlalchemy import ColumnElement, and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from fdpindex import exchange, geo
from fdpindex.db import utcnow
from fdpindex.durations import format_duration
from fdpindex.errors import NotFound, PingDenied, RateLimited
from fdpindex.findings import Page, WalkSummary, analyse, page_key, read_page
from fdpindex.harvest import KnownNode, harvest
from fdpindex.models import (
    Concept,
    EntryPermit,
    EntryState,
    Event,
    EventType,
    HarvestedNode,
    HarvestWalk,
    IndexEntry,
    MetadataHealth,
)
from fdpindex.outbound import OutboundError
from fdpindex.rdf import ACCEPT, RdfParseError, extract_repository_metadata, parse_rdf
from fdpindex.urls import url_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.orm import InstrumentedAttribute

    from fdpindex.geo import GeoIp
    from fdpindex.jobs import JobRunner
    from fdpindex.outbound import OutboundClient, OutboundResponse
    from fdpindex.settings import IndexSettings, SettingsService
    from fdpindex.webhooks import WebhookService

log = structlog.get_logger(__name__)

# Failed nodes listed in a Harvest event (the count covers all of them).
MAX_REPORTED_FAILURES = 20
# Failed pages a walk keeps for the health report.
MAX_HEALTH_FAILURES = 1000
# Pages read per database round when a health report is computed.
HEALTH_BATCH = 2000

EVENTS_IN_DETAIL = 10

API_STATES = ("UNKNOWN", "ACTIVE", "INACTIVE", "UNREACHABLE", "INVALID")


def api_state(entry: IndexEntry, valid_threshold: datetime) -> str:
    """The reference's ``IndexEntryStateDTO``: ``Valid`` splits into ACTIVE / INACTIVE."""
    if entry.state is EntryState.VALID:
        retrieved = entry.last_retrieval_at
        return "ACTIVE" if retrieved is not None and retrieved > valid_threshold else "INACTIVE"
    return entry.state.value.upper()


@dataclass(frozen=True)
class HarvestChanges:
    """What a harvest changed in ``harvested_node``, by node IRI (search design §6.5)."""

    added: frozenset[str] = frozenset()
    changed: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()

    @property
    def empty(self) -> bool:
        return not (self.added or self.changed or self.removed)


@dataclass(frozen=True)
class IndexChange:
    """A notification for derived stores (the SPARQL store, the search projection).

    ``kind`` is ``harvested`` (an entry's nodes changed), ``removed`` (an entry's
    harvested data is gone: rejected or deleted) or ``entries`` (entry fields such
    as state or title changed). Listeners must be cheap: record and return.
    """

    kind: Literal["harvested", "removed", "entries"]
    entry_id: UUID | None = None
    changes: HarvestChanges | None = None


@dataclass
class Registration:
    """The outcome of :meth:`IndexService.register`."""

    entry: IndexEntry
    new_entry: bool
    retrieved: bool  # False: skipped by the retrieval rate limit; the state is the last one
    error: str | None


@dataclass
class PingOutcome:
    event_id: UUID
    entry_id: UUID
    new_entry: bool


@dataclass
class Retrieval:
    state: EntryState
    error: str | None
    metadata: dict[str, Any] | None
    exchange: dict[str, Any]
    not_modified: bool = False
    content_hash: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    # Who answered (TASKS 15.4): the host after redirects, and its address's country.
    served_from: str | None = None
    server_country: str | None = None


def _read_pages(batch: list[tuple[str, str]], root: str) -> list[Page]:
    return [read_page(iri, text, root=page_key(iri) == root) for iri, text in batch]


def _instant(moment: datetime | None) -> str | None:
    """As ``java.time.Instant.toString()``, for event payloads."""
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _harvest_due(entry: IndexEntry, now: datetime) -> bool:
    """The entry's own harvest interval has passed since its last harvest (TASKS 4.7)."""
    if not entry.harvest_interval_seconds:
        return False
    last = entry.last_harvest_at
    return last is None or now - last >= timedelta(seconds=entry.harvest_interval_seconds)


def last_contact(entry: IndexEntry) -> datetime:
    """The last successful retrieval, or the registration of an entry never retrieved."""
    return entry.last_success_at or entry.created_at


def next_check(now: datetime, interval: timedelta) -> datetime:
    """``now + interval``, spread by up to ±10% so checks do not bunch up."""
    jitter = interval.total_seconds() * random.uniform(-0.1, 0.1)  # noqa: S311 - not security
    return now + interval + timedelta(seconds=jitter)


@dataclass
class EntryPage:
    items: list[IndexEntry]
    total: int
    page: int
    size: int


# Sort properties the API accepts. The reference client sends createdAt / updatedAt,
# the reference API documents registrationTime / modificationTime: both work.
SORT_COLUMNS: dict[str, InstrumentedAttribute[Any]] = {
    "clientUrl": IndexEntry.client_url,
    "registrationTime": IndexEntry.created_at,
    "createdAt": IndexEntry.created_at,
    "modificationTime": IndexEntry.updated_at,
    "updatedAt": IndexEntry.updated_at,
    "lastRetrievalTime": IndexEntry.last_retrieval_at,
    "state": IndexEntry.state,
    "permit": IndexEntry.permit,
}


class IndexService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SettingsService,
        outbound: OutboundClient,
        jobs: JobRunner,
        webhooks: WebhookService,
        *,
        body_limit: int,
        harvest_max_nodes: int,
        harvest_max_bytes: int = 50 * 1024 * 1024,
        harvest_max_seconds: float = 900.0,
        harvest_node_timeout: float = 20.0,
        harvest_walk_max_nodes: int = 100_000,
        harvest_continue_seconds: float = 600.0,
        geoip: GeoIp | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._geoip = geoip
        self._settings = settings
        self._outbound = outbound
        self._jobs = jobs
        self._webhooks = webhooks
        self._body_limit = body_limit
        self._harvest_max_nodes = harvest_max_nodes
        self._harvest_max_bytes = harvest_max_bytes
        self._harvest_max_seconds = harvest_max_seconds
        self._harvest_node_timeout = harvest_node_timeout
        self._harvest_walk_max_nodes = harvest_walk_max_nodes
        self._harvest_continue_seconds = harvest_continue_seconds
        self._harvest_locks: dict[UUID, asyncio.Lock] = {}
        self._harvest_waiting: dict[UUID, set[UUID]] = {}
        self._health_running: set[UUID] = set()
        self._listeners: list[Callable[[IndexChange], None]] = []
        # Set when conformance runs (TASKS 11.4): a condition on IndexEntry that public
        # lists add, or None when the directory shows every FDP.
        self.directory_condition: Callable[[], Awaitable[ColumnElement[bool] | None]] | None = None

    def add_listener(self, listener: Callable[[IndexChange], None]) -> None:
        self._listeners.append(listener)

    def _notify(self, change: IndexChange) -> None:
        for listener in self._listeners:
            try:
                listener(change)
            except Exception:  # a derived store must never break the index
                log.exception("index_listener_failed", kind=change.kind)

    # --- ping intake --------------------------------------------------------

    async def accept_ping(
        self,
        client_url: str,
        *,
        remote_addr: str | None,
        method: str,
        path: str,
        headers: dict[str, list[str]],
        body: str | None,
    ) -> PingOutcome:
        settings = await self._settings.get()
        if settings.denies(client_url):
            log.info("ping_denied", client_url=client_url)
            raise PingDenied(f"Client URL is denied: {client_url}")
        since = utcnow() - settings.ping.rate_limit_duration
        async with self._session_factory() as session:
            previous = await session.scalar(
                select(func.count())
                .select_from(Event)
                .where(
                    Event.type == EventType.INCOMING_PING,
                    Event.remote_addr == remote_addr,
                    Event.created_at > since,
                )
            )
        hits = settings.ping.rate_limit_hits
        if (previous or 0) > hits:
            log.warning("ping_rate_limited", remote_addr=remote_addr)
            raise RateLimited(
                f"Rate limit reached for {remote_addr} (max. {hits} per "
                f"{format_duration(settings.ping.rate_limit_duration)}) - PING ignored"
            )
        ping_exchange = exchange.incoming(
            method=method,
            path=path,
            headers=headers,
            body=body,
            remote_addr=remote_addr,
            limit=self._body_limit,
        )
        for attempt in (1, 2):
            try:
                return await self._record_ping(client_url, remote_addr, ping_exchange, settings)
            except IntegrityError:
                # Two first pings for one URL at the same time: the loser updates instead.
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _record_ping(
        self,
        client_url: str,
        remote_addr: str | None,
        ping_exchange: dict[str, Any],
        settings: IndexSettings,
    ) -> PingOutcome:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            entry, new_entry = await self._store_entry(session, client_url, settings)
            ping_exchange["state"] = "Retrieved"
            ping_exchange["response"]["code"] = 201
            event = Event(
                uuid=uuid4(),
                type=EventType.INCOMING_PING,
                related_to=entry.uuid,
                payload={"incomingPing": {"exchange": ping_exchange, "newEntry": new_entry}},
                remote_addr=remote_addr,
                created_at=now,
                executed_at=now,
                finished_at=utcnow(),
            )
            session.add(event)
        log.info("ping_accepted", client_url=client_url, new_entry=new_entry)
        return PingOutcome(event_id=event.uuid, entry_id=entry.uuid, new_entry=new_entry)

    async def _store_entry(
        self, session: AsyncSession, client_url: str, settings: IndexSettings
    ) -> tuple[IndexEntry, bool]:
        now = utcnow()
        entry = await _known_entry(session, client_url)
        if entry is not None:
            entry.updated_at = now
            return entry, False
        entry = IndexEntry(
            uuid=uuid4(),
            client_url=client_url,
            state=EntryState.UNKNOWN,
            permit=EntryPermit.ACCEPTED if settings.auto_permit else EntryPermit.PENDING,
            created_at=now,
            updated_at=now,
            repository_metadata={},
        )
        session.add(entry)
        await session.flush()
        return entry, True

    def after_trigger(self, event_id: UUID) -> None:
        """What every ping and admin trigger sets off: webhooks, then retrievals."""
        self._webhooks.schedule_trigger(event_id)
        self._jobs.submit(f"retrievals:{event_id}", lambda: self.start_retrievals(event_id))

    # --- admin triggers -------------------------------------------------------

    async def admin_trigger(
        self, client_url: str | None, *, actor: str, remote_addr: str | None
    ) -> UUID:
        """Record an ``AdminTrigger``; with a URL, the entry is created when missing (as in the reference)."""
        settings = await self._settings.get()
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            entry_id: UUID | None = None
            if client_url is not None:
                entry, _ = await self._store_entry(session, client_url, settings)
                entry_id = entry.uuid
            event = Event(
                uuid=uuid4(),
                type=EventType.ADMIN_TRIGGER,
                related_to=entry_id,
                payload={
                    "adminTrigger": {
                        "remoteAddr": remote_addr,
                        "tokenName": actor,
                        "clientUrl": client_url,
                    }
                },
                remote_addr=remote_addr,
                created_at=now,
                executed_at=now,
                finished_at=now,
            )
            session.add(event)
        return event.uuid

    # --- metadata retrieval -----------------------------------------------------

    async def start_retrievals(self, trigger_id: UUID) -> None:
        """One ``MetadataRetrieval`` per entry the trigger concerns (all entries for trigger-all)."""
        async with self._session_factory() as session, session.begin():
            trigger = await session.get(Event, trigger_id)
            if trigger is None:
                return
            if trigger.related_to is not None:
                entry_ids = [trigger.related_to]
            elif trigger.type is EventType.ADMIN_TRIGGER:
                entry_ids = list((await session.execute(select(IndexEntry.uuid))).scalars())
            else:
                entry_ids = []
            retrieval_ids: list[UUID] = []
            for entry_id in entry_ids:
                retrieval = Event(
                    uuid=uuid4(),
                    type=EventType.METADATA_RETRIEVAL,
                    triggered_by=trigger.uuid,
                    related_to=entry_id,
                    payload={
                        "metadataRetrieval": {"error": None, "exchange": None, "metadata": None}
                    },
                    created_at=utcnow(),
                )
                session.add(retrieval)
                retrieval_ids.append(retrieval.uuid)
        for retrieval_id in retrieval_ids:
            self._jobs.submit(
                f"retrieval:{retrieval_id}", lambda rid=retrieval_id: self.run_retrieval(rid)
            )

    async def run_retrieval(self, retrieval_id: UUID) -> None:
        """Run a ``MetadataRetrieval`` (after a ping or trigger) or a ``HealthCheck`` (scheduled)."""
        settings = await self._settings.get()
        async with self._session_factory() as session:
            event = await session.get(Event, retrieval_id)
            if event is None or event.finished_at is not None:
                return
            entry = await session.get(IndexEntry, event.related_to) if event.related_to else None
            if entry is None:
                event.finished_at = utcnow()
                await session.commit()
                return
            client_url = entry.client_url
            last = entry.last_retrieval_at
            scheduled = event.type is EventType.HEALTH_CHECK
            # Conditional requests only make sense for a check of something already valid.
            validators = (
                (entry.http_etag, entry.http_last_modified)
                if scheduled and entry.state is EntryState.VALID
                else (None, None)
            )
        wait = settings.retrieval.rate_limit_wait
        if last is not None and utcnow() - last <= wait:
            log.info("retrieval_rate_limited", client_url=client_url)
            await self._finish_retrieval(
                retrieval_id, None, error="Rate limit reached (skipping)", settings=settings
            )
            return
        async with self._session_factory() as session, session.begin():
            event = await session.get(Event, retrieval_id)
            if event is not None:
                event.executed_at = utcnow()
        result = await self.retrieve(
            client_url, settings, etag=validators[0], last_modified=validators[1]
        )
        await self._finish_retrieval(retrieval_id, result, error=result.error, settings=settings)

    async def retrieve(
        self,
        client_url: str,
        settings: IndexSettings,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> Retrieval:
        headers = {"Accept": ACCEPT}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response: OutboundResponse | None = None
        error: OutboundError | None = None
        try:
            response = await self._outbound.request(
                "GET",
                client_url,
                timeout_seconds=settings.retrieval.timeout.total_seconds(),
                headers=headers,
            )
        except OutboundError as err:
            error = err
        recorded = exchange.outgoing(
            method="GET",
            url=client_url,
            request_body=None,
            response=response,
            error=error,
            limit=self._body_limit,
        )
        if response is None:
            assert error is not None
            return Retrieval(EntryState.UNREACHABLE, error.message, None, recorded)
        served_from = urlsplit(response.url).hostname
        server_country = self._geoip.country(response.address) if self._geoip else None
        if response.status == 304 and (etag or last_modified):
            return Retrieval(
                EntryState.VALID,
                None,
                None,
                recorded,
                not_modified=True,
                served_from=served_from,
                server_country=server_country,
            )
        if response.status >= 500:
            return Retrieval(EntryState.UNREACHABLE, f"HTTP {response.status}", None, recorded)
        if not 200 <= response.status < 300:
            return Retrieval(EntryState.INVALID, f"HTTP {response.status}", None, recorded)
        if response.truncated:
            return Retrieval(EntryState.INVALID, "Response too large", None, recorded)
        try:
            graph = await asyncio.to_thread(
                parse_rdf, response.body, response.content_type, client_url
            )
        except RdfParseError:
            return Retrieval(EntryState.INVALID, "Cannot parse metadata", None, recorded)
        repository = extract_repository_metadata(graph, client_url)
        if repository is None:
            return Retrieval(EntryState.INVALID, "Repository not found in metadata", None, recorded)
        metadata = {
            "metadataVersion": repository.metadata_version,
            "repositoryUri": repository.repository_uri,
            "metadata": repository.metadata,
        }
        return Retrieval(
            EntryState.VALID,
            None,
            metadata,
            recorded,
            content_hash=hashlib.sha256(response.body.encode("utf-8")).hexdigest(),
            etag=(response.headers.get("etag") or [None])[0],
            last_modified=(response.headers.get("last-modified") or [None])[0],
            served_from=served_from,
            server_country=server_country,
        )

    async def _finish_retrieval(
        self,
        retrieval_id: UUID,
        result: Retrieval | None,
        *,
        error: str | None,
        settings: IndexSettings,
    ) -> None:
        check = settings.health_check
        harvest_entry: UUID | None = None
        state_changed = False
        scheduled = False
        async with self._session_factory() as session, session.begin():
            event = await session.get(Event, retrieval_id)
            if event is None:
                return
            scheduled = event.type is EventType.HEALTH_CHECK
            entry = await session.get(IndexEntry, event.related_to) if event.related_to else None
            payload: dict[str, Any] = {"error": error, "exchange": None, "metadata": None}
            now = utcnow()
            if entry is not None:
                # A check skipped by the rate limit counts from the retrieval that
                # caused the skip, not from now.
                base = (
                    entry.last_retrieval_at if result is None and entry.last_retrieval_at else now
                )
                entry.next_check_at = next_check(base, check.interval)
            if result is not None and entry is not None:
                previous = entry.state
                payload["exchange"] = result.exchange
                payload["metadata"] = result.metadata
                changed = self._apply(
                    entry, result, scheduled=scheduled, threshold=check.failure_threshold
                )
                entry.last_retrieval_at = now
                state_changed = entry.state is not previous
                if (
                    entry.permit is EntryPermit.PENDING
                    and entry.state is EntryState.VALID
                    and settings.verified_permit
                ):
                    # Verified approval (TASKS 4.8): the FDP answered with valid metadata.
                    entry.permit = EntryPermit.ACCEPTED
                    payload["approved"] = "verified"
                    log.info("entry_approved_verified", client_url=entry.client_url)
                if scheduled:
                    payload["notModified"] = result.not_modified
                    payload["metadataChanged"] = changed
                    payload["stateChanged"] = state_changed
                    payload["consecutiveFailures"] = entry.consecutive_failures
                wants_harvest = (
                    not scheduled
                    or (changed and check.reharvest_on_change)
                    or _harvest_due(entry, now)
                )
                if (
                    entry.state is EntryState.VALID
                    and entry.permit is EntryPermit.ACCEPTED
                    and entry.harvest_enabled
                    and wants_harvest
                ):
                    harvest_entry = self._new_harvest(session, entry.uuid, event.uuid).uuid
            key = "healthCheck" if scheduled else "metadataRetrieval"
            event.payload = {key: payload}
            event.finished_at = now
        # Pings notify on every retrieval, as in the reference; scheduled checks only
        # when the state changed, so an outage does not send a message per check.
        if result is not None and (not scheduled or state_changed):
            self._webhooks.schedule_trigger(retrieval_id)
        if result is not None:
            self._notify(IndexChange("entries"))
        if harvest_entry is not None:
            self.schedule_harvest(harvest_entry)

    @staticmethod
    def _apply(entry: IndexEntry, result: Retrieval, *, scheduled: bool, threshold: int) -> bool:
        """Update ``entry`` from ``result``; return whether its root metadata changed."""
        if result.state is EntryState.UNREACHABLE:
            entry.consecutive_failures += 1
            # A ping-triggered failure counts at once (reference behaviour); a scheduled
            # check only after ``threshold`` failures in a row, to ride out short outages.
            if not scheduled or entry.consecutive_failures >= threshold:
                entry.state = EntryState.UNREACHABLE
            return False
        entry.consecutive_failures = 0
        entry.state = result.state
        if result.state is not EntryState.VALID:
            return False
        if result.served_from:
            entry.served_from = result.served_from
            entry.server_country = result.server_country
        entry.last_success_at = utcnow()
        entry.expired_at = None  # contact again: no longer expired (TASKS 4.6)
        if result.not_modified or result.metadata is None:
            return False
        entry.repository_uri = result.metadata["repositoryUri"]
        entry.repository_metadata = dict(result.metadata["metadata"])
        entry.metadata_version = int(result.metadata["metadataVersion"])
        entry.http_etag = result.etag
        entry.http_last_modified = result.last_modified
        changed = result.content_hash != entry.metadata_hash
        entry.metadata_hash = result.content_hash
        return changed

    # --- liveness checks (ADR-0004) -------------------------------------------------

    async def schedule_due_checks(self, *, batch: int = 100) -> int:
        """Start a ``HealthCheck`` for every entry whose next check is due; return how many."""
        settings = await self._settings.get()
        check = settings.health_check
        if not check.enabled or not check.permits:
            return 0
        now = utcnow()
        busy = (
            select(Event.uuid)
            .where(
                Event.related_to == IndexEntry.uuid,
                Event.finished_at.is_(None),
                Event.type.in_([EventType.HEALTH_CHECK, EventType.METADATA_RETRIEVAL]),
            )
            .exists()
        )
        started: list[UUID] = []
        async with self._session_factory() as session, session.begin():
            rows = await session.execute(
                select(IndexEntry)
                .where(
                    IndexEntry.permit.in_(check.permits),
                    or_(IndexEntry.next_check_at.is_(None), IndexEntry.next_check_at <= now),
                    ~busy,
                )
                .order_by(IndexEntry.next_check_at.asc().nulls_first())
                .limit(batch)
                # Several replicas may sweep at once: each claims different rows.
                .with_for_update(skip_locked=True)
            )
            for entry in rows.scalars():
                # Claim it now, so the next sweep does not pick it up while the check runs.
                entry.next_check_at = next_check(now, check.interval)
                event = Event(
                    uuid=uuid4(),
                    type=EventType.HEALTH_CHECK,
                    related_to=entry.uuid,
                    payload={"healthCheck": {}},
                    created_at=now,
                )
                session.add(event)
                started.append(event.uuid)
        for event_id in started:
            self._jobs.submit(f"health:{event_id}", lambda eid=event_id: self.run_retrieval(eid))
        if started:
            log.info("health_checks_started", count=len(started))
        return len(started)

    # --- expiry (TASKS 4.6) ----------------------------------------------------------

    async def mark_expired(self, *, batch: int = 100) -> int:
        """Mark accepted entries without contact for longer than the expiry setting.

        Each is marked once: an ``Expiry`` event, which the webhooks turn into
        ``EntryExpired``. A successful retrieval clears the mark.
        """
        settings = await self._settings.get()
        expiry = settings.expiry
        if not expiry.enabled:
            return 0
        now = utcnow()
        cutoff = now - expiry.after
        marked: list[UUID] = []
        async with self._session_factory() as session, session.begin():
            rows = await session.scalars(
                select(IndexEntry)
                .where(
                    IndexEntry.permit == EntryPermit.ACCEPTED,
                    IndexEntry.expired_at.is_(None),
                    func.coalesce(IndexEntry.last_success_at, IndexEntry.created_at) < cutoff,
                )
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
            for entry in rows:
                entry.expired_at = now
                event = Event(
                    uuid=uuid4(),
                    type=EventType.EXPIRY,
                    related_to=entry.uuid,
                    payload={
                        "expiry": {
                            "lastContact": last_contact(entry).isoformat(),
                            "after": format_duration(expiry.after),
                            "hidden": expiry.hide,
                        }
                    },
                    created_at=now,
                    executed_at=now,
                    finished_at=now,
                )
                session.add(event)
                marked.append(event.uuid)
        for event_id in marked:
            self._webhooks.schedule_trigger(event_id)
        if marked:
            log.info("entries_expired", count=len(marked))
            self._notify(IndexChange("entries"))
        return len(marked)

    async def hides_expired(self) -> bool:
        settings = await self._settings.get()
        return settings.expiry.enabled and settings.expiry.hide

    # --- per-FDP options (TASKS 4.7) ------------------------------------------------

    async def update_options(
        self,
        entry_id: UUID,
        *,
        harvest_enabled: bool | None = None,
        harvest_interval: timedelta | None = None,
        clear_interval: bool = False,
        country: str | None = None,
        clear_country: bool = False,
        budget: dict[str, Any] | None = None,
    ) -> IndexEntry:
        """Directory only (no harvest), the entry's own harvest interval, and its country.

        ``budget`` holds the keys given of ``run_pages``, ``walk_pages`` and
        ``origins`` (TASKS 15.7); ``None`` as a value returns to the default.
        """
        async with self._session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                raise NotFound("Index entry not found")
            stop_harvest = harvest_enabled is False and entry.harvest_enabled
            if harvest_enabled is not None:
                entry.harvest_enabled = harvest_enabled
            if clear_interval:
                entry.harvest_interval_seconds = None
            elif harvest_interval is not None:
                entry.harvest_interval_seconds = int(harvest_interval.total_seconds())
            if clear_country:
                entry.country = None
            elif country is not None:
                entry.country = country
            for key, value in (budget or {}).items():
                setattr(entry, f"harvest_{key}", value or None)
        if stop_harvest:
            # Directory only: what was harvested leaves search and SPARQL.
            await self.delete_harvested(entry_id)
        return await self.get_entry(entry_id)

    # --- location (TASKS 15.4) -------------------------------------------------------

    async def locate(self, entries: list[IndexEntry]) -> dict[UUID, tuple[str, str] | None]:
        """Each entry's country and the source that placed it (``fdpindex.geo``)."""
        publishers = {
            str(p) for e in entries if (p := (e.repository_metadata or {}).get("publisher"))
        }
        countries: dict[str, str] = {}
        if publishers:
            async with self._session_factory() as session:
                for start in range(0, len(publishers), 500):
                    chunk = sorted(publishers)[start : start + 500]
                    rows = await session.execute(
                        select(Concept.iri, Concept.country).where(
                            Concept.iri.in_(chunk), Concept.country.is_not(None)
                        )
                    )
                    countries.update({str(iri): str(code) for iri, code in rows.all()})
        return {
            e.uuid: geo.locate(
                admin=e.country,
                stated=geo.country_of((e.repository_metadata or {}).get("country")),
                publisher=countries.get(str((e.repository_metadata or {}).get("publisher"))),
                server=e.server_country,
                domain=geo.domain_country(e.served_from or urlsplit(e.client_url).hostname),
            )
            for e in entries
        }

    # --- registration (TASKS 4.9) ---------------------------------------------------

    async def register(
        self,
        client_url: str,
        *,
        remote_addr: str | None,
        actor: str | None = None,
        permit: EntryPermit | None = None,
        harvest_enabled: bool | None = None,
        harvest_interval: timedelta | None = None,
    ) -> Registration:
        """Register an FDP and retrieve its root metadata now; return the outcome.

        Visitors go through the ping intake (deny list, rate limit, approval
        policy); ``actor`` is an administrator, whose registration is an admin
        trigger. Either way the retrieval runs here rather than in the
        background, so the caller sees its result; harvests follow as usual.
        An administrator's ``permit`` and options apply before the retrieval, so
        a directory-only entry is never harvested.
        """
        if actor is None:
            outcome = await self.accept_ping(
                client_url,
                remote_addr=remote_addr,
                method="POST",
                path="/index/register",
                headers={},
                body=None,
            )
            trigger_id, entry_id, new_entry = outcome.event_id, outcome.entry_id, outcome.new_entry
        else:
            async with self._session_factory() as session:
                existing = await _known_entry(session, client_url)
            trigger_id = await self.admin_trigger(client_url, actor=actor, remote_addr=remote_addr)
            new_entry = existing is None
            entry_id = (await self.get_event(trigger_id)).related_to
            assert entry_id is not None
            async with self._session_factory() as session, session.begin():
                entry = await session.get(IndexEntry, entry_id)
                assert entry is not None
                if permit is not None:
                    entry.permit = permit
                if harvest_enabled is not None:
                    entry.harvest_enabled = harvest_enabled
                if harvest_interval is not None:
                    entry.harvest_interval_seconds = int(harvest_interval.total_seconds())
        self._webhooks.schedule_trigger(trigger_id)
        retrieval_id = uuid4()
        async with self._session_factory() as session, session.begin():
            session.add(
                Event(
                    uuid=retrieval_id,
                    type=EventType.METADATA_RETRIEVAL,
                    triggered_by=trigger_id,
                    related_to=entry_id,
                    payload={
                        "metadataRetrieval": {"error": None, "exchange": None, "metadata": None}
                    },
                    created_at=utcnow(),
                )
            )
        await self.run_retrieval(retrieval_id)
        retrieval = await self.get_event(retrieval_id)
        entry = await self.get_entry(entry_id)
        detail = (retrieval.payload or {}).get("metadataRetrieval") or {}
        return Registration(
            entry=entry,
            new_entry=new_entry,
            retrieved=retrieval.executed_at is not None,
            error=detail.get("error"),
        )

    # --- harvest -------------------------------------------------------------------

    def schedule_harvest(self, harvest_id: UUID) -> None:
        self._jobs.submit(f"harvest:{harvest_id}", lambda: self.run_harvest(harvest_id))

    async def harvest_entry(self, entry_id: UUID, *, triggered_by: UUID | None = None) -> UUID:
        """Record a ``Harvest`` event for ``entry_id`` and run it now; return the event."""
        async with self._session_factory() as session, session.begin():
            event = self._new_harvest(session, entry_id, triggered_by)
        await self.run_harvest(event.uuid)
        return event.uuid

    @staticmethod
    def _new_harvest(session: AsyncSession, entry_id: UUID, triggered_by: UUID | None) -> Event:
        event = Event(
            uuid=uuid4(),
            type=EventType.HARVEST,
            triggered_by=triggered_by,
            related_to=entry_id,
            payload={"harvest": {}},
            created_at=utcnow(),
        )
        session.add(event)
        return event

    async def run_harvest(self, harvest_id: UUID) -> None:
        """Run a recorded ``Harvest``; one harvest per entry at a time.

        A harvest asked for while another one of the same entry runs is done by
        the running one's next pass, and both events get that pass's result.
        """
        async with self._session_factory() as session:
            event = await session.get(Event, harvest_id)
            if event is None or event.finished_at is not None or event.related_to is None:
                return
            entry_id = event.related_to
        self._harvest_waiting.setdefault(entry_id, set()).add(harvest_id)
        lock = self._harvest_locks.setdefault(entry_id, asyncio.Lock())
        if lock.locked():
            return
        async with lock:
            while self._harvest_waiting.get(entry_id):
                await self._harvest_once(entry_id, self._harvest_waiting.pop(entry_id))
        self._harvest_locks.pop(entry_id, None)

    async def _harvest_once(self, entry_id: UUID, harvest_ids: set[UUID]) -> None:
        """One run of the entry's walk (TASKS 15.7): a new walk, or the next run of one.

        A run reads up to its budget and stores what it read. While pages are
        queued the walk continues ``harvest_continue_seconds`` later (or when
        the FDP's ``Retry-After`` says), through :meth:`schedule_due_walks`.
        Only the run that ends the walk removes nodes no longer listed, and only
        when the walk reached everything below the pages it read.
        """
        started = utcnow()
        async with self._session_factory() as session, session.begin():
            for event in await session.scalars(select(Event).where(Event.uuid.in_(harvest_ids))):
                event.executed_at = started
            entry = await session.get(IndexEntry, entry_id)
            accepted = entry is not None and entry.permit is EntryPermit.ACCEPTED
            client_url = entry.client_url if entry is not None else ""
            run_pages = (entry.harvest_run_pages if entry else None) or self._harvest_max_nodes
            walk_pages = (
                entry.harvest_walk_pages if entry else None
            ) or self._harvest_walk_max_nodes
            origins = list((entry.harvest_origins if entry else None) or [])
            walk = await session.get(HarvestWalk, entry_id)
            walk_started = walk.started_at if walk is not None else started
            queue = list(walk.queue) if walk is not None else None
            already = walk.pages if walk is not None else 0
            known: dict[str, KnownNode] = {}
            read_in_walk: set[str] = set()
            for node in await session.scalars(
                select(HarvestedNode).where(HarvestedNode.entry_uuid == entry_id)
            ):
                iri = str(node.node_iri)
                known[iri] = KnownNode(
                    ntriples=node.ntriples,
                    content_hash=node.content_hash,
                    etag=node.http_etag,
                    last_modified=node.http_last_modified,
                )
                if walk is not None and node.harvested_at >= walk_started:
                    read_in_walk.add(iri)
            seen = {*read_in_walk, *(walk.failed if walk is not None else {})}
        if not accepted:
            await self._finish_harvest(harvest_ids, {"outcome": "skipped"})
            return
        settings = await self._settings.get()
        result = await harvest(
            self._outbound,
            client_url,
            timeout_seconds=min(
                settings.retrieval.timeout.total_seconds(), self._harvest_node_timeout
            ),
            max_nodes=max(1, min(run_pages, walk_pages - already)),
            max_bytes=self._harvest_max_bytes,
            max_seconds=self._harvest_max_seconds,
            known=known,
            queue=queue,
            seen=seen,
            origins=origins,
        )
        now = utcnow()
        added: set[str] = set()
        changed: set[str] = set()
        removed: set[str] = set()
        stale = 0
        async with self._session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, entry_id)
            if entry is None or entry.permit is not EntryPermit.ACCEPTED:
                await session.execute(delete(HarvestWalk).where(HarvestWalk.entry_uuid == entry_id))
                await self._finish_harvest(harvest_ids, {"outcome": "skipped"}, session=session)
                return
            # Upsert by node IRI, so unchanged nodes keep their rows and derived
            # stores learn exactly what changed.
            existing = {
                node.node_iri: node
                for node in (
                    await session.scalars(
                        select(HarvestedNode).where(HarvestedNode.entry_uuid == entry_id)
                    )
                )
            }
            fresh = {node.node_iri: node for node in result.nodes}
            for iri, node in fresh.items():
                row = existing.get(iri)
                if row is None:
                    added.add(iri)
                    session.add(
                        HarvestedNode(
                            entry_uuid=entry_id,
                            node_iri=iri,
                            ntriples=node.ntriples,
                            content_hash=node.content_hash,
                            harvested_at=now,
                            http_etag=node.etag,
                            http_last_modified=node.last_modified,
                        )
                    )
                    continue
                row.harvested_at = now
                row.http_etag = node.etag
                row.http_last_modified = node.last_modified
                row.stale_since = None
                row.fetch_error = None
                if row.content_hash != node.content_hash:
                    changed.add(iri)
                    row.ntriples = node.ntriples
                    row.content_hash = node.content_hash
            read_in_walk |= set(fresh)
            # The FDP says these do not exist (404, 410): remove them whatever else happened.
            gone = result.gone & set(existing)
            if gone:
                removed |= gone
                await session.execute(
                    delete(HarvestedNode).where(
                        HarvestedNode.entry_uuid == entry_id, HarvestedNode.node_iri.in_(gone)
                    )
                )
            # A page that failed keeps its stored version, marked stale (TASKS 1.14).
            for iri, reason in result.failed.items():
                row = existing.get(iri)
                if row is not None:
                    row.stale_since = row.stale_since or now
                    row.fetch_error = reason[:512]
                    stale += 1

            walk = await session.get(HarvestWalk, entry_id)
            if walk is None:
                walk = HarvestWalk(
                    entry_uuid=entry_id,
                    started_at=walk_started,
                    runs=0,
                    pages=0,
                    queue=[],
                    failed={},
                    unreached=False,
                    skipped_off_origin=0,
                )
                session.add(walk)
            walk.runs += 1
            walk.pages += len(result.nodes) + len(result.failed)
            walk.failed = {**walk.failed, **{k: v[:200] for k, v in result.failed.items()}}
            walk.unreached = walk.unreached or bool(result.unreached_below)
            walk.skipped_off_origin += result.skipped_off_origin
            walk.queue = list(result.queue)
            at_ceiling = bool(result.queue) and walk.pages >= walk_pages
            finished = not result.queue or at_ceiling
            continues_at: datetime | None = None
            if not finished:
                wait = self._harvest_continue_seconds
                if result.retry_after:
                    wait = max(wait, result.retry_after)
                continues_at = now + timedelta(seconds=wait)
                walk.continue_at = continues_at
                outcome = "in progress"
            else:
                missing = set(existing) - read_in_walk - set(walk.failed) - removed
                if read_in_walk and not at_ceiling and not walk.unreached:
                    # The walk reached everything below the pages it read: what it
                    # did not find is no longer listed.
                    removed |= missing
                    if missing:
                        await session.execute(
                            delete(HarvestedNode).where(
                                HarvestedNode.entry_uuid == entry_id,
                                HarvestedNode.node_iri.in_(missing),
                            )
                        )
                else:
                    reason = (
                        "not reached: the walk stopped at its ceiling"
                        if at_ceiling
                        else "not reached: a page above it could not be fetched"
                    )
                    for iri in missing:
                        row = existing[iri]
                        row.stale_since = row.stale_since or now
                        row.fetch_error = reason
                        stale += 1
                complete = bool(read_in_walk) and not walk.failed and not at_ceiling
                outcome = "complete" if complete else "partial" if read_in_walk else "failed"
                # What the walk found, for the metadata health report (TASKS 15.8).
                summary = {
                    "failed": dict(list(walk.failed.items())[:MAX_HEALTH_FAILURES]),
                    "unread": len(result.queue),
                    "offSite": walk.skipped_off_origin,
                    "atCeiling": at_ceiling,
                    "complete": complete,
                }
                health = await session.get(MetadataHealth, entry_id)
                if health is None:
                    session.add(
                        MetadataHealth(entry_uuid=entry_id, walk=summary, walk_finished_at=now)
                    )
                else:
                    health.walk = summary
                    health.walk_finished_at = now
                if walk in session.new:  # a walk done in one run is never stored
                    session.expunge(walk)
                else:
                    await session.delete(walk)
            failed = dict(list(result.failed.items())[:MAX_REPORTED_FAILURES])
            await self._finish_harvest(
                harvest_ids,
                {
                    "outcome": outcome,
                    "nodes": len(fresh),
                    "notModified": result.not_modified,
                    "added": len(added),
                    "changed": len(changed),
                    "removed": len(removed),
                    "stale": stale,
                    "failedCount": len(result.failed),
                    "failed": failed,
                    "limitReached": result.limit_reached,
                    "skippedOffOrigin": result.skipped_off_origin,
                    "seconds": round((utcnow() - started).total_seconds(), 1),
                    "walk": {
                        "startedAt": _instant(walk_started),
                        "run": walk.runs,
                        "pages": walk.pages,
                        "queued": len(result.queue),
                        "continuesAt": _instant(continues_at),
                        "atCeiling": at_ceiling,
                        "retryAfter": result.retry_after,
                    },
                },
                session=session,
            )
        log.info(
            "harvested",
            client_url=client_url,
            outcome=outcome,
            nodes=len(fresh),
            not_modified=result.not_modified,
            added=len(added),
            changed=len(changed),
            removed=len(removed),
            stale=stale,
            queued=len(result.queue),
        )
        changes = HarvestChanges(frozenset(added), frozenset(changed), frozenset(removed))
        if fresh or removed:
            self._notify(IndexChange("harvested", entry_id, changes))

    async def schedule_due_walks(self, *, batch: int = 100) -> int:
        """Start the next run of every walk whose wait is over; return how many (TASKS 15.7).

        The claim pushes ``continue_at`` ahead by the longest a run may take, so
        a run that dies is retried, and replicas sweeping at once skip it.
        """
        now = utcnow()
        lease = timedelta(seconds=self._harvest_max_seconds * 2 + 60)
        started: list[UUID] = []
        async with self._session_factory() as session, session.begin():
            walks = await session.scalars(
                select(HarvestWalk)
                .where(HarvestWalk.continue_at.is_not(None), HarvestWalk.continue_at <= now)
                .order_by(HarvestWalk.continue_at)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
            for walk in walks:
                walk.continue_at = now + lease
                started.append(self._new_harvest(session, walk.entry_uuid, None).uuid)
        for event_id in started:
            self.schedule_harvest(event_id)
        if started:
            log.info("harvest_walks_continued", count=len(started))
        return len(started)

    async def _finish_harvest(
        self,
        harvest_ids: set[UUID],
        report: dict[str, Any],
        *,
        session: AsyncSession | None = None,
    ) -> None:
        async def write(active: AsyncSession) -> None:
            now = utcnow()
            for event in await active.scalars(select(Event).where(Event.uuid.in_(harvest_ids))):
                event.payload = {"harvest": report}
                event.executed_at = event.executed_at or now
                event.finished_at = now
                entry = await active.get(IndexEntry, event.related_to) if event.related_to else None
                if entry is not None:
                    entry.last_harvest_at = now

        if session is not None:
            await write(session)
            return
        async with self._session_factory() as active, active.begin():
            await write(active)

    async def delete_harvested(self, entry_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(delete(HarvestedNode).where(HarvestedNode.entry_uuid == entry_id))
            await session.execute(delete(HarvestWalk).where(HarvestWalk.entry_uuid == entry_id))
            await session.execute(
                delete(MetadataHealth).where(MetadataHealth.entry_uuid == entry_id)
            )
        self._notify(IndexChange("removed", entry_id))

    async def harvested_ntriples(self, entry_id: UUID) -> str:
        """The entry's harvested nodes as one N-Triples document, as stored."""
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                raise NotFound("Index entry not found")
            rows = await session.execute(
                select(HarvestedNode.ntriples)
                .where(HarvestedNode.entry_uuid == entry_id)
                .order_by(HarvestedNode.node_iri)
            )
            return "".join(str(ntriples) for (ntriples,) in rows)

    async def harvested_graph(self, entry_id: UUID) -> Graph:
        text = await self.harvested_ntriples(entry_id)

        def parse() -> Graph:
            graph = Graph()
            graph.parse(data=text, format="nt")
            return graph

        return await asyncio.to_thread(parse)

    # --- metadata health (TASKS 15.8) --------------------------------------------------

    async def metadata_health(self, entry_id: UUID) -> MetadataHealth | None:
        async with self._session_factory() as session:
            return await session.get(MetadataHealth, entry_id)

    async def health_reports(self, entry_ids: list[UUID]) -> dict[UUID, MetadataHealth]:
        """The computed reports of these entries."""
        if not entry_ids:
            return {}
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(MetadataHealth).where(
                    MetadataHealth.entry_uuid.in_(entry_ids), MetadataHealth.report.is_not(None)
                )
            )
            return {row.entry_uuid: row for row in rows}

    async def schedule_health(self, *, batch: int = 2) -> int:
        """Compute the health reports that are due; return how many were started.

        Due: a walk ended after the report was computed, or an accepted FDP has
        harvested pages and no report (harvested before TASKS 15.8). An FDP whose
        walk is in progress waits for it to end.
        """
        started: list[UUID] = []
        async with self._session_factory() as session, session.begin():
            has_pages = (
                select(HarvestedNode.id).where(HarvestedNode.entry_uuid == IndexEntry.uuid).exists()
            )
            has_health = (
                select(MetadataHealth.entry_uuid)
                .where(MetadataHealth.entry_uuid == IndexEntry.uuid)
                .exists()
            )
            for entry_id in await session.scalars(
                select(IndexEntry.uuid).where(
                    IndexEntry.permit == EntryPermit.ACCEPTED, has_pages, ~has_health
                )
            ):
                session.add(MetadataHealth(entry_uuid=entry_id, walk={}))
            walking = (
                select(HarvestWalk.entry_uuid)
                .where(HarvestWalk.entry_uuid == MetadataHealth.entry_uuid)
                .exists()
            )
            due = await session.scalars(
                select(MetadataHealth.entry_uuid)
                .where(
                    or_(
                        MetadataHealth.computed_at.is_(None),
                        MetadataHealth.computed_at < MetadataHealth.walk_finished_at,
                    ),
                    ~walking,
                )
                .order_by(MetadataHealth.walk_finished_at.desc().nulls_last())
                .limit(batch + len(self._health_running))
            )
            for entry_id in due:
                if entry_id not in self._health_running and len(started) < batch:
                    started.append(entry_id)
        for entry_id in started:
            self._health_running.add(entry_id)
            self._jobs.submit(
                f"metadata-health:{entry_id}", lambda eid=entry_id: self.compute_health(eid)
            )
        return len(started)

    async def compute_health(self, entry_id: UUID) -> None:
        """Compute an FDP's health report from its stored pages (``fdpindex.findings``)."""
        try:
            started = utcnow()
            async with self._session_factory() as session:
                entry = await session.get(IndexEntry, entry_id)
                health = await session.get(MetadataHealth, entry_id)
                if entry is None or health is None:
                    return
                client_url = entry.client_url
                origins = list(entry.harvest_origins or [])
                walk = dict(health.walk or {})
            root = page_key(client_url)
            pages: list[Page] = []
            last = 0
            while True:
                async with self._session_factory() as session:
                    rows = (
                        await session.execute(
                            select(HarvestedNode.id, HarvestedNode.node_iri, HarvestedNode.ntriples)
                            .where(HarvestedNode.entry_uuid == entry_id, HarvestedNode.id > last)
                            .order_by(HarvestedNode.id)
                            .limit(HEALTH_BATCH)
                        )
                    ).all()
                if not rows:
                    break
                last = rows[-1][0]
                batch = [(str(iri), str(text)) for _id, iri, text in rows]
                pages.extend(await asyncio.to_thread(_read_pages, batch, root))
            summary = WalkSummary(
                failed=walk.get("failed") or {},
                unread=int(walk.get("unread") or 0),
                off_site=int(walk.get("offSite") or 0),
                at_ceiling=bool(walk.get("atCeiling")),
                complete=walk.get("complete"),
            )
            report = await asyncio.to_thread(analyse, client_url, pages, summary, origins=origins)
            async with self._session_factory() as session, session.begin():
                health = await session.get(MetadataHealth, entry_id)
                if health is None:
                    return
                health.report = report
                health.reach = report["reach"]
                health.status = report["status"]
                health.computed_at = started
            log.info(
                "metadata_health_computed",
                entry=str(entry_id),
                pages=report["pages"],
                reach=report["reach"],
                status=report["status"],
            )
        finally:
            self._health_running.discard(entry_id)

    async def harvest_walk(self, entry_id: UUID) -> HarvestWalk | None:
        """The entry's walk in progress, if any (TASKS 15.7)."""
        async with self._session_factory() as session:
            return await session.get(HarvestWalk, entry_id)

    def harvest_budget(self, entry: IndexEntry) -> tuple[int, int]:
        """Pages per run and per walk for ``entry``: its own, or the deployment's."""
        return (
            entry.harvest_run_pages or self._harvest_max_nodes,
            entry.harvest_walk_pages or self._harvest_walk_max_nodes,
        )

    async def harvest_summary(self, entry_id: UUID) -> tuple[int, datetime | None]:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(func.count(), func.max(HarvestedNode.harvested_at)).where(
                        HarvestedNode.entry_uuid == entry_id
                    )
                )
            ).one()
        return int(row[0]), row[1]

    # --- start-up ------------------------------------------------------------------

    async def resume_unfinished(self) -> int:
        """Resume retrievals, harvests and webhook deliveries a stopped process left unfinished."""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(Event.uuid, Event.type)
                .where(Event.finished_at.is_(None))
                .order_by(Event.created_at)
            )
            unfinished = [(row[0], row[1]) for row in rows]
        for event_id, event_type in unfinished:
            if event_type in (EventType.METADATA_RETRIEVAL, EventType.HEALTH_CHECK):
                self._jobs.submit(
                    f"resume:{event_id}", lambda eid=event_id: self.run_retrieval(eid)
                )
            elif event_type is EventType.WEBHOOK_TRIGGER:
                self._jobs.submit(
                    f"resume:{event_id}", lambda eid=event_id: self._webhooks.resume(eid)
                )
            elif event_type is EventType.HARVEST:
                self._jobs.submit(f"resume:{event_id}", lambda eid=event_id: self.run_harvest(eid))
        if unfinished:
            log.info("resumed_unfinished_events", count=len(unfinished))
        return len(unfinished)

    # --- entries ----------------------------------------------------------------------

    def _state_filter(self, state: str, valid_threshold: datetime) -> ColumnElement[bool] | None:
        match state.upper():
            case "ACTIVE":
                return and_(
                    IndexEntry.state == EntryState.VALID,
                    IndexEntry.last_retrieval_at > valid_threshold,
                )
            case "INACTIVE":
                return and_(
                    IndexEntry.state == EntryState.VALID,
                    IndexEntry.last_retrieval_at <= valid_threshold,
                )
            case "UNREACHABLE":
                return IndexEntry.state == EntryState.UNREACHABLE
            case "INVALID":
                return IndexEntry.state == EntryState.INVALID
            case "UNKNOWN":
                return IndexEntry.state == EntryState.UNKNOWN
            case _:
                return None

    async def valid_threshold(self) -> datetime:
        settings = await self._settings.get()
        return utcnow() - settings.ping.valid_duration

    async def list_entries(
        self,
        *,
        permits: list[EntryPermit],
        state: str = "",
        query: str = "",
        sort: list[str] | None = None,
        page: int = 0,
        size: int = 20,
        public: bool = False,
    ) -> EntryPage:
        threshold = await self.valid_threshold()
        conditions: list[ColumnElement[bool]] = [
            IndexEntry.permit.in_(permits),
            *await self._public_conditions(public),
        ]
        state_condition = self._state_filter(state, threshold)
        if state_condition is not None:
            conditions.append(state_condition)
        if query.strip():
            # A small directory: a substring match on the URL and the title is enough.
            text = (
                query.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            needle = f"%{text}%"
            conditions.append(
                func.lower(IndexEntry.client_url).like(needle, escape="\\")
                | func.lower(IndexEntry.repository_metadata["title"].as_string()).like(
                    needle, escape="\\"
                )
            )
        order = _order_by(sort or [])
        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(IndexEntry).where(*conditions)
            )
            rows = await session.execute(
                select(IndexEntry)
                .where(*conditions)
                .order_by(*order)
                .offset(page * size)
                .limit(size)
            )
            items = list(rows.scalars().all())
        return EntryPage(items=items, total=int(total or 0), page=page, size=size)

    async def public_conditions(self) -> list[ColumnElement[bool]]:
        """The public FDP list's conditions on ``index_entry`` (the statistics use them)."""
        return await self._public_conditions(True)

    async def _public_conditions(self, public: bool) -> list[ColumnElement[bool]]:
        """For anonymous callers: expired entries leave the lists when the policy hides them,
        and FDPs below the directory's minimum conformance (TASKS 11.4) when one is set."""
        if not public:
            return []
        conditions: list[ColumnElement[bool]] = []
        if await self.hides_expired():
            conditions.append(IndexEntry.expired_at.is_(None))
        if self.directory_condition is not None:
            extra = await self.directory_condition()
            if extra is not None:
                conditions.append(extra)
        return conditions

    async def all_entries(
        self, permits: list[EntryPermit], *, public: bool = False
    ) -> list[IndexEntry]:
        hidden = await self._public_conditions(public)
        async with self._session_factory() as session:
            rows = await session.execute(
                select(IndexEntry)
                .where(IndexEntry.permit.in_(permits), *hidden)
                .order_by(IndexEntry.created_at.desc())
            )
            return list(rows.scalars().all())

    async def counts(self, permits: list[EntryPermit], *, public: bool = False) -> dict[str, int]:
        threshold = await self.valid_threshold()
        hidden = await self._public_conditions(public)
        result: dict[str, int] = {}
        async with self._session_factory() as session:
            base = (
                select(func.count())
                .select_from(IndexEntry)
                .where(IndexEntry.permit.in_(permits), *hidden)
            )
            result["ALL"] = int(await session.scalar(base) or 0)
            for state in API_STATES:
                condition = self._state_filter(state, threshold)
                assert condition is not None
                result[state] = int(await session.scalar(base.where(condition)) or 0)
        return result

    async def get_entry(self, entry_id: UUID) -> IndexEntry:
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
        if entry is None:
            raise NotFound("Index entry not found")
        return entry

    async def events(
        self, entry_id: UUID, *, limit: int = EVENTS_IN_DETAIL, offset: int = 0
    ) -> tuple[list[Event], int]:
        async with self._session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(Event).where(Event.related_to == entry_id)
            )
            rows = await session.execute(
                select(Event)
                .where(Event.related_to == entry_id)
                .order_by(Event.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(rows.scalars().unique().all()), int(total or 0)

    async def get_event(self, event_id: UUID) -> Event:
        async with self._session_factory() as session:
            event = await session.get(Event, event_id)
        if event is None:
            raise NotFound("Event not found")
        return event

    async def update_permit(
        self, entry_id: UUID, permit: EntryPermit, *, actor: str, remote_addr: str | None
    ) -> IndexEntry:
        async with self._session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                raise NotFound("Index entry not found")
            if entry.permit is not permit:
                entry.permit = permit
            client_url = entry.client_url
        if permit is EntryPermit.ACCEPTED:
            event_id = await self.admin_trigger(client_url, actor=actor, remote_addr=remote_addr)
            self.after_trigger(event_id)
        else:
            await self.delete_harvested(entry_id)
        return await self.get_entry(entry_id)

    async def delete_entry(self, entry_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                raise NotFound("Index entry not found")
            await session.execute(delete(HarvestedNode).where(HarvestedNode.entry_uuid == entry_id))
            await session.execute(delete(Event).where(Event.related_to == entry_id))
            await session.delete(entry)
        self._notify(IndexChange("removed", entry_id))

    # --- duplicates (fdpindex.urls) ---------------------------------------------------------

    async def duplicates(self) -> list[DuplicateGroup]:
        """Entries that are one FDP under variants of its URL, and which one to keep."""
        async with self._session_factory() as session:
            keys = (
                select(IndexEntry.url_key)
                .group_by(IndexEntry.url_key)
                .having(func.count() > 1)
                .scalar_subquery()
            )
            rows = (
                await session.scalars(
                    select(IndexEntry)
                    .where(IndexEntry.url_key.in_(keys))
                    .order_by(IndexEntry.url_key, IndexEntry.created_at)
                )
            ).all()
        groups: dict[str, list[IndexEntry]] = {}
        for row in rows:
            groups.setdefault(row.url_key, []).append(row)
        return [_duplicate_group(key, entries) for key, entries in groups.items()]

    async def merge_duplicates(self) -> list[DuplicateGroup]:
        """Keep one entry of each group and delete the others, unless the group needs a person."""
        groups = await self.duplicates()
        for group in groups:
            if group.conflicts:
                continue
            for entry in group.remove:
                await self.delete_entry(entry.uuid)
            group.merged = True
            log.info(
                "duplicates_merged",
                kept=group.keep.client_url,
                removed=[entry.client_url for entry in group.remove],
            )
        return groups


async def _known_entry(session: AsyncSession, client_url: str) -> IndexEntry | None:
    """The entry for this URL or a variant of it; the exact URL first, then the oldest."""
    candidates = (
        await session.scalars(
            select(IndexEntry)
            .where(IndexEntry.url_key == url_key(client_url))
            .order_by(IndexEntry.created_at)
        )
    ).all()
    exact = [entry for entry in candidates if entry.client_url == client_url]
    return (exact or list(candidates) or [None])[0]


@dataclass
class DuplicateGroup:
    key: str
    keep: IndexEntry
    remove: list[IndexEntry]
    # Why the group is left to an administrator; empty when it can be merged.
    conflicts: list[str]
    merged: bool = False


_STATE_RANK = {
    EntryState.VALID: 0,
    EntryState.UNKNOWN: 1,
    EntryState.INVALID: 2,
    EntryState.UNREACHABLE: 3,
}


def _duplicate_group(key: str, entries: list[IndexEntry]) -> DuplicateGroup:
    """Keep the entry that works, then the one the FDP names as itself, then https, then the oldest."""

    def rank(entry: IndexEntry) -> tuple[int, int, int, float, float]:
        declared = entry.repository_uri is not None and (
            entry.repository_uri.rstrip("/") == entry.client_url.strip().rstrip("/")
        )
        success = entry.last_success_at.timestamp() if entry.last_success_at else 0.0
        return (
            _STATE_RANK.get(entry.state, 9),
            0 if declared else 1,
            0 if entry.client_url.lower().startswith("https:") else 1,
            -success,
            entry.created_at.timestamp(),
        )

    keep, *remove = sorted(entries, key=rank)
    conflicts: list[str] = []
    if any(entry.permit != keep.permit for entry in remove):
        conflicts.append(
            "approval differs: " + ", ".join(sorted({e.permit.value for e in entries}))
        )
    for entry in remove:
        if entry.country and entry.country != keep.country:
            conflicts.append(f"{entry.client_url} has a country set by an administrator")
        if entry.harvest_enabled != keep.harvest_enabled:
            conflicts.append(f"{entry.client_url} differs in whether it is harvested")
        if (
            entry.harvest_interval_seconds is not None
            and entry.harvest_interval_seconds != keep.harvest_interval_seconds
        ):
            conflicts.append(f"{entry.client_url} has its own harvest interval")
    return DuplicateGroup(key=key, keep=keep, remove=remove, conflicts=conflicts)


def _order_by(sort: list[str]) -> list[Any]:
    order: list[Any] = []
    for item in sort:
        field, _, direction = item.partition(",")
        column = SORT_COLUMNS.get(field.strip())
        if column is None:
            continue
        descending = direction.strip().lower() == "desc"
        order.append(column.desc().nulls_last() if descending else column.asc().nulls_last())
    if not order:
        order.append(IndexEntry.created_at.desc())
    order.append(IndexEntry.uuid)  # stable pages when the sort key ties
    return order
