"""Search routes (search design §5): new paths under ``/index/search``.

* ``GET /index/search``: faceted search; every facet field of the profile is
  a repeatable filter parameter (``theme=…&theme=…``, ``!value`` negates).
* ``GET /index/search/facets``: one facet's buckets, for "show more".
* ``GET /index/search/sparql``: the same search as a SPARQL query.
* ``GET /index/search/resources/{id}``: one resource in full, or its
  harvested triples as Turtle, JSON-LD or N-Triples.
* ``GET /index/search/landing``: counters, facet descriptions, card fields.
* ``GET /index/search/labels``: labels for IRIs.
* ``GET /index/search/properties``: properties found in the harvested
  metadata, for facets on any property (``prop:<IRI>`` as facet and filter).
* Administrators: ``GET /index/search/profile`` (and ``…/examples/{name}``, bundled
  overlays such as HealthDCAT-AP), ``POST …/reindex``, ``GET …/stats``,
  ``GET``/``PUT``/``DELETE …/settings`` (default facets, property facets).

Anonymous callers see resources of accepted entries only and are rate
limited per address; the reference ``/index/*`` contract is untouched.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from fdpindex.api.routes import PREFIXES, RDF_TYPES, _negotiate
from fdpindex.auth import Principal, current_principal, require_admin
from fdpindex.errors import BadRequest, IndexServiceError, NotFound, RateLimited
from fdpindex.search.period import parse_day
from fdpindex.search.profile import KINDS, Kind
from fdpindex.search.profile_store import ProfileError, examples
from fdpindex.search.project import parse_datetime
from fdpindex.search.query import DEFAULT_TYPES, SearchParams, SearchService
from fdpindex.search.settings import PROPERTY_FACET, SearchSettings, SearchSettingsView

router = APIRouter(tags=["Search"])

Caller = Annotated[Principal, Depends(current_principal)]

RESERVED = {
    "q",
    "collapse",
    "type",
    "fdp",
    "fdpState",
    "lang",
    "sort",
    "page",
    "size",
    "facets",
    "facetSize",
    "highlight",
    "tree",
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
    """``lang``, else the browser's first language, else the index's default (TASKS 8.7)."""
    if lang:
        return lang[:16]
    accept = request.headers.get("accept-language", "")
    first = accept.split(",", 1)[0].split(";", 1)[0].strip()
    if first and first != "*":
        return first[:16]
    service: SearchService | None = getattr(request.app.state, "search", None)
    return service.search_settings.default_language if service is not None else "en"


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


def _day(name: str, value: str | None, *, end: bool = False) -> date | None:
    """A year, month or day for ``temporalFrom``/``temporalTo``: its first or last day."""
    if not value:
        return None
    parsed = parse_day(value, end=end)
    if parsed is None:
        raise BadRequest(f"{name}: not a year (2010), month (2010-05) or date (2010-05-31)")
    return parsed


def _params(request: Request, service: SearchService, lang: str) -> SearchParams:
    query = request.query_params
    filters = {
        name: [v for v in query.getlist(name) if v]
        for name in dict.fromkeys(
            [
                *service.profile.facets,
                *service.profile.hierarchies,
                *(k for k in query if k.startswith(PROPERTY_FACET)),
            ]
        )
        if query.getlist(name)
    }
    number_fields = [n for n, spec in service.profile.fields.items() if spec.kind == "number"]
    unknown = [
        key
        for key in query
        if key not in RESERVED
        and key not in filters
        and not any(key in (f"{r}From", f"{r}To") for r in (*RANGES, "temporal"))
        and not any(key in (f"{n}Min", f"{n}Max") for n in number_fields)
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
    temporal = (
        (
            _day("temporalFrom", query.get("temporalFrom")),
            _day("temporalTo", query.get("temporalTo"), end=True),
        )
        if query.get("temporalFrom") or query.get("temporalTo")
        else None
    )
    numbers = {
        name: (
            _float(f"{name}Min", query.get(f"{name}Min")),
            _float(f"{name}Max", query.get(f"{name}Max")),
        )
        for name in number_fields
        if query.get(f"{name}Min") or query.get(f"{name}Max")
    }
    facets = query.get("facets")
    params = SearchParams(
        q=query.get("q", ""),
        types=[t for v in query.getlist("type") for t in v.split(",") if t],  # pyright: ignore[reportArgumentType]
        fdps=fdps,
        filters=filters,
        ranges=ranges,
        temporal=temporal,
        numbers=numbers,
        fdp_state=query.get("fdpState") or None,
        lang=lang,
        sort=query.get("sort", ""),
        page=_int(query.get("page"), 0, "page"),
        size=_int(query.get("size"), 20, "size"),
        facet_size=_int(query.get("facetSize"), 20, "facetSize"),
        highlight=query.get("highlight", "").lower() in {"true", "1", "yes"},
        collapse=query.get("collapse", "").lower() in {"true", "1", "yes"},
    )
    if facets is not None:
        params.facets = [] if facets == "none" else [f for f in facets.split(",") if f]
    return params


def _float(name: str, value: str | None) -> float | None:
    if not value:
        return None
    try:
        number = float(value)
    except ValueError as err:
        raise BadRequest(f"{name}: not a number") from err
    if not math.isfinite(number):
        raise BadRequest(f"{name}: not a number")
    return number


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
    field are ORed, fields are ANDed, `!value` excludes. Any property found in
    the harvested metadata is a facet and filter too, named `prop:<IRI>`
    (see `/index/search/properties`). Also `type`, `fdp`,
    `issuedFrom`/`issuedTo`, `modifiedFrom`/`modifiedTo`,
    `harvestedFrom`/`harvestedTo`, `temporalFrom`/`temporalTo` (years, months or
    dates; a resource matches when its temporal coverage overlaps the period),
    `<field>Min`/`<field>Max` for number fields (`recordsMin=1000`),
    `collapse=true` (one hit per group of copies across FDPs; `totalResults`
    counts groups, `resources` the resources), every hit lists its copies in
    other FDPs (`duplicates`, `duplicateCount`),
    `fdpState=ACTIVE`, `sort`, `page`, `size`,
    `facets` (comma-separated, or `none`) and `facetSize`. With `highlight=true`,
    each hit has `highlight`: its title and description as text segments with
    the matching words marked (`{"text", "match"}`), never HTML.
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
    tree: bool = False,
) -> dict[str, Any]:
    """All buckets of one facet. With `tree=true` for a field with a hierarchy
    (Subject): `tree`, nested by broader concepts, each counting resources tagged
    with it or anything narrower; filter on them with `<field>^`."""
    _limit(request, caller)
    params = _params(request, service, _language(request, lang))
    return await service.facet(
        params, field, contains=contains, size=size, admin=caller.is_admin, tree=tree
    )


@router.get("/index/search/sparql", summary="A faceted search as SPARQL")
async def search_sparql(
    request: Request, service: Search, caller: Caller, lang: str | None = None
) -> dict[str, Any]:
    """The SPARQL equivalent of `GET /index/search` with the same parameters.

    Returns `query` (with `PREFIX` lines), `prefixes` and `approximation`:
    the parts SPARQL can only approximate (`fulltext`, `ranking`, `dates`,
    `harvested`, `type`), also explained in comments at the top of the query.
    """
    _limit(request, caller)
    params = _params(request, service, _language(request, lang))
    return await service.sparql(params, admin=caller.is_admin)


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


@router.get("/index/search/suggest", summary="Suggestions while typing")
async def suggest(
    request: Request,
    service: Search,
    caller: Caller,
    q: str = "",
    type: Annotated[list[str] | None, Query()] = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Resources whose title contains `q`, and filter values (keywords, subjects, themes,
    publishers) whose name starts with it, with counts. At least two characters."""
    _limit(request, caller)
    kinds = [t for v in type or [] for t in v.split(",") if t]
    unknown = [t for t in kinds if t not in KINDS]
    if unknown:
        raise BadRequest(f"Unknown type: {', '.join(unknown)}")
    wanted: list[Kind] = [t for t in KINDS if t in kinds] or list(DEFAULT_TYPES)
    return await service.suggest(
        q, types=wanted, lang=_language(request, lang), admin=caller.is_admin
    )


