"""Importing a Java FAIR Data Point's index (TASKS 3.4)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from sqlalchemy import func, select
from testcontainers.community.postgres import PostgresContainer

from fdpindex.cli import alembic_config
from fdpindex.db import make_engine, make_session_factory
from fdpindex.importer import import_java
from fdpindex.models import EntryPermit, EntryState, Event, IndexEntry, Webhook
from fdpindex.settings import SettingsService

pytestmark = pytest.mark.integration

SCHEMA = (Path(__file__).parent / "java_index_schema.sql").read_text(encoding="utf-8")

E1 = "11111111-1111-1111-1111-111111111111"
E2 = "22222222-2222-2222-2222-222222222222"
HOOK = "33333333-3333-3333-3333-333333333333"
SETTINGS = "44444444-4444-4444-4444-444444444444"

SEED = f"""
INSERT INTO index_settings VALUES ('{SETTINGS}', false, 'PT5M', 'PT30S', 'PT336H', 'PT1H', 3, now(), now());
INSERT INTO index_settings_ping_deny VALUES ('{SETTINGS}', '^https://spam\\..*$');
INSERT INTO index_entry VALUES
  ('{E1}', 'https://fdp-one.example.org', 'Valid', 'ACCEPTED', 1, 'https://fdp-one.example.org',
   '{{"title": "FDP one", "publisherName": "Uni"}}', now() - interval '1 day', now() - interval '30 days', now()),
  ('{E2}', 'https://already.example.org', 'Unknown', 'PENDING', 1, NULL, '{{}}', NULL, now(), now());
INSERT INTO index_event VALUES
  ('a0000000-0000-0000-0000-000000000001', 'IncomingPing', 1,
   '{{"incomingPing": {{"newEntry": true, "exchange": {{"direction": "INCOMING"}}}}}}', '198.51.100.1',
   NULL, '{E1}', now(), now(), now() - interval '2 days', now()),
  ('a0000000-0000-0000-0000-000000000002', 'MetadataRetrieval', 1,
   '{{"metadataRetrieval": {{"error": null, "metadata": {{"repositoryUri": "x"}}}}}}', NULL,
   'a0000000-0000-0000-0000-000000000001', '{E1}', now(), now(), now() - interval '2 days', now()),
  ('a0000000-0000-0000-0000-000000000003', 'WebhookTrigger', 1,
   '{{"webhookTrigger": {{"webhookUuid": "{HOOK}", "matchedEvent": "NewEntry"}}}}', NULL,
   'a0000000-0000-0000-0000-000000000001', '{E1}', NULL, NULL, now() - interval '2 days', now()),
  ('a0000000-0000-0000-0000-000000000004', 'IncomingPing', 1, '{{}}', NULL,
   NULL, '{E2}', now(), now(), now() - interval '1 day', now());
INSERT INTO index_webhook VALUES ('{HOOK}', 'https://hooks.example.net/x', 'old-secret', false, true, true, now(), now());
INSERT INTO index_webhook_event VALUES ('{HOOK}', 'NewEntry'), ('{HOOK}', 'EntryUnreachable');
"""


async def _seed(target: str) -> str:
    """Create the Java FDP's database next to the target one; return its URL."""
    plain = target.replace("postgresql+asyncpg://", "postgresql://")
    admin = await asyncpg.connect(plain)
    await admin.execute("CREATE DATABASE javafdp")
    await admin.close()
    source = plain.rsplit("/", 1)[0] + "/javafdp"
    java = await asyncpg.connect(source)
    await java.execute(SCHEMA)
    await java.execute(SEED)
    await java.close()
    return source.replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="module")
def databases() -> Iterator[tuple[str, str]]:
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as container:
        target = container.get_connection_url()
        source = asyncio.run(_seed(target))
        config = alembic_config(target)
        config.attributes["configure_logger"] = False
        command.upgrade(config, "head")
        yield source, target


async def test_import_keeps_identifiers_settings_webhooks_and_events(
    databases: tuple[str, str],
) -> None:
    source, target = databases
    engine = make_engine(target)
    factory = make_session_factory(engine)
    async with factory() as session, session.begin():
        session.add(IndexEntry(client_url="https://already.example.org", repository_metadata={}))

    dry = await import_java(source, factory, dry_run=True)
    assert dry.entries == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(IndexEntry)) == 1

    report = await import_java(source, factory)
    assert (report.entries, report.entries_skipped) == (1, 1)
    assert report.webhooks == 1
    assert report.settings
    assert report.events == 3  # the event of the skipped entry is left out
    assert report.events_closed == 1

    async with factory() as session:
        entry = await session.get(IndexEntry, UUID(E1))
        assert entry is not None
        assert entry.state is EntryState.VALID and entry.permit is EntryPermit.ACCEPTED
        assert entry.repository_metadata == {"title": "FDP one", "publisherName": "Uni"}
        assert entry.next_check_at is None  # due at once, so it is checked and harvested
        hook = await session.scalar(select(Webhook))
        assert hook is not None
        assert hook.events == ["EntryUnreachable", "NewEntry"]
        assert hook.all_entries and hook.secret == "old-secret"
        unfinished = await session.scalar(
            select(func.count()).select_from(Event).where(Event.finished_at.is_(None))
        )
        assert unfinished == 0
        retrieval = await session.get(Event, UUID("a0000000-0000-0000-0000-000000000002"))
        assert retrieval is not None
        assert str(retrieval.triggered_by) == "a0000000-0000-0000-0000-000000000001"

    settings = await SettingsService(factory, auto_permit_default=True).view()
    assert settings.auto_permit is False
    assert settings.ping.deny_list == ["^https://spam\\..*$"]
    assert settings.ping.valid_duration.days == 14
    assert settings.retrieval.timeout.total_seconds() == 30
    assert not settings.is_default

    again = await import_java(source, factory)
    assert again.entries == 0 and again.webhooks == 0 and not again.settings
    await engine.dispose()
