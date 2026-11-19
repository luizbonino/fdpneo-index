"""Keep the search tables in step with ``harvested_node`` (search design §3.2).

``IndexService`` notifies of harvests and removals; a background loop
re-projects the affected entries, coalescing bursts. Each entry's last
projection is recorded in ``search_entry_state`` (a fingerprint of its
nodes' content hashes and the profile hash), so the start-up reconcile
finds exactly the entries that changed while the process was down.

Deviation from the design: no ``search_reindex`` event. The fingerprint
comparison makes every pass resumable on its own: an interrupted pass
leaves an old fingerprint behind and the entry is projected again.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import delete, func, insert, select, update

from fdpindex.db import utcnow
from fdpindex.models import (
    EntryPermit,
    HarvestedNode,
    IndexEntry,
    SearchEntryState,
    SearchLabel,
    SearchProperty,
    SearchResource,
    SearchValue,
)
from fdpindex.search.concepts import concept_fields, concept_texts
from fdpindex.search.project import (
    PROJECTION_VERSION,
    EntryProjection,
    ResourceRow,
    enrich,
    project_entry,
)
from fdpindex.search.vocab import pack_digest

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.search.concepts import ConceptResolver
    from fdpindex.search.profile import Profile
    from fdpindex.service import IndexChange

log = structlog.get_logger(__name__)

BATCH = 500


def fingerprint(content_hashes: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for item in sorted(content_hashes):
        digest.update(item.encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _chunks[T](items: Sequence[T], size: int = BATCH) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class SearchIndexer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        profile: Profile,
        *,
        debounce: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self.profile = profile
        # A new projection version re-projects every entry, like a changed profile.
        # The profile, the projection code and the label pack all shape the rows.
        self._profile_hash = f"{profile.hash[:48]}:v{PROJECTION_VERSION}:{pack_digest()[:8]}"
        self._concept_fields = set(concept_fields(profile))
        # Set when concept labels are resolved (ADR-0006): woken after projections.
        self.after_projection: Callable[[], None] | None = None
        self.resolver: ConceptResolver | None = None
        self._debounce = debounce
        self._entries: set[UUID] = set()
        self._reconcile = True
        self._forced: set[UUID] = set()
        self._force_all = False
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._busy = asyncio.Lock()
        self.idle = asyncio.Event()
        # Bumped after every write, so caches (landing counters) know to refresh.
        self.generation = 0

    # --- notifications (IndexService listener) ------------------------------------

    def notify(self, change: IndexChange) -> None:
        if change.kind == "harvested" and change.entry_id is not None:
            self._entries.add(change.entry_id)
        elif change.kind == "removed":
            self._reconcile = True
        else:
            return  # entry state and titles are joined at query time
        self.idle.clear()
        self._wake.set()

    def reindex(self, entry_id: UUID | None = None) -> None:
        """Re-project one entry, or all, even if nothing changed."""
        if entry_id is None:
            self._force_all = True
            self._reconcile = True
        else:
            self._forced.add(entry_id)
            self._entries.add(entry_id)
        self.idle.clear()
        self._wake.set()

    # --- loop -------------------------------------------------------------------

    def start(self) -> None:
        self.idle.clear()
        self._wake.set()
        self._task = asyncio.create_task(self._run(), name="search-indexer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            await asyncio.sleep(self._debounce)
            try:
                await self.sync_once()
            except Exception:
                log.exception("search_index_failed")
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
                await self.project(self._entries.pop())

    async def _reconcile_all(self) -> None:
        force = self._force_all
        self._force_all = False
        async with self._session_factory() as session:
            accepted = set(
                await session.scalars(
                    select(IndexEntry.uuid).where(IndexEntry.permit == EntryPermit.ACCEPTED)
                )
            )
            indexed = set(await session.scalars(select(SearchEntryState.entry_uuid))) | set(
                await session.scalars(select(SearchResource.entry_uuid).distinct())
            )
        for stale in indexed - accepted:
            await self._clear(stale)
        for entry_id in sorted(accepted):
            await self.project(entry_id, force=force)

    async def project(self, entry_id: UUID, *, force: bool = False) -> bool:
        """Re-project one entry if its nodes or the profile changed. True if rows were written."""
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
            state = await session.get(SearchEntryState, entry_id)
        if not accepted or not rows:
            if state is not None or not accepted:
                await self._clear(entry_id)
            return False
        wanted = fingerprint(str(h) for _, _, h in rows)
        if (
            not force
            and state is not None
            and state.fingerprint == wanted
            and state.profile_hash == self._profile_hash
        ):
            return False
        nodes = [(str(iri), str(nt)) for iri, nt, _ in rows]
        projection = await asyncio.to_thread(project_entry, str(entry_id), nodes, self.profile)
        # Names resolved from the concepts' vocabularies (ADR-0006).
        iris = {
            value.value
            for row in projection.resources
            for value in row.values
            if value.field in self._concept_fields and value.value.startswith("http")
        }
        if iris:
            async with self._session_factory() as session:
                concepts = await concept_texts(session, iris)
            enrich(projection, concepts, self.profile)
        written = await self._write(entry_id, projection, wanted)
        if self.after_projection is not None:
            self.after_projection()
        log.info(
            "search_entry_projected",
            entry=str(entry_id),
            resources=len(projection.resources),
            written=written,
        )
        return written > 0

    async def _clear(self, entry_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            ids = list(
                await session.scalars(
                    select(SearchResource.id).where(SearchResource.entry_uuid == entry_id)
                )
            )
            for chunk in _chunks(ids):
                await session.execute(delete(SearchValue).where(SearchValue.resource_id.in_(chunk)))
            await session.execute(
                delete(SearchResource).where(SearchResource.entry_uuid == entry_id)
            )
            await session.execute(delete(SearchLabel).where(SearchLabel.entry_uuid == entry_id))
            await session.execute(
                delete(SearchEntryState).where(SearchEntryState.entry_uuid == entry_id)
            )
        self.generation += 1

    async def _write(self, entry_id: UUID, projection: EntryProjection, wanted: str) -> int:
        now = utcnow()
        async with self._session_factory() as session, session.begin():
            postgres = session.get_bind().dialect.name == "postgresql"
            existing = {
                str(iri): (int(rid), str(row_hash))
                for rid, iri, row_hash in (
                    await session.execute(
                        select(
                            SearchResource.id, SearchResource.iri, SearchResource.row_hash
                        ).where(SearchResource.entry_uuid == entry_id)
                    )
                ).all()
            }
            fresh = {row.iri: row for row in projection.resources}
            removed = [existing[iri][0] for iri in existing.keys() - fresh.keys()]
            changed: list[tuple[int, ResourceRow, str]] = []
            added: list[tuple[ResourceRow, str]] = []
            for iri, row in fresh.items():
                row_hash = row.row_hash
                if iri not in existing:
                    added.append((row, row_hash))
                elif existing[iri][1] != row_hash:
                    changed.append((existing[iri][0], row, row_hash))
            for chunk in _chunks(removed):
                await session.execute(delete(SearchValue).where(SearchValue.resource_id.in_(chunk)))
                await session.execute(delete(SearchResource).where(SearchResource.id.in_(chunk)))
            touched: list[tuple[int, ResourceRow]] = []
            if changed:
                ids = [rid for rid, _, _ in changed]
                for chunk in _chunks(ids):
                    await session.execute(
                        delete(SearchValue).where(SearchValue.resource_id.in_(chunk))
                    )
                for chunk in _chunks(changed):
                    await session.execute(
                        update(SearchResource),
                        [
                            {"id": rid, **_columns(row, row_hash, now, postgres=postgres)}
                            for rid, row, row_hash in chunk
                        ],
                    )
                touched.extend((rid, row) for rid, row, _ in changed)
            for chunk in _chunks(added):
                result = await session.execute(
                    insert(SearchResource).returning(SearchResource.id, SearchResource.iri),
                    [
                        {
                            "entry_uuid": entry_id,
                            **_columns(row, row_hash, now, postgres=postgres),
                        }
                        for row, row_hash in chunk
                    ],
                )
                touched.extend((int(rid), fresh[str(iri)]) for rid, iri in result.all())
            values = [
                {
                    "resource_id": rid,
                    "field": v.field,
                    "value": v.value,
                    "lang": v.lang,
                    "kind": row.kind,
                    "entry_uuid": entry_id,
                }
                for rid, row in touched
                for v in row.values
            ]
            for chunk in _chunks(values, 2000):
                await session.execute(insert(SearchValue), list(chunk))
            if postgres and touched:
                for chunk in _chunks([rid for rid, _ in touched]):
                    await session.execute(
                        update(SearchResource)
                        .where(SearchResource.id.in_(chunk))
                        .values(
                            tsv_lang=func.fdpindex_tsv(SearchResource.text_segments, False),
                            tsv_simple=func.fdpindex_tsv(SearchResource.text_segments, True),
                        )
                        .execution_options(synchronize_session=False)
                    )
            await _register_properties(session, projection.properties)
            await session.execute(delete(SearchLabel).where(SearchLabel.entry_uuid == entry_id))
            labels = [
                {"entry_uuid": entry_id, "iri": label.iri, "lang": label.lang, "label": label.label}
                for label in projection.labels
                if len(label.iri) <= 2048
            ]
            for chunk in _chunks(labels, 2000):
                await session.execute(insert(SearchLabel), list(chunk))
            state = await session.get(SearchEntryState, entry_id)
            if state is None:
                session.add(
                    SearchEntryState(
                        entry_uuid=entry_id,
                        fingerprint=wanted,
                        profile_hash=self._profile_hash,
                        resources=len(fresh),
                        projected_at=now,
                    )
                )
            else:
                state.fingerprint = wanted
                state.profile_hash = self._profile_hash
                state.resources = len(fresh)
                state.projected_at = now
        written = len(removed) + len(touched)
        if written:
            self.generation += 1
        return written


async def _register_properties(session: AsyncSession, properties: dict[str, str]) -> None:
    """Record the IRIs of captured property keys not seen before (one projection at a time)."""
    keys = sorted(properties)
    known: set[str] = set()
    for chunk in _chunks(keys):
        known.update(
            await session.scalars(
                select(SearchProperty.field).where(SearchProperty.field.in_(chunk))
            )
        )
    new = [{"field": key, "iri": properties[key]} for key in keys if key not in known]
    for chunk in _chunks(new):
        await session.execute(insert(SearchProperty), list(chunk))


def _columns(row: ResourceRow, row_hash: str, now: Any, *, postgres: bool) -> dict[str, Any]:
    return {
        "iri": row.iri,
        "node_iri": row.node_iri,
        "public_id": row.public_id,
        "kind": row.kind,
        "type_iris": row.type_iris,
        "parent_iri": row.parent_iri,
        "catalog_iri": row.catalog_iri,
        "dataset_iri": row.dataset_iri,
        "title": row.title,
        "titles": row.titles,
        "description": row.description,
        "descriptions": row.descriptions,
        "landing_page": row.landing_page,
        "issued": row.issued,
        "modified": row.modified,
        "completeness": row.completeness,
        "facts": row.facts,
        "text_segments": [list(segment) for segment in row.segments],
        # PostgreSQL matches on the vectors; SQLite (unit tests) on this text.
        "search_text": None if postgres else row.search_text,
        "row_hash": row_hash,
        "harvested_at": now,
    }