@router.get("/index/search/properties", summary="Properties to add as facets")
async def properties(
    request: Request,
    service: Search,
    caller: Caller,
    type: Annotated[list[str] | None, Query()] = None,
    contains: str = "",
) -> dict[str, Any]:
    """Properties of the harvested metadata, with the number of resources that have them.

    Each is a facet and a filter named `prop:<IRI>` on `/index/search`.
    Values are IRIs and short literals, one step from the resource. Visitors
    see the properties the index offers (`visitors` false: none).
    """
    _limit(request, caller)
    kinds = [t for v in type or [] for t in v.split(",") if t]
    unknown = [t for t in kinds if t not in KINDS]
    if unknown:
        raise BadRequest(f"Unknown type: {', '.join(unknown)}")
    wanted: list[Kind] = [t for t in KINDS if t in kinds] or list(DEFAULT_TYPES)
    return await service.properties(wanted, admin=caller.is_admin, contains=contains[:100])


@router.get(
    "/index/search/settings",
    summary="Search settings",
    dependencies=[Depends(require_admin)],
)
async def get_search_settings(service: Search) -> SearchSettingsView:
    return await service.search_settings.view()


@router.put(
    "/index/search/settings",
    summary="Change the search settings",
    dependencies=[Depends(require_admin)],
)
async def put_search_settings(service: Search, body: SearchSettings) -> SearchSettingsView:
    """Default facets (order, `main`/`more`/`hidden`, labels) and property facets.

    `facets` lists profile facets, `fdp` and `prop:<IRI>`; facets left out keep
    the profile's place after the listed ones. `properties.visitors` lets
    visitors add facets on any property, except those in `properties.hidden`.
    """
    try:
        return await service.search_settings.update(body)
    except ValueError as err:
        raise BadRequest(str(err)) from err


