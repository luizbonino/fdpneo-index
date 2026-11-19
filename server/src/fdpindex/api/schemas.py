"""Request and response shapes of the HTTP API.

Field names and meanings are the reference's DTOs (``PingDTO``,
``IndexEntryDTO``, ``IndexEntryDetailDTO``, ``IndexEntryInfoDTO``,
``EventDTO``, ``IndexEntryUpdateDTO``). Fields marked *addition* are new;
existing clients ignore them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fdpindex.models import EntryPermit, Event, IndexEntry, Webhook, WebhookEvent


def instant(moment: datetime | None) -> str | None:
    """Format like ``java.time.Instant.toString()``."""
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _http_url(value: str) -> str:
    text = value.strip()
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("must be an http(s) URL")
    return text


class _Api(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class PingRequest(BaseModel):
    # Unknown fields are ignored, as Spring's default Jackson configuration does.
    model_config = ConfigDict(extra="ignore")

    client_url: str = Field(alias="clientUrl")

    @field_validator("client_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _http_url(value)


class EntryView(_Api):
    uuid: str
    client_url: str = Field(serialization_alias="clientUrl")
    state: str
    permit: EntryPermit
    registration_time: str = Field(serialization_alias="registrationTime")
    modification_time: str = Field(serialization_alias="modificationTime")
    # additions
    last_retrieval_time: str | None = Field(serialization_alias="lastRetrievalTime")
    title: str | None = None

    @classmethod
    def of(cls, entry: IndexEntry, state: str) -> EntryView:
        return cls(
            uuid=str(entry.uuid),
            client_url=entry.client_url,
            state=state,
            permit=entry.permit,
            registration_time=instant(entry.created_at) or "",
            modification_time=instant(entry.updated_at) or "",
            last_retrieval_time=instant(entry.last_retrieval_at),
            title=(entry.repository_metadata or {}).get("title"),
        )


class EntryPageView(_Api):
    """A Spring Data ``Page`` as Jackson writes it, minus ``pageable`` / ``sort`` internals."""

    content: list[EntryView]
    total_elements: int = Field(serialization_alias="totalElements")
    total_pages: int = Field(serialization_alias="totalPages")
    number: int
    size: int
    number_of_elements: int = Field(serialization_alias="numberOfElements")
    first: bool
    last: bool
    empty: bool


class RepositoryMetadataView(_Api):
    metadata_version: int = Field(serialization_alias="metadataVersion")
    repository_uri: str | None = Field(serialization_alias="repositoryUri")
    metadata: dict[str, str]


class EventView(_Api):
    uuid: UUID
    type: str
    created: str
    finished: str | None
    # additions
    executed: str | None = None
    outcome: str | None = None

    @classmethod
    def of(cls, event: Event) -> EventView:
        return cls(
            uuid=event.uuid,
            type=event.type.value,
            created=instant(event.created_at) or "",
            finished=instant(event.finished_at),
            executed=instant(event.executed_at),
            outcome=event_outcome(event),
        )


class EventDetailView(EventView):
    """Admin-only: the whole event, including recorded exchanges (addition)."""

    triggered_by: UUID | None = Field(serialization_alias="triggeredBy")
    related_to: UUID | None = Field(serialization_alias="relatedTo")
    payload: dict[str, Any]

    @classmethod
    def of(cls, event: Event) -> EventDetailView:
        base = EventView.of(event)
        return cls(
            **base.model_dump(),
            triggered_by=event.triggered_by,
            related_to=event.related_to,
            payload=event.payload,
        )


class EventPageView(_Api):
    content: list[EventDetailView]
    total_elements: int = Field(serialization_alias="totalElements")
    number: int
    size: int


class HarvestView(_Api):
    nodes: int
    harvested_at: str | None = Field(serialization_alias="harvestedAt")


class HealthView(_Api):
    """Liveness-check state of an entry (addition, ADR-0004)."""

    next_check_time: str | None = Field(serialization_alias="nextCheckTime")
    last_success_time: str | None = Field(serialization_alias="lastSuccessTime")
    consecutive_failures: int = Field(serialization_alias="consecutiveFailures")


class EntryDetailView(_Api):
    uuid: str
    client_url: str = Field(serialization_alias="clientUrl")
    state: str
    permit: EntryPermit
    current_metadata: RepositoryMetadataView | None = Field(serialization_alias="currentMetadata")
    events: list[EventView]
    registration_time: str = Field(serialization_alias="registrationTime")
    modification_time: str = Field(serialization_alias="modificationTime")
    last_retrieval_time: str | None = Field(serialization_alias="lastRetrievalTime")
    # additions
    harvest: HarvestView | None = None
    health: HealthView | None = None


class EntryUpdateRequest(BaseModel):
    permit: EntryPermit


class EntriesInfoView(_Api):
    entries_count: dict[str, int] = Field(serialization_alias="entriesCount")


class TriggerRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_url: str = Field(alias="clientUrl")

    @field_validator("client_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _http_url(value)


class WebhookView(_Api):
    uuid: UUID
    payload_url: str = Field(serialization_alias="payloadUrl")
    has_secret: bool = Field(serialization_alias="hasSecret")
    all_events: bool = Field(serialization_alias="allEvents")
    events: list[WebhookEvent]
    all_entries: bool = Field(serialization_alias="allEntries")
    entries: list[str]
    enabled: bool
    created_at: str = Field(serialization_alias="createdAt")
    updated_at: str = Field(serialization_alias="updatedAt")

    @classmethod
    def of(cls, webhook: Webhook) -> WebhookView:
        return cls(
            uuid=webhook.uuid,
            payload_url=webhook.payload_url,
            has_secret=bool(webhook.secret),
            all_events=webhook.all_events,
            events=[WebhookEvent(e) for e in webhook.events],
            all_entries=webhook.all_entries,
            entries=list(webhook.entries),
            enabled=webhook.enabled,
            created_at=instant(webhook.created_at) or "",
            updated_at=instant(webhook.updated_at) or "",
        )


class WebhookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    payload_url: str = Field(alias="payloadUrl")
    secret: str | None = Field(default=None, min_length=8, max_length=512)
    all_events: bool = Field(default=False, alias="allEvents")
    events: list[WebhookEvent] = Field(default_factory=lambda: [])
    all_entries: bool = Field(default=True, alias="allEntries")
    entries: list[str] = Field(default_factory=lambda: [])
    enabled: bool = True

    @field_validator("payload_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _http_url(value)

    @field_validator("entries")
    @classmethod
    def _entries(cls, value: list[str]) -> list[str]:
        return sorted({_http_url(item) for item in value})


class ServiceInfo(_Api):
    name: str
    version: str
    ping: str
    oidc: dict[str, str | None] | None
    auth: list[Literal["oidc", "token"]]
    # addition: whether the SPARQL endpoint and page exist, and their limits
    sparql: dict[str, Any] | None = None
    # addition: the faceted search (search design §5)
    search: dict[str, Any] | None = None


def _harvest_outcome(report: dict[str, Any]) -> str | None:
    outcome = report.get("outcome")
    if outcome is None:
        return None
    if outcome == "skipped":
        return "Skipped: the entry is not accepted"
    if outcome == "failed":
        failed = report.get("failed") or {}
        first = next(iter(failed.values()), "nothing could be fetched")
        return f"Failed: {first}; the stored metadata is kept"
    nodes = report.get("nodes", 0)
    parts = [
        f"{report[key]} {label}"
        for key, label in (("added", "added"), ("changed", "changed"), ("removed", "removed"))
        if report.get(key)
    ]
    text = f"{nodes} nodes" + (f": {', '.join(parts)}" if parts else ", unchanged")
    if outcome == "partial":
        reason = (
            "a limit was reached" if report.get("limitReached") else "some could not be fetched"
        )
        text += f"; {report.get('stale', 0)} kept from before ({reason})"
    return text


def event_outcome(event: Event) -> str | None:
    """A one-line summary for timelines (addition)."""
    payload = event.payload or {}
    match event.type.value:
        case "IncomingPing":
            ping = payload.get("incomingPing") or {}
            return "New entry" if ping.get("newEntry") else "Ping"
        case "MetadataRetrieval":
            retrieval = payload.get("metadataRetrieval") or {}
            if retrieval.get("error"):
                return str(retrieval["error"])
            return "Metadata retrieved" if retrieval.get("metadata") else None
        case "WebhookTrigger":
            trigger = payload.get("webhookTrigger") or {}
            exchange = trigger.get("exchange") or {}
            code = (exchange.get("response") or {}).get("code")
            summary = (
                f"{trigger.get('matchedEvent')} → HTTP {code}"
                if code is not None
                else exchange.get("error") or trigger.get("matchedEvent")
            )
            attempts = len(trigger.get("attempts") or [])
            if trigger.get("nextAttemptAt"):
                return f"{summary}; attempt {attempts} failed, will retry"
            return f"{summary} (attempt {attempts})" if attempts > 1 else summary
        case "Harvest":
            return _harvest_outcome(payload.get("harvest") or {})
        case "AdminTrigger":
            # Not the administrator's name: outcomes are shown to anonymous callers too.
            return "Triggered by an administrator"
        case "HealthCheck":
            check = payload.get("healthCheck") or {}
            if check.get("error"):
                failures = check.get("consecutiveFailures")
                suffix = f" (failure {failures} in a row)" if failures else ""
                return f"{check['error']}{suffix}"
            if check.get("notModified"):
                return "Reachable, unchanged"
            if check.get("metadataChanged"):
                return "Reachable, metadata changed"
            return "Reachable" if check.get("exchange") else None
        case _:
            return None
