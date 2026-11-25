"""Conformance reports for data holders (TASKS 11.3; conformance design §8).

* An FDP's report: per active pack, its records by status, the warnings and
  violations found, and the most frequent problems (grouped by severity,
  path, constraint and shape, with an example record).
* Records with their status against a pack, paged, optionally only those
  with one problem ("show records").
* One record's findings, per pack.

Problem counts are of stored findings, at most 50 per record and pack; the
per-record counts are complete.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select

from fdpindex.api.schemas import instant
from fdpindex.models import (
    ConformanceEntryState,
    ConformanceFinding,
    ConformanceResult,
    SearchResource,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

TOP_PROBLEMS = 20
Severity = Literal["violation", "warning", "info"]
# undeclared and unresolved only for the pseudo-pack "declared" (TASKS 15.12).
Status = Literal["conforms", "violations", "error", "undeclared", "unresolved"]
_SEVERITY_ORDER = case(
    (ConformanceFinding.severity == "violation", 0),
    (ConformanceFinding.severity == "warning", 1),
    else_=2,
)


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class Problem(_Camel):
    pack: str
    severity: Severity
    path: str | None
    component: str = Field(description="The SHACL constraint component, without its namespace")
    shape: str | None
    message: str | None = Field(description="One of the messages of this problem")
    findings: int
    records: int = Field(description="Records with this problem")
    example: str = Field(description="IRI of a record with this problem")


class PackSummary(_Camel):
    pack: str = Field(description="The pack's id")
    version: str | None
    records: int = Field(description="Records validated against the pack")
    conforms: int
    violations: int = Field(description="Records with violations")
    errors: int = Field(description="Records that could not be validated")
    with_warnings: int = Field(alias="withWarnings", description="Records with warnings")
    undeclared: int = Field(
        default=0, description="For `declared`: records that declare no profile"
    )
    unresolved: int = Field(
        default=0,
        description="For `declared`: records whose declared profiles could not be resolved",
    )
    last_validated: str | None = Field(alias="lastValidated")


class EntryReport(_Camel):
    entry: str
    records: int = Field(description="Records of the FDP the last validation saw")
    validated_at: str | None = Field(alias="validatedAt")
    packs: list[PackSummary]
    problems: list[Problem] = Field(description="The most frequent problems, worst first")


class RecordStatus(_Camel):
    public_id: str = Field(alias="publicId")
    iri: str
    kind: str
    title: str | None
    pack: str
    status: Status
    violations: int
    warnings: int
    infos: int
    variant: str | None
    validated_at: str | None = Field(alias="validatedAt")


class RecordPage(_Camel):
    total: int
    items: list[RecordStatus]


class FindingView(_Camel):
    severity: Severity
    focus_node: str = Field(alias="focusNode")
    path: str | None
    component: str
    shape: str | None
    message: str | None
    value: str | None


class PackResult(_Camel):
    pack: str
    version: str
    variant: str | None
    status: Status
    violations: int
    warnings: int
    infos: int
    error: str | None
    validated_at: str | None = Field(alias="validatedAt")
    findings: list[FindingView]


class RecordReport(_Camel):
    public_id: str = Field(alias="publicId")
    iri: str
    kind: str
    entry: str
    title: str | None
    results: list[PackResult]


def _when(moment: datetime | None) -> str | None:
    return instant(moment)


async def entry_report(
    session: AsyncSession, entry_id: UUID, packs: list[tuple[str, str]]
) -> EntryReport:
    """``packs``: the active packs as ``(id, version)``, in the deployment's order."""
    state = await session.get(ConformanceEntryState, entry_id)
    rows = (
        await session.execute(
            select(
                ConformanceResult.pack,
                func.count(),
                func.sum(case((ConformanceResult.status == "conforms", 1), else_=0)),
                func.sum(case((ConformanceResult.status == "violations", 1), else_=0)),
                func.sum(case((ConformanceResult.status == "error", 1), else_=0)),
                func.sum(case((ConformanceResult.warnings > 0, 1), else_=0)),
                func.max(ConformanceResult.validated_at),
                func.sum(case((ConformanceResult.status == "undeclared", 1), else_=0)),
                func.sum(case((ConformanceResult.status == "unresolved", 1), else_=0)),
            )
            .where(ConformanceResult.entry_uuid == entry_id)
            .group_by(ConformanceResult.pack)
        )
    ).all()
    by_pack = {str(r[0]): r for r in rows}
    versions = dict(packs)
    order = [p for p, _ in packs] + sorted(set(by_pack) - set(versions))
    summaries: list[PackSummary] = []
    for pack in order:
        row = by_pack.get(pack)
        summaries.append(
            PackSummary(
                pack=pack,
                version=versions.get(pack),
                records=int(row[1]) if row else 0,
                conforms=int(row[2] or 0) if row else 0,
                violations=int(row[3] or 0) if row else 0,
                errors=int(row[4] or 0) if row else 0,
                withWarnings=int(row[5] or 0) if row else 0,
                undeclared=int(row[7] or 0) if row else 0,
                unresolved=int(row[8] or 0) if row else 0,
                lastValidated=_when(row[6]) if row else None,
            )
        )
    problems = (
        await session.execute(
            select(
                ConformanceResult.pack,
                ConformanceFinding.severity,
                ConformanceFinding.path,
                ConformanceFinding.component,
                ConformanceFinding.shape,
                func.min(ConformanceFinding.message),
                func.count(),
                func.count(func.distinct(ConformanceResult.id)),
                func.min(ConformanceResult.record_iri),
            )
            .join(ConformanceResult, ConformanceResult.id == ConformanceFinding.result_id)
            .where(ConformanceResult.entry_uuid == entry_id)
            .group_by(
                ConformanceResult.pack,
                ConformanceFinding.severity,
                ConformanceFinding.path,
                ConformanceFinding.component,
                ConformanceFinding.shape,
            )
            .order_by(
                func.min(_SEVERITY_ORDER),
                func.count(func.distinct(ConformanceResult.id)).desc(),
                ConformanceResult.pack,
                ConformanceFinding.path,
            )
            .limit(TOP_PROBLEMS)
        )
    ).all()
    return EntryReport(
        entry=str(entry_id),
        records=state.records if state else 0,
        validatedAt=_when(state.validated_at) if state else None,
        packs=summaries,
        problems=[
            Problem(
                pack=str(p[0]),
                severity=cast("Severity", p[1]),
                path=p[2],
                component=str(p[3]),
                shape=p[4],
                message=p[5],
                findings=int(p[6]),
                records=int(p[7]),
                example=str(p[8]),
            )
            for p in problems
        ],
    )


