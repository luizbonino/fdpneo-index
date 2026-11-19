"""Persistent model: entries, events, settings, webhooks, harvested nodes, search rows.

Names and values follow the reference implementation's tables
(``index_entry``, ``index_event``, ``index_settings``, ``index_webhook``), so
the importer (TASKS 3.4) maps them one to one.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fdpindex.db import AwareDateTime, Base, JsonType, utcnow


class EntryState(enum.StrEnum):
    """Stored state. The API derives ACTIVE / INACTIVE from ``Valid`` and the retrieval age."""

    UNKNOWN = "Unknown"
    VALID = "Valid"
    UNREACHABLE = "Unreachable"
    INVALID = "Invalid"


class EntryPermit(enum.StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class EventType(enum.StrEnum):
    ADMIN_TRIGGER = "AdminTrigger"
    METADATA_RETRIEVAL = "MetadataRetrieval"
    WEBHOOK_TRIGGER = "WebhookTrigger"
    INCOMING_PING = "IncomingPing"
    WEBHOOK_PING = "WebhookPing"
    HEALTH_CHECK = "HealthCheck"  # addition: scheduled liveness check (ADR-0004)
    HARVEST = "Harvest"  # addition: a harvest of an entry's metadata tree (TASKS 1.15)
    EXPIRY = "Expiry"  # addition: the expiry policy marked an entry (TASKS 4.6)
    REGISTRATION = "Registration"  # addition: self-service or admin registration (TASKS 4.9)


class WebhookEvent(enum.StrEnum):
    NEW_ENTRY = "NewEntry"
    INCOMING_PING = "IncomingPing"
    ENTRY_VALID = "EntryValid"
    ENTRY_INVALID = "EntryInvalid"
    ENTRY_UNREACHABLE = "EntryUnreachable"
    ADMIN_TRIGGER = "AdminTrigger"
    WEBHOOK_PING = "WebhookPing"
    # additions
    ENTRY_EXPIRED = "EntryExpired"


def _enum(cls: type[enum.StrEnum], name: str) -> SAEnum:
    # Stored as plain strings (no PostgreSQL enum type), so adding a value is not a migration.
    return SAEnum(cls, name=name, native_enum=False, length=32, values_callable=_values)


def _values(cls: type[enum.StrEnum]) -> list[str]:
    return [member.value for member in cls]


class IndexEntry(Base):
    __tablename__ = "index_entry"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    client_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    state: Mapped[EntryState] = mapped_column(
        _enum(EntryState, "entry_state"), nullable=False, default=EntryState.UNKNOWN
    )
    permit: Mapped[EntryPermit] = mapped_column(
        _enum(EntryPermit, "entry_permit"), nullable=False, default=EntryPermit.PENDING
    )
    # The reference calls these registrationTime / modificationTime in its API.
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)
    last_retrieval_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    repository_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    repository_metadata: Mapped[dict[str, str]] = mapped_column(
        "metadata", JsonType, nullable=False, default=dict
    )
    # --- liveness checks (ADR-0004) ---
    next_check_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # SHA-256 of the last root metadata parsed; a change triggers a new harvest.
    metadata_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Validators for conditional requests (If-None-Match / If-Modified-Since).
    http_etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    http_last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # --- per-FDP options and expiry (TASKS 4.6, 4.7) ---
    # False: directory only, checked but never harvested.
    harvest_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Harvest again at least this often, even when the root metadata is unchanged.
    harvest_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_harvest_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    # Set when the expiry policy marked the entry; cleared by a successful retrieval.
    expired_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    __table_args__ = (
        Index("ix_index_entry_state_permit", "state", "permit"),
        Index("ix_index_entry_created_at", "created_at"),
        Index("ix_index_entry_next_check_at", "next_check_at"),
    )


class Event(Base):
    __tablename__ = "index_event"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    type: Mapped[EventType] = mapped_column(_enum(EventType, "event_type"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    triggered_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("index_event.uuid", ondelete="SET NULL"), nullable=True
    )
    related_to: Mapped[UUID | None] = mapped_column(
        ForeignKey("index_entry.uuid", ondelete="CASCADE"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    # Copied out of the payload so the per-IP ping rate limit is an indexed query.
    remote_addr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)
    executed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    entry: Mapped[IndexEntry | None] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_index_event_rate_limit", "type", "remote_addr", "created_at"),
        Index("ix_index_event_related_to_created_at", "related_to", "created_at"),
        Index("ix_index_event_unfinished", "finished_at"),
    )


class IndexSettingsRow(Base):
    """The single settings row. ``value`` is the validated :class:`fdpindex.settings.IndexSettings`."""

    __tablename__ = "index_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)


class Webhook(Base):
    __tablename__ = "index_webhook"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    payload_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(512), nullable=False)
    all_events: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    events: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    all_entries: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    entries: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)


class HarvestedNode(Base):
    """One harvested metadata node of an entry (ADR-0002)."""

    __tablename__ = "harvested_node"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("index_entry.uuid", ondelete="CASCADE"), nullable=False
    )
    node_iri: Mapped[str] = mapped_column(String(2048), nullable=False)
    ntriples: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    harvested_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)
    # Validators for conditional requests of this node (TASKS 1.16).
    http_etag: Mapped[str | None] = mapped_column(String(512), nullable=True)
    http_last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set when the last harvest could not fetch or reach this node: the stored
    # version is kept (TASKS 1.14); cleared when a harvest fetches it again.
    stale_since: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    fetch_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("entry_uuid", "node_iri", name="uq_harvested_node_entry_iri"),
    )


# --- search projection (search design §3.1, ADR-0005) ------------------------------

# Full-text vectors exist in PostgreSQL only; unit tests on SQLite match on search_text.
TsVector = TSVECTOR().with_variant(Text(), "sqlite")
BigId = BigInteger().with_variant(Integer(), "sqlite")


class SearchResource(Base):
    """One DCAT resource of an accepted entry, projected from ``harvested_node``."""

    __tablename__ = "search_resource"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    entry_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("index_entry.uuid", ondelete="CASCADE"), nullable=False
    )
    iri: Mapped[str] = mapped_column(String(2048), nullable=False)
    node_iri: Mapped[str] = mapped_column(String(2048), nullable=False)
    public_id: Mapped[str] = mapped_column(String(22), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    type_iris: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    parent_iri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    catalog_iri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    dataset_iri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    titles: Mapped[dict[str, str]] = mapped_column(JsonType, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    descriptions: Mapped[dict[str, str]] = mapped_column(JsonType, nullable=False, default=dict)
    landing_page: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    issued: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    modified: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    # Temporal coverage (TASKS 8.3): first and last day; either may be open.
    temporal_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    temporal_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The strongest identifier it states: copies in other FDPs share it (TASKS 8.8).
    dedup_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Counts and flags computed over the entry: datasetCount, distributionCount, titleFallback.
    facts: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    # [[text search configuration, weight, text], ...]; the vectors are built from it.
    text_segments: Mapped[list[list[str]]] = mapped_column(JsonType, nullable=False, default=list)
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tsv_lang: Mapped[str | None] = mapped_column(TsVector, nullable=True)
    tsv_simple: Mapped[str | None] = mapped_column(TsVector, nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    harvested_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("entry_uuid", "iri", name="uq_search_resource_entry_iri"),
        Index("ix_search_resource_kind_modified", "kind", "modified"),
        Index("ix_search_resource_entry_kind", "entry_uuid", "kind"),
        Index("ix_search_resource_temporal", "temporal_start", "temporal_end"),
        Index(
            "ix_search_resource_dedup_key",
            "dedup_key",
            "entry_uuid",
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
        Index("ix_search_resource_parent", "entry_uuid", "parent_iri"),
        Index("ix_search_resource_tsv_lang", "tsv_lang", postgresql_using="gin"),
        Index("ix_search_resource_tsv_simple", "tsv_simple", postgresql_using="gin"),
        Index(
            "ix_search_resource_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )


class SearchValue(Base):
    """A filterable value of a resource: facets, filters and facet counts run on these."""

    __tablename__ = "search_value"

    resource_id: Mapped[int] = mapped_column(
        BigId, ForeignKey("search_resource.id", ondelete="CASCADE"), primary_key=True
    )
    field: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512), primary_key=True)
    lang: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # A number field's value as a number, for range filters (TASKS 8.5).
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Copies of the resource's, so facet counts over a type or an FDP need no join (0004).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_uuid: Mapped[UUID] = mapped_column(nullable=False)

    __table_args__ = (
        Index("ix_search_value_field_value", "field", "value", "resource_id"),
        Index(
            "ix_search_value_field_kind_value",
            "field",
            "kind",
            "value",
            postgresql_include=["entry_uuid", "resource_id"],
        ),
    )


class SearchLabel(Base):
    """A label an entry's metadata gives an IRI (``source='harvested'`` in the design)."""

    __tablename__ = "search_label"

    entry_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("index_entry.uuid", ondelete="CASCADE"), primary_key=True
    )
    iri: Mapped[str] = mapped_column(String(2048), primary_key=True)
    lang: Mapped[str] = mapped_column(String(32), primary_key=True, default="")
    label: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (Index("ix_search_label_iri", "iri"),)


