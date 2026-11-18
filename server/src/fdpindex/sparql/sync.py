"""Keep the SPARQL store in step with PostgreSQL (search design §2.2.1).

``IndexService`` notifies of harvests, removals and entry changes; this
records them and a background loop applies them, coalescing bursts:

* a harvested entry is reloaded when its fingerprint differs from the store's;
* removals reconcile the store's graphs against the accepted entries;
* the entries graph is rewritten after any entry change;
* the VoID description is regenerated at most every ``void_interval``
  seconds, because counting partitions reads the whole store.

At start-up everything is reconciled, so a fresh or stale volume catches up
on its own. PostgreSQL stays the source of truth; the store is never backed up.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import func, select

from fdpindex.db import utcnow
from fdpindex.models import EntryPermit, HarvestedNode, IndexEntry
from fdpindex.service import IndexChange, api_state
from fdpindex.sparql.store import EntryFacts, SparqlStore, fingerprint

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.settings import SettingsService

log = structlog.get_logger(__name__)


class SparqlSync:
    def __init__(
        self,
        store: SparqlStore,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SettingsService,
        *,
        endpoint: str,
        void_interval: float = 60.0,
        debounce: float = 1.0,
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._settings = settings
        self._endpoint = endpoint
        self._void_interval = void_interval
        self._debounce = debounce
        self._entries: set[UUID] = set()
        self._reconcile = True
        self._entries_dirty = True
        self._void_dirty = True
        self._void_at = 0.0
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.idle = asyncio.Event()
        self._busy = asyncio.Lock()

    # --- notifications (IndexService listener) ------------------------------------

    def notify(self, change: IndexChange) -> None:
        if change.kind == "harvested" and change.entry_id is not None:
            self._entries.add(change.entry_id)
        elif change.kind == "removed":
            self._reconcile = True
        self._entries_dirty = True
        self.idle.clear()
        self._wake.set()

    # --- loop -------------------------------------------------------------------

    def start(self) -> None:
        self.idle.clear()
        self._wake.set()
        self._task = asyncio.create_task(self._run(), name="sparql-sync")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            # Wake on notifications, and at least every void_interval: then the entries
            # graph is refreshed too, because ACTIVE turns INACTIVE by time alone
            # (unchanged facts are not rewritten).
            tick = max(1.0, self._void_interval)
            if self._void_dirty:
                tick = max(0.0, min(tick, self._void_at + self._void_interval - time.monotonic()))
            try:
                await asyncio.wait_for(self._wake.wait(), tick)
            except TimeoutError:
                self._entries_dirty = True
            self._wake.clear()
            await asyncio.sleep(self._debounce)  # let a burst of notifications settle
            try:
                await self.sync_once()
            except Exception:
                log.exception("sparql_sync_failed")
                # The pass may have consumed pending work before failing; a reconcile
                # (cheap, by fingerprint) catches up with everything on the retry.
                self._reconcile = True
                self._entries_dirty = True
                await asyncio.sleep(5)
                self._wake.set()
                continue
            if not (self._entries or self._reconcile or self._entries_dirty):
                self.idle.set()

    async def sync_once(self, *, force_void: bool = False) -> None:
        """Apply what is pending. Public for tests and the rebuild command."""
        async with self._busy:
            await self._sync_once(force_void=force_void)

    async def _sync_once(self, *, force_void: bool) -> None:
        loaded = False
        if self._reconcile:
            self._reconcile = False
            loaded |= await self._reconcile_all()
        while self._entries:
            loaded |= await self._sync_entry(self._entries.pop())
        if self._entries_dirty:
            self._entries_dirty = False
            await self._write_entries()
        self._void_dirty |= loaded
        due = time.monotonic() >= self._void_at + self._void_interval
        if self._void_dirty and (due or force_void):
            self._void_dirty = False
            self._void_at = time.monotonic()
            await asyncio.to_thread(self._store.write_void, self._endpoint)
        # One snapshot per pass: queries see a whole pass or none of it.
        await asyncio.to_thread(self._store.publish)

    async def _reconcile_all(self) -> bool:
        async with self._session_factory() as session:
            accepted = dict(
                (
                    await session.execute(
                        select(IndexEntry.uuid, IndexEntry.client_url).where(
                            IndexEntry.permit == EntryPermit.ACCEPTED
                        )
                    )
                ).all()
            )
        urls = set(accepted.values())
        loaded = False
        for stale in await asyncio.to_thread(self._store.fdp_graphs) - urls:
            await asyncio.to_thread(self._store.drop_graph, stale)
            log.info("sparql_graph_dropped", client_url=stale)
            loaded = True
        fingerprints = await asyncio.to_thread(self._store.fingerprints)
        for entry_id in accepted:
            loaded |= await self._sync_entry(entry_id, fingerprints)
        return loaded

    async def _sync_entry(self, entry_id: UUID, fingerprints: dict[str, str] | None = None) -> bool:
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                self._reconcile = True
                return False
            client_url = entry.client_url
            accepted = entry.permit is EntryPermit.ACCEPTED
            rows = (
                await session.execute(
                    select(HarvestedNode.ntriples, HarvestedNode.content_hash).where(
                        HarvestedNode.entry_uuid == entry_id
                    )
                )
            ).all()
            nodes = [(str(ntriples), str(content_hash)) for ntriples, content_hash in rows]
        if not accepted or not nodes:
            if client_url in await asyncio.to_thread(self._store.fdp_graphs):
                await asyncio.to_thread(self._store.drop_graph, client_url)
                return True
            return False
        wanted = fingerprint(h for _, h in nodes)
        if fingerprints is None:
            fingerprints = await asyncio.to_thread(self._store.fingerprints)
        if fingerprints.get(client_url) == wanted:
            return False
        triples = await asyncio.to_thread(
            self._store.replace_graph, client_url, (n for n, _ in nodes), wanted
        )
        log.info("sparql_graph_loaded", client_url=client_url, nodes=len(nodes), triples=triples)
        return True

    async def _write_entries(self) -> None:
        settings = await self._settings.get()
        threshold = utcnow() - settings.ping.valid_duration
        async with self._session_factory() as session:
            counts = dict(
                (
                    await session.execute(
                        select(HarvestedNode.entry_uuid, func.count()).group_by(
                            HarvestedNode.entry_uuid
                        )
                    )
                ).all()
            )
            entries = list(
                await session.scalars(
                    select(IndexEntry).where(IndexEntry.permit == EntryPermit.ACCEPTED)
                )
            )
            facts = [
                EntryFacts(
                    client_url=entry.client_url,
                    uuid=str(entry.uuid),
                    state=api_state(entry, threshold),
                    title=(entry.repository_metadata or {}).get("title"),
                    country=(entry.repository_metadata or {}).get("country"),
                    publisher=(entry.repository_metadata or {}).get("publisherName")
                    or (entry.repository_metadata or {}).get("publisher"),
                    last_retrieval=entry.last_retrieval_at,
                    nodes=int(counts.get(entry.uuid, 0)),
                )
                for entry in entries
            ]
        await asyncio.to_thread(self._store.write_entries, facts)
