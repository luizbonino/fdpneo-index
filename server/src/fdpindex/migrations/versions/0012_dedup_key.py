"""Copies of one resource across FDPs (TASKS 8.8).

``search_resource.dedup_key`` holds the strongest identifier a dataset,
series, service or catalog states (a DOI, a global identifier, the target of
``owl:sameAs``, or its own IRI; see ``fdpindex.search.dedup``). The projection
version changes with this release: entries are re-projected in the background.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("search_resource") as batch:
        batch.add_column(sa.Column("dedup_key", sa.String(512), nullable=True))
    op.create_index(
        "ix_search_resource_dedup_key",
        "search_resource",
        ["dedup_key", "entry_uuid"],
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_search_resource_dedup_key", table_name="search_resource")
    with op.batch_alter_table("search_resource") as batch:
        batch.drop_column("dedup_key")
