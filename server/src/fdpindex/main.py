"""Application assembly: ``create_app()`` wires configuration, storage and services.

Run with ``uvicorn fdpindex.main:app --proxy-headers``: the per-IP ping rate
limit needs the client address, so behind a reverse proxy set
``FORWARDED_ALLOW_IPS`` to the proxy's address.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from fdpindex import __version__
from fdpindex.api.catalog import router as catalog_router
from fdpindex.api.conformance import router as conformance_router
from fdpindex.api.routes import router
from fdpindex.api.search import router as search_router
from fdpindex.api.sparql import router as sparql_router
from fdpindex.api.stats import router as stats_router
from fdpindex.auth import Authenticator, OidcVerifier
from fdpindex.catalog.feed import CatalogFeed, CatalogMeta
from fdpindex.config import Settings, get_settings
from fdpindex.conformance.packs import discover as discover_packs
from fdpindex.conformance.service import ConformanceService
from fdpindex.conformance.settings import ConformanceSettingsService
from fdpindex.conformance.workers import ValidationPool, default_workers
from fdpindex.db import create_schema, make_engine, make_session_factory
from fdpindex.deployment import deployment_profile
from fdpindex.errors import register_exception_handlers
from fdpindex.health import HealthScheduler
from fdpindex.jobs import JobRunner
from fdpindex.logging import configure_logging
from fdpindex.outbound import OutboundClient, system_resolver
from fdpindex.search.concepts import ConceptResolver, ResolverSettings
from fdpindex.search.indexer import SearchIndexer
from fdpindex.search.profile_store import ProfileStore, set_default_overlay
from fdpindex.search.query import SearchService
from fdpindex.search.semantic import SemanticIndex
from fdpindex.service import IndexService
from fdpindex.settings import SettingsService
from fdpindex.sparql.prefixes import PREFIXES
from fdpindex.sparql.service import Limits, RateLimiter, SparqlService
from fdpindex.sparql.store import SparqlStore
from fdpindex.sparql.sync import SparqlSync
from fdpindex.sparql.workers import WorkerPool
from fdpindex.stats import StatsScheduler, StatsService
from fdpindex.webhooks import WebhookService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.search.profile import Profile

log = structlog.get_logger(__name__)

MAX_BODY_BYTES = 1024 * 1024  # pings, settings, queries: all far smaller


class BodyLimit:
    """Refuse request bodies over ``max_bytes`` (413), declared or streamed.

    nginx caps bodies in the Compose stack; this protects a server run without it.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await _too_large(send)
            return
        seen = 0

        async def limited() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited, send)
        except _BodyTooLarge:
            await _too_large(send)


class _BodyTooLarge(Exception):  # noqa: N818 - control flow
    pass


async def _too_large(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {"type": "http.response.body", "body": b'{"status":413,"message":"Request body too large"}'}
    )


