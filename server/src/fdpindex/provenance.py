"""Harvest provenance in PROV-O and FDP-O 2.1 (TASKS 15.2, 15.3; design note provenance).

A **harvest** is one walk over an FDP's metadata (``fdp-o:Harvest``, a
``prov:Activity``); its **runs** are activities that are part of it
(``fdp-o:HarvestRun``, ``dct:isPartOf``). The harvest ``prov:used`` the FDP
(its client URL) and ``prov:wasAssociatedWith`` the index
(``fdp-o:FAIRDataPointIndex``). The index's copy of a record is described by
its catalogue-feed record: ``prov:wasDerivedFrom`` the record, ``prov:
wasGeneratedBy`` the harvest that last fetched it, with ``fdp-o:fetchedAt``,
``fdp-o:contentHash`` and, when the copy is kept from an earlier harvest,
``fdp-o:staleSince``.

IRIs: a harvest is ``{public URL}/index/harvests/{id}``, a run
``…/harvests/{id}#run-{n}`` (resolved with its harvest's document), the index
``{public URL}/``. Without ``FDPINDEX_PUBLIC_URL`` they are ``urn:fdpindex:``
names. Published: times, outcome, counts and failure reasons in words; never
callers' addresses or response bodies (decision 3).

The FDP-O terms are proposed for FDP-O 2.1 (``FAIRDataTeam/FDP-O``); they
resolve once it is released.
"""

from __future__ import annotations

import bisect
from datetime import datetime
from typing import TYPE_CHECKING, Any

from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef
from sqlalchemy import select

from fdpindex import __version__
from fdpindex.models import HarvestedNode, HarvestRecord

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

PROV = Namespace("http://www.w3.org/ns/prov#")
FDPO = Namespace("https://w3id.org/fdp/fdp-o#")
DCT = Namespace("http://purl.org/dc/terms/")

OUTCOMES = {
    "complete": FDPO.HarvestComplete,
    "partial": FDPO.HarvestPartial,
    "failed": FDPO.HarvestFailed,
}
COUNTS = (
    ("pages_read", "pagesRead"),
    ("added", "pagesAdded"),
    ("changed", "pagesChanged"),
    ("removed", "pagesRemoved"),
    ("not_modified", "pagesNotModified"),
    ("failed", "pagesFailed"),
)
RUN_COUNTS = (
    ("pagesRead", "pagesRead"),
    ("added", "pagesAdded"),
    ("changed", "pagesChanged"),
    ("removed", "pagesRemoved"),
    ("notModified", "pagesNotModified"),
    ("failed", "pagesFailed"),
)


def bind(graph: Graph) -> Graph:
    for prefix, namespace in (("prov", PROV), ("fdp-o", FDPO), ("dct", DCT)):
        graph.bind(prefix, namespace, override=False)
    return graph


def harvest_iri(base: str | None, harvest_id: object) -> str:
    if base:
        return f"{base.rstrip('/')}/index/harvests/{harvest_id}"
    return f"urn:fdpindex:harvest:{harvest_id}"


def index_iri(base: str | None) -> str:
    return f"{base.rstrip('/')}/" if base else "urn:fdpindex:index"


def _time(moment: datetime) -> Literal:
    return Literal(moment.isoformat(), datatype=XSD.dateTime)


def _count(value: int) -> Literal:
    return Literal(max(0, int(value)), datatype=XSD.nonNegativeInteger)


def describe_index(graph: Graph, base: str | None, title: str) -> URIRef:
    """The index as the agent of its harvests."""
    agent = URIRef(index_iri(base))
    graph.add((agent, RDF.type, FDPO.FAIRDataPointIndex))
    graph.add((agent, RDF.type, PROV.SoftwareAgent))
    graph.add((agent, DCT.title, Literal(title)))
    graph.add((agent, DCT.hasVersion, Literal(__version__)))
    return agent


