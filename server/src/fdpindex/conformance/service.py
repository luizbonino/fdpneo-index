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

Besides the active packs, each record is checked against what it declares
with ``dct:conformsTo`` (TASKS 15.12; ``fdpindex.conformance.declared``), and
the result is kept as the pseudo-pack ``declared``: ``conforms``,
``violations`` or ``error`` as for a pack (against all it declares at once),
``undeclared`` when it declares nothing, ``unresolved`` when nothing it
declares could be resolved to shapes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import delete, exists, insert, select

from fdpindex.conformance.declared import DeclaredResolver, Target, declared
from fdpindex.conformance.records import Record, assemble
from fdpindex.conformance.settings import kinds_of, pack_digest
from fdpindex.conformance.validator import Finding, Outcome
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

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.conformance.packs import Pack
    from fdpindex.conformance.settings import ActivePack, ConformanceSettingsService
    from fdpindex.conformance.workers import ValidationPool
    from fdpindex.search.profile import Profile
    from fdpindex.service import IndexChange

log = structlog.get_logger(__name__)

BATCH = 25  # records per worker call
DECLARED = "declared"  # the pseudo-pack of what records declare (TASKS 15.12)
# Raised when the declared check changes what it stores, so every entry is checked again
# on upgrade (2: the result names what was declared, TASKS 15.13).
DECLARED_VERSION = 2
DECLARED_TITLE = {
    "en": "What it declares",
    "nl": "Wat het opgeeft",
    "de": "Was er angibt",
    "fr": "Ce qu’il déclare",  # noqa: RUF001  # French apostrophe
    "es": "Lo que declara",
    "it": "Ciò che dichiara",
    "pt": "O que declara",
}
# What visitors filter search on for "meets what it declares" (TASKS 15.13): an internal
# value, never republished (the feed uses specs(), which leaves it out).
DECLARED_IRI = "urn:fdpneo-index:conformance:declared"
DECLARED_SEARCH_TITLE = {
    "en": "Meets what it declares",
    "nl": "Voldoet aan wat het opgeeft",
    "de": "Erfüllt, was er angibt",
    "fr": "Conforme à ce qu’il déclare",  # noqa: RUF001  # French apostrophe
    "es": "Cumple lo que declara",
    "it": "Rispetta ciò che dichiara",
    "pt": "Cumpre o que declara",
}
_ORDER = {"violation": 0, "warning": 1, "info": 2}


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
        resolver: DeclaredResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resolver = resolver
        self.settings = settings
        self._profile = profile
        self._pool = pool
        self._debounce = debounce
        self._entries: set[UUID] = set()
        self._removed = False
        self._forced: set[UUID] = set()
        self._force_all = False
        self._reconcile = True
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._busy = asyncio.Lock()
        self.idle = asyncio.Event()
        # Told when an entry's results changed: the search projection and the SPARQL
        # store carry conformance (TASKS 11.4). Listeners record and return.
        self.listeners: list[Callable[[UUID], None]] = []
        self.settings_listeners: list[Callable[[], None]] = []

    # --- what other surfaces ask ----------------------------------------------------

    async def specs(self) -> dict[str, tuple[str, dict[str, str]]]:
        """Active packs by id: the specification's IRI and the pack's title."""
        settings = await self.settings.get()
        packs = self.settings.packs
        return {
            packs[a.key].id: (packs[a.key].manifest.specification, packs[a.key].manifest.title)
            for a in settings.packs
            if a.key in packs
        }

    async def search_specs(self) -> dict[str, tuple[str, dict[str, str]]]:
        """specs(), and what records declare, for the search's "Meets profile" filter."""
        specs = await self.specs()
        if await self.declared_on():
            specs = {DECLARED: (DECLARED_IRI, DECLARED_SEARCH_TITLE), **specs}
        return specs

    async def roles(self) -> dict[str, str]:
        """Each active pack's role by pack id: ``required`` or ``lens`` (TASKS 15.13)."""
        settings = await self.settings.get()
        packs = self.settings.packs
        return {packs[a.key].id: a.role for a in settings.packs if a.key in packs}

    async def minimum(self, surface: str) -> str | None:
        """The pack id a surface requires visitors' records to conform to, if any."""
        settings = await self.settings.get()
        return getattr(settings.surfaces, surface, None)

    async def directory_condition(self) -> ColumnElement[bool] | None:
        """The public FDP list's condition: the FDP's own description conforms."""
        pack = await self.minimum("directory")
        if pack is None:
            return None
        return exists(
            select(ConformanceResult.id).where(
                ConformanceResult.entry_uuid == IndexEntry.uuid,
                ConformanceResult.kind == "fdp",
                ConformanceResult.pack == pack,
                ConformanceResult.status == "conforms",
            )
        )

    async def minimum_specification(self, surface: str) -> str | None:
        """A surface's minimum as the pack's specification IRI (search values carry it)."""
        pack = await self.minimum(surface)
        if pack is None:
            return None
        found = (await self.specs()).get(pack)
        return found[0] if found else None

    def _changed(self, entry_id: UUID) -> None:
        for listener in self.listeners:
            try:
                listener(entry_id)
            except Exception:
                log.exception("conformance_listener_failed")

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
        """Packs, their files or the surfaces changed: every entry is compared again."""
        for listener in self.settings_listeners:
            try:
                listener()
            except Exception:
                log.exception("conformance_listener_failed")
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

    def _settings_hash(self, uses: Sequence[_Use], declared_on: bool, day: str) -> str:
        """What the entry was validated with. With declared checks on, the day is part of
        it: declared profiles are resolved again daily (``declared.FRESH``)."""
        described = [(u.pack.key, u.digest, sorted(u.kinds)) for u in uses]
        payload = json.dumps(
            [self._profile().hash, described, declared_on and day, declared_on and DECLARED_VERSION]
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def declared_on(self) -> bool:
        return (await self.settings.get()).declared and self._resolver is not None

    async def validate_entry(self, entry_id: UUID, *, force: bool = False) -> int:
        """Validate an entry's changed records. Returns how many records were validated."""
        force = force or entry_id in self._forced
        self._forced.discard(entry_id)
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            accepted = entry is not None and entry.permit is EntryPermit.ACCEPTED
            origins = (
                [entry.client_url, *(entry.harvest_origins or [])] if entry is not None else []
            )
            rows = (
                await session.execute(
                    select(
                        HarvestedNode.node_iri, HarvestedNode.ntriples, HarvestedNode.content_hash
                    ).where(HarvestedNode.entry_uuid == entry_id)
                )
            ).all()
            state = await session.get(ConformanceEntryState, entry_id)
        uses = await self._uses()
        declared_on = await self.declared_on()
        if not accepted or not rows or not (uses or declared_on):
            if state is not None or not accepted or not (uses or declared_on):
                await self._clear(entry_id)
            return 0
        wanted = fingerprint(str(h) for _, _, h in rows)
        settings_hash = self._settings_hash(uses, declared_on, utcnow().date().isoformat())
        if (
            not force
            and state is not None
            and state.fingerprint == wanted
            and state.settings_hash == settings_hash
        ):
            return 0
        nodes = [(str(i), str(nt), str(h)) for i, nt, h in rows]
        records = await asyncio.to_thread(assemble, str(entry_id), nodes, self._profile())
        targets: dict[str, Target] | None = None
        if declared_on and self._resolver is not None:
            iris = sorted({iri for r in records for iri in declared(r.nodes[0], r.iri)})
            targets = await self._resolver.resolve(iris, origins)
        done = await self._validate_records(entry_id, records, uses, targets, force=force)
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
        if done or self._removed:
            self._changed(entry_id)
        self._removed = False
        return done

    def _declared_uses(
        self, record: Record, targets: dict[str, Target]
    ) -> tuple[str, list[PackUse], Outcome | None, str | None]:
        """What the record declares: a digest of its resolution, the shapes to validate
        against, or the outcome when there is nothing to validate, and what it declared
        that could be checked (``,dcat-ap,profile,``: pack ids, and ``profile`` for an FDP's
        own), kept as the result's ``variant`` so reports know which packs a record claims."""
        iris = declared(record.nodes[0], record.iri)
        resolved = [targets[i] for i in iris if i in targets]
        # "2": the result names what was declared (``variant``) since TASKS 15.13.
        digest = hashlib.sha256(
            json.dumps([2, [(t.iri, t.kind, t.digest or t.reason) for t in resolved]]).encode()
        ).hexdigest()
        if not iris:
            return digest, [], Outcome(status="undeclared"), None
        jobs: list[PackUse] = []
        names: set[str] = set()
        packs = self.settings.packs
        for target in resolved:
            if target.kind == "pack" and target.pack_key in packs:
                pack = packs[target.pack_key]
                names.add(pack.id)
                jobs.append(PackUse(directory=str(pack.directory), key=pack.key, files=None))
            elif target.kind == "shapes" and target.shapes is not None:
                names.add("profile")
                jobs.append(
                    PackUse(
                        directory="",
                        key=f"declared:{target.digest}",
                        files=None,
                        shapes=target.shapes,
                    )
                )
        if not jobs:
            reasons = "; ".join(f"{t.iri}: {t.reason}" for t in resolved if t.reason)
            return digest, [], Outcome(status="unresolved", error=reasons[:500] or None), None
        label = f",{','.join(sorted(names))},"
        return digest, jobs, None, label if len(label) <= 64 else None

    async def _validate_records(
        self,
        entry_id: UUID,
        records: list[Record],
        uses: list[_Use],
        targets: dict[str, Target] | None,
        *,
        force: bool,
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
        work: list[tuple[Record, list[_Use], tuple[str, list[PackUse], str | None] | None]] = []
        ready: list[tuple[Record, list[_Written]]] = []
        for record in records:
            stale: list[_Use] = []
            for use in uses:
                if record.kind not in use.kinds:
                    continue
                wanted.add((record.iri, use.pack.id))
                seen = existing.get((record.iri, use.pack.id))
                if force or seen is None or seen[1:] != (record.content_hash, use.digest):
                    stale.append(use)
            declared_job: tuple[str, list[PackUse], str | None] | None = None
            if targets is not None:
                wanted.add((record.iri, DECLARED))
                digest, jobs, outcome, label = self._declared_uses(record, targets)
                seen = existing.get((record.iri, DECLARED))
                if force or seen is None or seen[1:] != (record.content_hash, digest):
                    if outcome is not None:
                        ready.append((record, [(DECLARED, DECLARED, digest, outcome)]))
                    else:
                        declared_job = (digest, jobs, label)
            if stale or declared_job is not None:
                work.append((record, stale, declared_job))
        gone = [v[0] for k, v in existing.items() if k not in wanted]
        if gone:
            await self._delete_results(gone)
            self._removed = True
        if ready:
            await self._write(entry_id, ready)
        done = len(ready)
        for start in range(0, len(work), BATCH):
            batch = work[start : start + BATCH]
            jobs = [
                Job(
                    root=r.iri,
                    nodes=r.nodes,
                    packs=tuple(u.job for u in us) + tuple(d[1] if d else ()),
                )
                for r, us, d in batch
            ]
            outcomes = await self._pool.run(jobs)
            written: list[tuple[Record, list[_Written]]] = []
            for (record, us, d), results in zip(batch, outcomes, strict=True):
                rows: list[_Written] = [
                    (u.pack.id, u.pack.version, u.digest, o)
                    for u, o in zip(us, results[: len(us)], strict=True)
                ]
                if d is not None:
                    merged = _merge(results[len(us) :])
                    merged.variant = d[2]
                    rows.append((DECLARED, DECLARED, d[0], merged))
                written.append((record, rows))
            await self._write(entry_id, written)
            done += len(batch)
        return done

    async def _write(self, entry_id: UUID, batch: list[tuple[Record, list[_Written]]]) -> None:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            for record, results in batch:
                for pack_id, version, digest, outcome in results:
                    old = await session.scalar(
                        select(ConformanceResult.id).where(
                            ConformanceResult.entry_uuid == entry_id,
                            ConformanceResult.record_iri == record.iri,
                            ConformanceResult.pack == pack_id,
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
                        pack=pack_id,
                        pack_version=version,
                        pack_digest=digest,
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
        if ids:
            self._changed(entry_id)


_Written = tuple[str, str, str, "Outcome"]  # pack id, version, digest, outcome


def _merge(outcomes: Sequence[Outcome]) -> Outcome:
    """One outcome for all a record declares: it must meet each of them."""
    findings: list[Finding] = sorted(
        (f for o in outcomes for f in o.findings), key=lambda f: _ORDER[f.severity]
    )
    merged = Outcome(
        status="conforms",
        violations=sum(o.violations for o in outcomes),
        warnings=sum(o.warnings for o in outcomes),
        infos=sum(o.infos for o in outcomes),
        findings=findings[:50],
    )
    errors = [o.error for o in outcomes if o.status == "error" and o.error]
    if merged.violations:
        merged.status = "violations"
    elif any(o.status == "error" for o in outcomes):
        merged.status = "error"
        merged.error = "; ".join(errors)[:500] or None
    return merged


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
