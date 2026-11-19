"""Index behaviour end to end through the services, against a fake network."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from fdpindex.db import utcnow
from fdpindex.errors import PingDenied, RateLimited
from fdpindex.models import (
    EntryPermit,
    EntryState,
    Event,
    EventType,
    HarvestedNode,
    IndexEntry,
    WebhookEvent,
)
from fdpindex.service import IndexChange, api_state
from fdpindex.settings import IndexSettings
from fdpindex.webhooks import SECRET_PLACEHOLDER, build_delivery
from tests.conftest import CATALOG_TTL, DATASET_TTL, FDP, FakeNetwork, Services, ping

pytestmark = pytest.mark.unit


async def _entry(services: Services, client_url: str = FDP) -> IndexEntry:
    async with services.session_factory() as session:
        entry = await session.scalar(select(IndexEntry).where(IndexEntry.client_url == client_url))
    assert entry is not None
    return entry


async def _count(services: Services, model: type, *conditions: Any) -> int:
    async with services.session_factory() as session:
        return int(
            await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
        )


async def _set(services: Services, **changes: object) -> None:
    current = (await services.settings.get()).model_dump(mode="json", by_alias=True)
    for path, value in changes.items():
        group, key = path.split("__")
        current[group][key] = value
    await services.settings.update(IndexSettings.model_validate(current))


# --- ping, retrieval, harvest ------------------------------------------------------------


async def test_first_ping_registers_retrieves_and_harvests(services: Services) -> None:
    outcome = await ping(services)
    assert outcome.new_entry
    entry = await _entry(services)
    assert entry.permit is EntryPermit.ACCEPTED  # auto-permit on by default
    assert entry.state is EntryState.VALID
    assert entry.repository_uri == FDP
    assert entry.repository_metadata["title"] == "Demo FDP"
    assert entry.last_retrieval_at is not None
    assert await _count(services, HarvestedNode, HarvestedNode.entry_uuid == entry.uuid) == 3
    types = {e.type for e in (await services.index.events(entry.uuid, limit=50))[0]}
    assert types == {EventType.INCOMING_PING, EventType.METADATA_RETRIEVAL, EventType.HARVEST}
    threshold = await services.index.valid_threshold()
    assert api_state(entry, threshold) == "ACTIVE"


async def test_second_ping_updates_the_entry(services: Services) -> None:
    await ping(services)
    first = await _entry(services)
    outcome = await ping(services)
    assert not outcome.new_entry
    second = await _entry(services)
    assert second.uuid == first.uuid
    assert second.updated_at > first.updated_at
    assert second.created_at == first.created_at


async def test_retrieval_rate_limit_skips_without_touching_the_retrieval_time(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    first = await _entry(services)
    fetched = len(network.requests)
    await ping(services)
    assert len(network.requests) == fetched  # nothing fetched
    second = await _entry(services)
    assert second.last_retrieval_at == first.last_retrieval_at
    events, _ = await services.index.events(second.uuid, limit=50)
    skipped = [e for e in events if e.type is EventType.METADATA_RETRIEVAL]
    assert skipped[0].payload["metadataRetrieval"]["error"] == "Rate limit reached (skipping)"


async def test_auto_permit_off_keeps_new_entries_pending_and_unharvested(
    services: Services,
) -> None:
    current = (await services.settings.get()).model_copy(update={"auto_permit": False})
    await services.settings.update(current)
    await ping(services)
    entry = await _entry(services)
    assert entry.permit is EntryPermit.PENDING
    assert entry.state is EntryState.VALID  # retrieved all the same, as in the reference
    assert await _count(services, HarvestedNode) == 0


@pytest.mark.parametrize(
    ("setup", "state", "error"),
    [
        ("down", EntryState.UNREACHABLE, "IO error"),
        ("500", EntryState.UNREACHABLE, "HTTP 500"),
        ("404", EntryState.INVALID, "HTTP 404"),
        ("garbage", EntryState.INVALID, "Cannot parse metadata"),
        ("no-repository", EntryState.INVALID, "Repository not found in metadata"),
    ],
)
async def test_retrieval_outcomes(
    services: Services, network: FakeNetwork, setup: str, state: EntryState, error: str
) -> None:
    match setup:
        case "down":
            network.failing.add(FDP)
        case "500":
            network.serve(FDP, "oops", status=500, content_type="text/plain")
        case "404":
            del network.pages[FDP]
        case "garbage":
            network.serve(FDP, "<html>not turtle", content_type="text/html")
        case "no-repository":
            network.serve(FDP, f'<{FDP}> <http://purl.org/dc/terms/title> "x" .')
        case _:
            raise AssertionError(setup)
    await ping(services)
    entry = await _entry(services)
    assert entry.state is state
    events, _ = await services.index.events(entry.uuid, limit=50)
    retrieval = next(e for e in events if e.type is EventType.METADATA_RETRIEVAL)
    assert retrieval.payload["metadataRetrieval"]["error"].startswith(error)
    assert await _count(services, HarvestedNode) == 0


async def test_deny_list_refuses_before_recording_anything(services: Services) -> None:
    with pytest.raises(PingDenied):
        await ping(services, "http://localhost:8000")
    assert await _count(services, Event) == 0


async def test_ping_rate_limit_per_address(services: Services) -> None:
    await _set(services, ping__rateLimitHits=2)
    for _ in range(3):  # the reference refuses only when *more than* hits were seen
        await ping(services, remote="198.51.100.1")
    with pytest.raises(RateLimited, match=r"max\. 2 per PT6H"):
        await ping(services, remote="198.51.100.1")
    await ping(services, remote="198.51.100.2")  # another address is unaffected


async def test_inactive_after_the_valid_duration(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(last_retrieval_at=utcnow() - timedelta(days=8))
        )
    stale = await _entry(services)
    assert api_state(stale, await services.index.valid_threshold()) == "INACTIVE"
    counts = await services.index.counts([EntryPermit.ACCEPTED])
    assert counts == {
        "ALL": 1,
        "UNKNOWN": 0,
        "ACTIVE": 0,
        "INACTIVE": 1,
        "UNREACHABLE": 0,
        "INVALID": 0,
    }
    page = await services.index.list_entries(permits=[EntryPermit.ACCEPTED], state="INACTIVE")
    assert [e.uuid for e in page.items] == [entry.uuid]


async def test_failed_harvest_keeps_previous_data(services: Services, network: FakeNetwork) -> None:
    await ping(services)
    entry = await _entry(services)
    for url in list(network.pages):
        if url != FDP:
            del network.pages[url]
    # The root still answers the retrieval, so only the harvest loses nodes.
    await services.index.harvest_entry(entry.uuid)
    assert await _count(services, HarvestedNode) == 1
    network.failing.add(FDP)
    await services.index.harvest_entry(entry.uuid)
    assert await _count(services, HarvestedNode) == 1  # nothing reached: data kept


async def _harvest_report(services: Services, harvest_id: Any) -> dict[str, Any]:
    async with services.session_factory() as session:
        event = await session.get(Event, harvest_id)
        assert event is not None and event.finished_at is not None
        return dict(event.payload["harvest"])


async def test_a_temporary_failure_keeps_the_stored_node(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    entry = await _entry(services)
    dataset = f"{FDP}/dataset/d1"
    network.failing.add(dataset)
    report = await _harvest_report(services, await services.index.harvest_entry(entry.uuid))
    assert report["outcome"] == "partial"
    assert (report["nodes"], report["stale"], report["removed"]) == (2, 1, 0)
    assert await _count(services, HarvestedNode) == 3
    async with services.session_factory() as session:
        node = await session.scalar(select(HarvestedNode).where(HarvestedNode.node_iri == dataset))
    assert node is not None and node.stale_since is not None
    assert "refused" in (node.fetch_error or "")
    # Fetched again: no longer stale.
    network.failing.clear()
    report = await _harvest_report(services, await services.index.harvest_entry(entry.uuid))
    assert report["outcome"] == "complete"
    async with services.session_factory() as session:
        node = await session.scalar(select(HarvestedNode).where(HarvestedNode.node_iri == dataset))
    assert node is not None and node.stale_since is None and node.fetch_error is None


async def test_nodes_are_asked_for_conditionally(services: Services, network: FakeNetwork) -> None:
    for number, url in enumerate((FDP, f"{FDP}/catalog/c1", f"{FDP}/dataset/d1")):
        network.etags[url] = f'"v{number}"'
    await ping(services)
    entry = await _entry(services)
    network.requests.clear()
    report = await _harvest_report(services, await services.index.harvest_entry(entry.uuid))
    assert (report["outcome"], report["nodes"], report["notModified"]) == ("complete", 3, 3)
    assert (report["added"], report["changed"], report["removed"]) == (0, 0, 0)
    sent = {str(r.url): r.headers.get("If-None-Match") for r in network.requests}
    assert sent[f"{FDP}/dataset/d1"] == '"v2"'
    assert await _count(services, HarvestedNode) == 3


async def test_an_unfinished_harvest_resumes_at_start_up(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    entry = await _entry(services)
    async with services.session_factory() as session, session.begin():
        event = Event(
            uuid=uuid4(),
            type=EventType.HARVEST,
            related_to=entry.uuid,
            payload={"harvest": {}},
            created_at=utcnow(),
        )
        session.add(event)
    assert await services.index.resume_unfinished() == 1
    await services.jobs.drain()
    assert (await _harvest_report(services, event.uuid))["outcome"] == "complete"


# --- permits, triggers, deletion --------------------------------------------------------------


async def test_rejecting_drops_harvested_data_and_accepting_harvests_again(
    services: Services, network: FakeNetwork
) -> None:
    await ping(services)
    entry = await _entry(services)
    await services.index.update_permit(
        entry.uuid, EntryPermit.REJECTED, actor="admin", remote_addr=None
    )
    await services.jobs.drain()
    assert await _count(services, HarvestedNode) == 0
    # Accepting triggers a retrieval; move the last retrieval back past the rate limit.
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(last_retrieval_at=utcnow() - timedelta(hours=1))
        )
    await services.index.update_permit(
        entry.uuid, EntryPermit.ACCEPTED, actor="admin", remote_addr="192.0.2.1"
    )
    await services.jobs.drain()
    assert await _count(services, HarvestedNode) == 3
    assert await _count(services, Event, Event.type == EventType.ADMIN_TRIGGER) == 1


async def test_admin_trigger_creates_missing_entries(services: Services) -> None:
    event_id = await services.index.admin_trigger(FDP, actor="admin", remote_addr=None)
    services.index.after_trigger(event_id)
    await services.jobs.drain()
    entry = await _entry(services)
    assert entry.state is EntryState.VALID


async def test_trigger_all_retrieves_every_entry(services: Services, network: FakeNetwork) -> None:
    other = "https://other.example.org"
    network.serve(other, f"<{other}> a <https://w3id.org/fdp/fdp-o#MetadataService> .")
    await ping(services)
    await ping(services, other)
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(last_retrieval_at=utcnow() - timedelta(hours=1))
        )
    event_id = await services.index.admin_trigger(None, actor="admin", remote_addr=None)
    services.index.after_trigger(event_id)
    await services.jobs.drain()
    retrievals = await _count(
        services, Event, Event.type == EventType.METADATA_RETRIEVAL, Event.triggered_by == event_id
    )
    assert retrievals == 2


async def test_delete_entry_removes_events_and_data(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    await services.index.delete_entry(entry.uuid)
    assert await _count(services, IndexEntry) == 0
    assert await _count(services, Event, Event.related_to == entry.uuid) == 0
    assert await _count(services, HarvestedNode) == 0


async def test_unfinished_retrievals_resume(services: Services) -> None:
    await ping(services)
    entry = await _entry(services)
    async with services.session_factory() as session, session.begin():
        await session.execute(
            update(IndexEntry).values(last_retrieval_at=None, state=EntryState.UNKNOWN)
        )
        session.add(
            Event(
                uuid=uuid4(),
                type=EventType.METADATA_RETRIEVAL,
                related_to=entry.uuid,
                payload={"metadataRetrieval": {}},
            )
        )
    assert await services.index.resume_unfinished() == 1
    await services.jobs.drain()
    assert (await _entry(services)).state is EntryState.VALID


async def test_listing_sorts_filters_and_searches(services: Services, network: FakeNetwork) -> None:
    for name in ("alpha", "beta", "gamma"):
        url = f"https://{name}.example.org"
        network.serve(
            url,
            f'<{url}> a <https://w3id.org/fdp/fdp-o#MetadataService> ; <http://purl.org/dc/terms/title> "{name.title()} FDP" .',
        )
        await ping(services, url)
    page = await services.index.list_entries(
        permits=[EntryPermit.ACCEPTED], sort=["clientUrl,desc"], size=2
    )
    assert page.total == 3
    assert [e.client_url for e in page.items] == [
        "https://gamma.example.org",
        "https://beta.example.org",
    ]
    found = await services.index.list_entries(permits=[EntryPermit.ACCEPTED], query="BETA fdp")
    assert [e.client_url for e in found.items] == ["https://beta.example.org"]
    none = await services.index.list_entries(permits=[EntryPermit.PENDING])
    assert none.total == 0


# --- webhooks ---------------------------------------------------------------------------------------


def test_signature_is_the_reference_sha1_over_the_payload_with_secret() -> None:
    moment = utcnow()
    event_uuid = uuid4()
    body, headers = build_delivery(
        webhook_event="NewEntry",
        event_uuid=event_uuid,
        client_url=FDP,
        secret="s3cret-value",
        moment=moment,
    )
    sent = json.loads(body)
    assert list(sent) == ["event", "uuid", "clientUrl", "timestamp", "secret"]
    assert sent["secret"] == SECRET_PLACEHOLDER
    assert sent["timestamp"].endswith("Z")
    with_secret = body.replace(json.dumps(SECRET_PLACEHOLDER), json.dumps("s3cret-value"))
    expected = hashlib.sha1(with_secret.encode(), usedforsecurity=False).hexdigest()
    assert headers["X-Signature"] == f"sha1={expected}"
    mac = hmac.new(b"s3cret-value", body.encode(), hashlib.sha256).hexdigest()
    assert headers["X-Signature-256"] == f"sha256={mac}"


async def test_webhooks_fire_for_matching_events_only(
    services: Services, network: FakeNetwork
) -> None:
    hooks = "https://hooks.example.net/fdp"
    await services.webhooks.save_webhook(
        None,
        {
            "payload_url": hooks,
            "secret": "s3cret-value",
            "all_events": False,
            "events": [WebhookEvent.NEW_ENTRY.value, WebhookEvent.ENTRY_VALID.value],
            "all_entries": True,
            "entries": [],
            "enabled": True,
        },
    )
    await services.webhooks.save_webhook(
        None,
        {
            "payload_url": hooks + "/disabled",
            "secret": "another-secret",
            "all_events": True,
            "events": [],
            "all_entries": True,
            "entries": [],
            "enabled": False,
        },
    )
    await ping(services)
    received = sorted(json.loads(call.content)["event"] for call in network.webhook_calls)
    assert received == ["EntryValid", "NewEntry"]
    assert all(call.headers["X-Signature"].startswith("sha1=") for call in network.webhook_calls)
    deliveries = await _count(services, Event, Event.type == EventType.WEBHOOK_TRIGGER)
    assert deliveries == 2


async def test_webhook_entry_filter(services: Services, network: FakeNetwork) -> None:
    await services.webhooks.save_webhook(
        None,
        {
            "payload_url": "https://hooks.example.net/only-other",
            "secret": "s3cret-value",
            "all_events": True,
            "events": [],
            "all_entries": False,
            "entries": ["https://other.example.org"],
            "enabled": True,
        },
    )
    await ping(services)
    assert network.webhook_calls == []


async def test_webhook_ping_goes_to_that_webhook(services: Services, network: FakeNetwork) -> None:
    webhook = await services.webhooks.save_webhook(
        None,
        {
            "payload_url": "https://hooks.example.net/test",
            "secret": "s3cret-value",
            "all_events": False,
            "events": [],
            "all_entries": True,
            "entries": [],
            "enabled": True,
        },
    )
    await services.webhooks.ping(webhook.uuid, actor="admin", remote_addr=None)
    await services.jobs.drain()
    assert [json.loads(c.content)["event"] for c in network.webhook_calls] == ["WebhookPing"]
    assert json.loads(network.webhook_calls[0].content)["clientUrl"] is None


async def test_a_failed_webhook_delivery_is_retried(
    services: Services, network: FakeNetwork
) -> None:
    url = "https://hooks.example.net/down"
    webhook = await services.webhooks.save_webhook(
        None,
        {
            "payload_url": url,
            "secret": "s3cret-value",
            "all_events": False,
            "events": [],
            "all_entries": True,
            "entries": [],
            "enabled": True,
        },
    )
    network.failing.add(url)
    await services.webhooks.ping(webhook.uuid, actor="admin", remote_addr=None)
    await services.jobs.drain()
    async with services.session_factory() as session:
        delivery = await session.scalar(
            select(Event).where(Event.type == EventType.WEBHOOK_TRIGGER)
        )
    assert delivery is not None and delivery.finished_at is not None
    trigger = delivery.payload["webhookTrigger"]
    assert len(trigger["attempts"]) == 3  # the first try and two retries
    assert trigger["delivered"] is False and trigger["nextAttemptAt"] is None
    assert sum(1 for r in network.requests if str(r.url) == url) == 3
    # A delivery that succeeds is tried once.
    network.failing.clear()
    network.requests.clear()
    await services.webhooks.ping(webhook.uuid, actor="admin", remote_addr=None)
    await services.jobs.drain()
    assert sum(1 for r in network.requests if str(r.url) == url) == 1


async def test_harvest_reports_what_changed_by_node(
    services: Services, network: FakeNetwork
) -> None:
    changes: list[IndexChange] = []
    services.index.add_listener(changes.append)
    outcome = await ping(services)
    harvested = [c for c in changes if c.kind == "harvested"]
    assert harvested[-1].entry_id == outcome.entry_id
    first = harvested[-1].changes
    assert first is not None and len(first.added) == 3 and not first.changed and not first.removed

    # The dataset changes, the catalog stops listing nothing new; harvest again.
    network.serve(f"{FDP}/dataset/d1", DATASET_TTL.replace("Dataset one", "Dataset one, revised"))
    changes.clear()
    await services.index.harvest_entry(outcome.entry_id)
    second = next(c for c in changes if c.kind == "harvested").changes
    assert second is not None
    assert second.changed == frozenset({f"{FDP}/dataset/d1"})
    assert not second.added and not second.removed

    # The catalog drops the dataset: its node is removed, the rest stay.
    network.serve(f"{FDP}/catalog/c1", CATALOG_TTL.replace(f"<{FDP}/dataset/d1>, ", ""))
    changes.clear()
    await services.index.harvest_entry(outcome.entry_id)
    third = next(c for c in changes if c.kind == "harvested").changes
    assert third is not None and third.removed == frozenset({f"{FDP}/dataset/d1"})
    nodes, _ = await services.index.harvest_summary(outcome.entry_id)
    assert nodes == 2

    changes.clear()
    await services.index.delete_entry(outcome.entry_id)
    assert [c.kind for c in changes] == ["removed"]


async def test_unchanged_blank_nodes_do_not_count_as_changes(
    services: Services, network: FakeNetwork
) -> None:
    network.serve(
        f"{FDP}/dataset/d1",
        DATASET_TTL.replace(
            'dct:title "Dataset one" ;',
            'dct:title "Dataset one" ; dcat:contactPoint [ dct:title "Helpdesk" ] ;',
        ),
    )
    changes: list[IndexChange] = []
    services.index.add_listener(changes.append)
    outcome = await ping(services)
    changes.clear()
    await services.index.harvest_entry(outcome.entry_id)
    again = next(c for c in changes if c.kind == "harvested").changes
    assert again is not None and again.empty
