"""SPARQL routes (search design §2.2.2): new paths under ``/index/sparql``.

* ``GET`` / ``POST /index/sparql``: the SPARQL 1.1 Protocol, queries only;
  without a query, the service description.
* ``POST /index/sparql/validate``: parse and gate, for the editor.
* ``GET /index/sparql/schema``: classes, properties, graphs and prefixes.
* ``GET /index/sparql/suggest``: values for parameter forms and the builder.
* ``GET /index/sparql/examples``; ``POST …/{id}/expand`` and ``…/{id}/run``.

Anonymous callers are rate limited per address; administrators are not, and
get the longer time budget.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from fdpindex.auth import Principal, current_principal
from fdpindex.errors import IndexServiceError
from fdpindex.sparql.examples import Library
from fdpindex.sparql.gate import QueryRejected
from fdpindex.sparql.service import SparqlService
from fdpindex.sparql.workers import QueryFailed, QueryResult

router = APIRouter(tags=["SPARQL"])

Caller = Annotated[Principal, Depends(current_principal)]


class SparqlError(IndexServiceError):
    code = "index.sparql"

    def __init__(self, message: str, status: int, *, errors: dict[str, object] | None = None):
        super().__init__(message, errors=errors)
        self.http_status = status  # pyright: ignore[reportAttributeAccessIssue]


class SparqlDisabled(IndexServiceError):
    code = "index.sparql_disabled"
    http_status = 404


def _sparql(request: Request) -> SparqlService:
    service: SparqlService | None = getattr(request.app.state, "sparql", None)
    if service is None:
        raise SparqlDisabled("SPARQL is not enabled on this index.")
    return service


Sparql = Annotated[SparqlService, Depends(_sparql)]


def _limit(service: SparqlService, request: Request, caller: Principal, cost: int = 1) -> None:
    if caller.is_admin:
        return
    key = request.client.host if request.client else "unknown"
    if not service.rate.allow(key, cost):
        raise SparqlError(
            "Too many SPARQL requests; wait a minute and try again.",
            429,
        )


def _response(result: QueryResult) -> Response:
    headers = {
        "X-Fdpindex-Rows": str(result.rows),
        "X-Fdpindex-Seconds": f"{result.seconds:.3f}",
        "Access-Control-Expose-Headers": "X-Fdpindex-Rows, X-Fdpindex-Seconds, X-Fdpindex-Truncated",
    }
    if result.truncated:
        headers["X-Fdpindex-Truncated"] = str(result.rows)
    return Response(result.body, media_type=result.media_type, headers=headers)


async def _run(
    service: SparqlService,
    request: Request,
    caller: Principal,
    query: str,
    *,
    fmt: str | None,
    default_graph_uri: list[str] | None = None,
    named_graph_uri: list[str] | None = None,
) -> Response:
    _limit(service, request, caller)
    try:
        result = await service.query(
            query,
            fmt=fmt,
            accept=request.headers.get("accept"),
            admin=caller.is_admin,
            default_graph_uri=default_graph_uri,
            named_graph_uri=named_graph_uri,
        )
    except QueryRejected as err:
        raise SparqlError(
            err.message, 400, errors={"line": err.line, "column": err.column}
        ) from err
    except QueryFailed as err:
        raise SparqlError(err.message, err.status) from err
    return _response(result)


@router.get("/index/sparql", name="sparql_get")
async def sparql_get(
    request: Request,
    service: Sparql,
    caller: Caller,
    query: str | None = None,
    format: str | None = None,
    default_graph_uri: Annotated[list[str] | None, Query(alias="default-graph-uri")] = None,
    named_graph_uri: Annotated[list[str] | None, Query(alias="named-graph-uri")] = None,
) -> Response:
    """SPARQL 1.1 Protocol query by GET; without ``query``, the service description (Turtle)."""
    if not query:
        return Response(
            service.service_description(str(request.url_for("sparql_get"))),
            media_type="text/turtle; charset=utf-8",
        )
    return await _run(
        service,
        request,
        caller,
        query,
        fmt=format,
        default_graph_uri=default_graph_uri,
        named_graph_uri=named_graph_uri,
    )


@router.post(
    "/index/sparql",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/sparql-query": {"schema": {"type": "string"}},
                "application/x-www-form-urlencoded": {
                    "schema": {"type": "object", "properties": {"query": {"type": "string"}}}
                },
            }
        }
    },
)
async def sparql_post(
    request: Request,
    service: Sparql,
    caller: Caller,
    format: str | None = None,
    default_graph_uri: Annotated[list[str] | None, Query(alias="default-graph-uri")] = None,
    named_graph_uri: Annotated[list[str] | None, Query(alias="named-graph-uri")] = None,
) -> Response:
    """SPARQL 1.1 Protocol query by POST: a form with ``query`` or an ``application/sparql-query`` body."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/sparql-query":
        query = (await request.body()).decode("utf-8", errors="replace")
    elif content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
        form = await request.form()
        query = str(form.get("query") or "")
        default_graph_uri = default_graph_uri or [str(v) for v in form.getlist("default-graph-uri")]
        named_graph_uri = named_graph_uri or [str(v) for v in form.getlist("named-graph-uri")]
    else:
        raise SparqlError(
            "Send the query as application/sparql-query or as a form field named query.", 415
        )
    return await _run(
        service,
        request,
        caller,
        query,
        fmt=format,
        default_graph_uri=default_graph_uri or None,
        named_graph_uri=named_graph_uri or None,
    )


