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
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from rdflib import Graph
from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.exc import IntegrityError

from fdpindex import exchange
from fdpindex.db import utcnow
from fdpindex.durations import format_duration
from fdpindex.errors import NotFound, PingDenied, RateLimited
from fdpindex.harvest import harvest
from fdpindex.models import EntryPermit, EntryState, Event, EventType, HarvestedNode, IndexEntry
from fdpindex.outbound import OutboundError
from fdpindex.rdf import ACCEPT, RdfParseError, extract_repository_metadata, parse_rdf

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.orm import InstrumentedAttribute

    from fdpindex.jobs import JobRunner
    from fdpindex.outbound import OutboundClient, OutboundResponse
    from fdpindex.settings import IndexSettings, SettingsService
    from fdpindex.webhooks import WebhookService

log = structlog.get_logger(__name__)

EVENTS_IN_DETAIL = 10

API_STATES = ("UNKNOWN", "ACTIVE", "INACTIVE", "UNREACHABLE", "INVALID")


def api_state(entry: IndexEntry, valid_threshold: datetime) -> str:
    """The reference's ``IndexEntryStateDTO``: ``Valid`` splits into ACTIVE / INACTIVE."""
    if entry.state is EntryState.VALID:
        retrieved = entry.last_retrieval_at
        return "ACTIVE" if retrieved is not None and retrieved > valid_threshold else "INACTIVE"
    return entry.state.value.upper()


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
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._outbound = outbound
        self._jobs = jobs
        self._webhooks = webhooks
        self._body_limit = body_limit
        self._harvest_max_nodes = harvest_max_nodes
        self._harvest_locks: dict[UUID, asyncio.Lock] = {}
        self._harvest_again: set[UUID] = set()

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
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == client_url))
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
        wait = settings.retrieval.rate_limit_wait
        if last is not None and utcnow() - last <= wait:
            log.info("retrieval_rate_limited", client_url=client_url)
            await self._finish_retrieval(retrieval_id, None, error="Rate limit reached (skipping)")
            return
        async with self._session_factory() as session, session.begin():
            event = await session.get(Event, retrieval_id)
            if event is not None:
                event.executed_at = utcnow()
        result = await self.retrieve(client_url, settings)
        await self._finish_retrieval(retrieval_id, result, error=result.error)

    async def retrieve(self, client_url: str, settings: IndexSettings) -> Retrieval:
        response: OutboundResponse | None = None
        error: OutboundError | None = None
        try:
            response = await self._outbound.request(
                "GET",
                client_url,
                timeout_seconds=settings.retrieval.timeout.total_seconds(),
                headers={"Accept": ACCEPT},
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
        if response.status >= 500:
            return Retrieval(EntryState.UNREACHABLE, f"HTTP {response.status}", None, recorded)
        if not 200 <= response.status < 300:
            return Retrieval(EntryState.INVALID, f"HTTP {response.status}", None, recorded)
        if response.truncated:
            return Retrieval(EntryState.INVALID, "Response too large", None, recorded)
        try:
            graph = parse_rdf(response.body, response.content_type, client_url)
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
        return Retrieval(EntryState.VALID, None, metadata, recorded)

    async def _finish_retrieval(
        self, retrieval_id: UUID, result: Retrieval | None, *, error: str | None
    ) -> None:
        harvest_entry: UUID | None = None
        async with self._session_factory() as session, session.begin():
            event = await session.get(Event, retrieval_id)
            if event is None:
                return
            entry = await session.get(IndexEntry, event.related_to) if event.related_to else None
            payload: dict[str, Any] = {"error": error, "exchange": None, "metadata": None}
            if result is not None and entry is not None:
                payload["exchange"] = result.exchange
                payload["metadata"] = result.metadata
                entry.state = result.state
                entry.last_retrieval_at = utcnow()
                if result.metadata is not None:
                    entry.repository_uri = result.metadata["repositoryUri"]
                    entry.repository_metadata = dict(result.metadata["metadata"])
                    entry.metadata_version = int(result.metadata["metadataVersion"])
                if result.state is EntryState.VALID and entry.permit is EntryPermit.ACCEPTED:
                    harvest_entry = entry.uuid
            event.payload = {"metadataRetrieval": payload}
            event.finished_at = utcnow()
        if result is not None:
            self._webhooks.schedule_trigger(retrieval_id)
        if harvest_entry is not None:
            self.schedule_harvest(harvest_entry)

    # --- harvest -------------------------------------------------------------------

    def schedule_harvest(self, entry_id: UUID) -> None:
        self._jobs.submit(f"harvest:{entry_id}", lambda: self.harvest_entry(entry_id))

    async def harvest_entry(self, entry_id: UUID) -> None:
        lock = self._harvest_locks.setdefault(entry_id, asyncio.Lock())
        if lock.locked():
            # One harvest per entry at a time; run once more when the current one ends.
            self._harvest_again.add(entry_id)
            return
        async with lock:
            while True:
                self._harvest_again.discard(entry_id)
                await self._harvest_once(entry_id)
                if entry_id not in self._harvest_again:
                    break
        self._harvest_locks.pop(entry_id, None)

    async def _harvest_once(self, entry_id: UUID) -> None:
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            if entry is None or entry.permit is not EntryPermit.ACCEPTED:
                return
            client_url = entry.client_url
        settings = await self._settings.get()
        result = await harvest(
            self._outbound,
            client_url,
            timeout_seconds=settings.retrieval.timeout.total_seconds(),
            max_nodes=self._harvest_max_nodes,
        )
        if not result.nodes:
            log.warning("harvest_found_nothing", client_url=client_url, failed=result.failed)
            return
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, entry_id)
            if entry is None or entry.permit is not EntryPermit.ACCEPTED:
                return
            await session.execute(delete(HarvestedNode).where(HarvestedNode.entry_uuid == entry_id))
            for node in result.nodes:
                session.add(
                    HarvestedNode(
                        entry_uuid=entry_id,
                        node_iri=node.node_iri,
                        ntriples=node.ntriples,
                        content_hash=node.content_hash,
                        harvested_at=now,
                    )
                )

    async def delete_harvested(self, entry_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(delete(HarvestedNode).where(HarvestedNode.entry_uuid == entry_id))

    async def harvested_graph(self, entry_id: UUID) -> Graph:
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                raise NotFound("Index entry not found")
            rows = await session.execute(
                select(HarvestedNode.ntriples).where(HarvestedNode.entry_uuid == entry_id)
            )
            graph = Graph()
            for (ntriples,) in rows:
                graph.parse(data=ntriples, format="nt")
        return graph

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
        """Resume retrievals and webhook deliveries left unfinished by a stopped process."""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(Event.uuid, Event.type)
                .where(Event.finished_at.is_(None))
                .order_by(Event.created_at)
            )
            unfinished = [(row[0], row[1]) for row in rows]
        for event_id, event_type in unfinished:
            if event_type is EventType.METADATA_RETRIEVAL:
                self._jobs.submit(
                    f"resume:{event_id}", lambda eid=event_id: self.run_retrieval(eid)
                )
            elif event_type is EventType.WEBHOOK_TRIGGER:
                self._jobs.submit(
                    f"resume:{event_id}", lambda eid=event_id: self._webhooks.resume(eid)
                )
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
    ) -> EntryPage:
        threshold = await self.valid_threshold()
        conditions: list[ColumnElement[bool]] = [IndexEntry.permit.in_(permits)]
        state_condition = self._state_filter(state, threshold)
        if state_condition is not None:
            conditions.append(state_condition)
        if query.strip():
            # A small directory: a substring match on the URL and the title is enough.
            needle = f"%{query.strip().lower()}%"
            conditions.append(
                func.lower(IndexEntry.client_url).like(needle)
                | func.lower(IndexEntry.repository_metadata["title"].as_string()).like(needle)
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

    async def all_entries(self, permits: list[EntryPermit]) -> list[IndexEntry]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(IndexEntry)
                .where(IndexEntry.permit.in_(permits))
                .order_by(IndexEntry.created_at.desc())
            )
            return list(rows.scalars().all())

    async def counts(self, permits: list[EntryPermit]) -> dict[str, int]:
        threshold = await self.valid_threshold()
        result: dict[str, int] = {}
        async with self._session_factory() as session:
            base = (
                select(func.count()).select_from(IndexEntry).where(IndexEntry.permit.in_(permits))
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
