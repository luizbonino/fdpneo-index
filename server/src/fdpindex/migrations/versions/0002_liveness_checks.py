"""Liveness checks (ADR-0004): next check, last success, failures in a row, change detection.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "index_entry", sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "index_entry", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "index_entry",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("index_entry", sa.Column("metadata_hash", sa.String(length=64), nullable=True))
    op.add_column("index_entry", sa.Column("http_etag", sa.String(length=512), nullable=True))
    op.add_column(
        "index_entry", sa.Column("http_last_modified", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_index_entry_next_check_at", "index_entry", ["next_check_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_index_entry_next_check_at", table_name="index_entry")
    op.drop_column("index_entry", "http_last_modified")
    op.drop_column("index_entry", "http_etag")
    op.drop_column("index_entry", "metadata_hash")
    op.drop_column("index_entry", "consecutive_failures")
    op.drop_column("index_entry", "last_success_at")
    op.drop_column("index_entry", "next_check_at")
