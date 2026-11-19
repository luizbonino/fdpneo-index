"""Keep conformance results in step with harvests (TASKS 11.3; conformance design §3, §4).

Like the search indexer, a background loop takes notifications from
``IndexService`` (an entry was harvested or removed), coalesces bursts, and
validates the affected entries. A record is validated against a pack again
only when its content hash or the pack's digest changed, so a harvest that
changed one dataset validates that dataset only.

Deviation from the design (§3, "jobs in the event table"): as in the search
indexer, no events. ``conformance_entry_state`` records each entry's node
fingerprint and the settings it was validated with; the start-up reconcile
compares them and finds exactly the entries left to do, so an interrupted
pass resumes on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import delete, insert, select

from fdpindex.conformance.records import Record, assemble
from fdpindex.conformance.settings import kinds_of, pack_digest
from fdpindex.conformance.workers import Job, PackUse
from fdpindex.db import utcnow
from fdpindex.models import (
    ConformanceEntryState,
    ConformanceFinding,
    ConformanceResult,
    EntryPermit,
    HarvestedNode,
    IndexEntry,
)
from fdpindex.search.indexer import fingerprint

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.conformance.packs import Pack
    from fdpindex.conformance.settings import ActivePack, ConformanceSettingsService
    from fdpindex.conformance.validator import Outcome
    from fdpindex.conformance.workers import ValidationPool
    from fdpindex.search.profile import Profile
    from fdpindex.service import IndexChange

log = structlog.get_logger(__name__)

BATCH = 25  # records per worker call


class _Use:
    """An active pack as one entry's validation uses it."""

    def __init__(self, pack: Pack, active: ActivePack) -> None:
        self.pack = pack
        self.digest = pack_digest(pack, active)
        self.kinds = kinds_of(pack, active)
        self.files = tuple(active.files) if active.files is not None else None
        self.job = PackUse(directory=str(pack.directory), key=pack.key, files=self.files)


