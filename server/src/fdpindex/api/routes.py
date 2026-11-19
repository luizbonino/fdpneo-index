"""HTTP routes.

Reference paths, unchanged: ``POST /`` (ping), ``/index/entries`` (+ ``/all``,
``/info``, ``/{uuid}``, ``/{uuid}/data``), ``/index/settings``,
``/index/admin/trigger``, ``/index/admin/trigger-all``,
``/index/admin/ping-webhook``. Additions: ``/index/entries/{uuid}/events``,
``/index/events/{uuid}``, ``/index/webhooks``, ``/index/config``, and the
operational ``/healthz``, ``/readyz``.
"""

from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from fdpindex import __version__
from fdpindex.api.schemas import (
    EntriesInfoView,
    EntryCreateRequest,
    EntryDetailView,
    EntryOptionsRequest,
    EntryOptionsView,
    EntryPageView,
    EntryUpdateRequest,
    EntryView,
    EventDetailView,
    EventPageView,
    EventView,
    HarvestView,
    HealthView,
    PingRequest,
    RegisterRequest,
    RegistrationView,
    RepositoryMetadataView,
    ServiceInfo,
    TriggerRequest,
    WebhookRequest,
    WebhookView,
    instant,
)
from fdpindex.auth import Principal, current_principal, require_admin
from fdpindex.durations import format_duration, parse_duration
from fdpindex.errors import BadRequest, NotFound, RateLimited
from fdpindex.models import EntryPermit
from fdpindex.service import IndexService, api_state
from fdpindex.settings import IndexSettings, IndexSettingsView, SettingsService
from fdpindex.webhooks import WebhookService

log = structlog.get_logger(__name__)

MAX_PAGE_SIZE = 2000  # Spring Data's default cap

# Readable prefixes for harvested data (rdflib invents ns1, ns2, … otherwise).
PREFIXES = {
    "ldp": "http://www.w3.org/ns/ldp#",
    "fdp": "https://w3id.org/fdp/fdp-o#",
    "r3d": "http://www.re3data.org/schema/3-0#",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "sio": "http://semanticscience.org/resource/",
    "odrl": "http://www.w3.org/ns/odrl/2/",
}

RDF_TYPES = {
    "text/turtle": "turtle",
    "application/ld+json": "json-ld",
    "application/n-triples": "nt",
    "application/rdf+xml": "xml",
}


def _index(request: Request) -> IndexService:
    return request.app.state.index


def _settings(request: Request) -> SettingsService:
    return request.app.state.settings_service


def _webhooks(request: Request) -> WebhookService:
    return request.app.state.webhooks


def public_url(request: Request, route: str) -> str:
    """An absolute URL of a route: from FDPINDEX_PUBLIC_URL when set, not from the Host header."""
    base: str | None = request.app.state.config.public_url
    path = request.app.url_path_for(route)
    if base:
        return base.rstrip("/") + str(path)
    return str(request.url_for(route))


def _remote(request: Request) -> str | None:
    return request.client.host if request.client else None


def _permits(principal: Principal, query: str) -> list[EntryPermit]:
    """Anonymous callers see accepted entries only; admins choose (``ALL`` or a list)."""
    if not principal.is_admin:
        return [EntryPermit.ACCEPTED]
    wanted = {item.strip().upper() for item in query.split(",") if item.strip()}
    if "ALL" in wanted:
        return list(EntryPermit)
    return [permit for permit in EntryPermit if permit.value in wanted]


Index = Annotated[IndexService, Depends(_index)]
SettingsDep = Annotated[SettingsService, Depends(_settings)]
Webhooks = Annotated[WebhookService, Depends(_webhooks)]
Caller = Annotated[Principal, Depends(current_principal)]
Admin = Annotated[Principal, Depends(require_admin)]

router = APIRouter()


# --- ping intake ---------------------------------------------------------------


