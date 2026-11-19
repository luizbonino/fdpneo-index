"""Facet values carry their resource's kind and entry (search performance check, TASKS 6.15).

Counting a facet over a type tab or an FDP then reads ``search_value`` alone,
through ``ix_search_value_field_kind_value``, instead of looking up every
value's resource. On 2 M resources that took the format facet of the
unfiltered view from about 6 s to well under one. Existing rows are filled
from ``search_resource``.

On PostgreSQL, multi-column statistics on ``(field, value)`` let the planner
see how common a filter value is (a common theme matched 338 000 rows where
it expected 8 000, and chose a nested loop). Two expression indexes match the
default sort (newest first) and the title sort, so a first page stops after
its rows instead of sorting every match.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    with op.batch_alter_table("search_value") as batch:
        batch.add_column(sa.Column("kind", sa.String(16), nullable=True))
        batch.add_column(sa.Column("entry_uuid", sa.Uuid(), nullable=True))
    if postgres:
        op.execute(
            "UPDATE search_value AS v SET kind = r.kind, entry_uuid = r.entry_uuid "
            "FROM search_resource AS r WHERE r.id = v.resource_id"
        )
    else:
        op.execute(
            "UPDATE search_value SET "
            "kind = (SELECT kind FROM search_resource WHERE id = resource_id), "
            "entry_uuid = (SELECT entry_uuid FROM search_resource WHERE id = resource_id)"
        )
    with op.batch_alter_table("search_value") as batch:
        batch.alter_column("kind", existing_type=sa.String(16), nullable=False)
        batch.alter_column("entry_uuid", existing_type=sa.Uuid(), nullable=False)
    op.create_index(
        "ix_search_value_field_kind_value",
        "search_value",
        ["field", "kind", "value"],
        postgresql_include=["entry_uuid", "resource_id"],
    )
    if postgres:
        op.execute(
            "CREATE STATISTICS search_value_field_value (mcv, dependencies) "
            "ON field, value FROM search_value"
        )
        # The whole ORDER BY of search.query._order, so a page stops after its rows even
        # when thousands of resources share one date.
        op.execute(
            "CREATE INDEX ix_search_resource_recent ON search_resource "
            "((coalesce(modified, issued)) DESC NULLS LAST, lower(title), completeness DESC, id)"
        )
        op.execute(
            "CREATE INDEX ix_search_resource_title_lower ON search_resource "
            "(lower(title), completeness DESC, id)"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_search_resource_title_lower")
        op.execute("DROP INDEX IF EXISTS ix_search_resource_recent")
        op.execute("DROP STATISTICS IF EXISTS search_value_field_value")
    op.drop_index("ix_search_value_field_kind_value", table_name="search_value")
    with op.batch_alter_table("search_value") as batch:
        batch.drop_column("entry_uuid")
        batch.drop_column("kind")
