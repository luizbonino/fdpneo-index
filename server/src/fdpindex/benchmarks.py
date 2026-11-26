"""Running a network's benchmarks on its FDPs (TASKS 15.10, design note benchmarks).

A network profile names FAIR Testing Resources (FTR) benchmarks. Each
benchmark's description says which runner executes it (``ftr:supportedBy``),
with which scoring algorithm (``ftr:scoringFunction``), and which records it
applies to (``dpv:isApplicableFor``). The index reads that description (kept
for a day in ``benchmark_definition``), calls the runner on every record of
an FDP the benchmark applies to, and keeps the latest result per record and
benchmark (``benchmark_result``). It does not contain the tests.

* **Which records**: the classes the benchmark applies to map to the index's
  levels where DCAT and schema.org agree (``schema:Dataset`` and
  ``dcat:Dataset`` are the FDP's datasets, the network profile's own dataset
  classes included); other classes match records of that class. A benchmark
  that states nothing applies to every record.
* **When**: with the setting ``benchmarks.mode`` at ``scheduled``, after a walk
  that ended since the FDP was last planned, at most once per
  ``benchmarks.interval`` (a week). Planning compares each record with its
  result: only a record whose content changed, or whose benchmark's algorithm
  changed, is called again. At ``onRequest`` only administrators' runs are
  made; at ``off`` (the default) no call leaves the index.
* **Calls**: one at a time, ``pauseSeconds`` apart, at most ``dailyCalls`` a
  day; each sends the record's IRI only. A call that failed (the runner did
  not answer, an HTTP error) is retried later (an hour, six hours, a day) and
  never counts as the record's result; after three failures in a row the
  queue rests for half an hour.
* **Runners**: the index can call FAIR Champion (``POST
  {runner}/assess/algorithm/d/{sheet}`` with ``{"guid": record}``), at the
  address the benchmark names or at another instance an administrator gives
  (``benchmarks.runner``). A benchmark with another runner is listed as not
  run, with the reason; a second runner is one more adapter.

Results are the network's own verdict, shown beside the metadata health and
counted on the Statistics page, never in the health status (decision 5).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit
from uuid import UUID

import structlog
from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import defer

from fdpindex.db import utcnow
from fdpindex.errors import BadRequest, Conflict, NotFound
from fdpindex.findings import level_of, page_key, read_page
from fdpindex.models import (
    BenchmarkDefinition,
    BenchmarkPlan,
    BenchmarkResult,
    EntryPermit,
    HarvestedNode,
    HarvestWalk,
    IndexEntry,
    MetadataHealth,
)
from fdpindex.outbound import OutboundError
from fdpindex.rdf import ACCEPT, RdfParseError, parse_rdf

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.jobs import JobRunner
    from fdpindex.network import Expectations
    from fdpindex.outbound import OutboundClient
    from fdpindex.service import IndexChange
    from fdpindex.settings import BenchmarkSettings, SettingsService

log = structlog.get_logger(__name__)

FTR = Namespace("https://w3id.org/ftr#")
DCT = Namespace("http://purl.org/dc/terms/")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
APPLICABLE = (
    URIRef("https://w3id.org/dpv#IsApplicableFor"),
    URIRef("https://w3id.org/dpv#isApplicableFor"),
    URIRef("http://www.w3.org/ns/dpv#isApplicableFor"),
)
FRESH = timedelta(days=1)
FETCH_TIMEOUT = 30.0
# FAIR Champion allows a run 180 seconds; the answer needs a moment more.
CALL_TIMEOUT = 200.0
RETRIES = (timedelta(hours=1), timedelta(hours=6), timedelta(days=1))
MAX_ATTEMPTS = 4
FAILURES_TO_REST = 3
REST = timedelta(minutes=30)
PLAN_BATCH = 500
CACHE_SECONDS = 30.0
MAX_LOG = 20_000

_DCAT = "http://www.w3.org/ns/dcat#"
_FDP_O = "https://w3id.org/fdp/fdp-o#"
# Classes DCAT and schema.org agree on, as the index's levels.
CLASS_LEVELS: dict[str, str] = {
    _DCAT + "Dataset": "dataset",
    _DCAT + "DatasetSeries": "dataset",
    _DCAT + "Catalog": "catalog",
    _DCAT + "DataService": "dataservice",
    _DCAT + "Distribution": "distribution",
    _FDP_O + "FAIRDataPoint": "fdp",
    _FDP_O + "MetadataService": "fdp",
    **{f"{s}://schema.org/Dataset": "dataset" for s in ("http", "https")},
    **{f"{s}://schema.org/DataCatalog": "catalog" for s in ("http", "https")},
    **{f"{s}://schema.org/DataDownload": "distribution" for s in ("http", "https")},
}

# The runners the index can call, by address without scheme or trailing slash.
CHAMPIONS = frozenset({"w3id.org/fair-champion", "tools.ostrails.eu/champion"})
_SHEET = re.compile(r"^https://docs\.google\.com/spreadsheets/(d/[A-Za-z0-9_-]+)")


def _key(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{(parts.hostname or '').lower()}{parts.path.rstrip('/')}".lower()


def result_key(benchmark: str, record: str) -> str:
    return hashlib.sha256(f"{benchmark}\n{record}".encode()).hexdigest()


def champion_endpoint(runner: str, algorithm: str) -> str | None:
    """Where FAIR Champion runs ``algorithm``; ``None`` when it is not one of its sheets."""
    match = _SHEET.match(algorithm)
    if match is None:
        return None
    return f"{runner.rstrip('/')}/assess/algorithm/{match.group(1)}"


# --- the benchmark's description -------------------------------------------------------


@dataclass(frozen=True)
class Definition:
    """A benchmark as the index read it."""

    iri: str
    resolved: bool
    title: str | None = None
    landing_page: str | None = None
    runner: str | None = None
    algorithm: str | None = None
    applies_to: tuple[str, ...] = ()
    reason: str | None = None
    fetched_at: datetime | None = None

    @property
    def levels(self) -> frozenset[str]:
        return frozenset(CLASS_LEVELS[c] for c in self.applies_to if c in CLASS_LEVELS)

    @property
    def classes(self) -> frozenset[str]:
        return frozenset(c for c in self.applies_to if c not in CLASS_LEVELS)

    def applies(self, types: frozenset[str], level: str | None) -> bool:
        """Whether a record with these classes, at this level, is one the benchmark assesses."""
        if not self.applies_to:
            return level is not None
        return (level is not None and level in self.levels) or bool(types & self.classes)


def _text(graph: Graph, subject: URIRef, *predicates: URIRef) -> str | None:
    for predicate in predicates:
        values = list(graph.objects(subject, predicate))
        preferred = [v for v in values if isinstance(v, Literal) and v.language in (None, "en")]
        for value in preferred or values:
            text = str(value).strip()
            if text:
                return text[:512]
    return None


def _web(graph: Graph, subject: URIRef, predicate: URIRef) -> str | None:
    for value in graph.objects(subject, predicate):
        text = str(value).strip()
        if text.startswith(("http://", "https://")):
            return text[:2048]
    return None


def read_definition(graph: Graph, iri: str) -> Definition:
    """What a benchmark's description says, with the reason when it cannot be run."""
    subject = URIRef(iri)
    if (subject, None, None) not in graph:
        found = list(graph.subjects(RDF.type, FTR.Benchmark))
        if len(found) != 1 or not isinstance(found[0], URIRef):
            return Definition(iri, False, reason="not a benchmark description (no ftr:Benchmark)")
        subject = found[0]
    applies = sorted(
        {
            str(v)
            for p in APPLICABLE
            for v in graph.objects(subject, p)
            if str(v).startswith(("http://", "https://"))
        }
    )
    definition = Definition(
        iri,
        True,
        title=_text(graph, subject, DCT.title, RDFS.label),
        landing_page=_web(graph, subject, DCAT.landingPage),
        runner=_web(graph, subject, FTR.supportedBy),
        algorithm=_web(graph, subject, FTR.scoringFunction),
        applies_to=tuple(applies[:20]),
    )
    reason = None
    if definition.algorithm is None:
        reason = "names no scoring algorithm (ftr:scoringFunction)"
    elif definition.runner is None:
        reason = "names no runner (ftr:supportedBy)"
    elif _key(definition.runner) not in CHAMPIONS:
        reason = f"the index cannot call its runner, {definition.runner}"
    elif champion_endpoint(definition.runner, definition.algorithm) is None:
        reason = "its scoring algorithm is not one FAIR Champion runs (a Google Sheet)"
    if reason is None:
        return definition
    return Definition(
        iri,
        False,
        title=definition.title,
        landing_page=definition.landing_page,
        runner=definition.runner,
        algorithm=definition.algorithm,
        applies_to=definition.applies_to,
        reason=reason,
    )


