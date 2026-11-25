"""Profiles records declare, as resolved (TASKS 15.12).

``declared_profile`` caches, per IRI a record names with ``dct:conformsTo``,
the SHACL shapes its validation resources point to, so records are checked
against what they declare.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "declared_profile",
        sa.Column("iri", sa.String(2048), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("shapes", sa.Text(), nullable=True),
        sa.Column("digest", sa.String(64), nullable=True),
        sa.Column("sources", _JSON, nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("declared_profile")
