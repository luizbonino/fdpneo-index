"""Statistics API (TASKS 15.1).

* ``GET /index/stats``: what the index holds now (FDPs by state, records by
  kind, the FDPs with most records, conformance shares, metadata health),
  FDPs by month of registration, datasets by year of issue, and the daily
  history.
* ``GET /index/health``: every FDP's metadata health in one table (TASKS 15.8).

Public, and counted as visitors see the index (``fdpindex.stats``);
conformance shares and metadata health only for those who may read
conformance reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from fdpindex.auth import Principal, current_principal
from fdpindex.errors import Forbidden, Unauthorized

if TYPE_CHECKING:
    from fdpindex.stats import StatsService

router = APIRouter(tags=["Statistics"])

Caller = Annotated[Principal, Depends(current_principal)]


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class FdpCountry(_Camel):
    country: str = Field(description="ISO 3166-1 alpha-2")
    fdps: int
    by_source: dict[str, int] = Field(
        alias="bySource",
        description="How many were placed by each source: admin, stated, publisher, domain, server",
    )


class RecordCountry(_Camel):
    country: str = Field(description="ISO 3166-1 alpha-2")
    records: int = Field(
        description="Records whose dct:spatial names this country or a region in it"
    )
    values: list[str] = Field(description="The spatial IRIs seen for it (at most 50)")


class FdpCounts(_Camel):
    total: int = Field(description="FDPs in the public list")
    by_state: dict[str, int] = Field(
        alias="byState", description="By state: ACTIVE, INACTIVE, UNREACHABLE, INVALID, UNKNOWN"
    )
    by_country: list[FdpCountry] = Field(
        alias="byCountry", description="Where the FDPs are (see the entry's `location`)"
    )
    unplaced: int = Field(description="FDPs no source could place")


class RecordCounts(_Camel):
    total: int = Field(description="Records visitors find in search")
    by_kind: dict[str, int] = Field(
        alias="byKind", description="By kind: fdp, catalog, dataset, series, service …"
    )
    by_country: list[RecordCountry] = Field(
        alias="byCountry", description="Where the data is about: dct:spatial by country"
    )


class LargestFdp(_Camel):
    uuid: str
    client_url: str = Field(alias="clientUrl")
    title: str | None
    records: int = Field(description="Its records, the FDP's own description left out")


class KindShare(_Camel):
    checked: int
    conforms: int


class ConformanceShare(_Camel):
    pack: str = Field(description="The profile pack's id")
    specification: str = Field(description="The specification's IRI")
    title: dict[str, str] = Field(description="The pack's title by language")
    checked: int = Field(description="Records checked against it")
    conforms: int = Field(description="Records that meet it")
    by_kind: dict[str, KindShare] = Field(alias="byKind")
    undeclared: int = Field(
        default=0,
        description="For `declared`: records that declare no profile, not counted as checked",
    )


class MonthCount(_Camel):
    month: str = Field(description="YYYY-MM")
    count: int


class YearCount(_Camel):
    year: int
    count: int


class History(_Camel):
    days: list[str] = Field(description="Every day from the first snapshot, ISO dates")
    series: dict[str, list[int | None]] = Field(
        description="Per metric (fdps.total, fdps.state.ACTIVE, records.total, "
        "records.kind.dataset, conformance.<pack>.conforms …), one value per day; "
        "null where no snapshot was taken"
    )


class HealthSummary(_Camel):
    """Metadata health over the FDPs in the public list that have a report (TASKS 15.8)."""

    fdps: int = Field(description="FDPs with a health report")
    by_reach: dict[str, int] = Field(
        alias="byReach",
        description="By the deepest level reached: none, fdp, catalog, dataset, distribution, "
        "dataservice",
    )
    by_status: dict[str, int] = Field(alias="byStatus", description="fine, findings, unreadable")
    findings: dict[str, int] = Field(description="FDPs with each finding")
    notes: dict[str, int] = Field(
        default_factory=lambda: {}, description="FDPs with each note (DCAT's advice)"
    )
    failures: dict[str, int] = Field(description="Pages that failed, by kind")
    partial: int = Field(description="FDPs whose harvest stopped at its page limit")


class HealthRow(_Camel):
    uuid: str
    client_url: str = Field(alias="clientUrl")
    title: str | None
    reach: str | None
    status: str
    pages: int
    levels: dict[str, int] = Field(description="Pages per level")
    findings: dict[str, int] = Field(description="Count per finding")
    notes: dict[str, int] = Field(default_factory=lambda: {}, description="Count per note")
    failed: int = Field(description="Pages that could not be read")
    partial: bool = Field(description="The harvest stopped at its page limit")


class Statistics(_Camel):
    generated_at: str = Field(alias="generatedAt")
    fdps: FdpCounts
    records: RecordCounts | None = Field(description="Null when search is off")
    largest: list[LargestFdp] = Field(description="The FDPs with most records")
    conformance: list[ConformanceShare] | None = Field(
        description="Null when conformance is off or its reports are for administrators"
    )
    registrations: list[MonthCount] = Field(
        description="FDPs in the public list by month of registration"
    )
    published: list[YearCount] = Field(description="Datasets by year of dct:issued")
    history: History
    health: HealthSummary | None = Field(
        default=None, description="Null when health reports are for administrators"
    )


def _service(request: Request) -> StatsService:
    return request.app.state.stats


@router.get("/index/stats", response_model=Statistics, summary="Statistics on the index")
async def statistics(
    request: Request,
    caller: Caller,
    days: Annotated[int, Query(ge=1, le=3660, description="Days of history")] = 365,
) -> Statistics:
    view = await _service(request).view(admin=caller.is_admin, days=days)
    return Statistics.model_validate(view)


@router.get(
    "/index/health",
    response_model=list[HealthRow],
    summary="Every FDP's metadata health",
    responses={401: {"description": "Reports are for administrators"}},
)
async def health_table(request: Request, caller: Caller) -> list[HealthRow]:
    """One row per FDP in the public list with a health report (TASKS 15.8)."""
    service = _service(request)
    if not await service.health_readable(admin=caller.is_admin):
        if caller.anonymous:
            raise Unauthorized("Health reports are for administrators")
        raise Forbidden("Health reports are for administrators")
    return [HealthRow.model_validate(row) for row in await service.health_table()]
