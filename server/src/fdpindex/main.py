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
from fdpindex.api.routes import router
from fdpindex.api.sparql import router as sparql_router
from fdpindex.auth import Authenticator, OidcVerifier
from fdpindex.config import Settings, get_settings
from fdpindex.db import create_schema, make_engine, make_session_factory
from fdpindex.errors import register_exception_handlers
from fdpindex.health import HealthScheduler
from fdpindex.jobs import JobRunner
from fdpindex.logging import configure_logging
from fdpindex.outbound import OutboundClient, system_resolver
from fdpindex.service import IndexService
from fdpindex.settings import SettingsService
from fdpindex.sparql.prefixes import PREFIXES
from fdpindex.sparql.service import Limits, RateLimiter, SparqlService
from fdpindex.sparql.store import SparqlStore
from fdpindex.sparql.sync import SparqlSync
from fdpindex.sparql.workers import WorkerPool
from fdpindex.webhooks import WebhookService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
            OidcVerifier(config.oidc_issuer, config.oidc_audience, http)
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
        app.state.session_factory = factory
        app.state.jobs = jobs
        app.state.settings_service = settings_service
        app.state.webhooks = webhooks
        app.state.index = index
        app.state.authenticator = authenticator
        app.state.data_rate = RateLimiter(30)  # anonymous harvested-data downloads per minute
        sparql = await _start_sparql(config, factory, settings_service, index)
        app.state.sparql = sparql
        if resume_on_startup:
            await index.resume_unfinished()
        scheduler = HealthScheduler(
            index, sweep_seconds=config.health_sweep_seconds, batch=config.health_batch
        )
        if run_scheduler:
            scheduler.start()
        log.info("fdpindex_started", version=__version__)
        try:
            yield
        finally:
            await scheduler.stop()
            await jobs.close()
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
    return app


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
