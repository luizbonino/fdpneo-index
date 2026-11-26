"""Benchmark results API (TASKS 15.10, design note benchmarks).

* ``GET /index/entries/{uuid}/benchmarks``: per benchmark of the network
  profile, how many of the FDP's records are assessed and how many meet each
  condition, and the result on the FDP's root.
* ``GET /index/entries/{uuid}/benchmarks/results``: the results per record,
  paged, by benchmark, condition and whether it is met.
* ``POST /index/entries/{uuid}/benchmarks/runs`` (administrators): run the
  benchmarks on one record now (the root by default).
* ``GET /index/benchmark-results/{id}``: one result, with every test's log.
* ``GET /index/benchmark-results/{id}/result-set``: the FAIR Testing Resources
  result set as the runner gave it (JSON-LD), republished.

Who may read results follows the metadata health reports (the conformance
``reports`` setting); the FDP must be one the caller may see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from fdpindex.api.routes import Admin, Caller, Index, _visible  # pyright: ignore[reportPrivateUsage]
from fdpindex.api.schemas import instant
from fdpindex.benchmarks import result_set_text
from fdpindex.errors import Forbidden, NotFound, Unauthorized

if TYPE_CHECKING:
    from fdpindex.auth import Principal
    from fdpindex.benchmarks import BenchmarkService

router = APIRouter(tags=["Benchmarks"])


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class BenchmarkInfo(_Camel):
    iri: str
    title: str | None
    landing_page: str | None = Field(alias="landingPage")
    runner: str | None = Field(description="The runner the benchmark names (ftr:supportedBy)")
    algorithm: str | None = Field(description="Its scoring algorithm (ftr:scoringFunction)")
    applies_to: list[str] = Field(
        alias="appliesTo", description="Classes it applies to; empty: every record"
    )
    runnable: bool = Field(description="The index can run it")
    reason: str | None = Field(description="Why it cannot be run")


class BenchmarkGuidance(_Camel):
    url: str
    text: str


class BenchmarkCondition(_Camel):
    id: str
    description: str
    met: bool | None = Field(description="Null: the runner could not decide")
    message: str | None = Field(description="The runner's sentence, as it wrote it")
    guidance: list[BenchmarkGuidance]


class BenchmarkTestVerdict(_Camel):
    reference: str | None
    title: str | None
    test: str | None
    metric: str | None
    result: str | None = Field(description="pass, fail, indeterminate or error")
    log: str | None = None
    summary: str | None = None


class BenchmarkResultView(_Camel):
    id: str
    record: str
    benchmark: str
    state: Literal["due", "done", "failed"] = Field(
        description="due: waiting for a call (the fields show the last result, if any); "
        "failed: the runner did not answer, retried later"
    )
    assessed_at: str | None = Field(alias="assessedAt")
    attempted_at: str | None = Field(alias="attemptedAt")
    runner: str | None
    incomplete: bool = Field(description="A test errored: the result is incomplete")
    conditions: list[BenchmarkCondition]
    tests: list[BenchmarkTestVerdict]
    error: str | None = Field(description="Why the last call failed")


class BenchmarkResultDetail(BenchmarkResultView):
    entry: str
    result_set: str | None = Field(
        alias="resultSet", description="Where the FAIR Testing Resources result set is"
    )


class BenchmarkRecordCounts(_Camel):
    total: int = Field(description="Records the benchmark applies to")
    assessed: int
    due: int
    failed: int


class BenchmarkConditionCount(_Camel):
    id: str
    description: str
    met: int = Field(description="Records that meet it")
    not_met: int = Field(alias="notMet")
    undecided: int


class EntryBenchmark(BenchmarkInfo):
    records: BenchmarkRecordCounts
    last_assessed_at: str | None = Field(alias="lastAssessedAt")
    conditions: list[BenchmarkConditionCount]
    root: BenchmarkResultView | None = Field(description="The result on the FDP's root")


class EntryBenchmarks(_Camel):
    mode: Literal["off", "onRequest", "scheduled"]
    benchmarks: list[EntryBenchmark]


class BenchmarkResultPage(_Camel):
    items: list[BenchmarkResultView]
    total: int
    page: int
    size: int


class BenchmarkRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record: str | None = Field(default=None, description="A record of the FDP; default its root")
    benchmark: str | None = Field(default=None, description="One benchmark; default all")


class BenchmarkRunQueued(_Camel):
    queued: int = Field(description="Calls queued, one per benchmark")


class NetworkBenchmarkCondition(_Camel):
    id: str
    description: str
    fdps: int = Field(description="FDPs whose assessed records all meet it")
    records: int = Field(description="Records that meet it")
    assessed: int = Field(description="Records assessed")


class NetworkBenchmark(BenchmarkInfo):
    fdps: int = Field(description="FDPs with a result")
    conditions: list[NetworkBenchmarkCondition]


def _service(request: Request) -> BenchmarkService:
    return request.app.state.benchmarks


async def _readable(request: Request, caller: Principal) -> None:
    if not await request.app.state.stats.health_readable(admin=caller.is_admin):
        if caller.anonymous:
            raise Unauthorized("Benchmark results are for administrators")
        raise Forbidden("Benchmark results are for administrators")


def _result(view: dict[str, Any]) -> dict[str, Any]:
    return {
        **view,
        "assessedAt": instant(view["assessedAt"]),
        "attemptedAt": instant(view["attemptedAt"]),
    }


@router.get(
    "/index/entries/{entry_id}/benchmarks",
    response_model=EntryBenchmarks,
    summary="The network's benchmarks on an FDP",
    responses={401: {"description": "Results are for administrators"}},
)
async def entry_benchmarks(
    entry_id: UUID, request: Request, index: Index, caller: Caller
) -> EntryBenchmarks:
    """The network's own verdict, beside the metadata health: never part of its status."""
    await _visible(index, caller, entry_id)
    await _readable(request, caller)
    entry = await index.get_entry(entry_id)
    summary = await _service(request).summary(entry_id, entry.client_url)
    return EntryBenchmarks.model_validate(
        {
            "mode": summary["mode"],
            "benchmarks": [
                {
                    **b,
                    "lastAssessedAt": instant(b["lastAssessedAt"]),
                    "root": _result(b["root"]) if b["root"] else None,
                }
                for b in summary["benchmarks"]
            ],
        }
    )


