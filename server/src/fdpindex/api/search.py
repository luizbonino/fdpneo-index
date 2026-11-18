"""Search routes (search design §5): new paths under ``/index/search``.

* ``GET /index/search``: faceted search; every facet field of the profile is
  a repeatable filter parameter (``theme=…&theme=…``, ``!value`` negates).
* ``GET /index/search/facets``: one facet's buckets, for "show more".
* ``GET /index/search/resources/{id}``: one resource in full, or its
  harvested triples as Turtle, JSON-LD or N-Triples.
* ``GET /index/search/landing``: counters, facet descriptions, card fields.
* ``GET /index/search/labels``: labels for IRIs.
* Administrators: ``GET /index/search/profile``, ``POST …/reindex``, ``GET …/stats``.

Anonymous callers see resources of accepted entries only and are rate
limited per address; the reference ``/index/*`` contract is untouched.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from fdpindex.api.routes import PREFIXES, RDF_TYPES, _negotiate
from fdpindex.auth import Principal, current_principal, require_admin
from fdpindex.errors import BadRequest, IndexServiceError, RateLimited
from fdpindex.search.project import parse_datetime
from fdpindex.search.query import SearchParams, SearchService

router = APIRouter(tags=["Search"])

Caller = Annotated[Principal, Depends(current_principal)]

RESERVED = {
    "q",
    "type",
    "fdp",
    "fdpState",
    "lang",
    "sort",
    "page",
    "size",
    "facets",
    "facetSize",
    "field",
    "contains",
}
RANGES = ("issued", "modified", "harvested")


class SearchDisabled(IndexServiceError):
    code = "index.search_disabled"
    http_status = 404


def _search(request: Request) -> SearchService:
    service: SearchService | None = getattr(request.app.state, "search", None)
    if service is None:
        raise SearchDisabled("Search is not enabled on this index.")
    return service


Search = Annotated[SearchService, Depends(_search)]


def _limit(request: Request, caller: Principal) -> None:
    if caller.is_admin:
        return
    key = request.client.host if request.client else "unknown"
    if not request.app.state.search_rate.allow(key):
        raise RateLimited("Too many searches; wait a minute and try again.")


def _language(request: Request, lang: str | None) -> str:
    if lang:
        return lang[:16]
    accept = request.headers.get("accept-language", "")
    first = accept.split(",", 1)[0].split(";", 1)[0].strip()
    return first[:16] if first and first != "*" else "en"


def _date(name: str, value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise BadRequest(f"{name}: not a date or date-time")
    if end and len(value.strip()) <= 10:
        # A date as the upper bound includes that whole day.
        parsed = parsed.replace(hour=23, minute=59, second=59, tzinfo=parsed.tzinfo or UTC)
    return parsed


def _params(request: Request, service: SearchService, lang: str) -> SearchParams:
    query = request.query_params
    filters = {
        name: [v for v in query.getlist(name) if v]
        for name in service.profile.facets
        if query.getlist(name)
    }
    unknown = [
        key
        for key in query
        if key not in RESERVED
        and key not in filters
        and not any(key in (f"{r}From", f"{r}To") for r in RANGES)
    ]
    if unknown:
        raise BadRequest(f"Unknown parameter: {', '.join(sorted(unknown))}")
    try:
        fdps = [UUID(v) for v in query.getlist("fdp") if v]
    except ValueError as err:
        raise BadRequest("fdp: not a UUID") from err
    ranges = {
        name: (
            _date(f"{name}From", query.get(f"{name}From")),
            _date(f"{name}To", query.get(f"{name}To"), end=True),
        )
        for name in RANGES
        if query.get(f"{name}From") or query.get(f"{name}To")
    }
    facets = query.get("facets")
    params = SearchParams(
        q=query.get("q", ""),
        types=[t for v in query.getlist("type") for t in v.split(",") if t],  # pyright: ignore[reportArgumentType]
        fdps=fdps,
        filters=filters,
        ranges=ranges,
        fdp_state=query.get("fdpState") or None,
        lang=lang,
        sort=query.get("sort", ""),
        page=_int(query.get("page"), 0, "page"),
        size=_int(query.get("size"), 20, "size"),
        facet_size=_int(query.get("facetSize"), 20, "facetSize"),
    )
    if facets is not None:
        params.facets = [] if facets == "none" else [f for f in facets.split(",") if f]
    return params


def _int(value: str | None, default: int, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as err:
        raise BadRequest(f"{name}: not a number") from err


@router.get("/index/search", summary="Search the harvested metadata")
async def search(
    request: Request,
    service: Search,
    caller: Caller,
    q: Annotated[str, Query(description='Words, "phrases", or, -word')] = "",
    lang: str | None = None,
) -> dict[str, Any]:
    """Faceted search over every accepted FDP's harvested DCAT metadata.

    Filters: every facet field (`theme`, `keyword`, `publisher`, `format`,
    `license`, `accessRights`, …) is a repeatable parameter; values within a
    field are ORed, fields are ANDed, `!value` excludes. Also `type`, `fdp`,
    `issuedFrom`/`issuedTo`, `modifiedFrom`/`modifiedTo`,
    `harvestedFrom`/`harvestedTo`, `fdpState=ACTIVE`, `sort`, `page`, `size`,
    `facets` (comma-separated, or `none`) and `facetSize`.
    """
    del q
    _limit(request, caller)
    params = _params(request, service, _language(request, lang))
    return await service.search(params, admin=caller.is_admin)


@router.get("/index/search/facets", summary="All buckets of one facet")
async def facet(
    request: Request,
    service: Search,
    caller: Caller,
    field: str,
    contains: str = "",
    size: Annotated[int, Query(ge=1, le=1000)] = 200,
    lang: str | None = None,
) -> dict[str, Any]:
    _limit(request, caller)
    params = _params(request, service, _language(request, lang))
    return await service.facet(params, field, contains=contains, size=size, admin=caller.is_admin)


@router.get(
    "/index/search/resources/{public_id}",
    summary="One resource",
    responses={200: {"content": {"application/json": {}, **{m: {} for m in RDF_TYPES}}}},
)
async def resource(
    public_id: str,
    request: Request,
    service: Search,
    caller: Caller,
    format: str | None = None,
    lang: str | None = None,
) -> Response:
    """The resource with every value and its parents and children, as JSON.

    With `format` (`ttl`, `jsonld`, `nt`, `rdf`) or an RDF `Accept` header, the
    triples harvested for it, as the FDP published them.
    """
    _limit(request, caller)
    if len(public_id) != 22:
        raise BadRequest("Not a resource id")
    accept = request.headers.get("accept", "")
    wants_rdf = format not in (None, "", "json") or (
        not format and any(m in accept for m in RDF_TYPES) and "application/json" not in accept
    )
    if not wants_rdf:
        body = await service.resource(
            public_id, lang=_language(request, lang), admin=caller.is_admin
        )
        return Response(content=_json(body), media_type="application/json")
    media = _negotiate(format, accept)
    graph = await service.resource_graph(public_id)

    def render() -> str:
        for prefix, namespace in PREFIXES.items():
            graph.bind(prefix, namespace, override=False)
        return graph.serialize(format=RDF_TYPES[media])

    return Response(content=await asyncio.to_thread(render), media_type=media)


def _json(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


@router.get("/index/search/landing", summary="Counters and facet descriptions")
async def landing(service: Search) -> dict[str, Any]:
    return await service.landing()


@router.get("/index/search/labels", summary="Labels for IRIs")
async def labels(
    request: Request,
    service: Search,
    caller: Caller,
    iri: Annotated[list[str], Query(max_length=100)],
    lang: str | None = None,
) -> dict[str, str]:
    _limit(request, caller)
    if len(iri) > 100:
        raise BadRequest("At most 100 IRIs")
    return await service.labels(iri, _language(request, lang))


class ReindexRequest(BaseModel):
    entry: UUID | None = Field(default=None, description="One entry; all when absent")


@router.get(
    "/index/search/profile",
    summary="The search profile",
    dependencies=[Depends(require_admin)],
)
async def profile(service: Search) -> dict[str, Any]:
    return {"hash": service.profile.hash, "profile": service.profile.model_dump(mode="json")}


@router.post(
    "/index/search/reindex",
    status_code=202,
    summary="Re-project harvested metadata",
    dependencies=[Depends(require_admin)],
)
async def reindex(service: Search, body: ReindexRequest | None = None) -> dict[str, Any]:
    entry = body.entry if body else None
    service.indexer.reindex(entry)
    return {"entry": str(entry) if entry else None, "status": "scheduled"}


@router.get(
    "/index/search/stats",
    summary="Projection statistics",
    dependencies=[Depends(require_admin)],
)
async def stats(service: Search) -> dict[str, Any]:
    return await service.stats()
