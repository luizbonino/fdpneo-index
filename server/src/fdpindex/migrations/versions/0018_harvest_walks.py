"""Walks that continue over budgeted runs, and per-FDP budgets (TASKS 15.7).

``harvest_walk`` holds a walk with runs still to go: its queue, the pages it
asked for and those that failed. ``index_entry`` gains an administrator's
``harvest_run_pages``, ``harvest_walk_pages`` and ``harvest_origins``.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("index_entry") as batch:
        batch.add_column(sa.Column("harvest_run_pages", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("harvest_walk_pages", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("harvest_origins", _JSON, nullable=True))
    op.create_table(
        "harvest_walk",
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey("index_entry.uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("queue", _JSON, nullable=False),
        sa.Column("failed", _JSON, nullable=False),
        sa.Column("unreached", sa.Boolean(), nullable=False),
        sa.Column("skipped_off_origin", sa.Integer(), nullable=False),
        sa.Column("continue_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_harvest_walk_continue_at", "harvest_walk", ["continue_at"])


def downgrade() -> None:
    op.drop_index("ix_harvest_walk_continue_at", "harvest_walk")
    op.drop_table("harvest_walk")
    with op.batch_alter_table("index_entry") as batch:
        batch.drop_column("harvest_origins")
        batch.drop_column("harvest_walk_pages")
        batch.drop_column("harvest_run_pages")
