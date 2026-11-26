"""Statistics on the index (TASKS 15.1): what it holds now, and how that changed.

Counted as visitors see the index: FDPs as the public FDP list counts them
(accepted, expired ones left out when the policy hides them, the directory's
minimum conformance), records as visitors' searches find them
(``SearchService.visitor_conditions``). Conformance shares and metadata
health (TASKS 15.8) follow the ``reports`` setting: with admin-only reports,
only administrators get them.

History comes from two places:

* **Daily snapshots** (``stats_daily``): every ``stats_snapshot_seconds`` the
  day's counts are taken again, so a day holds its last snapshot. Nothing
  earlier than the upgrade that introduced them can be recovered: harvests
  replace records and state changes overwrite each other.
* **Dates the data carries**: FDPs' registration dates (by month) and the
  datasets' own ``dct:issued`` (by year), which reach back before the index.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import delete, func, select

from fdpindex.conformance.service import DECLARED, DECLARED_TITLE, NETWORK, NETWORK_TITLE
from fdpindex.db import utcnow
from fdpindex.findings import FAILURE_KINDS, FINDINGS, LEVELS, NOTES
from fdpindex.geo import SOURCES, spatial_country
from fdpindex.models import (
    ConformanceResult,
    EntryPermit,
    IndexEntry,
    SearchResource,
    SearchValue,
    StatsDaily,
)
from fdpindex.service import API_STATES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.benchmarks import BenchmarkService
    from fdpindex.conformance.service import ConformanceService
    from fdpindex.search.query import SearchService
    from fdpindex.service import IndexService

log = structlog.get_logger(__name__)

R = SearchResource
E = IndexEntry

# Record kinds in display order (the search profile's, ``other`` last).
KINDS = ("fdp", "catalog", "dataset", "series", "service", "distribution", "other")
# FDPs with the most records, listed.
LARGEST = 10
# Years of dct:issued outside this range are typing errors, not history.
FIRST_YEAR = 1950
CACHE_SECONDS = 60


class StatsService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        index: IndexService,
        search: SearchService | None,
        conformance: ConformanceService | None,
        benchmarks: BenchmarkService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._benchmarks = benchmarks
        self._index = index
        self._search = search
        self._conformance = conformance
        self._cache: dict[tuple[bool, bool, int], tuple[float, dict[str, Any]]] = {}

    def invalidate(self) -> None:
        """Forget the cached view (taken again within a minute otherwise)."""
        self._cache.clear()

    # --- what visitors may read ---------------------------------------------------------

    async def conformance_readable(self, *, admin: bool) -> bool:
        if self._conformance is None:
            return False
        if admin:
            return True
        return (await self._conformance.settings.get()).reports == "public"

    async def health_readable(self, *, admin: bool) -> bool:
        """Metadata health follows the conformance reports' ``reports`` setting."""
        if admin or self._conformance is None:
            return True
        return (await self._conformance.settings.get()).reports == "public"

    async def view(self, *, admin: bool, days: int) -> dict[str, Any]:
        """The Statistics page's data, cached for a minute."""
        readable = await self.conformance_readable(admin=admin)
        health = await self.health_readable(admin=admin)
        key = (readable, health, days)
        cached = self._cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        current = await self.current(conformance=readable, health=health)
        result: dict[str, Any] = {
            "generatedAt": utcnow().isoformat(),
            **current,
            "registrations": await self.registrations(),
            "published": await self.published(),
            "history": await self.history(days, conformance=readable, health=health),
        }
        self._cache[key] = (time.monotonic(), result)
        return result

    # --- now ----------------------------------------------------------------------------

    async def current(self, *, conformance: bool = True, health: bool = True) -> dict[str, Any]:
        counts = await self._index.counts([EntryPermit.ACCEPTED], public=True)
        by_country, unplaced = await self._fdp_countries()
        result: dict[str, Any] = {
            "fdps": {
                "total": counts["ALL"],
                "byState": {state: counts[state] for state in API_STATES},
                "byCountry": by_country,
                "unplaced": unplaced,
            },
            "records": None,
            "largest": [],
            "conformance": None,
            "health": await self.health_summary() if health else None,
            "benchmarks": await self.benchmark_summary() if health else None,
        }
        if self._search is None:
            return result
        visible = await self._search.visitor_conditions()
        async with self._session_factory() as session:
            kinds = dict(
                (
                    await session.execute(
                        select(R.kind, func.count())
                        .join(E, E.uuid == R.entry_uuid)
                        .where(*visible)
                        .group_by(R.kind)
                    )
                ).all()
            )
            by_kind = {kind: int(kinds.get(kind, 0)) for kind in KINDS}
            result["records"] = {
                "total": sum(by_kind.values()),
                "byKind": by_kind,
                "byCountry": await self._record_countries(session, visible),
            }
            result["largest"] = await self._largest(session, visible)
            if conformance and self._conformance is not None:
                result["conformance"] = await self._conformance_shares(session, visible)
        return result

    async def _fdp_countries(self) -> tuple[list[dict[str, Any]], int]:
        """FDPs in the public list by country, with the sources that placed them (geo.py)."""
        entries = await self._index.all_entries([EntryPermit.ACCEPTED], public=True)
        located = await self._index.locate(entries)
        per: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(SOURCES, 0))
        unplaced = 0
        for found in located.values():
            if found is None:
                unplaced += 1
            else:
                per[found[0]][found[1]] += 1
        ordered = sorted(per.items(), key=lambda item: (-sum(item[1].values()), item[0]))
        rows: list[dict[str, Any]] = [
            {"country": country, "fdps": sum(sources.values()), "bySource": sources}
            for country, sources in ordered
        ]
        return rows, unplaced

    async def _record_countries(
        self, session: AsyncSession, visible: list[Any]
    ) -> list[dict[str, Any]]:
        """Records visitors find by the country their ``dct:spatial`` names; a record about
        several regions of one country counts once there. ``values`` are the spatial IRIs
        seen for the country, for a search link (at most 50)."""
        rows = (
            await session.execute(
                select(SearchValue.resource_id, SearchValue.value)
                .join(R, R.id == SearchValue.resource_id)
                .join(E, E.uuid == R.entry_uuid)
                .where(*visible, SearchValue.field == "spatial")
            )
        ).all()
        records: dict[str, set[int]] = defaultdict(set)
        values: dict[str, set[str]] = defaultdict(set)
        for resource_id, value in rows:
            country = spatial_country(str(value))
            if country is not None:
                records[country].add(int(resource_id))
                values[country].add(str(value))
        ordered = sorted(records.items(), key=lambda item: (-len(item[1]), item[0]))
        return [
            {"country": country, "records": len(ids), "values": sorted(values[country])[:50]}
            for country, ids in ordered
        ]

    async def _largest(self, session: AsyncSession, visible: list[Any]) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(R.entry_uuid, E.client_url, func.count().label("n"))
                .join(E, E.uuid == R.entry_uuid)
                .where(*visible, R.kind != "fdp")
                .group_by(R.entry_uuid, E.client_url)
                .order_by(func.count().desc(), E.client_url)
                .limit(LARGEST)
            )
        ).all()
        ids = [row[0] for row in rows]
        titles = dict(
            (
                await session.execute(
                    select(R.entry_uuid, R.title).where(R.entry_uuid.in_(ids), R.kind == "fdp")
                )
            ).all()
        )
        return [
            {
                "uuid": str(uuid),
                "clientUrl": client_url,
                "title": titles.get(uuid) or None,
                "records": int(n),
            }
            for uuid, client_url, n in rows
        ]

    async def _conformance_shares(
        self, session: AsyncSession, visible: list[Any]
    ) -> list[dict[str, Any]]:
        """Per active profile: of the records visitors find, how many were checked and met it."""
        assert self._conformance is not None
        specs = await self._conformance.specs()
        roles = await self._conformance.roles()
        declared_on = await self._conformance.declared_on()
        network_on = await self._conformance.network_on()
        wanted = [*specs, *([DECLARED] if declared_on else []), *([NETWORK] if network_on else [])]
        rows = (
            await session.execute(
                select(
                    ConformanceResult.pack,
                    ConformanceResult.kind,
                    ConformanceResult.status,
                    func.count(),
                )
                .join(R, R.public_id == ConformanceResult.public_id)
                .join(E, E.uuid == R.entry_uuid)
                .where(*visible, ConformanceResult.pack.in_(wanted))
                .group_by(ConformanceResult.pack, ConformanceResult.kind, ConformanceResult.status)
            )
        ).all()
        checked: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        conforming: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        undeclared = 0
        for pack, kind, status, n in rows:
            if status == "undeclared":
                undeclared += int(n)
                continue  # nothing to check: not counted as checked
            checked[pack][kind] += int(n)
            if status == "conforms":
                conforming[pack][kind] += int(n)
        shares: list[dict[str, Any]] = []
        if declared_on:
            # What records declare (TASKS 15.12): first, as the default judgement.
            shares.append(
                {
                    "pack": DECLARED,
                    "role": "declared",
                    "specification": "",
                    "title": DECLARED_TITLE,
                    "checked": sum(checked[DECLARED].values()),
                    "conforms": sum(conforming[DECLARED].values()),
                    "undeclared": undeclared,
                    "byKind": {
                        kind: {
                            "checked": checked[DECLARED][kind],
                            "conforms": conforming[DECLARED][kind],
                        }
                        for kind in KINDS
                        if checked[DECLARED][kind]
                    },
                }
            )
        if network_on:
            # The network profile's SHACL (TASKS 15.9): required of every record.
            shares.append(
                {
                    "pack": NETWORK,
                    "role": "required",
                    "specification": "",
                    "title": NETWORK_TITLE,
                    "checked": sum(checked[NETWORK].values()),
                    "conforms": sum(conforming[NETWORK].values()),
                    "byKind": {
                        kind: {
                            "checked": checked[NETWORK][kind],
                            "conforms": conforming[NETWORK][kind],
                        }
                        for kind in KINDS
                        if checked[NETWORK][kind]
                    },
                }
            )
        return shares + [
            {
                "pack": pack,
                "role": roles.get(pack, "required"),
                "specification": specification,
                "title": title,
                "checked": sum(checked[pack].values()),
                "conforms": sum(conforming[pack].values()),
                "byKind": {
                    kind: {"checked": checked[pack][kind], "conforms": conforming[pack][kind]}
                    for kind in KINDS
                    if checked[pack][kind]
                },
            }
            for pack, (specification, title) in specs.items()
        ]

    # --- metadata health (TASKS 15.8) -----------------------------------------------------

    async def _health_reports(self) -> list[tuple[IndexEntry, dict[str, Any]]]:
        entries = await self._index.all_entries([EntryPermit.ACCEPTED], public=True)
        reports = await self._index.health_reports([e.uuid for e in entries])
        return [(e, reports[e.uuid].report or {}) for e in entries if e.uuid in reports]

    async def health_summary(self) -> dict[str, Any]:
        """FDPs in the public list by reach and status, and how many have each finding."""
        rows = await self._health_reports()
        by_reach = dict.fromkeys(("none", *LEVELS), 0)
        by_status = dict.fromkeys(("fine", "findings", "unreadable"), 0)
        findings = dict.fromkeys(FINDINGS, 0)
        notes = dict.fromkeys(NOTES, 0)
        failures = dict.fromkeys(FAILURE_KINDS, 0)
        partial = 0
        missing_requirements = 0
        for _entry, report in rows:
            if report.get("requirements"):
                missing_requirements += 1
            by_reach[report.get("reach") or "none"] += 1
            by_status[report.get("status") or "unreadable"] += 1
            found: list[dict[str, Any]] = report.get("findings") or []
            noted: list[dict[str, Any]] = report.get("notes") or []
            failed: list[dict[str, Any]] = report.get("failures") or []
            stops: dict[str, Any] = report.get("stops") or {}
            for finding in found:
                if finding["code"] in findings:
                    findings[finding["code"]] += 1
            for note in noted:
                if note["code"] in notes:
                    notes[note["code"]] += 1
            for failure in failed:
                if failure["kind"] in failures:
                    failures[failure["kind"]] += int(failure["count"])
            if stops.get("atCeiling"):
                partial += 1
        return {
            "fdps": len(rows),
            "byReach": by_reach,
            "byStatus": by_status,
            "findings": findings,
            "notes": notes,
            "failures": failures,
            "partial": partial,
            "missingRequirements": missing_requirements,
        }

    async def health_table(self) -> list[dict[str, Any]]:
        """One row per FDP in the public list that has a report, for the Statistics table."""
        rows = await self._health_reports()
        out: list[dict[str, Any]] = []
        for entry, report in rows:
            levels: dict[str, Any] = report.get("levels") or {}
            found: list[dict[str, Any]] = report.get("findings") or []
            noted: list[dict[str, Any]] = report.get("notes") or []
            failed: list[dict[str, Any]] = report.get("failures") or []
            stops: dict[str, Any] = report.get("stops") or {}
            out.append(
                {
                    "uuid": str(entry.uuid),
                    "clientUrl": entry.client_url,
                    "title": (entry.repository_metadata or {}).get("title"),
                    "reach": report.get("reach"),
                    "status": report.get("status") or "unreadable",
                    "pages": int(report.get("pages") or 0),
                    "levels": {name: int(levels.get(name) or 0) for name in LEVELS},
                    "findings": {f["code"]: int(f["count"]) for f in found},
                    "notes": {f["code"]: int(f["count"]) for f in noted},
                    "failed": sum(int(f["count"]) for f in failed),
                    "requirements": len(report.get("requirements") or []),
                    "partial": bool(stops.get("atCeiling")),
                }
            )
        out.sort(key=lambda row: (row["title"] or row["clientUrl"]).lower())
        return out

    async def benchmark_summary(self) -> list[dict[str, Any]] | None:
        """The network's benchmarks over the public list (TASKS 15.10); none while off."""
        if self._benchmarks is None or (await self._benchmarks.options()).mode == "off":
            return None
        entries = await self._index.all_entries([EntryPermit.ACCEPTED], public=True)
        return await self._benchmarks.network(e.uuid for e in entries)

    # --- dates the data carries ---------------------------------------------------------

    async def registrations(self) -> list[dict[str, Any]]:
        """FDPs in the public list by month of registration, every month from the first."""
        hidden = await self._index.public_conditions()
        async with self._session_factory() as session:
            created = list(
                await session.scalars(
                    select(E.created_at).where(E.permit == EntryPermit.ACCEPTED, *hidden)
                )
            )
        if not created:
            return []
        per_month: dict[str, int] = defaultdict(int)
        for moment in created:
            per_month[f"{moment.year:04d}-{moment.month:02d}"] += 1
        first = min(created)
        year, month = first.year, first.month
        now = utcnow()
        result: list[dict[str, Any]] = []
        while (year, month) <= (now.year, now.month):
            key = f"{year:04d}-{month:02d}"
            result.append({"month": key, "count": per_month.get(key, 0)})
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return result

    async def published(self) -> list[dict[str, Any]]:
        """Datasets visitors find, by the year of their ``dct:issued``, every year from the first."""
        if self._search is None:
            return []
        visible = await self._search.visitor_conditions()
        async with self._session_factory() as session:
            issued = list(
                await session.scalars(
                    select(R.issued)
                    .join(E, E.uuid == R.entry_uuid)
                    .where(*visible, R.kind == "dataset", R.issued.is_not(None))
                )
            )
        last = utcnow().year
        per_year: dict[int, int] = defaultdict(int)
        for moment in issued:
            if moment is not None and FIRST_YEAR <= moment.year <= last:
                per_year[moment.year] += 1
        if not per_year:
            return []
        return [{"year": y, "count": per_year.get(y, 0)} for y in range(min(per_year), last + 1)]

    # --- history ------------------------------------------------------------------------

    async def snapshot(self, day: date | None = None) -> dict[str, int]:
        """Count today's statistics and keep them as the day's values."""
        day = day or utcnow().date()
        current = await self.current(conformance=True)
        metrics: dict[str, int] = {"fdps.total": current["fdps"]["total"]}
        for state, n in current["fdps"]["byState"].items():
            metrics[f"fdps.state.{state}"] = n
        records = current["records"]
        if records is not None:
            metrics["records.total"] = records["total"]
            for kind, n in records["byKind"].items():
                metrics[f"records.kind.{kind}"] = n
        health: dict[str, Any] | None = current["health"]
        if health is not None:
            for reach, n in health["byReach"].items():
                metrics[f"health.reach.{reach}"] = n
            for status, n in health["byStatus"].items():
                metrics[f"health.status.{status}"] = n
        benchmarks: list[dict[str, Any]] = current["benchmarks"] or []
        for benchmark in benchmarks:
            tag = hashlib.sha256(benchmark["iri"].encode()).hexdigest()[:8]
            metrics[f"health.benchmark.{tag}.fdps"] = benchmark["fdps"]
            conditions: list[dict[str, Any]] = benchmark["conditions"]
            for condition in conditions:
                for count in ("fdps", "records", "assessed"):
                    metrics[f"health.benchmark.{tag}.{condition['id']}.{count}"] = condition[count]
        shares: list[dict[str, Any]] = current["conformance"] or []
        for share in shares:
            metrics[f"conformance.{share['pack']}.checked"] = share["checked"]
            metrics[f"conformance.{share['pack']}.conforms"] = share["conforms"]
        async with self._session_factory() as session, session.begin():
            await session.execute(delete(StatsDaily).where(StatsDaily.day == day))
            session.add_all(StatsDaily(day=day, metric=m, value=v) for m, v in metrics.items())
        self.invalidate()
        return metrics

    async def history(
        self, days: int, *, conformance: bool = True, health: bool = True
    ) -> dict[str, Any]:
        """Every day from the first snapshot within ``days`` until today; ``None`` where
        no snapshot was taken (the index was down), so charts show the gap."""
        today = utcnow().date()
        since = today - timedelta(days=days - 1)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(StatsDaily.day, StatsDaily.metric, StatsDaily.value)
                    .where(StatsDaily.day >= since)
                    .order_by(StatsDaily.day)
                )
            ).all()
        if not rows:
            return {"days": [], "series": {}}
        first = rows[0][0]
        dates = [first + timedelta(days=i) for i in range((today - first).days + 1)]
        position = {d: i for i, d in enumerate(dates)}
        series: dict[str, list[int | None]] = {}
        for day, metric, value in rows:
            if metric.startswith("conformance.") and not conformance:
                continue
            if metric.startswith("health.") and not health:
                continue
            series.setdefault(metric, [None] * len(dates))[position[day]] = int(value)
        return {"days": [d.isoformat() for d in dates], "series": series}


class StatsScheduler:
    """Takes the day's snapshot now and then every ``seconds``."""

    def __init__(self, stats: StatsService, *, seconds: float, delay: float = 30) -> None:
        self._stats = stats
        self._seconds = seconds
        self._delay = delay
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None and self._seconds > 0:
            self._task = asyncio.create_task(self._loop(), name="stats-snapshots")

    async def _loop(self) -> None:
        # Let a starting index finish its first projections before counting.
        await asyncio.sleep(self._delay)
        while True:
            try:
                await self._stats.snapshot()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stats_snapshot_failed")
            await asyncio.sleep(self._seconds)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