def create_app(
    config: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Callable[[str, int], Awaitable[list[str]]] | None = None,
    resume_on_startup: bool = True,
    create_tables: bool = False,
    run_scheduler: bool = True,
    configure_logs: bool = True,
) -> FastAPI:
    """Build the app. The keyword arguments exist for tests (an in-memory database, a mocked network)."""
    config = config or get_settings()
    deployment = deployment_profile(config.deployment_profile)
    set_default_overlay(deployment.search_overlay())
    if configure_logs:
        configure_logging(config.environment)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = None
        factory = session_factory
        if factory is None:
            engine = make_engine(config.database_url)
            if create_tables:
                await create_schema(engine)
            factory = make_session_factory(engine)
        http = httpx.AsyncClient(transport=transport, follow_redirects=False)
        outbound = OutboundClient(
            http,
            allow_private=config.outbound_allow_private,
            max_bytes=config.outbound_max_bytes,
            max_redirects=config.outbound_max_redirects,
            user_agent=config.user_agent,
            resolver=resolver or system_resolver,
        )
        jobs = JobRunner(config.worker_concurrency)
        settings_service = SettingsService(factory, auto_permit_default=config.auto_permit)
        webhooks = WebhookService(
            factory, settings_service, outbound, jobs, body_limit=config.event_body_max_chars
        )
        index = IndexService(
            factory,
            settings_service,
            outbound,
            jobs,
            webhooks,
            body_limit=config.event_body_max_chars,
            harvest_max_nodes=config.harvest_max_nodes,
            harvest_max_bytes=config.harvest_max_bytes,
            harvest_max_seconds=config.harvest_max_seconds,
            harvest_node_timeout=config.harvest_node_timeout_seconds,
        )
        verifier = (
            OidcVerifier(
                config.oidc_issuer,
                config.oidc_audience,
                http,
                discovery_url=config.oidc_discovery_url,
            )
            if config.oidc_issuer
            else None
        )
        authenticator = Authenticator(config, verifier)
        if not authenticator.configured:
            log.warning(
                "no_administrators_configured",
                hint="set FDPINDEX_OIDC_ISSUER or FDPINDEX_ADMIN_TOKENS",
            )
        if config.oidc_issuer and not config.oidc_audience:
            log.warning(
                "oidc_audience_not_set",
                hint="set FDPINDEX_OIDC_AUDIENCE: without it, a token the issuer gave to any "
                "client of the realm is accepted",
            )

        app.state.config = config
        app.state.conformance_packs = discover_packs(config.conformance_packs)
        app.state.session_factory = factory
        app.state.jobs = jobs
        app.state.settings_service = settings_service
        app.state.webhooks = webhooks
        app.state.index = index
        app.state.authenticator = authenticator
        app.state.data_rate = RateLimiter(30)  # anonymous harvested-data downloads per minute
        sparql = await _start_sparql(config, factory, settings_service, index)
        app.state.sparql = sparql
        search = await _start_search(
            config, factory, settings_service, index, http, resolver or system_resolver
        )
        app.state.search = search
        app.state.search_rate = RateLimiter(config.search_rate_per_minute)
        conformance_settings = ConformanceSettingsService(
            factory, app.state.conformance_packs, deployment.conformance
        )
        app.state.conformance_settings = conformance_settings
        conformance = await _start_conformance(
            config, factory, index, search, conformance_settings, sparql
        )
        app.state.conformance = conformance
        app.state.catalog_feed = _catalog_feed(config, factory, index, search, conformance)
        stats = StatsService(factory, index, search, conformance)
        app.state.stats = stats
        if resume_on_startup:
            await index.resume_unfinished()
        scheduler = HealthScheduler(
            index, sweep_seconds=config.health_sweep_seconds, batch=config.health_batch
        )
        stats_scheduler = StatsScheduler(stats, seconds=config.stats_snapshot_seconds)
        if run_scheduler:
            scheduler.start()
            stats_scheduler.start()
        log.info("fdpindex_started", version=__version__)
        try:
            yield
        finally:
            await scheduler.stop()
            await stats_scheduler.stop()
            await jobs.close()
            if conformance is not None:
                await conformance.stop()
            if search is not None:
                if search.semantic is not None:
                    await search.semantic.stop()
                if search.indexer.resolver is not None:
                    await search.indexer.resolver.stop()
                await search.indexer.stop()
            if sparql is not None:
                if sparql.sync is not None:
                    await sparql.sync.stop()
                await sparql.pool.close()
                sparql.store.close()
            await http.aclose()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="FDPneo Index",
        version=__version__,
        description="A directory of FAIR Data Points: ping intake, reachability, harvest and administration.",
        lifespan=lifespan,
    )
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    register_exception_handlers(app)
    app.add_middleware(BodyLimit, max_bytes=MAX_BODY_BYTES)
    app.include_router(router)
    app.include_router(sparql_router)
    app.include_router(search_router)
    app.include_router(conformance_router)
    app.include_router(catalog_router)
    app.include_router(stats_router)
    return app


def _catalog_feed(
    config: Settings,
    factory: async_sessionmaker[AsyncSession],
    index: IndexService,
    search: SearchService | None,
    conformance: ConformanceService | None,
) -> CatalogFeed | None:
    """The catalogue feed (TASKS 11.5): it lists records from the search projection."""
    if not config.catalog_enabled or search is None:
        return None
    meta = CatalogMeta(
        title=config.catalog_title,
        description=config.catalog_description,
        publisher=config.catalog_publisher or config.catalog_title,
        publisher_url=config.catalog_publisher_url,
        homepage=config.public_url,
    )
    return CatalogFeed(
        factory,
        meta,
        hide_expired=index.hides_expired,
        minimum=(lambda: conformance.minimum("feed")) if conformance is not None else None,
        specs=conformance.specs if conformance is not None else None,
    )


async def _start_conformance(
    config: Settings,
    factory: async_sessionmaker[AsyncSession],
    index: IndexService,
    search: SearchService | None,
    settings: ConformanceSettingsService,
    sparql: SparqlService | None = None,
) -> ConformanceService | None:
    if not config.conformance_enabled:
        return None
    # Record kinds come from the search profile: the one search uses, kept current when an
    # administrator changes it, or the effective profile when search is off.
    fixed = None if search is not None else await ProfileStore(factory).effective()

    def profile() -> Profile:
        if search is not None:
            return search.indexer.profile
        assert fixed is not None
        return fixed

    workers = config.conformance_workers if config.conformance_workers >= 0 else default_workers()
    service = ConformanceService(factory, settings, profile, ValidationPool(workers))
    index.add_listener(service.notify)
    # Conformance reaches the other surfaces (TASKS 11.4): search values and the search
    # minimum, the SPARQL store's minimum, and the public directory.
    if search is not None:
        search.indexer.conformance_specs = service.specs
        search.conformance_minimum = lambda: service.minimum_specification("search")
        service.listeners.append(search.indexer.touch)
    if sparql is not None and sparql.sync is not None:
        sparql.sync.conformance_minimum = lambda: service.minimum("sparql")
        service.listeners.append(sparql.sync.touch)
        service.settings_listeners.append(sparql.sync.reconcile)
    index.directory_condition = service.directory_condition
    service.start()
    log.info("conformance_started", packs=len(settings.packs), workers=workers)
    return service


