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
    expired_time: str | None = Field(default=None, serialization_alias="expiredTime")

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
            expired_time=instant(entry.expired_at),
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


class HealthFindingView(_Api):
    code: str = Field(
        description="Findings: linked-only-in-dcat, not-linked-in-dcat, "
        "catalog-without-datasets. Notes: dataset-without-distributions, "
        "distribution-without-access"
    )
    count: int
    examples: list[str] = Field(description="Up to five pages or links it concerns")


class HealthFailureView(_Api):
    kind: str = Field(
        description="not-found, http-error, timeout, unparsable, too-large, refused, other"
    )
    count: int
    examples: list[str] = Field(description="Up to five pages that failed")


class HealthStopsView(_Api):
    off_site: int = Field(
        serialization_alias="offSite", description="Links to other sites, not followed"
    )
    unread: int = Field(description="Pages left unread when the harvest stopped at its limit")
    at_ceiling: bool = Field(
        serialization_alias="atCeiling", description="The harvest stopped at its page limit"
    )


class MetadataHealthView(_Api):
    """What the index found in an FDP's metadata (TASKS 15.8)."""

    computed_at: str = Field(serialization_alias="computedAt")
    harvested_at: str | None = Field(
        serialization_alias="harvestedAt", description="When the harvest it describes ended"
    )
    reach: str | None = Field(
        description="The deepest level reached: fdp, catalog, dataset, distribution, "
        "dataservice; null when nothing could be read"
    )
    status: Literal["fine", "findings", "unreadable"]
    pages: int = Field(description="Pages the index holds")
    levels: dict[str, int] = Field(description="Pages per level, and other pages")
    findings: list[HealthFindingView] = Field(
        description="Against the FDP specification, which every FDP claims"
    )
    notes: list[HealthFindingView] = Field(
        default_factory=lambda: [],
        description="DCAT's advice, which an FDP never promised: not counted in status",
    )
    failures: list[HealthFailureView] = Field(description="Pages that could not be read, by kind")
    stops: HealthStopsView = Field(description="Where the index stopped by its own rules")
    complete: bool | None = Field(
        description="The harvest read every page it found; null when not known"
    )


class HarvestWalkView(_Api):
    """A harvest walk with runs still to go (TASKS 15.7)."""

    started_at: str = Field(serialization_alias="startedAt")
    runs: int = Field(description="Runs so far")
    pages: int = Field(description="Pages asked for so far, read or failed")
    queued: int = Field(description="Pages found and not yet read")
    running: bool = Field(default=False, description="A run is reading the FDP now")
    continues_at: str | None = Field(
        serialization_alias="continuesAt",
        description="When the next run starts; null while one runs",
    )


class HarvestView(_Api):
    nodes: int
    harvested_at: str | None = Field(serialization_alias="harvestedAt")
    walk: HarvestWalkView | None = Field(
        default=None, description="Set while a harvest continues over several runs"
    )


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
    options: EntryOptionsView | None = None
    expired_time: str | None = Field(default=None, serialization_alias="expiredTime")
    location: LocationView | None = Field(
        default=None, description="Where the FDP is, and which source says so (TASKS 15.4)"
    )


class LocationView(_Api):
    country: str = Field(description="ISO 3166-1 alpha-2")
    source: Literal["admin", "stated", "publisher", "domain", "server"] = Field(
        description="admin: set by an administrator; stated: the FDP's r3d:country; "
        "publisher: its publisher's ROR record; domain: its country-code domain; "
        "server: where the server that answered is (IP geolocation, after redirects)"
    )


class EntryOptionsView(_Api):
    """Per-FDP options (TASKS 4.7)."""

    harvest: bool = Field(description="False: directory only, checked but never harvested")
    harvest_interval: str | None = Field(
        default=None,
        serialization_alias="harvestInterval",
        description="Harvest again at least this often (ISO-8601 duration); null: when changed",
    )
    country: str | None = Field(
        default=None,
        description="Set by an administrator (ISO 3166-1 alpha-2); first of the sources",
    )
    harvest_pages_per_run: int = Field(
        serialization_alias="harvestPagesPerRun",
        description="Pages one harvest run reads before it continues later",
    )
    harvest_pages_per_walk: int = Field(
        serialization_alias="harvestPagesPerWalk",
        description="Pages one harvest reads over all its runs, at most",
    )
    harvest_budget_set: bool = Field(
        serialization_alias="harvestBudgetSet",
        description="True: the pages above were set for this FDP; false: the defaults",
    )
    harvest_origins: list[str] = Field(
        default_factory=lambda: [],
        serialization_alias="harvestOrigins",
        description="Other sites the harvest may read for this FDP",
    )