class ConformanceService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: ConformanceSettingsService,
        profile: Callable[[], Profile],
        pool: ValidationPool,
        *,
        debounce: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self.settings = settings
        self._profile = profile
        self._pool = pool
        self._debounce = debounce
        self._entries: set[UUID] = set()
        self._forced: set[UUID] = set()
        self._force_all = False
        self._reconcile = True
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._busy = asyncio.Lock()
        self.idle = asyncio.Event()

    # --- notifications ------------------------------------------------------------

    def notify(self, change: IndexChange) -> None:
        if change.kind == "harvested" and change.entry_id is not None:
            self._entries.add(change.entry_id)
        elif change.kind == "removed":
            self._reconcile = True
        else:
            return
        self.idle.clear()
        self._wake.set()

    def revalidate(self, entry_id: UUID | None = None) -> None:
        """Validate one entry, or all, again even if nothing changed."""
        if entry_id is None:
            self._force_all = True
            self._reconcile = True
        else:
            self._forced.add(entry_id)
            self._entries.add(entry_id)
        self.idle.clear()
        self._wake.set()

    def settings_changed(self) -> None:
        """Packs or their files changed: every entry is compared again."""
        self._reconcile = True
        self.idle.clear()
        self._wake.set()

    # --- loop -------------------------------------------------------------------

    def start(self) -> None:
        self.idle.clear()
        self._wake.set()
        self._task = asyncio.create_task(self._run(), name="conformance")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._pool.close()

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await asyncio.sleep(self._debounce)
            try:
                await self.sync_once()
            except Exception:
                log.exception("conformance_failed")
                self._reconcile = True
                await asyncio.sleep(5)
                self._wake.set()
                continue
            if not (self._entries or self._reconcile):
                self.idle.set()

    async def sync_once(self) -> None:
        """Apply what is pending. Public for tests."""
        async with self._busy:
            if self._reconcile:
                self._reconcile = False
                await self._reconcile_all()
            while self._entries:
                await self.validate_entry(self._entries.pop())

    async def _reconcile_all(self) -> None:
        force = self._force_all
        self._force_all = False
        async with self._session_factory() as session:
            accepted = set(
                await session.scalars(
                    select(IndexEntry.uuid).where(IndexEntry.permit == EntryPermit.ACCEPTED)
                )
            )
            validated = set(await session.scalars(select(ConformanceEntryState.entry_uuid))) | set(
                await session.scalars(select(ConformanceResult.entry_uuid).distinct())
            )
        for stale in validated - accepted:
            await self._clear(stale)
        for entry_id in sorted(accepted):
            await self.validate_entry(entry_id, force=force)

    # --- one entry ----------------------------------------------------------------

    async def _uses(self) -> list[_Use]:
        settings = await self.settings.get()
        packs = self.settings.packs
        return [_Use(packs[a.key], a) for a in settings.packs if a.key in packs]

    def _settings_hash(self, uses: Sequence[_Use]) -> str:
        described = [(u.pack.key, u.digest, sorted(u.kinds)) for u in uses]
        payload = json.dumps([self._profile().hash, described])
        return hashlib.sha256(payload.encode()).hexdigest()

    async def validate_entry(self, entry_id: UUID, *, force: bool = False) -> int:
        """Validate an entry's changed records. Returns how many records were validated."""
        force = force or entry_id in self._forced
        self._forced.discard(entry_id)
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            accepted = entry is not None and entry.permit is EntryPermit.ACCEPTED
            rows = (
                await session.execute(
                    select(
                        HarvestedNode.node_iri, HarvestedNode.ntriples, HarvestedNode.content_hash
                    ).where(HarvestedNode.entry_uuid == entry_id)
                )
            ).all()
            state = await session.get(ConformanceEntryState, entry_id)
        uses = await self._uses()
        if not accepted or not rows or not uses:
            if state is not None or not accepted or not uses:
                await self._clear(entry_id)
            return 0
        wanted = fingerprint(str(h) for _, _, h in rows)
        settings_hash = self._settings_hash(uses)
        if (
            not force
            and state is not None
            and state.fingerprint == wanted
            and state.settings_hash == settings_hash
        ):
            return 0
        nodes = [(str(i), str(nt), str(h)) for i, nt, h in rows]
        records = await asyncio.to_thread(assemble, str(entry_id), nodes, self._profile())
        done = await self._validate_records(entry_id, records, uses, force=force)
        async with self._session_factory() as session, session.begin():
            state = await session.get(ConformanceEntryState, entry_id)
            if state is None:
                session.add(
                    ConformanceEntryState(
                        entry_uuid=entry_id,
                        fingerprint=wanted,
                        settings_hash=settings_hash,
                        records=len(records),
                        validated_at=utcnow(),
                    )
                )
            else:
                state.fingerprint = wanted
                state.settings_hash = settings_hash
                state.records = len(records)
                state.validated_at = utcnow()
        log.info("conformance_entry_validated", entry=str(entry_id), records=done)
        return done

    async def _validate_records(
        self, entry_id: UUID, records: list[Record], uses: list[_Use], *, force: bool
    ) -> int:
        async with self._session_factory() as session:
            existing = {
                (str(r.record_iri), str(r.pack)): (
                    int(r.id),
                    str(r.content_hash),
                    str(r.pack_digest),
                )
                for r in (
                    await session.execute(
                        select(
                            ConformanceResult.id,
                            ConformanceResult.record_iri,
                            ConformanceResult.pack,
                            ConformanceResult.content_hash,
                            ConformanceResult.pack_digest,
                        ).where(ConformanceResult.entry_uuid == entry_id)
                    )
                ).all()
            }
        wanted: set[tuple[str, str]] = set()
        work: list[tuple[Record, list[_Use]]] = []
        for record in records:
            stale: list[_Use] = []
            for use in uses:
                if record.kind not in use.kinds:
                    continue
                wanted.add((record.iri, use.pack.id))
                seen = existing.get((record.iri, use.pack.id))
                if force or seen is None or seen[1:] != (record.content_hash, use.digest):
                    stale.append(use)
            if stale:
                work.append((record, stale))
        gone = [v[0] for k, v in existing.items() if k not in wanted]
        if gone:
            await self._delete_results(gone)
        done = 0
        for start in range(0, len(work), BATCH):
            batch = work[start : start + BATCH]
            jobs = [
                Job(root=r.iri, nodes=r.nodes, packs=tuple(u.job for u in us)) for r, us in batch
            ]
            outcomes = await self._pool.run(jobs)
            await self._write(entry_id, batch, outcomes)
            done += len(batch)
        return done

    async def _write(
        self,
        entry_id: UUID,
        batch: list[tuple[Record, list[_Use]]],
        outcomes: list[list[Outcome]],
    ) -> None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            for (record, uses), results in zip(batch, outcomes, strict=True):
                for use, outcome in zip(uses, results, strict=True):
                    old = await session.scalar(
                        select(ConformanceResult.id).where(
                            ConformanceResult.entry_uuid == entry_id,
                            ConformanceResult.record_iri == record.iri,
                            ConformanceResult.pack == use.pack.id,
                        )
                    )
                    if old is not None:
                        await session.execute(
                            delete(ConformanceFinding).where(ConformanceFinding.result_id == old)
                        )
                        await session.execute(
                            delete(ConformanceResult).where(ConformanceResult.id == old)
                        )
                    row = ConformanceResult(
                        entry_uuid=entry_id,
                        record_iri=record.iri,
                        public_id=record.public_id,
                        kind=record.kind,
                        pack=use.pack.id,
                        pack_version=use.pack.version,
                        pack_digest=use.digest,
                        variant=outcome.variant,
                        status=outcome.status,
                        violations=outcome.violations,
                        warnings=outcome.warnings,
                        infos=outcome.infos,
                        content_hash=record.content_hash,
                        error=outcome.error,
                        validated_at=now,
                    )
                    session.add(row)
                    await session.flush()
                    if outcome.findings:
                        await session.execute(
                            insert(ConformanceFinding),
                            [_finding(row.id, f) for f in outcome.findings],
                        )

    async def _delete_results(self, ids: list[int]) -> None:
        async with self._session_factory() as session, session.begin():
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                await session.execute(
                    delete(ConformanceFinding).where(ConformanceFinding.result_id.in_(chunk))
                )
                await session.execute(
                    delete(ConformanceResult).where(ConformanceResult.id.in_(chunk))
                )

    async def _clear(self, entry_id: UUID) -> None:
        async with self._session_factory() as session:
            ids = list(
                await session.scalars(
                    select(ConformanceResult.id).where(ConformanceResult.entry_uuid == entry_id)
                )
            )
        await self._delete_results(ids)
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(ConformanceEntryState).where(ConformanceEntryState.entry_uuid == entry_id)
            )


def _finding(result_id: int, finding: Any) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "severity": finding.severity,
        "focus_node": finding.focus_node[:2048],
        "path": finding.path[:2048] if finding.path else None,
        "component": finding.component[:128],
        "shape": finding.shape[:2048] if finding.shape else None,
        "message": finding.message,
        "value": finding.value,
    }
