"""Structured logging: JSON lines in production, readable lines in development."""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")


def configure_logging(environment: Literal["development", "production"]) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if environment == "development"
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if environment == "development" else logging.INFO)
    # Driver chatter drowns everything else at DEBUG.
    for name in ("aiosqlite", "asyncio", "httpcore", "httpx"):
        logging.getLogger(name).setLevel(logging.INFO)
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        # logging.config.fileConfig (alembic.ini) disables existing loggers.
        logger.disabled = False