class SearchProperty(Base):
    """A captured property: the ``p:`` key of its ``search_value`` rows and its IRI."""

    __tablename__ = "search_property"

    field: Mapped[str] = mapped_column(String(64), primary_key=True)
    iri: Mapped[str] = mapped_column(String(2048), nullable=False)


class SearchEntryState(Base):
    """What the projection last saw of an entry: compared to find work after a restart."""

    __tablename__ = "search_entry_state"

    entry_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("index_entry.uuid", ondelete="CASCADE"), primary_key=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projected_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)


# --- concept labels (TASKS 8.6, ADR-0006) ------------------------------------------


class ConceptStatus(enum.StrEnum):
    PENDING = "pending"  # found in harvested metadata, not fetched yet
    RESOLVED = "resolved"  # labels found
    MISSING = "missing"  # fetched, but no label for it
    FAILED = "failed"  # the fetch failed; tried again later
    SKIPPED = "skipped"  # host not on the allow-list (rechecked when the list changes)


class Concept(Base):
    """An IRI used as a value in harvested metadata, and what its vocabulary says about it.

    Shared by every FDP that uses the IRI; harvested data that labels it is in
    ``search_label`` instead.
    """

    __tablename__ = "concept"

    iri: Mapped[str] = mapped_column(String(2048), primary_key=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ConceptStatus] = mapped_column(
        _enum(ConceptStatus, "concept_status"), nullable=False, default=ConceptStatus.PENDING
    )
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Levels above an IRI used in metadata (broader concepts are resolved too, up to a depth).
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broader: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    # The same concept elsewhere (TASKS 8.11): skos:exactMatch, owl:sameAs, Wikidata P486.
    exact_matches: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    fetched_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    due_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_concept_status_due", "status", "due_at"),)


class ConceptLabel(Base):
    """A label (``pref``) or synonym (``alt``) of a concept, per language (``""`` untagged)."""

    __tablename__ = "concept_label"

    iri: Mapped[str] = mapped_column(
        ForeignKey("concept.iri", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    lang: Mapped[str] = mapped_column(String(32), primary_key=True)
    label: Mapped[str] = mapped_column(String(512), primary_key=True)