@router.get(
    "/index/entries/{entry_id}/benchmarks/results",
    response_model=BenchmarkResultPage,
    summary="An FDP's benchmark results per record",
)
async def entry_benchmark_results(
    entry_id: UUID,
    request: Request,
    index: Index,
    caller: Caller,
    benchmark: str | None = None,
    condition: str | None = Query(default=None, max_length=50),
    met: bool | None = None,
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
) -> BenchmarkResultPage:
    await _visible(index, caller, entry_id)
    await _readable(request, caller)
    items, total = await _service(request).results(
        entry_id, benchmark=benchmark, condition=condition, met=met, page=page, size=size
    )
    return BenchmarkResultPage.model_validate(
        {"items": [_result(i) for i in items], "total": total, "page": page, "size": size}
    )


@router.post(
    "/index/entries/{entry_id}/benchmarks/runs",
    response_model=BenchmarkRunQueued,
    status_code=202,
    summary="Run the benchmarks on one record now (administrators)",
    responses={409: {"description": "Benchmark runs are off"}},
)
async def run_entry_benchmarks(
    entry_id: UUID, body: BenchmarkRunRequest, request: Request, _admin: Admin
) -> BenchmarkRunQueued:
    """Queued before scheduled work; the results replace the record's earlier ones."""
    queued = await _service(request).request(entry_id, record=body.record, benchmark=body.benchmark)
    return BenchmarkRunQueued(queued=queued)


async def _visible_result(
    request: Request, index: Index, caller: Principal, result_id: UUID
) -> tuple[Any, dict[str, Any]]:
    row, view = await _service(request).result(result_id)
    try:
        await _visible(index, caller, row.entry_uuid)
    except NotFound as err:
        raise NotFound("Benchmark result not found") from err
    await _readable(request, caller)
    return row, view


@router.get(
    "/index/benchmark-results/{result_id}",
    response_model=BenchmarkResultDetail,
    summary="One benchmark result, with the tests' logs",
)
async def benchmark_result(
    result_id: UUID, request: Request, index: Index, caller: Caller
) -> BenchmarkResultDetail:
    _row, view = await _visible_result(request, index, caller, result_id)
    has_set = view.pop("hasResultSet")
    return BenchmarkResultDetail.model_validate(
        {
            **_result(view),
            "resultSet": f"/index/benchmark-results/{result_id}/result-set" if has_set else None,
        }
    )


@router.get(
    "/index/benchmark-results/{result_id}/result-set",
    summary="The FAIR Testing Resources result set, as the runner gave it",
    response_class=Response,
    responses={200: {"content": {"application/ld+json": {}}}},
)
async def benchmark_result_set(
    result_id: UUID, request: Request, index: Index, caller: Caller
) -> Response:
    row, _view = await _visible_result(request, index, caller, result_id)
    text = result_set_text(row.result_set)
    if text is None:
        raise NotFound("This result has no result set")
    return Response(content=text, media_type="application/ld+json")
