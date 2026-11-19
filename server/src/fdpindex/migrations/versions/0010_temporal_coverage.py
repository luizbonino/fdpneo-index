"""Temporal coverage (TASKS 8.3).

``search_resource.temporal_start`` and ``temporal_end`` hold the first and
last day of a resource's ``dct:temporal``; either may be open. The projection
version changes with this release, so every entry is re-projected and the
columns fill in the background.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("search_resource") as batch:
        batch.add_column(sa.Column("temporal_start", sa.Date(), nullable=True))
        batch.add_column(sa.Column("temporal_end", sa.Date(), nullable=True))
    op.create_index(
        "ix_search_resource_temporal", "search_resource", ["temporal_start", "temporal_end"]
    )


def downgrade() -> None:
    op.drop_index("ix_search_resource_temporal", table_name="search_resource")
    with op.batch_alter_table("search_resource") as batch:
        batch.drop_column("temporal_end")
        batch.drop_column("temporal_start")
