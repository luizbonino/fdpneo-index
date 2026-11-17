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

from fdpindex import __version__
from fdpindex.api.routes import router
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
from fdpindex.webhooks import WebhookService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)


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

        app.state.config = config
        app.state.session_factory = factory
        app.state.jobs = jobs
        app.state.settings_service = settings_service
        app.state.webhooks = webhooks
        app.state.index = index
        app.state.authenticator = authenticator
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
            await http.aclose()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="FDP Index",
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
    app.include_router(router)
    return app


def app() -> FastAPI:  # pragma: no cover - uvicorn factory entry point
    return create_app()
