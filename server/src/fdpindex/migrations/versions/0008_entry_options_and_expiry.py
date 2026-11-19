"""Per-FDP options and expiry (TASKS 4.6, 4.7).

``index_entry`` gains ``harvest_enabled`` (false: directory only),
``harvest_interval_seconds`` (harvest again at least this often),
``last_harvest_at``, and ``expired_at`` (set by the expiry policy).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("index_entry") as batch:
        batch.add_column(
            sa.Column("harvest_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("harvest_interval_seconds", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_harvest_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("index_entry") as batch:
        batch.drop_column("expired_at")
        batch.drop_column("last_harvest_at")
        batch.drop_column("harvest_interval_seconds")
        batch.drop_column("harvest_enabled")
