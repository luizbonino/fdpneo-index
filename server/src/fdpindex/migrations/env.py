"""Alembic environment: async engine, DSN from FDPINDEX_DATABASE_URL or the caller."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from fdpindex import models  # noqa: F401  # pyright: ignore[reportUnusedImport]  (registers the tables)
from fdpindex.config import Settings
from fdpindex.db import Base

config = context.config

# ``fdpindex db migrate`` runs in its own process and logs through alembic.ini.
# A caller inside a configured process sets ``configure_logger`` to False:
# fileConfig would replace its log handler and disable its existing loggers.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return str(config.attributes.get("database_url") or Settings().database_url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
