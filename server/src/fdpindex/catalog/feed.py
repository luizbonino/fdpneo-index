"""The catalogue feed: harvested metadata republished as DCAT-AP (TASKS 11.5).

Conformance design §6. One ``dcat:CatalogRecord`` per harvested catalogue,
dataset, dataset series or data service, minted under the index's own URL
(from the search projection's stable id). The record says what it describes
(``foaf:primaryTopic``, the resource under its own IRI), where the index got
it (``dct:source``, the FDP's document) and when the index last saw it change
(``dct:modified``), and which checked profiles it conforms to
(``dct:conformsTo``). It is also the index's statement about its copy
(TASKS 15.3, ``fdpindex.provenance``): ``prov:wasDerivedFrom`` the resource,
``prov:wasGeneratedBy`` the harvest that last fetched it, when
(``fdp-o:fetchedAt``), its hash and, for a copy kept from an earlier harvest,
``fdp-o:staleSince``. The resource's triples follow as harvested: nothing is
normalised or merged, and copies in several FDPs stay separate records.

Records are read from the search projection, which already knows every
resource's kind, IRI and node; the feed needs search on. Pages are keyset
pages over the projection's row id, so a harvest between two requests
neither repeats nor skips records. A strict feed (the conformance setting
``surfaces.feed``) keeps only records that conform to the named pack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from rdflib import RDF, XSD, BNode, Graph, Literal, Namespace, URIRef
from sqlalchemy import and_, exists, func, or_, select, tuple_

from fdpindex.models import (
    ConformanceResult,
    EntryPermit,
    HarvestedNode,
    HarvestRecord,
    IndexEntry,
    SearchResource,
)
from fdpindex.provenance import (
    Harvests,
    bind as bind_provenance,
    describe_copy,
    describe_index,
    harvest_iri,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
HYDRA = Namespace("http://www.w3.org/ns/hydra/core#")

FEED_KINDS = ("catalog", "dataset", "series", "service")
LINK: dict[str, URIRef] = {
    "dataset": DCAT.dataset,
    "series": DCAT.dataset,
    "service": DCAT.service,
    "catalog": DCAT.catalog,
}
MAX_PAGE = 500


@dataclass(frozen=True)
class CatalogMeta:
    """The catalogue's own description, from the deployment's configuration."""

    title: str
    description: str
    publisher: str
    publisher_url: str | None = None
    homepage: str | None = None


@dataclass
class FeedRecord:
    id: int
    public_id: str
    iri: str
    kind: str
    entry_uuid: UUID
    node_iri: str
    modified: datetime
    conforms_to: list[str] = field(default_factory=lambda: [])
    nodes: list[str] = field(default_factory=lambda: [])  # N-Triples: own node, distributions
    # The copy's provenance (TASKS 15.3): its own node's fetch, and the harvest of it.
    fetched_at: datetime | None = None
    content_hash: str | None = None
    stale_since: datetime | None = None
    harvest_id: UUID | None = None


@dataclass
class FeedPage:
    records: list[FeedRecord]
    total: int
    next_after: int | None


class CatalogFeed:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        meta: CatalogMeta,
        *,
        hide_expired: Callable[[], Awaitable[bool]],
        minimum: Callable[[], Awaitable[str | None]] | None = None,
        specs: Callable[[], Awaitable[dict[str, tuple[str, dict[str, str]]]]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.meta = meta
        self._hide_expired = hide_expired
        self._minimum = minimum  # the feed's minimum: a pack id
        self._specs = specs

    # --- which records ------------------------------------------------------------

    async def _conditions(
        self, since: datetime | None, until: datetime | None
    ) -> list[ColumnElement[bool]]:
        R = SearchResource  # noqa: N806
        conditions: list[ColumnElement[bool]] = [
            R.kind.in_(FEED_KINDS),
            IndexEntry.permit == EntryPermit.ACCEPTED,
        ]
        if await self._hide_expired():
            conditions.append(IndexEntry.expired_at.is_(None))
        pack = await self._minimum() if self._minimum else None
        if pack is not None:
            conditions.append(
                exists(
                    select(ConformanceResult.id).where(
                        ConformanceResult.public_id == R.public_id,
                        ConformanceResult.pack == pack,
                        ConformanceResult.status == "conforms",
                    )
                )
            )
        if since is not None:
            conditions.append(R.harvested_at >= since)
        if until is not None:
            conditions.append(R.harvested_at <= until)
        return conditions

    async def page(
        self,
        *,
        after: int | None = None,
        size: int = 100,
        since: datetime | None = None,
        until: datetime | None = None,
        count: bool = True,
    ) -> FeedPage:
        size = max(1, min(size, MAX_PAGE))
        conditions = await self._conditions(since, until)
        R = SearchResource  # noqa: N806
        async with self._session_factory() as session:
            total = 0
            if count:
                total = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(R)
                        .join(IndexEntry, IndexEntry.uuid == R.entry_uuid)
                        .where(*conditions)
                    )
                    or 0
                )
            query = (
                select(R)
                .join(IndexEntry, IndexEntry.uuid == R.entry_uuid)
                .where(*conditions)
                .order_by(R.id)
                .limit(size + 1)
            )
            if after is not None:
                query = query.where(R.id > after)
            rows = list(await session.scalars(query))
            more = len(rows) > size
            rows = rows[:size]
            records = [_record(r) for r in rows]
            await self._fill(session, records)
        return FeedPage(records, total, records[-1].id if more and records else None)

    async def record(self, public_id: str) -> FeedRecord | None:
        conditions = await self._conditions(None, None)
        async with self._session_factory() as session:
            row = await session.scalar(
                select(SearchResource)
                .join(IndexEntry, IndexEntry.uuid == SearchResource.entry_uuid)
                .where(SearchResource.public_id == public_id, *conditions)
            )
            if row is None:
                return None
            records = [_record(row)]
            await self._fill(session, records)
        return records[0]

    async def earliest(self) -> datetime | None:
        conditions = await self._conditions(None, None)
        async with self._session_factory() as session:
            return await session.scalar(
                select(func.min(SearchResource.harvested_at))
                .join(IndexEntry, IndexEntry.uuid == SearchResource.entry_uuid)
                .where(*conditions)
            )

    async def _fill(self, session: AsyncSession, records: Sequence[FeedRecord]) -> None:
        """The records' nodes (their own and their distributions') and conformance."""
        if not records:
            return
        wanted: dict[tuple[UUID, str], list[FeedRecord]] = {}
        for record in records:
            wanted.setdefault((record.entry_uuid, record.node_iri), []).append(record)
        with_distributions = [r for r in records if r.kind in {"dataset", "series"}]
        if with_distributions:
            pairs = [(r.entry_uuid, r.iri) for r in with_distributions]
            by_dataset = {(r.entry_uuid, r.iri): r for r in with_distributions}
            for entry, dataset, node in (
                await session.execute(
                    select(
                        SearchResource.entry_uuid,
                        SearchResource.dataset_iri,
                        SearchResource.node_iri,
                    ).where(
                        SearchResource.kind == "distribution",
                        tuple_(SearchResource.entry_uuid, SearchResource.dataset_iri).in_(pairs),
                    )
                )
            ).all():
                owner = by_dataset.get((entry, str(dataset)))
                if owner is not None and node != owner.node_iri:
                    wanted.setdefault((entry, str(node)), []).append(owner)
        keys = list(wanted)
        for start in range(0, len(keys), 200):
            chunk = keys[start : start + 200]
            rows = (
                await session.execute(
                    select(
                        HarvestedNode.entry_uuid,
                        HarvestedNode.node_iri,
                        HarvestedNode.ntriples,
                        HarvestedNode.harvested_at,
                        HarvestedNode.content_hash,
                        HarvestedNode.stale_since,
                    ).where(
                        or_(
                            *(
                                and_(HarvestedNode.entry_uuid == e, HarvestedNode.node_iri == n)
                                for e, n in chunk
                            )
                        )
                    )
                )
            ).all()
            for entry, node, ntriples, fetched_at, content_hash, stale_since in rows:
                for record in wanted.get((entry, str(node)), []):
                    record.nodes.append(str(ntriples))
                    if record.node_iri == str(node):
                        record.fetched_at = fetched_at
                        record.content_hash = str(content_hash)
                        record.stale_since = stale_since
        entries = {r.entry_uuid for r in records}
        started: dict[UUID, list[tuple[datetime, UUID]]] = {}
        for entry, harvest_id, moment in (
            await session.execute(
                select(HarvestRecord.entry_uuid, HarvestRecord.id, HarvestRecord.started_at).where(
                    HarvestRecord.entry_uuid.in_(list(entries))
                )
            )
        ).all():
            started.setdefault(entry, []).append((moment, harvest_id))
        by_entry = {entry: Harvests(pairs) for entry, pairs in started.items()}
        for record in records:
            harvests = by_entry.get(record.entry_uuid)
            record.harvest_id = harvests.at(record.fetched_at) if harvests else None
        if self._specs is None:
            return
        specs = await self._specs()
        if not specs:
            return
        by_public = {r.public_id: r for r in records}
        for public, pack in (
            await session.execute(
                select(ConformanceResult.public_id, ConformanceResult.pack).where(
                    ConformanceResult.public_id.in_(list(by_public)),
                    ConformanceResult.status == "conforms",
                )
            )
        ).all():
            spec = specs.get(str(pack))
            if spec is not None:
                by_public[str(public)].conforms_to.append(spec[0])
        # In the deployment's order of packs: the feed names the first (see add_record).
        rank = {iri: i for i, (iri, _) in enumerate(specs.values())}
        for record in records:
            record.conforms_to.sort(key=lambda iri: rank.get(iri, len(rank)))

    # --- RDF -------------------------------------------------------------------

    def catalog_node(self, graph: Graph, catalog: URIRef) -> None:
        meta = self.meta
        graph.add((catalog, RDF.type, DCAT.Catalog))
        graph.add((catalog, DCT.title, Literal(meta.title, lang="en")))
        graph.add((catalog, DCT.description, Literal(meta.description, lang="en")))
        publisher: URIRef | BNode = URIRef(meta.publisher_url) if meta.publisher_url else BNode()
        graph.add((catalog, DCT.publisher, publisher))
        graph.add((publisher, RDF.type, FOAF.Agent))
        graph.add((publisher, FOAF.name, Literal(meta.publisher)))
        if meta.homepage:
            graph.add((catalog, FOAF.homepage, URIRef(meta.homepage)))

    def add_record(
        self,
        graph: Graph,
        catalog: URIRef | None,
        record: FeedRecord,
        iri: str,
        *,
        base: str | None = None,
    ) -> None:
        node = URIRef(iri)
        topic = URIRef(record.iri)
        graph.add((node, RDF.type, DCAT.CatalogRecord))
        graph.add((node, FOAF.primaryTopic, topic))
        graph.add((node, DCT.source, URIRef(record.node_iri)))
        graph.add((node, DCT.modified, Literal(record.modified.isoformat(), datatype=XSD.dateTime)))
        describe_copy(
            graph,
            node,
            source=record.iri,
            harvest=harvest_iri(base, record.harvest_id) if record.harvest_id else None,
            fetched_at=record.fetched_at,
            content_hash=record.content_hash,
            stale_since=record.stale_since,
        )
        if record.conforms_to:
            # DCAT-AP allows one dct:conformsTo on a catalogue record: the first profile it
            # meets, in the deployment's order. The search and the reports list them all.
            graph.add((node, DCT.conformsTo, URIRef(record.conforms_to[0])))
        if catalog is not None:
            graph.add((catalog, DCAT.record, node))
            graph.add((catalog, LINK[record.kind], topic))
        for ntriples in record.nodes:
            part = Graph()
            part.parse(data=ntriples, format="nt")  # fresh blank nodes per node
            for triple in part:
                graph.add(triple)


def _record(row: SearchResource) -> FeedRecord:
    return FeedRecord(
        id=row.id,
        public_id=row.public_id,
        iri=row.iri,
        kind=row.kind,
        entry_uuid=row.entry_uuid,
        node_iri=row.node_iri,
        modified=row.harvested_at,
    )


def bind(graph: Graph) -> Graph:
    for prefix, ns in (("dcat", DCAT), ("dct", DCT), ("foaf", FOAF), ("hydra", HYDRA)):
        graph.bind(prefix, ns)
    return bind_provenance(graph)


def page_graph(
    feed: CatalogFeed,
    page: FeedPage,
    *,
    catalog_iri: str,
    record_iri: Callable[[str], str],
    view_iri: str,
    first_iri: str,
    next_iri: str | None,
    base: str | None = None,
) -> Graph:
    """One page of the feed: the catalogue, its Hydra view, and the page's records."""
    graph = bind(Graph())
    catalog = URIRef(catalog_iri)
    feed.catalog_node(graph, catalog)
    if base is not None:  # the agent of the harvests the records name
        describe_index(graph, base, feed.meta.title)
    graph.add((catalog, RDF.type, HYDRA.Collection))
    graph.add((catalog, HYDRA.totalItems, Literal(page.total, datatype=XSD.integer)))
    view = URIRef(view_iri)
    graph.add((catalog, HYDRA.view, view))
    graph.add((view, RDF.type, HYDRA.PartialCollectionView))
    graph.add((view, HYDRA.first, URIRef(first_iri)))
    if next_iri is not None:
        graph.add((view, HYDRA.next, URIRef(next_iri)))
    for record in page.records:
        feed.add_record(graph, catalog, record, record_iri(record.public_id), base=base)
    return graph


def record_graph(
    feed: CatalogFeed,
    record: FeedRecord,
    *,
    catalog_iri: str,
    iri: str,
    base: str | None = None,
) -> Graph:
    graph = bind(Graph())
    feed.add_record(graph, None, record, iri, base=base)
    graph.add((URIRef(catalog_iri), DCAT.record, URIRef(iri)))
    return graph