async def record_page(
    session: AsyncSession,
    *,
    entry_id: UUID | None,
    pack: str,
    status: str | None,
    path: str | None,
    component: str | None,
    severity: str | None = None,
    offset: int,
    limit: int,
) -> RecordPage:
    query = select(ConformanceResult).where(ConformanceResult.pack == pack)
    if entry_id is not None:
        query = query.where(ConformanceResult.entry_uuid == entry_id)
    if status is not None:
        query = query.where(ConformanceResult.status == status)
    if path is not None or component is not None or severity is not None:
        having = select(ConformanceFinding.result_id).where(
            ConformanceFinding.result_id == ConformanceResult.id
        )
        if path is not None:
            having = having.where(ConformanceFinding.path == path)
        if component is not None:
            having = having.where(ConformanceFinding.component == component)
        if severity is not None:
            having = having.where(ConformanceFinding.severity == severity)
        query = query.where(having.exists())
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    results = list(
        await session.scalars(
            query.order_by(ConformanceResult.violations.desc(), ConformanceResult.record_iri)
            .offset(offset)
            .limit(limit)
        )
    )
    titles = await _titles(session, [r.public_id for r in results])
    return RecordPage(
        total=total,
        items=[
            RecordStatus(
                publicId=r.public_id,
                iri=r.record_iri,
                kind=r.kind,
                title=titles.get(r.public_id),
                pack=r.pack,
                status=cast("Status", r.status),
                violations=r.violations,
                warnings=r.warnings,
                infos=r.infos,
                variant=r.variant,
                validatedAt=_when(r.validated_at),
            )
            for r in results
        ],
    )


async def record_report(
    session: AsyncSession, public_id: str, order: list[str]
) -> RecordReport | None:
    results = list(
        await session.scalars(
            select(ConformanceResult).where(ConformanceResult.public_id == public_id)
        )
    )
    if not results:
        return None
    rank = {p: i for i, p in enumerate(order)}
    results.sort(key=lambda r: (rank.get(r.pack, len(rank)), r.pack))
    findings = list(
        await session.scalars(
            select(ConformanceFinding)
            .where(ConformanceFinding.result_id.in_([r.id for r in results]))
            .order_by(ConformanceFinding.id)
        )
    )
    per_result: dict[int, list[FindingView]] = {}
    for f in findings:
        per_result.setdefault(f.result_id, []).append(
            FindingView(
                severity=cast("Severity", f.severity),
                focusNode=f.focus_node,
                path=f.path,
                component=f.component,
                shape=f.shape,
                message=f.message,
                value=f.value,
            )
        )
    first = results[0]
    titles = await _titles(session, [public_id])
    return RecordReport(
        publicId=public_id,
        iri=first.record_iri,
        kind=first.kind,
        entry=str(first.entry_uuid),
        title=titles.get(public_id),
        results=[
            PackResult(
                pack=r.pack,
                version=r.pack_version,
                variant=r.variant,
                status=cast("Status", r.status),
                violations=r.violations,
                warnings=r.warnings,
                infos=r.infos,
                error=r.error,
                validatedAt=_when(r.validated_at),
                findings=per_result.get(r.id, []),
            )
            for r in results
        ],
    )


async def _titles(session: AsyncSession, public_ids: list[str]) -> dict[str, str]:
    """Titles from the search projection, when search is on and has the resource."""
    if not public_ids:
        return {}
    rows = (
        await session.execute(
            select(SearchResource.public_id, SearchResource.title).where(
                SearchResource.public_id.in_(public_ids)
            )
        )
    ).all()
    return {str(p): str(t) for p, t in rows if t}