@router.delete(
    "/index/search/settings",
    summary="Restore the default search settings",
    dependencies=[Depends(require_admin)],
)
async def delete_search_settings(service: Search) -> SearchSettingsView:
    return await service.search_settings.reset()


class ReindexRequest(BaseModel):
    entry: UUID | None = Field(default=None, description="One entry; all when absent")


@router.get(
    "/index/search/profile",
    summary="The search profile",
    dependencies=[Depends(require_admin)],
)
async def profile(service: Search) -> dict[str, Any]:
    """The profile in use, and the administrator's overlay on the bundled one (if any)."""
    return await _profile_view(service)


async def _profile_view(service: SearchService) -> dict[str, Any]:
    return {
        "hash": service.profile.hash,
        "profile": service.profile.model_dump(mode="json"),
        "overlay": await service.profiles.overlay(),
        "indexing": not service.indexer.idle.is_set(),
        "examples": [{"name": e.name, "title": e.title} for e in examples()],
    }


@router.get(
    "/index/search/profile/examples/{name}",
    summary="A bundled example overlay",
    dependencies=[Depends(require_admin)],
)
async def profile_example(name: str) -> dict[str, Any]:
    """An overlay to start from (`healthdcat-ap`: HealthDCAT-AP's number, yes/no,
    vocabulary and agent fields): as YAML with its comments, and as an object. Apply
    it with `PUT /index/search/profile`."""
    for example in examples():
        if example.name == name:
            return {
                "name": example.name,
                "title": example.title,
                "yaml": example.text,
                "overlay": yaml.safe_load(example.text),
            }
    raise NotFound(f"No example profile named {name}")


@router.put(
    "/index/search/profile",
    summary="Change the search profile",
    dependencies=[Depends(require_admin)],
)
async def put_profile(request: Request, service: Search) -> dict[str, Any]:
    """Store an overlay on the bundled profile, as JSON or YAML; every entry is re-projected.

    Sections: `prefixes`, `fields` (a field by name replaces or adds one,
    `null` removes one), `types`, `normalise` (entries added), `cards`,
    `landing`, `capture`. A new field with `facet: main` or `more` is a
    facet and a filter at once; its counts appear as entries are projected.
    """
    raw = await request.body()
    try:
        if "yaml" in request.headers.get("content-type", ""):
            overlay = yaml.safe_load(raw.decode("utf-8")) or {}
        else:
            overlay = json.loads(raw or b"{}")
    except (ValueError, yaml.YAMLError) as err:
        raise BadRequest(f"Not JSON or YAML: {err}") from err
    if not isinstance(overlay, dict):
        raise BadRequest("The profile overlay is an object")
    try:
        await service.change_profile(overlay)  # pyright: ignore[reportUnknownArgumentType]
    except ProfileError as err:
        raise BadRequest(f"Invalid profile: {err}") from err
    return await _profile_view(service)


@router.delete(
    "/index/search/profile",
    summary="Restore the bundled search profile",
    dependencies=[Depends(require_admin)],
)
async def delete_profile(service: Search) -> dict[str, Any]:
    await service.change_profile(None)
    return await _profile_view(service)


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