MAX_HARVEST_ORIGINS = 10


class EntryOptionsRequest(BaseModel):
    """Leave a field out to keep it; ``null`` returns a budget or origins to the default."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    harvest: bool | None = None
    # An ISO-8601 duration; "" or null clears it. At least an hour.
    harvest_interval: str | None = Field(default=None, alias="harvestInterval")
    # ISO 3166-1 alpha-2; "" clears it (TASKS 15.4).
    country: str | None = None
    # TASKS 15.7: an administrator's budget for this FDP and other sites it may use.
    harvest_pages_per_run: int | None = Field(
        default=None, alias="harvestPagesPerRun", ge=1, le=100_000
    )
    harvest_pages_per_walk: int | None = Field(
        default=None, alias="harvestPagesPerWalk", ge=1, le=1_000_000
    )
    harvest_origins: list[str] | None = Field(
        default=None, alias="harvestOrigins", max_length=MAX_HARVEST_ORIGINS
    )

    @field_validator("harvest_origins")
    @classmethod
    def _origins(cls, value: list[str] | None) -> list[str] | None:
        """Each an http(s) origin, kept as scheme://host[:port]."""
        if value is None:
            return None
        origins: list[str] = []
        for item in value:
            parts = urlsplit(_http_url(item))
            if parts.username or parts.password:
                raise ValueError("an origin has no user name or password")
            try:
                port = parts.port
            except ValueError as err:
                raise ValueError(f"invalid port in {item}") from err
            host = (parts.hostname or "").lower()
            netloc = f"[{host}]" if ":" in host else host
            if port is not None:
                netloc = f"{netloc}:{port}"
            origins.append(f"{parts.scheme.lower()}://{netloc}")
        return list(dict.fromkeys(origins))


class EntryUpdateRequest(BaseModel):
    """The reference sends ``permit``; ``options`` is an addition. Either may be left out."""

    permit: EntryPermit | None = None
    options: EntryOptionsRequest | None = None


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_url: str = Field(alias="clientUrl")

    @field_validator("client_url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _http_url(value)


class RegistrationView(_Api):
    """The outcome of registering an FDP (TASKS 4.9)."""

    uuid: str
    client_url: str = Field(serialization_alias="clientUrl")
    new_entry: bool = Field(serialization_alias="newEntry")
    state: str
    permit: EntryPermit
    retrieved: bool = Field(
        description="False when a retrieval ran shortly before; the state is that one's"
    )
    error: str | None
    title: str | None


class EntryCreateRequest(RegisterRequest):
    permit: EntryPermit | None = None
    options: EntryOptionsRequest | None = None


class EntriesInfoView(_Api):
    entries_count: dict[str, int] = Field(serialization_alias="entriesCount")


class DuplicateGroupView(_Api):
    """Entries that are one FDP under variants of its URL (fdpindex.urls)."""

    key: str = Field(description="The URL without scheme, case, default port or trailing slash")
    keep: EntryView = Field(
        description="The entry kept: working, named by the FDP itself, https, oldest"
    )
    remove: list[EntryView] = Field(description="The entries a merge deletes")
    conflicts: list[str] = Field(
        description="Why a merge leaves this group to an administrator; empty when it can merge"
    )
    merged: bool = Field(description="True when this request merged the group")


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
    # addition: the catalogue feed (TASKS 11.5): DCAT-AP and OAI-PMH addresses
    catalog: dict[str, str] | None = None


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
    walk: dict[str, Any] = report.get("walk") or {}
    if outcome == "in progress":
        # One run of a walk that continues (TASKS 15.7).
        text = f"Run {walk.get('run', 1)}: {text}; {walk.get('queued', 0)} pages still to read"
        if walk.get("retryAfter") is not None:
            text += ", the FDP asked the index to wait"
        return text
    if walk.get("run", 1) > 1:
        text += f" (run {walk['run']}, {walk.get('pages', 0)} pages in all)"
    if outcome == "partial":
        if walk.get("atCeiling"):
            reason = "the harvest stopped at its page limit"
        elif report.get("limitReached"):
            reason = "a limit was reached"
        else:
            reason = "some could not be fetched"
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
        case "Expiry":
            expiry = payload.get("expiry") or {}
            return f"Expired: no contact since {str(expiry.get('lastContact', ''))[:10]}"
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
