"""Initial schema: entries, events, settings, webhooks, harvested nodes.

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_entry",
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("client_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "Unknown",
                "Valid",
                "Unreachable",
                "Invalid",
                name="entry_state",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "permit",
            sa.Enum(
                "PENDING", "ACCEPTED", "REJECTED", name="entry_permit", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_retrieval_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_version", sa.Integer(), nullable=False),
        sa.Column("repository_uri", sa.String(length=2048), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("uuid", name=op.f("pk_index_entry")),
        sa.UniqueConstraint("client_url", name=op.f("uq_index_entry_client_url")),
    )
    op.create_index("ix_index_entry_created_at", "index_entry", ["created_at"], unique=False)
    op.create_index("ix_index_entry_state_permit", "index_entry", ["state", "permit"], unique=False)
    op.create_table(
        "index_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_index_settings")),
    )
    op.create_table(
        "index_webhook",
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column("payload_url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.String(length=512), nullable=False),
        sa.Column("all_events", sa.Boolean(), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("all_entries", sa.Boolean(), nullable=False),
        sa.Column("entries", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("uuid", name=op.f("pk_index_webhook")),
    )
    op.create_table(
        "harvested_node",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_uuid", sa.Uuid(), nullable=False),
        sa.Column("node_iri", sa.String(length=2048), nullable=False),
        sa.Column("ntriples", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("harvested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entry_uuid"],
            ["index_entry.uuid"],
            name=op.f("fk_harvested_node_entry_uuid_index_entry"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_harvested_node")),
        sa.UniqueConstraint("entry_uuid", "node_iri", name="uq_harvested_node_entry_iri"),
    )
    op.create_table(
        "index_event",
        sa.Column("uuid", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "AdminTrigger",
                "MetadataRetrieval",
                "WebhookTrigger",
                "IncomingPing",
                "WebhookPing",
                name="event_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("triggered_by", sa.Uuid(), nullable=True),
        sa.Column("related_to", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("remote_addr", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["related_to"],
            ["index_entry.uuid"],
            name=op.f("fk_index_event_related_to_index_entry"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by"],
            ["index_event.uuid"],
            name=op.f("fk_index_event_triggered_by_index_event"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("uuid", name=op.f("pk_index_event")),
    )
    op.create_index(
        "ix_index_event_rate_limit",
        "index_event",
        ["type", "remote_addr", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_index_event_related_to_created_at",
        "index_event",
        ["related_to", "created_at"],
        unique=False,
    )
    op.create_index("ix_index_event_unfinished", "index_event", ["finished_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_index_event_unfinished", table_name="index_event")
    op.drop_index("ix_index_event_related_to_created_at", table_name="index_event")
    op.drop_index("ix_index_event_rate_limit", table_name="index_event")
    op.drop_table("index_event")
    op.drop_table("harvested_node")
    op.drop_table("index_webhook")
    op.drop_table("index_settings")
    op.drop_index("ix_index_entry_state_permit", table_name="index_entry")
    op.drop_index("ix_index_entry_created_at", table_name="index_entry")
    op.drop_table("index_entry")
