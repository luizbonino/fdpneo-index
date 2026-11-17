"""Persistent model: entries, events, settings, webhooks, harvested nodes.

Names and values follow the reference implementation's tables
(``index_entry``, ``index_event``, ``index_settings``, ``index_webhook``), so
the importer (TASKS 3.4) maps them one to one.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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


class WebhookEvent(enum.StrEnum):
    NEW_ENTRY = "NewEntry"
    INCOMING_PING = "IncomingPing"
    ENTRY_VALID = "EntryValid"
    ENTRY_INVALID = "EntryInvalid"
    ENTRY_UNREACHABLE = "EntryUnreachable"
    ADMIN_TRIGGER = "AdminTrigger"
    WEBHOOK_PING = "WebhookPing"


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

    __table_args__ = (
        Index("ix_index_entry_state_permit", "state", "permit"),
        Index("ix_index_entry_created_at", "created_at"),
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

    __table_args__ = (
        UniqueConstraint("entry_uuid", "node_iri", name="uq_harvested_node_entry_iri"),
    )