class ValidateRequest(BaseModel):
    query: str = Field(max_length=200_000)


@router.post("/index/sparql/validate")
async def sparql_validate(body: ValidateRequest, service: Sparql) -> dict[str, Any]:
    """Parse and gate a query without running it: ``ok``, or a message with line and column."""
    return service.validate(body.query)


@router.get("/index/sparql/schema")
async def sparql_schema(service: Sparql) -> dict[str, Any]:
    """Classes and properties in the data (with counts), named graphs, and known prefixes."""
    try:
        return await service.schema()
    except QueryFailed as err:
        raise SparqlError(err.message, err.status) from err


@router.get("/index/sparql/suggest")
async def sparql_suggest(
    request: Request,
    service: Sparql,
    caller: Caller,
    q: Annotated[str, Query(max_length=100)] = "",
    predicate: str | None = None,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[dict[str, Any]]:
    """Values of ``predicate`` (or instances of ``type``) containing ``q``, with labels and counts."""
    _limit(service, request, caller)
    try:
        return await service.suggest(predicate=predicate, type_iri=type, text=q, limit=limit)
    except QueryRejected as err:
        raise SparqlError(err.message, 400) from err
    except QueryFailed as err:
        raise SparqlError(err.message, err.status) from err


@router.get("/index/sparql/examples")
async def sparql_examples(service: Sparql) -> Library:
    """The query library: grouped example queries with typed parameters."""
    return service.library


class ExampleRequest(BaseModel):
    parameters: dict[str, str] = Field(default_factory=lambda: {})


def _expand(service: SparqlService, example_id: str, body: ExampleRequest) -> str:
    try:
        return service.expand_example(example_id, body.parameters)
    except KeyError as err:
        raise SparqlError("No such example query.", 404) from err
    except QueryRejected as err:
        raise SparqlError(err.message, 400) from err


@router.post("/index/sparql/examples/{example_id}/expand")
async def sparql_example_expand(
    example_id: str, body: ExampleRequest, service: Sparql
) -> dict[str, str]:
    """The example's query with its parameters bound, ready to run or edit."""
    return {"query": _expand(service, example_id, body)}


@router.post("/index/sparql/examples/{example_id}/run")
async def sparql_example_run(
    example_id: str,
    body: ExampleRequest,
    request: Request,
    service: Sparql,
    caller: Caller,
    format: str | None = None,
) -> Response:
    """Run an example with parameters; results as for ``/index/sparql``."""
    return await _run(service, request, caller, _expand(service, example_id, body), fmt=format)