def describe_harvest(graph: Graph, record: HarvestRecord, *, fdp: str, base: str | None) -> URIRef:
    """One harvest and its runs."""
    harvest = URIRef(harvest_iri(base, record.id))
    graph.add((harvest, RDF.type, FDPO.Harvest))
    graph.add((harvest, RDF.type, PROV.Activity))
    graph.add((harvest, PROV.used, URIRef(fdp)))
    graph.add((harvest, PROV.wasAssociatedWith, URIRef(index_iri(base))))
    graph.add((harvest, PROV.startedAtTime, _time(record.started_at)))
    if record.ended_at is not None:
        graph.add((harvest, PROV.endedAtTime, _time(record.ended_at)))
    outcome = OUTCOMES.get(record.outcome)
    if outcome is not None:
        graph.add((harvest, FDPO.harvestOutcome, outcome))
    for column, term in COUNTS:
        graph.add((harvest, FDPO[term], _count(getattr(record, column))))
    if record.failure_reason:
        graph.add((harvest, FDPO.failureReason, Literal(record.failure_reason)))
    for run in record.runs or []:
        node = URIRef(f"{harvest}#run-{run.get('run')}")
        graph.add((harvest, DCT.hasPart, node))
        graph.add((node, RDF.type, FDPO.HarvestRun))
        graph.add((node, RDF.type, PROV.Activity))
        graph.add((node, DCT.isPartOf, harvest))
        for key, predicate in (("startedAt", PROV.startedAtTime), ("endedAt", PROV.endedAtTime)):
            value = run.get(key)
            if value:
                graph.add((node, predicate, Literal(value, datatype=XSD.dateTime)))
        for key, term in RUN_COUNTS:
            graph.add((node, FDPO[term], _count(run.get(key) or 0)))
    return harvest


def describe_copy(
    graph: Graph,
    record: URIRef,
    *,
    source: str,
    harvest: str | None,
    fetched_at: datetime | None,
    content_hash: str | None,
    stale_since: datetime | None,
) -> None:
    """The provenance of the index's copy, on its catalogue-feed record."""
    graph.add((record, PROV.wasDerivedFrom, URIRef(source)))
    if harvest is not None:
        graph.add((record, PROV.wasGeneratedBy, URIRef(harvest)))
    if fetched_at is not None:
        graph.add((record, FDPO.fetchedAt, _time(fetched_at)))
    if content_hash:
        graph.add((record, FDPO.contentHash, Literal(f"sha256:{content_hash}")))
    if stale_since is not None:
        graph.add((record, FDPO.staleSince, _time(stale_since)))


class Harvests:
    """An FDP's harvests by start time: which one fetched a copy at a given moment."""

    def __init__(self, started: Sequence[tuple[datetime, Any]]) -> None:
        pairs = sorted(started, key=lambda pair: pair[0])
        self._times = [moment for moment, _ in pairs]
        self._ids = [harvest_id for _, harvest_id in pairs]

    def at(self, moment: datetime | None) -> Any:
        """The last harvest that started at or before ``moment``."""
        if moment is None or not self._times:
            return None
        position = bisect.bisect_right(self._times, moment)
        return self._ids[position - 1] if position else None


def record_view(record: HarvestRecord) -> dict[str, Any]:
    """A harvest as JSON for the API (camelCase, times as ISO 8601)."""
    return {
        "id": str(record.id),
        "entry": str(record.entry_uuid),
        "startedAt": record.started_at.isoformat(),
        "endedAt": record.ended_at.isoformat() if record.ended_at else None,
        "outcome": record.outcome,
        "pagesRead": record.pages_read,
        "added": record.added,
        "changed": record.changed,
        "removed": record.removed,
        "notModified": record.not_modified,
        "failed": record.failed,
        "atCeiling": record.at_ceiling,
        "failureReason": record.failure_reason,
        "runs": list(record.runs or []),
    }


async def copy_provenance(
    session: AsyncSession, entry_id: UUID, node_iri: str
) -> dict[str, Any] | None:
    """When the index fetched a page, whether its copy is kept from an earlier harvest,
    and the harvest that fetched it (for the resource page)."""
    node = (
        await session.execute(
            select(HarvestedNode.harvested_at, HarvestedNode.stale_since).where(
                HarvestedNode.entry_uuid == entry_id, HarvestedNode.node_iri == node_iri
            )
        )
    ).first()
    if node is None:
        return None
    fetched_at, stale_since = node
    record = await session.scalar(
        select(HarvestRecord)
        .where(HarvestRecord.entry_uuid == entry_id, HarvestRecord.started_at <= fetched_at)
        .order_by(HarvestRecord.started_at.desc())
        .limit(1)
    )
    return {
        "fetchedAt": fetched_at.isoformat(),
        "staleSince": stale_since.isoformat() if stale_since else None,
        "harvest": None
        if record is None
        else {
            "id": str(record.id),
            "startedAt": record.started_at.isoformat(),
            "endedAt": record.ended_at.isoformat() if record.ended_at else None,
            "outcome": record.outcome,
        },
    }
