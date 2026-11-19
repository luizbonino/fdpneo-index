"""Numeric values for range filters (TASKS 8.5).

A ``number`` field's values are stored as their bucket (``100..1000``) for the
facet, and now also as the number in ``search_value.value_num``, so
``<field>Min``/``<field>Max`` filter on exact bounds. The projection version
changes with this release: entries are re-projected in the background.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("search_value") as batch:
        batch.add_column(sa.Column("value_num", sa.Float(), nullable=True))
    op.create_index(
        "ix_search_value_field_num",
        "search_value",
        ["field", "value_num"],
        postgresql_where=sa.text("value_num IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_search_value_field_num", table_name="search_value")
    with op.batch_alter_table("search_value") as batch:
        batch.drop_column("value_num")
