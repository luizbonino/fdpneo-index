"""Metadata health per FDP (TASKS 15.8).

``metadata_health`` holds what an FDP's last harvest walk reported and the
health report computed from its stored pages (``fdpindex.findings``). Rows
for FDPs harvested before this migration are made by the health sweep.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "metadata_health",
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey("index_entry.uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("walk_finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("walk", _JSON, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reach", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("report", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("metadata_health")
