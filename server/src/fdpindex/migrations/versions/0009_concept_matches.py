"""Equivalent concepts (TASKS 8.11).

``concept.exact_matches`` holds the IRIs of the same concept in other
vocabularies (``skos:exactMatch``, ``owl:sameAs``, Wikidata's MeSH ID), so a
facet shows one value for them.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if postgres else sa.JSON()
    with op.batch_alter_table("concept") as batch:
        batch.add_column(sa.Column("exact_matches", json_type, nullable=False, server_default="[]"))
    # Concepts resolved before this release are fetched again, to learn their matches.
    op.execute("UPDATE concept SET due_at = CURRENT_TIMESTAMP WHERE status = 'resolved'")


def downgrade() -> None:
    with op.batch_alter_table("concept") as batch:
        batch.drop_column("exact_matches")
