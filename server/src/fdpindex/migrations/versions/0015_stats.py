"""Statistics over time (TASKS 15.1).

``stats_daily`` holds one value per statistic and day, the last snapshot of
the day; the index takes one every hour. History starts with the upgrade.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stats_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(128), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("day", "metric", name=op.f("pk_stats_daily")),
    )


def downgrade() -> None:
    op.drop_table("stats_daily")