async def _start_search(
    config: Settings,
    factory: async_sessionmaker[AsyncSession],
    settings_service: SettingsService,
    index: IndexService,
    http: httpx.AsyncClient,
    dns_resolver: Callable[[str, int], Awaitable[list[str]]],
) -> SearchService | None:
    if not config.search_enabled:
        return None
    # The bundled profile with an administrator's changes, if any (TASKS 6.12).
    indexer = SearchIndexer(factory, await ProfileStore(factory).effective())
    index.add_listener(indexer.notify)
    indexer.start()
    log.info("search_started", profile=indexer.profile.hash[:12])
    if config.labels_resolve:
        resolver = ConceptResolver(
            factory,
            OutboundClient(
                http,
                allow_private=config.outbound_allow_private,
                max_bytes=config.labels_max_bytes,
                max_redirects=config.outbound_max_redirects,
                user_agent=config.user_agent,
                resolver=dns_resolver,
            ),
            indexer.profile,
            ResolverSettings(
                enabled=True,
                hosts=frozenset(h.lower() for h in config.labels_hosts),
                languages=tuple(config.labels_languages),
                refresh_days=config.labels_refresh_days,
                retry_days=config.labels_retry_days,
                broader_depth=config.labels_broader_depth,
                interval_seconds=config.labels_interval_seconds,
                requests_per_second=config.labels_requests_per_second,
            ),
            on_changed=indexer.reindex,
        )
        indexer.after_projection = resolver.notify
        resolver.start()
        indexer.resolver = resolver
        log.info("concept_labels_started", hosts=len(config.labels_hosts))
    service = SearchService(
        factory,
        settings_service,
        indexer,
        timeout_seconds=config.search_timeout_seconds,
        admin_timeout_seconds=config.search_admin_timeout_seconds,
    )
    if config.search_embeddings == "local":
        service.semantic = await _start_semantic(config, factory)
    return service


async def _start_semantic(
    config: Settings, factory: async_sessionmaker[AsyncSession]
) -> SemanticIndex | None:
    """The semantic index (TASKS 10.1), or None with the reason logged."""
    if config.embeddings_model_dir is None:
        log.error("semantic_search_off", reason="FDPINDEX_EMBEDDINGS_MODEL_DIR is not set")
        return None
    try:
        from fdpindex.search.semantic import OnnxEmbedder

        embedder = OnnxEmbedder(config.embeddings_model_dir, threads=config.embeddings_threads)
    except (ImportError, OSError, ValueError) as err:
        log.error("semantic_search_off", reason=f"model or extra missing: {err}")
        return None
    semantic = SemanticIndex(factory, embedder, max_distance=config.embeddings_max_distance)
    try:
        await semantic.ensure_schema()
    except Exception as err:
        log.error("semantic_search_off", reason=f"pgvector unavailable: {err}"[:300])
        return None
    semantic.start()
    log.info("semantic_search_started", model=embedder.name)
    return semantic


async def _start_sparql(
    config: Settings,
    factory: async_sessionmaker[AsyncSession],
    settings_service: SettingsService,
    index: IndexService,
) -> SparqlService | None:
    if not config.sparql_enabled:
        return None
    store = SparqlStore(
        config.sparql_dir,
        keep_seconds=2 * max(config.sparql_timeout_seconds, config.sparql_admin_timeout_seconds)
        + 60,
    )
    pool = WorkerPool(
        size=config.sparql_workers,
        prefixes=PREFIXES,
        max_mb=config.sparql_worker_max_mb,
    )
    await pool.start()
    endpoint = (
        f"{config.public_url.rstrip('/')}/index/sparql"
        if config.public_url
        else "urn:fdpindex:sparql"
    )
    sync = SparqlSync(
        store,
        factory,
        settings_service,
        endpoint=endpoint,
        void_interval=config.sparql_void_interval_seconds,
    )
    index.add_listener(sync.notify)
    sync.start()
    log.info("sparql_started", directory=str(config.sparql_dir), workers=config.sparql_workers)
    return SparqlService(
        store,
        pool,
        sync,
        Limits(
            timeout=config.sparql_timeout_seconds,
            admin_timeout=config.sparql_admin_timeout_seconds,
            max_rows=config.sparql_max_rows,
            max_bytes=config.sparql_max_bytes,
            max_query_chars=config.sparql_max_query_chars,
            per_minute=config.sparql_rate_per_minute,
        ),
    )


def app() -> FastAPI:  # pragma: no cover - uvicorn factory entry point
    return create_app()