def _definition(row: BenchmarkDefinition) -> Definition:
    return Definition(
        row.iri,
        row.status == "resolved",
        title=row.title,
        landing_page=row.landing_page,
        runner=row.runner,
        algorithm=row.algorithm,
        applies_to=tuple(row.applies_to or []),
        reason=row.error,
        fetched_at=row.fetched_at,
    )


# --- FAIR Champion's answer ------------------------------------------------------------


@dataclass
class Outcome:
    conditions: list[dict[str, Any]]
    tests: list[dict[str, Any]]
    result_set: bytes | None
    incomplete: bool


def _str(value: object, limit: int = 1000) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _guidance(value: object) -> list[dict[str, str]]:
    """FAIR Champion's guidance, ``[[url, text], …]`` or one ``[url, text]``, as links."""
    if not isinstance(value, list) or not value:
        return []
    values = cast("list[object]", value)
    items = values if all(isinstance(v, list) for v in values) else [values]
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, list) or not item:
            continue
        pair = cast("list[object]", item)
        url = _str(pair[0], 2048)
        if url is None or not url.startswith(("http://", "https://")):
            continue
        text = _str(pair[1], 300) if len(pair) > 1 else None
        out.append({"url": url, "text": text or url})
    return out[:5]


def _dict(value: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def read_champion(data: object) -> Outcome:
    """The conditions met, the tests' verdicts and the FTR result set from FAIR Champion's JSON.

    A condition's narrative is its success or its failure message, in the
    conditions' order; one that is neither (a formula FAIR Champion could not
    evaluate) leaves the condition undecided.
    """
    body = _dict(data)
    verdicts = _dict(body.get("test_results")) if body is not None else None
    if body is None or verdicts is None or not isinstance(body.get("conditions"), list):
        raise ValueError("not a FAIR Champion result (no conditions or test results)")
    narratives = _list(body.get("narratives"))
    guidances = _list(body.get("guidances"))
    conditions: list[dict[str, Any]] = []
    for i, raw in enumerate(_list(body.get("conditions"))[:50]):
        condition = _dict(raw)
        if condition is None:
            continue
        message = _str(narratives[i]) if i < len(narratives) else None
        success, failure = _str(condition.get("success")), _str(condition.get("failure"))
        met = True if message == success else False if message == failure else None
        conditions.append(
            {
                "id": _str(condition.get("condition"), 50) or f"C{i}",
                "description": _str(condition.get("description")) or "",
                "met": met,
                "message": message,
                "guidance": _guidance(guidances[i]) if i < len(guidances) else [],
            }
        )
    tests: list[dict[str, Any]] = []
    for raw in _list(body.get("tests"))[:50]:
        test = _dict(raw)
        if test is None:
            continue
        reference = _str(test.get("reference"), 50)
        verdict = _dict(verdicts.get(reference or "")) or {}
        weight = verdict.get("weight")
        tests.append(
            {
                "reference": reference,
                "title": _str(test.get("title"), 300),
                "test": _str(test.get("testid"), 2048),
                "metric": _str(test.get("metric_url"), 2048),
                "result": _str(verdict.get("result"), 20),
                "weight": float(weight) if isinstance(weight, int | float) else None,
            }
        )
    raw_set = body.get("resultset")
    text = raw_set if isinstance(raw_set, str) else json.dumps(raw_set) if raw_set else None
    incomplete = bool(body.get("has_errors")) or any(t["result"] == "error" for t in tests)
    return Outcome(
        conditions=conditions,
        tests=tests,
        result_set=gzip.compress(text.encode()) if text else None,
        incomplete=incomplete,
    )


def result_set_text(data: bytes | None) -> str | None:
    return gzip.decompress(data).decode() if data else None


def _value(node: dict[str, Any], key: str) -> str | None:
    found = node.get(key)
    inner = _dict(found)
    if inner is not None:
        found = inner.get("@value") or inner.get("@id")
    return found if isinstance(found, str) else None


def ftr_logs(result_set: str | None) -> dict[str, dict[str, str | None]]:
    """Each test's log and summary from an FTR result set, by test IRI."""
    if not result_set:
        return {}
    try:
        document = _dict(json.loads(result_set))
    except ValueError:
        return {}
    out: dict[str, dict[str, str | None]] = {}
    for raw in _list(document.get("@graph")) if document is not None else []:
        node = _dict(raw)
        if node is None:
            continue
        types: object = node.get("@type")
        if "ftr:TestResult" not in (_list(types) or [types]):
            continue
        test = _value(node, "ftr:outputFromTest")
        if test is not None:
            log_text = _value(node, "ftr:log")
            out[test] = {
                "log": log_text[:MAX_LOG] if log_text else None,
                "summary": _value(node, "ftr:summary"),
            }
    return out


# --- the service -----------------------------------------------------------------------


@dataclass
class _Rows:
    at: float
    rows: list[BenchmarkResult] = field(default_factory=lambda: [])


class BenchmarkService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SettingsService,
        outbound: OutboundClient | None,
        jobs: JobRunner,
        *,
        benchmarks: Callable[[], Awaitable[list[str]]],
        expectations: Callable[[], Awaitable[Expectations]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._outbound = outbound
        self._jobs = jobs
        self._benchmarks = benchmarks
        self._expectations = expectations
        self._locks: dict[str, asyncio.Lock] = {}
        self._working = False
        self._planning: set[UUID] = set()
        self._failures = 0
        self._rest_until = 0.0
        self._day: str | None = None
        self._calls_today = 0
        self._cache: dict[UUID, _Rows] = {}

    async def options(self) -> BenchmarkSettings:
        return (await self._settings.get()).benchmarks

    # --- definitions -------------------------------------------------------------------

    async def definitions(self) -> list[Definition]:
        """The network profile's benchmarks, read again when older than a day."""
        return [await self.definition(iri) for iri in await self._benchmarks()]

    async def definition(self, iri: str) -> Definition:
        """With runs off, nothing is fetched: what was read before, or nothing."""
        lock = self._locks.setdefault(iri, asyncio.Lock())
        async with lock:
            async with self._session_factory() as session:
                row = await session.get(BenchmarkDefinition, iri)
            if row is None or utcnow() - row.fetched_at > FRESH:
                if (await self.options()).mode == "off":
                    if row is None:
                        return Definition(iri, False, reason="not read: benchmark runs are off")
                    return _definition(row)
                row = await self._fetch(iri)
        return _definition(row)

    async def _fetch(self, iri: str) -> BenchmarkDefinition:
        try:
            graph = await self._get(iri)
            definition = read_definition(graph, iri)
        except _ReadError as err:
            definition = Definition(iri, False, reason=f"could not be read: {err}")
        row = BenchmarkDefinition(
            iri=iri,
            fetched_at=utcnow(),
            status="resolved" if definition.resolved else "unresolved",
            title=definition.title,
            landing_page=definition.landing_page,
            runner=definition.runner,
            algorithm=definition.algorithm,
            applies_to=list(definition.applies_to),
            error=definition.reason[:512] if definition.reason else None,
        )
        async with self._session_factory() as session, session.begin():
            await session.merge(row)
        log.info("benchmark_read", iri=iri, status=row.status, reason=row.error)
        return row

    async def _get(self, url: str) -> Graph:
        if self._outbound is None:
            raise _ReadError("the index makes no outbound requests here")
        try:
            response = await self._outbound.request(
                "GET", url, timeout_seconds=FETCH_TIMEOUT, headers={"Accept": ACCEPT}
            )
        except OutboundError as err:
            raise _ReadError(err.message) from err
        if not 200 <= response.status < 300:
            raise _ReadError(f"HTTP {response.status}")
        if response.truncated:
            raise _ReadError("response too large")
        try:
            return await asyncio.to_thread(parse_rdf, response.body, response.content_type, url)
        except RdfParseError as err:
            raise _ReadError(f"cannot parse: {err}") from err

    async def profile_changed(self) -> None:
        """The network profile changed: every FDP is planned again at the next sweep."""
        async with self._session_factory() as session, session.begin():
            await session.execute(delete(BenchmarkPlan))
        self._cache.clear()

    # --- planning ----------------------------------------------------------------------

    async def schedule(self, *, batch: int = 2) -> int:
        """Plan the FDPs that are due and start the calls; return how many were planned."""
        options = await self.options()
        if options.mode == "off":
            return 0
        started: list[UUID] = []
        if options.mode == "scheduled":
            now = utcnow()
            async with self._session_factory() as session:
                walking = (
                    select(HarvestWalk.entry_uuid)
                    .where(HarvestWalk.entry_uuid == MetadataHealth.entry_uuid)
                    .exists()
                )
                due = await session.scalars(
                    select(MetadataHealth.entry_uuid)
                    .join(IndexEntry, IndexEntry.uuid == MetadataHealth.entry_uuid)
                    .outerjoin(BenchmarkPlan, BenchmarkPlan.entry_uuid == MetadataHealth.entry_uuid)
                    .where(
                        IndexEntry.permit == EntryPermit.ACCEPTED,
                        IndexEntry.harvest_enabled.is_(True),
                        MetadataHealth.walk_finished_at.is_not(None),
                        ~walking,
                        or_(
                            BenchmarkPlan.entry_uuid.is_(None),
                            (
                                or_(
                                    BenchmarkPlan.walk_finished_at.is_(None),
                                    BenchmarkPlan.walk_finished_at
                                    < MetadataHealth.walk_finished_at,
                                )
                                & (BenchmarkPlan.planned_at < now - options.interval)
                            ),
                        ),
                    )
                    .order_by(MetadataHealth.walk_finished_at.desc())
                    .limit(batch + len(self._planning))
                )
                for entry_id in due:
                    if entry_id not in self._planning and len(started) < batch:
                        started.append(entry_id)
            for entry_id in started:
                self._planning.add(entry_id)
                self._jobs.submit(f"benchmark-plan:{entry_id}", lambda e=entry_id: self._plan(e))
        await self.kick()
        return len(started)

    async def _plan(self, entry_id: UUID) -> None:
        try:
            await self.plan(entry_id)
        finally:
            self._planning.discard(entry_id)
        await self.kick()

    async def plan(self, entry_id: UUID) -> int:
        """Compare the FDP's records with their results; return how many became due."""
        definitions = [d for d in await self.definitions() if d.resolved]
        async with self._session_factory() as session:
            entry = await session.get(IndexEntry, entry_id)
            health = await session.get(MetadataHealth, entry_id)
            if entry is None:
                return 0
            client_url = entry.client_url
            walk_finished_at = health.walk_finished_at if health is not None else None
        expected = await self._expectations() if self._expectations is not None else None
        extra = expected.types if expected is not None else {}
        root = page_key(client_url)
        wanted: dict[str, tuple[str, str, str]] = {}  # key -> (benchmark, record, hash)
        records: set[str] = set()
        last = 0
        while definitions:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            HarvestedNode.id,
                            HarvestedNode.node_iri,
                            HarvestedNode.content_hash,
                            HarvestedNode.ntriples,
                        )
                        .where(HarvestedNode.entry_uuid == entry_id, HarvestedNode.id > last)
                        .order_by(HarvestedNode.id)
                        .limit(PLAN_BATCH)
                    )
                ).all()
            if not rows:
                break
            last = rows[-1][0]

            def classify(
                batch: list[tuple[str, str, str]],
            ) -> list[tuple[str, str, frozenset[str], str | None]]:
                out: list[tuple[str, str, frozenset[str], str | None]] = []
                for iri, digest, text in batch:
                    page = read_page(iri, text, root=page_key(iri) == root)
                    out.append((iri, digest, page.types, level_of(page.types, page.root, extra)))
                return out

            batch = [(str(iri), str(digest), str(text)) for _id, iri, digest, text in rows]
            for iri, digest, types, level in await asyncio.to_thread(classify, batch):
                records.add(iri)
                for definition in definitions:
                    if definition.applies(types, level):
                        wanted[result_key(definition.iri, iri)] = (definition.iri, iri, digest)
        algorithms = {d.iri: d.algorithm for d in definitions}
        due = 0
        async with self._session_factory() as session, session.begin():
            existing = {
                row.key: row
                for row in await session.scalars(
                    select(BenchmarkResult).where(BenchmarkResult.entry_uuid == entry_id)
                )
            }
            hashes = {
                str(iri): str(digest)
                for iri, digest in (
                    await session.execute(
                        select(HarvestedNode.node_iri, HarvestedNode.content_hash).where(
                            HarvestedNode.entry_uuid == entry_id
                        )
                    )
                ).all()
            }
            for key, row in existing.items():
                if row.benchmark_iri not in algorithms or row.record_iri not in records:
                    await session.delete(row)
                    continue
                changed = (
                    row.content_hash != hashes.get(row.record_iri)
                    or row.algorithm != algorithms[row.benchmark_iri]
                )
                if changed and row.state != "due":
                    row.state, row.attempts, row.next_try_at = "due", 0, None
                    due += 1
                wanted.pop(key, None)
            for key, (benchmark, record, _digest) in wanted.items():
                session.add(
                    BenchmarkResult(
                        entry_uuid=entry_id,
                        benchmark_iri=benchmark,
                        record_iri=record,
                        key=key,
                        state="due",
                        priority=False,
                        attempts=0,
                        incomplete=False,
                    )
                )
                due += 1
            plan = await session.get(BenchmarkPlan, entry_id)
            if plan is None:
                session.add(
                    BenchmarkPlan(
                        entry_uuid=entry_id, planned_at=utcnow(), walk_finished_at=walk_finished_at
                    )
                )
            else:
                plan.planned_at, plan.walk_finished_at = utcnow(), walk_finished_at
        self._cache.pop(entry_id, None)
        log.info("benchmarks_planned", entry=str(entry_id), due=due, records=len(records))
        return due

    async def request(
        self, entry_id: UUID, *, record: str | None = None, benchmark: str | None = None
    ) -> int:
        """An administrator's run on one record (the FDP's root by default); return how
        many calls were queued."""
        if (await self.options()).mode == "off":
            raise Conflict("Benchmark runs are off: switch them on in the settings first")
        definitions = [d for d in await self.definitions() if d.resolved]
        if benchmark is not None:
            definitions = [d for d in definitions if d.iri == benchmark]
            if not definitions:
                raise BadRequest("Not a benchmark of the network profile that the index can run")
        if not definitions:
            raise BadRequest("The network profile names no benchmark the index can run")
        async with self._session_factory() as session, session.begin():
            entry = await session.get(IndexEntry, entry_id)
            if entry is None:
                raise NotFound("Index entry not found")
            wanted = page_key(record or entry.client_url)
            nodes = (
                await session.execute(
                    select(HarvestedNode.node_iri).where(HarvestedNode.entry_uuid == entry_id)
                )
            ).all()
            found = next((str(iri) for (iri,) in nodes if page_key(str(iri)) == wanted), None)
            if found is None:
                raise BadRequest("Not a record the index harvested from this FDP")
            for definition in definitions:
                key = result_key(definition.iri, found)
                row = await session.scalar(
                    select(BenchmarkResult).where(
                        BenchmarkResult.entry_uuid == entry_id, BenchmarkResult.key == key
                    )
                )
                if row is None:
                    session.add(
                        BenchmarkResult(
                            entry_uuid=entry_id,
                            benchmark_iri=definition.iri,
                            record_iri=found,
                            key=key,
                            state="due",
                            priority=True,
                            attempts=0,
                            incomplete=False,
                        )
                    )
                else:
                    row.state, row.priority, row.attempts, row.next_try_at = "due", True, 0, None
        self._cache.pop(entry_id, None)
        self._rest_until = 0.0
        await self.kick()
        return len(definitions)

    def index_changed(self, change: IndexChange) -> None:
        """An FDP's harvested metadata is gone (rejected, deleted, directory only): its
        results went with it (``IndexService.delete_harvested``)."""
        if change.kind == "removed" and change.entry_id is not None:
            self._cache.pop(change.entry_id, None)

    # --- calls -------------------------------------------------------------------------

    async def kick(self) -> None:
        """Start the calls, unless they run already, rest, or nothing is due."""
        if self._working or time.monotonic() < self._rest_until:
            return
        options = await self.options()
        if options.mode == "off" or await self._next(options) is None:
            return
        self._working = True
        self._jobs.submit("benchmark-call", self._step)

    async def _step(self) -> None:
        again = False
        pause = 0.0
        try:
            options = await self.options()
            pause = float(options.pause_seconds)
            again = await self.call_next(options)
        finally:
            if again:
                self._jobs.submit_after("benchmark-call", pause, self._step)
            else:
                self._working = False

    async def _calls(self) -> int:
        today = utcnow().date().isoformat()
        if self._day != today:
            start = datetime.fromisoformat(today).replace(tzinfo=utcnow().tzinfo)
            async with self._session_factory() as session:
                self._calls_today = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(BenchmarkResult)
                        .where(BenchmarkResult.attempted_at >= start)
                    )
                    or 0
                )
            self._day = today
        return self._calls_today

    async def _next(self, options: BenchmarkSettings) -> BenchmarkResult | None:
        async with self._session_factory() as session:
            query = (
                select(BenchmarkResult)
                .join(IndexEntry, IndexEntry.uuid == BenchmarkResult.entry_uuid)
                .where(
                    BenchmarkResult.state == "due",
                    or_(
                        BenchmarkResult.next_try_at.is_(None),
                        BenchmarkResult.next_try_at <= utcnow(),
                    ),
                    IndexEntry.permit == EntryPermit.ACCEPTED,
                )
                .order_by(
                    BenchmarkResult.priority.desc(),
                    BenchmarkResult.assessed_at.is_not(None),
                    BenchmarkResult.attempts,
                    BenchmarkResult.id,
                )
                .limit(1)
            )
            if options.mode == "onRequest":
                query = query.where(BenchmarkResult.priority.is_(True))
            return await session.scalar(query)

    async def call_next(self, options: BenchmarkSettings | None = None) -> bool:
        """Make the next call that is due; return whether one was made."""
        options = options or await self.options()
        if options.mode == "off" or time.monotonic() < self._rest_until:
            return False
        if await self._calls() >= options.daily_calls:
            log.info("benchmark_daily_cap", calls=self._calls_today)
            return False
        row = await self._next(options)
        if row is None:
            return False
        definition = await self.definition(row.benchmark_iri)
        now = utcnow()
        if not definition.resolved or definition.runner is None or definition.algorithm is None:
            await self._store(row.id, state="failed", error=definition.reason, attempted_at=now)
            return True
        runner = options.runner or definition.runner
        async with self._session_factory() as session:
            digest = await session.scalar(
                select(HarvestedNode.content_hash).where(
                    HarvestedNode.entry_uuid == row.entry_uuid,
                    HarvestedNode.node_iri == row.record_iri,
                )
            )
        self._calls_today += 1
        try:
            outcome = await self._call(runner, definition.algorithm, row.record_iri)
        except _CallError as err:
            self._failures += 1
            attempts = row.attempts + 1
            gave_up = attempts >= MAX_ATTEMPTS
            await self._store(
                row.id,
                state="failed" if gave_up else "due",
                attempts=attempts,
                next_try_at=None if gave_up else now + RETRIES[min(attempts, len(RETRIES)) - 1],
                attempted_at=now,
                error=str(err)[:512],
            )
            log.warning(
                "benchmark_call_failed", record=row.record_iri, runner=runner, error=str(err)
            )
            if self._failures >= FAILURES_TO_REST:
                self._rest_until = time.monotonic() + REST.total_seconds()
                self._failures = 0
                return False
            return True
        self._failures = 0
        await self._store(
            row.id,
            state="done",
            priority=False,
            attempts=0,
            next_try_at=None,
            attempted_at=now,
            assessed_at=utcnow(),
            content_hash=digest,
            runner=runner,
            algorithm=definition.algorithm,
            incomplete=outcome.incomplete,
            conditions=outcome.conditions,
            tests=outcome.tests,
            result_set=outcome.result_set,
            error=None,
        )
        log.info(
            "benchmark_assessed",
            record=row.record_iri,
            met=sum(1 for c in outcome.conditions if c["met"]),
            conditions=len(outcome.conditions),
        )
        return True

    async def _store(self, result_id: UUID, **values: Any) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(BenchmarkResult).where(BenchmarkResult.id == result_id).values(**values)
            )
            entry_id = await session.scalar(
                select(BenchmarkResult.entry_uuid).where(BenchmarkResult.id == result_id)
            )
        if entry_id is not None:
            self._cache.pop(entry_id, None)

    async def _call(self, runner: str, algorithm: str, record: str) -> Outcome:
        endpoint = champion_endpoint(runner, algorithm)
        if endpoint is None or self._outbound is None:
            raise _CallError("the index cannot call this runner")
        try:
            response = await self._outbound.request(
                "POST",
                endpoint,
                timeout_seconds=CALL_TIMEOUT,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                content=json.dumps({"guid": record}),
            )
        except OutboundError as err:
            raise _CallError(err.message) from err
        if response.status not in (200, 207):
            raise _CallError(f"HTTP {response.status}")
        if response.truncated:
            raise _CallError("response too large")
        try:
            return read_champion(json.loads(response.body))
        except ValueError as err:
            raise _CallError(f"cannot read the answer: {err}") from err

    # --- reading -----------------------------------------------------------------------

    async def _rows(self, entry_id: UUID) -> list[BenchmarkResult]:
        cached = self._cache.get(entry_id)
        if cached is not None and time.monotonic() - cached.at < CACHE_SECONDS:
            return cached.rows
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(BenchmarkResult)
                    .options(defer(BenchmarkResult.result_set, raiseload=True))
                    .where(BenchmarkResult.entry_uuid == entry_id)
                    .order_by(BenchmarkResult.record_iri)
                )
            )
        self._cache[entry_id] = _Rows(time.monotonic(), rows)
        return rows

    async def summary(self, entry_id: UUID, client_url: str) -> dict[str, Any]:
        """Per benchmark: what it is, how many records are assessed, and per condition how
        many records meet it; the FDP's root result when there is one."""
        options = await self.options()
        rows = await self._rows(entry_id)
        root = page_key(client_url)
        out: list[dict[str, Any]] = []
        for definition in await self.definitions():
            mine = [r for r in rows if r.benchmark_iri == definition.iri]
            assessed = [r for r in mine if r.assessed_at is not None]
            out.append(
                {
                    **_definition_view(definition),
                    "records": {
                        "total": len(mine),
                        "assessed": len(assessed),
                        "due": sum(1 for r in mine if r.state == "due"),
                        "failed": sum(1 for r in mine if r.state == "failed"),
                    },
                    "lastAssessedAt": max(
                        (r.assessed_at for r in assessed if r.assessed_at is not None),
                        default=None,
                    ),
                    "conditions": _condition_counts(assessed),
                    "root": next(
                        (_result_view(r) for r in mine if page_key(r.record_iri) == root), None
                    ),
                }
            )
        return {"mode": options.mode, "benchmarks": out}

    async def results(
        self,
        entry_id: UUID,
        *,
        benchmark: str | None,
        condition: str | None,
        met: bool | None,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = await self._rows(entry_id)
        if benchmark is not None:
            rows = [r for r in rows if r.benchmark_iri == benchmark]
        if condition is not None or met is not None:
            rows = [r for r in rows if _matches(r, condition, met)]
        return [_result_view(r) for r in rows[page * size : (page + 1) * size]], len(rows)

    async def result(self, result_id: UUID) -> tuple[BenchmarkResult, dict[str, Any]]:
        """One result with every test's log, and its entry."""
        async with self._session_factory() as session:
            row = await session.get(BenchmarkResult, result_id)
        if row is None:
            raise NotFound("Benchmark result not found")
        text = await asyncio.to_thread(result_set_text, row.result_set)
        logs = await asyncio.to_thread(ftr_logs, text)
        view = _result_view(row)
        view["benchmark"] = row.benchmark_iri
        view["entry"] = str(row.entry_uuid)
        view["tests"] = [{**t, **logs.get(t.get("test") or "", {})} for t in row.tests or []]
        view["hasResultSet"] = row.result_set is not None
        return row, view

    async def network(self, entry_ids: Iterable[UUID]) -> list[dict[str, Any]]:
        """Per benchmark and condition: FDPs whose assessed records all meet it, FDPs with
        a result, records that meet it and records assessed (Statistics page)."""
        ids = list(entry_ids)
        definitions = await self.definitions()
        if not definitions or not ids:
            return []
        per: dict[tuple[str, UUID], list[list[dict[str, Any]]]] = defaultdict(list)
        async with self._session_factory() as session:
            for start in range(0, len(ids), 500):
                rows = await session.execute(
                    select(
                        BenchmarkResult.benchmark_iri,
                        BenchmarkResult.entry_uuid,
                        BenchmarkResult.conditions,
                    ).where(
                        BenchmarkResult.entry_uuid.in_(ids[start : start + 500]),
                        BenchmarkResult.assessed_at.is_not(None),
                    )
                )
                for iri, entry_id, conditions in rows.all():
                    per[(str(iri), entry_id)].append(list(conditions or []))
        out: list[dict[str, Any]] = []
        for definition in definitions:
            order: list[str] = []
            descriptions: dict[str, str] = {}
            counts: dict[str, dict[str, int]] = {}
            fdps = 0
            for (iri, _entry), results in per.items():
                if iri != definition.iri:
                    continue
                fdps += 1
                every: dict[str, bool] = {}
                for conditions in results:
                    for c in conditions:
                        cid = str(c.get("id"))
                        if cid not in counts:
                            order.append(cid)
                            descriptions[cid] = str(c.get("description") or "")
                            counts[cid] = {"fdps": 0, "records": 0, "assessed": 0}
                        counts[cid]["assessed"] += 1
                        counts[cid]["records"] += 1 if c.get("met") is True else 0
                        every[cid] = every.get(cid, True) and c.get("met") is True
                for cid, all_met in every.items():
                    counts[cid]["fdps"] += 1 if all_met else 0
            out.append(
                {
                    **_definition_view(definition),
                    "fdps": fdps,
                    "conditions": [
                        {"id": cid, "description": descriptions[cid], **counts[cid]}
                        for cid in order
                    ],
                }
            )
        return out


class _ReadError(Exception):
    pass


class _CallError(Exception):
    pass


def _definition_view(definition: Definition) -> dict[str, Any]:
    return {
        "iri": definition.iri,
        "title": definition.title,
        "landingPage": definition.landing_page,
        "runner": definition.runner,
        "algorithm": definition.algorithm,
        "appliesTo": list(definition.applies_to),
        "runnable": definition.resolved,
        "reason": definition.reason,
    }


def _result_view(row: BenchmarkResult) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "record": row.record_iri,
        "benchmark": row.benchmark_iri,
        "state": row.state,
        "assessedAt": row.assessed_at,
        "attemptedAt": row.attempted_at,
        "runner": row.runner,
        "incomplete": row.incomplete,
        "conditions": list(row.conditions or []),
        "tests": [
            {k: t.get(k) for k in ("reference", "title", "test", "metric", "result")}
            for t in row.tests or []
        ],
        "error": row.error,
    }


def _condition_counts(rows: list[BenchmarkResult]) -> list[dict[str, Any]]:
    order: list[str] = []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        for c in row.conditions or []:
            cid = str(c.get("id"))
            if cid not in out:
                order.append(cid)
                out[cid] = {
                    "id": cid,
                    "description": c.get("description") or "",
                    "met": 0,
                    "notMet": 0,
                    "undecided": 0,
                }
            key = (
                "met"
                if c.get("met") is True
                else "notMet"
                if c.get("met") is False
                else "undecided"
            )
            out[cid][key] += 1
    return [out[cid] for cid in order]


def _matches(row: BenchmarkResult, condition: str | None, met: bool | None) -> bool:
    if row.assessed_at is None:
        return False
    for c in row.conditions or []:
        if condition is not None and str(c.get("id")) != condition:
            continue
        if met is None or c.get("met") is met:
            return True
    return False