@router.post(
    "/",
    status_code=204,
    tags=["Index"],
    summary="Ping: announce a running FAIR Data Point",
    description=(
        "Inform about a running FAIR Data Point. It is expected to send pings regularly "
        "(at least weekly). There is a rate limit per IP address within a period of time."
    ),
    responses={
        400: {"description": "Invalid ping"},
        403: {"description": "URL denied"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def receive_ping(ping: PingRequest, request: Request, index: Index) -> Response:
    raw = (await request.body()).decode("utf-8", errors="replace")
    headers: dict[str, list[str]] = {}
    for key, value in request.headers.items():
        if key.lower() == "authorization":
            continue
        headers.setdefault(key.lower(), []).append(value)
    outcome = await index.accept_ping(
        ping.client_url,
        remote_addr=_remote(request),
        method=request.method,
        path=request.url.path,
        headers=headers,
        body=raw,
    )
    index.after_trigger(outcome.event_id)
    return Response(status_code=204)


@router.get("/", tags=["Service"], response_model=ServiceInfo)
async def service_info(request: Request) -> ServiceInfo:
    return _service_info(request)


@router.get("/index/config", tags=["Service"], response_model=ServiceInfo)
async def client_config(request: Request) -> ServiceInfo:
    """What the web client needs to know: version, and how administrators sign in."""
    return _service_info(request)


def _service_info(request: Request) -> ServiceInfo:
    config = request.app.state.config
    auth: list[str] = []
    if config.oidc_issuer:
        auth.append("oidc")
    if config.admin_tokens:
        auth.append("token")
    return ServiceInfo(
        name="FDPneo Index",
        version=__version__,
        ping=public_url(request, "receive_ping"),
        oidc={"issuer": config.oidc_issuer, "clientId": config.oidc_client_id}
        if config.oidc_issuer
        else None,
        auth=auth,  # pyright: ignore[reportArgumentType]
        sparql={
            "endpoint": public_url(request, "sparql_get"),
            "timeoutSeconds": config.sparql_timeout_seconds,
            "maxRows": config.sparql_max_rows,
            "perMinute": config.sparql_rate_per_minute,
        }
        if getattr(request.app.state, "sparql", None) is not None
        else None,
        search={
            "endpoint": public_url(request, "search"),
            "perMinute": config.search_rate_per_minute,
        }
        if getattr(request.app.state, "search", None) is not None
        else None,
        catalog={
            "feed": public_url(request, "catalog"),
            "oai": public_url(request, "oai_get"),
        }
        if getattr(request.app.state, "catalog_feed", None) is not None
        else None,
    )


# --- entries -----------------------------------------------------------------------


@router.get("/index/entries", tags=["Index"], response_model=EntryPageView)
async def list_entries(
    index: Index,
    caller: Caller,
    state: str = "",
    permit: str = "accepted",
    q: str = "",
    page: Annotated[int, Query(ge=0)] = 0,
    size: Annotated[int, Query(ge=1)] = 20,
    sort: Annotated[list[str] | None, Query()] = None,
) -> EntryPageView:
    size = min(size, MAX_PAGE_SIZE)
    result = await index.list_entries(
        permits=_permits(caller, permit),
        state=state,
        query=q,
        sort=sort,
        page=page,
        size=size,
        public=not caller.is_admin,
    )
    threshold = await index.valid_threshold()
    content = [EntryView.of(entry, api_state(entry, threshold)) for entry in result.items]
    total_pages = math.ceil(result.total / size) if result.total else 0
    return EntryPageView(
        content=content,
        total_elements=result.total,
        total_pages=total_pages,
        number=page,
        size=size,
        number_of_elements=len(content),
        first=page == 0,
        last=page >= total_pages - 1,
        empty=not content,
    )


@router.get("/index/entries/all", tags=["Index"], response_model=list[EntryView])
async def all_entries(index: Index, caller: Caller, permit: str = "accepted") -> list[EntryView]:
    threshold = await index.valid_threshold()
    entries = await index.all_entries(_permits(caller, permit), public=not caller.is_admin)
    return [EntryView.of(entry, api_state(entry, threshold)) for entry in entries]


@router.get("/index/entries/info", tags=["Index"], response_model=EntriesInfoView)
async def entries_info(index: Index, caller: Caller, permit: str = "accepted") -> EntriesInfoView:
    return EntriesInfoView(
        entries_count=await index.counts(_permits(caller, permit), public=not caller.is_admin)
    )


async def _visible(index: IndexService, caller: Principal, entry_id: UUID) -> None:
    entry = await index.get_entry(entry_id)
    if not caller.is_admin and entry.permit is not EntryPermit.ACCEPTED:
        # Not visible to the public: as if it did not exist.
        raise NotFound("Index entry not found")


async def _detail(index: IndexService, entry_id: UUID) -> EntryDetailView:
    entry = await index.get_entry(entry_id)
    threshold = await index.valid_threshold()
    events, _total = await index.events(entry_id)
    nodes, harvested_at = await index.harvest_summary(entry_id)
    metadata = None
    if entry.last_retrieval_at is not None:
        metadata = RepositoryMetadataView(
            metadata_version=entry.metadata_version,
            repository_uri=entry.repository_uri,
            metadata=dict(entry.repository_metadata or {}),
        )
    return EntryDetailView(
        uuid=str(entry.uuid),
        client_url=entry.client_url,
        state=api_state(entry, threshold),
        permit=entry.permit,
        current_metadata=metadata,
        events=[EventView.of(event) for event in events],
        registration_time=instant(entry.created_at) or "",
        modification_time=instant(entry.updated_at) or "",
        last_retrieval_time=instant(entry.last_retrieval_at),
        harvest=HarvestView(nodes=nodes, harvested_at=instant(harvested_at)),
        health=HealthView(
            next_check_time=instant(entry.next_check_at),
            last_success_time=instant(entry.last_success_at),
            consecutive_failures=entry.consecutive_failures,
        ),
        options=EntryOptionsView(
            harvest=entry.harvest_enabled,
            harvest_interval=format_duration(timedelta(seconds=entry.harvest_interval_seconds))
            if entry.harvest_interval_seconds
            else None,
        ),
        expired_time=instant(entry.expired_at),
    )


@router.get("/index/entries/{entry_id}", tags=["Index"], response_model=EntryDetailView)
async def get_entry(entry_id: UUID, index: Index, caller: Caller) -> EntryDetailView:
    await _visible(index, caller, entry_id)
    return await _detail(index, entry_id)


@router.put("/index/entries/{entry_id}", tags=["Index"], response_model=EntryDetailView)
async def update_entry(
    entry_id: UUID, body: EntryUpdateRequest, request: Request, index: Index, admin: Admin
) -> EntryDetailView:
    """Change the permit (as in the reference) and the per-FDP options (addition).

    `options.harvest: false` makes the entry directory only: it stays listed
    and checked, and its harvested metadata is removed from search and SPARQL.
    `options.harvestInterval` (ISO-8601, at least an hour; empty clears it)
    harvests again at that interval even when the root metadata did not
    change; it takes effect at the entry's checks.
    """
    if body.options is not None:
        await _apply_options(index, entry_id, body.options)
    if body.permit is not None:
        await index.update_permit(
            entry_id, body.permit, actor=admin.name, remote_addr=_remote(request)
        )
    return await _detail(index, entry_id)


def _interval(value: str | None) -> timedelta | None:
    if not value:
        return None
    try:
        interval = parse_duration(value)
    except ValueError as err:
        raise BadRequest(f"harvestInterval: {err}") from err
    if interval < timedelta(hours=1):
        raise BadRequest("harvestInterval: at least an hour")
    return interval


async def _apply_options(index: IndexService, entry_id: UUID, options: EntryOptionsRequest) -> None:
    interval = _interval(options.harvest_interval)
    clear = options.harvest_interval == ""
    await index.update_options(
        entry_id, harvest_enabled=options.harvest, harvest_interval=interval, clear_interval=clear
    )


@router.post(
    "/index/entries",
    tags=["Index"],
    status_code=201,
    response_model=EntryDetailView,
    summary="Add an FDP (administrators)",
)
async def create_entry(
    body: EntryCreateRequest, request: Request, index: Index, admin: Admin
) -> EntryDetailView:
    """Register an FDP and retrieve its root metadata now (addition).

    The entry gets `permit` (default: the approval policy's) and `options`;
    an existing entry with that URL is updated instead.
    """
    options = body.options or EntryOptionsRequest()
    outcome = await index.register(
        body.client_url,
        remote_addr=_remote(request),
        actor=admin.name,
        permit=body.permit,
        harvest_enabled=options.harvest,
        harvest_interval=_interval(options.harvest_interval),
    )
    return await _detail(index, outcome.entry.uuid)


@router.post(
    "/index/register",
    tags=["Index"],
    response_model=RegistrationView,
    summary="Register an FDP and check it now",
    responses={
        400: {"description": "Not an http(s) URL"},
        403: {"description": "URL denied"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def register(body: RegisterRequest, request: Request, index: Index) -> RegistrationView:
    """Self-service registration (addition): a ping whose retrieval runs before the answer.

    The same deny list, rate limit and approval policy apply as to `POST /`.
    The answer says whether the FDP's root metadata was found and valid, and
    whether the entry waits for approval.
    """
    outcome = await index.register(body.client_url, remote_addr=_remote(request))
    entry = outcome.entry
    threshold = await index.valid_threshold()
    return RegistrationView(
        uuid=str(entry.uuid),
        client_url=entry.client_url,
        new_entry=outcome.new_entry,
        state=api_state(entry, threshold),
        permit=entry.permit,
        retrieved=outcome.retrieved,
        error=outcome.error,
        title=(entry.repository_metadata or {}).get("title"),
    )


@router.delete(
    "/index/entries/{entry_id}",
    tags=["Index"],
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_entry(entry_id: UUID, index: Index) -> Response:
    await index.delete_entry(entry_id)
    return Response(status_code=204)


@router.get(
    "/index/entries/{entry_id}/data",
    tags=["Index"],
    responses={200: {"content": {media: {} for media in RDF_TYPES}}},
)
async def entry_data(
    entry_id: UUID, request: Request, index: Index, caller: Caller, format: str | None = None
) -> Response:
    """The harvested metadata of an entry, as Turtle (default), JSON-LD, N-Triples or RDF/XML.

    N-Triples are served as stored; other formats are rendered in a thread (a
    large harvest takes seconds), and anonymous callers are rate limited.
    """
    await _visible(index, caller, entry_id)
    if not caller.is_admin and not request.app.state.data_rate.allow(_remote(request) or "?"):
        raise RateLimited("Too many downloads; wait a minute and try again.")
    media = _negotiate(format, request.headers.get("accept", ""))
    if media == "application/n-triples":
        return Response(content=await index.harvested_ntriples(entry_id), media_type=media)
    graph = await index.harvested_graph(entry_id)

    def render() -> str:
        for prefix, namespace in PREFIXES.items():
            graph.bind(prefix, namespace, override=False)
        return graph.serialize(format=RDF_TYPES[media])

    return Response(content=await asyncio.to_thread(render), media_type=media)


def _negotiate(explicit: str | None, accept: str) -> str:
    aliases = {
        "ttl": "text/turtle",
        "turtle": "text/turtle",
        "jsonld": "application/ld+json",
        "nt": "application/n-triples",
        "rdf": "application/rdf+xml",
    }
    if explicit:
        media = aliases.get(explicit.lower(), explicit)
        if media not in RDF_TYPES:
            raise BadRequest(f"Unsupported format: {explicit}")
        return media
    for part in accept.split(","):
        media = part.split(";", 1)[0].strip().lower()
        if media in RDF_TYPES:
            return media
    return "text/turtle"


@router.get(
    "/index/entries/{entry_id}/events",
    tags=["Index"],
    response_model=EventPageView,
    dependencies=[Depends(require_admin)],
)
async def entry_events(
    entry_id: UUID,
    index: Index,
    page: Annotated[int, Query(ge=0)] = 0,
    size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EventPageView:
    await index.get_entry(entry_id)
    events, total = await index.events(entry_id, limit=size, offset=page * size)
    return EventPageView(
        content=[EventDetailView.of(event) for event in events],
        total_elements=total,
        number=page,
        size=size,
    )


@router.get(
    "/index/events/{event_id}",
    tags=["Index"],
    response_model=EventDetailView,
    dependencies=[Depends(require_admin)],
)
async def get_event(event_id: UUID, index: Index) -> EventDetailView:
    return EventDetailView.of(await index.get_event(event_id))


# --- settings ----------------------------------------------------------------------


@router.get(
    "/index/settings",
    tags=["Index"],
    response_model=IndexSettingsView,
    dependencies=[Depends(require_admin)],
)
async def get_settings(settings: SettingsDep) -> IndexSettingsView:
    return await settings.view()


@router.put(
    "/index/settings",
    tags=["Index"],
    response_model=IndexSettingsView,
    dependencies=[Depends(require_admin)],
)
async def update_settings(body: IndexSettings, settings: SettingsDep) -> IndexSettingsView:
    return await settings.update(body)


@router.delete(
    "/index/settings",
    tags=["Index"],
    response_model=IndexSettingsView,
    dependencies=[Depends(require_admin)],
)
async def reset_settings(settings: SettingsDep) -> IndexSettingsView:
    return await settings.reset()


# --- admin triggers ---------------------------------------------------------------------


@router.post("/index/admin/trigger", tags=["Index"], status_code=204)
async def trigger(body: TriggerRequest, request: Request, index: Index, admin: Admin) -> Response:
    """Retrieve (and, for accepted entries, harvest) one FDP now; creates the entry if missing."""
    event_id = await index.admin_trigger(
        body.client_url, actor=admin.name, remote_addr=_remote(request)
    )
    index.after_trigger(event_id)
    return Response(status_code=204)


@router.post("/index/admin/trigger-all", tags=["Index"], status_code=204)
async def trigger_all(request: Request, index: Index, admin: Admin) -> Response:
    event_id = await index.admin_trigger(None, actor=admin.name, remote_addr=_remote(request))
    index.after_trigger(event_id)
    return Response(status_code=204)


@router.post("/index/admin/ping-webhook", tags=["Index"], status_code=204)
async def ping_webhook(
    webhook: UUID, request: Request, webhooks: Webhooks, admin: Admin
) -> Response:
    await webhooks.ping(webhook, actor=admin.name, remote_addr=_remote(request))
    return Response(status_code=204)


# --- webhooks (addition) -------------------------------------------------------------------


@router.get(
    "/index/webhooks",
    tags=["Webhooks"],
    response_model=list[WebhookView],
    dependencies=[Depends(require_admin)],
)
async def list_webhooks(webhooks: Webhooks) -> list[WebhookView]:
    return [WebhookView.of(webhook) for webhook in await webhooks.list_webhooks()]


@router.post(
    "/index/webhooks",
    tags=["Webhooks"],
    response_model=WebhookView,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_webhook(body: WebhookRequest, webhooks: Webhooks) -> WebhookView:
    if not body.secret:
        raise BadRequest("A new webhook needs a secret (at least 8 characters)")
    return WebhookView.of(await webhooks.save_webhook(None, _webhook_values(body)))


@router.get(
    "/index/webhooks/{webhook_id}",
    tags=["Webhooks"],
    response_model=WebhookView,
    dependencies=[Depends(require_admin)],
)
async def get_webhook(webhook_id: UUID, webhooks: Webhooks) -> WebhookView:
    return WebhookView.of(await webhooks.get_webhook(webhook_id))


@router.put(
    "/index/webhooks/{webhook_id}",
    tags=["Webhooks"],
    response_model=WebhookView,
    dependencies=[Depends(require_admin)],
)
async def update_webhook(webhook_id: UUID, body: WebhookRequest, webhooks: Webhooks) -> WebhookView:
    return WebhookView.of(await webhooks.save_webhook(webhook_id, _webhook_values(body)))


@router.delete(
    "/index/webhooks/{webhook_id}",
    tags=["Webhooks"],
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_webhook(webhook_id: UUID, webhooks: Webhooks) -> Response:
    await webhooks.delete_webhook(webhook_id)
    return Response(status_code=204)


def _webhook_values(body: WebhookRequest) -> dict[str, object]:
    return {
        "payload_url": body.payload_url,
        "secret": body.secret,
        "all_events": body.all_events,
        "events": sorted({event.value for event in body.events}),
        "all_entries": body.all_entries,
        "entries": body.entries,
        "enabled": body.enabled,
    }


# --- operations ---------------------------------------------------------------------------


@router.get("/healthz", tags=["Service"], include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", tags=["Service"], include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        # The detail (hosts, database names) goes to the log, not to the caller.
        log.exception("readiness_check_failed")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok", "version": __version__})
