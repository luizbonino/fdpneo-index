"""Webhooks: which events notify whom, delivery, and management.

Matching and delivery follow the reference's ``WebhookService``:

* An index event maps to webhook events: ``AdminTrigger``; ``IncomingPing``
  (plus ``NewEntry`` for a first ping); ``MetadataRetrieval`` to
  ``EntryValid`` / ``EntryInvalid`` / ``EntryUnreachable`` by the entry's
  state; ``WebhookPing``. A scheduled ``HealthCheck`` (ADR-0004) maps like a
  retrieval, but only when it changed the entry's state.
* A webhook matches when it is enabled, takes all events or that event, and
  takes all entries or the event's entry (events without an entry match).
* Each delivery is a ``WebhookTrigger`` event with the recorded exchange.
* The body is ``{"event", "uuid", "clientUrl", "timestamp", "secret"}`` with
  the secret replaced by ``*** HIDDEN ***``. ``X-Signature`` is
  ``sha1=`` + SHA-1 of the same JSON *with* the real secret, exactly as the
  reference computes it. ``X-Signature-256`` adds a standard HMAC-SHA256 of
  the delivered body, keyed with the secret.

Differences: a webhook ping is delivered to the webhook it names only (the
reference sends it to every webhook subscribed to ``WebhookPing``), and the
reference has no management API; this one does (secrets are write-only).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select

from fdpindex import exchange
from fdpindex.db import utcnow
from fdpindex.errors import NotFound
from fdpindex.models import EntryState, Event, EventType, IndexEntry, Webhook, WebhookEvent
from fdpindex.outbound import OutboundError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.jobs import JobRunner
    from fdpindex.outbound import OutboundClient, OutboundResponse
    from fdpindex.settings import SettingsService

log = structlog.get_logger(__name__)

SECRET_PLACEHOLDER = "*** HIDDEN ***"  # noqa: S105 - the mask, not a secret


def webhook_events_for(event: Event, entry: IndexEntry | None) -> list[WebhookEvent]:
    match event.type:
        case EventType.ADMIN_TRIGGER:
            return [WebhookEvent.ADMIN_TRIGGER]
        case EventType.INCOMING_PING:
            events = [WebhookEvent.INCOMING_PING]
            if (event.payload.get("incomingPing") or {}).get("newEntry"):
                events.append(WebhookEvent.NEW_ENTRY)
            return events
        case EventType.METADATA_RETRIEVAL:
            state = entry.state if entry is not None else None
            mapping = {
                EntryState.VALID: WebhookEvent.ENTRY_VALID,
                EntryState.INVALID: WebhookEvent.ENTRY_INVALID,
                EntryState.UNREACHABLE: WebhookEvent.ENTRY_UNREACHABLE,
            }
            return [mapping[state]] if state is not None and state in mapping else []
        case EventType.WEBHOOK_PING:
            return [WebhookEvent.WEBHOOK_PING]
        case EventType.HEALTH_CHECK:
            # Scheduled checks notify on state transitions only (ADR-0004).
            if not (event.payload.get("healthCheck") or {}).get("stateChanged"):
                return []
            state = entry.state if entry is not None else None
            mapping = {
                EntryState.VALID: WebhookEvent.ENTRY_VALID,
                EntryState.INVALID: WebhookEvent.ENTRY_INVALID,
                EntryState.UNREACHABLE: WebhookEvent.ENTRY_UNREACHABLE,
            }
            return [mapping[state]] if state is not None and state in mapping else []
        case _:
            return []


def matches(webhook: Webhook, webhook_event: WebhookEvent, client_url: str | None) -> bool:
    match_event = webhook.all_events or webhook_event.value in webhook.events
    match_entry = webhook.all_entries or client_url is None or client_url in webhook.entries
    return webhook.enabled and match_event and match_entry


def _jackson_json(payload: dict[str, Any]) -> str:
    # Jackson's compact output: no spaces, insertion order, non-ASCII kept.
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _instant(moment: datetime) -> str:
    # java.time.Instant.toString(): ISO-8601 in UTC with a trailing Z.
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_delivery(
    *, webhook_event: str, event_uuid: UUID, client_url: str | None, secret: str, moment: datetime
) -> tuple[str, dict[str, str]]:
    """Return the body to send and the signature headers."""
    payload: dict[str, Any] = {
        "event": webhook_event,
        "uuid": str(event_uuid),
        "clientUrl": client_url,
        "timestamp": _instant(moment),
        "secret": secret,
    }
    signed = _jackson_json(payload)
    sha1 = hashlib.sha1(signed.encode("utf-8"), usedforsecurity=False).hexdigest()
    payload["secret"] = SECRET_PLACEHOLDER
    body = _jackson_json(payload)
    hmac256 = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return body, {"X-Signature": f"sha1={sha1}", "X-Signature-256": f"sha256={hmac256}"}


class WebhookService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SettingsService,
        outbound: OutboundClient,
        jobs: JobRunner,
        *,
        body_limit: int,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._outbound = outbound
        self._jobs = jobs
        self._body_limit = body_limit

    # --- triggering --------------------------------------------------------

    async def trigger(self, event_id: UUID) -> None:
        """Create one delivery per matching webhook and schedule it."""
        async with self._session_factory() as session, session.begin():
            event = await session.get(Event, event_id)
            if event is None:
                return
            entry = await session.get(IndexEntry, event.related_to) if event.related_to else None
            client_url = entry.client_url if entry is not None else None
            webhooks = (await session.execute(select(Webhook))).scalars().all()
            deliveries: list[UUID] = []
            for webhook_event in webhook_events_for(event, entry):
                for webhook in webhooks:
                    if matches(webhook, webhook_event, client_url):
                        deliveries.append(
                            self._new_delivery(session, webhook, webhook_event, event).uuid
                        )
        for delivery in deliveries:
            self._schedule(delivery)

    def schedule_trigger(self, event_id: UUID) -> None:
        self._jobs.submit(f"webhooks:{event_id}", lambda: self.trigger(event_id))

    def _new_delivery(
        self, session: AsyncSession, webhook: Webhook, webhook_event: WebhookEvent, cause: Event
    ) -> Event:
        delivery = Event(
            uuid=uuid4(),
            type=EventType.WEBHOOK_TRIGGER,
            triggered_by=cause.uuid,
            related_to=cause.related_to,
            payload={
                "webhookTrigger": {
                    "webhookUuid": str(webhook.uuid),
                    "matchedEvent": webhook_event.value,
                    "exchange": None,
                }
            },
            created_at=utcnow(),
        )
        session.add(delivery)
        return delivery

    def _schedule(self, delivery_id: UUID) -> None:
        self._jobs.submit(f"webhook-delivery:{delivery_id}", lambda: self.deliver(delivery_id))

    # --- delivery ------------------------------------------------------------

    async def deliver(self, delivery_id: UUID) -> None:
        settings = await self._settings.get()
        async with self._session_factory() as session:
            delivery = await session.get(Event, delivery_id)
            if delivery is None or delivery.finished_at is not None:
                return
            trigger = dict(delivery.payload.get("webhookTrigger") or {})
            webhook = await session.get(Webhook, UUID(str(trigger.get("webhookUuid"))))
            entry = (
                await session.get(IndexEntry, delivery.related_to) if delivery.related_to else None
            )
            delivery.executed_at = utcnow()
            if webhook is None:
                log.warning("webhook_gone", delivery=str(delivery_id))
                delivery.finished_at = utcnow()
                await session.commit()
                return
            await session.commit()
            url, secret = webhook.payload_url, webhook.secret
        body, signatures = build_delivery(
            webhook_event=str(trigger.get("matchedEvent")),
            event_uuid=delivery_id,
            client_url=entry.client_url if entry is not None else None,
            secret=secret,
            moment=utcnow(),
        )
        response: OutboundResponse | None = None
        error: OutboundError | None = None
        try:
            response = await self._outbound.request(
                "POST",
                url,
                timeout_seconds=settings.retrieval.timeout.total_seconds(),
                headers={"Content-Type": "application/json", **signatures},
                content=body,
            )
        except OutboundError as err:
            error = err
        trigger["exchange"] = exchange.outgoing(
            method="POST",
            url=url,
            request_body=body,
            response=response,
            error=error,
            limit=self._body_limit,
        )
        async with self._session_factory() as session, session.begin():
            delivery = await session.get(Event, delivery_id)
            if delivery is not None:
                delivery.payload = {"webhookTrigger": trigger}
                delivery.finished_at = utcnow()
        log.info(
            "webhook_delivered",
            delivery=str(delivery_id),
            status=response.status if response is not None else None,
            error=error.message if error is not None else None,
        )

    async def ping(self, webhook_id: UUID, *, actor: str, remote_addr: str | None) -> UUID:
        """Record a ``WebhookPing`` and deliver it to that webhook (404 if it does not exist)."""
        async with self._session_factory() as session, session.begin():
            event = Event(
                uuid=uuid4(),
                type=EventType.WEBHOOK_PING,
                payload={
                    "webhookPing": {
                        "remoteAddr": remote_addr,
                        "tokenName": actor,
                        "webhookUuid": str(webhook_id),
                    }
                },
                remote_addr=remote_addr,
                created_at=utcnow(),
                executed_at=utcnow(),
                finished_at=utcnow(),
            )
            session.add(event)
            webhook = await session.get(Webhook, webhook_id)
            delivery_id = (
                self._new_delivery(session, webhook, WebhookEvent.WEBHOOK_PING, event).uuid
                if webhook is not None
                else None
            )
        if delivery_id is None:
            raise NotFound(f"There is no such webhook: {webhook_id}")
        self._schedule(delivery_id)
        return event.uuid

    async def resume(self, delivery_id: UUID) -> None:
        await self.deliver(delivery_id)

    # --- management (not in the reference) ----------------------------------

    async def list_webhooks(self) -> list[Webhook]:
        async with self._session_factory() as session:
            rows = await session.execute(select(Webhook).order_by(Webhook.created_at))
            return list(rows.scalars().all())

    async def get_webhook(self, webhook_id: UUID) -> Webhook:
        async with self._session_factory() as session:
            webhook = await session.get(Webhook, webhook_id)
        if webhook is None:
            raise NotFound(f"There is no such webhook: {webhook_id}")
        return webhook

    async def save_webhook(self, webhook_id: UUID | None, values: dict[str, Any]) -> Webhook:
        """Create (``webhook_id`` None) or update a webhook. A ``secret`` of None keeps the old one."""
        async with self._session_factory() as session, session.begin():
            if webhook_id is None:
                webhook = Webhook(uuid=uuid4(), created_at=utcnow())
                session.add(webhook)
            else:
                found = await session.get(Webhook, webhook_id)
                if found is None:
                    raise NotFound(f"There is no such webhook: {webhook_id}")
                webhook = found
            for key, value in values.items():
                if key == "secret" and value is None:
                    continue
                setattr(webhook, key, value)
            webhook.updated_at = utcnow()
        return webhook

    async def delete_webhook(self, webhook_id: UUID) -> None:
        async with self._session_factory() as session, session.begin():
            webhook = await session.get(Webhook, webhook_id)
            if webhook is None:
                raise NotFound(f"There is no such webhook: {webhook_id}")
            await session.delete(webhook)
