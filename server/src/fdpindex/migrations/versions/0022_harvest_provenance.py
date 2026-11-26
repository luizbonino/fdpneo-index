"""Harvest provenance (TASKS 15.3).

``harvest`` keeps each walk over an FDP after it ends, with its runs, as the
public provenance of the index's copies; ``harvest_walk.harvest_id`` links a
walk in progress to its record. Walks harvested before this migration are
rebuilt from their events at start-up (``IndexService.backfill_harvests``).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "harvest",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey("index_entry.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("pages_read", sa.Integer(), nullable=False),
        sa.Column("added", sa.Integer(), nullable=False),
        sa.Column("changed", sa.Integer(), nullable=False),
        sa.Column("removed", sa.Integer(), nullable=False),
        sa.Column("not_modified", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("at_ceiling", sa.Boolean(), nullable=False),
        sa.Column("failure_reason", sa.String(512), nullable=True),
        sa.Column("runs", _JSON, nullable=False),
    )
    op.create_index("ix_harvest_entry_started", "harvest", ["entry_uuid", "started_at"])
    op.add_column("harvest_walk", sa.Column("harvest_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("harvest_walk", "harvest_id")
    op.drop_index("ix_harvest_entry_started", table_name="harvest")
    op.drop_table("harvest")
