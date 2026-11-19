"""Concept labels resolved from vocabularies (TASKS 8.6, ADR-0006).

``concept`` holds every IRI of harvested metadata that names a concept
(themes, subjects, publishers …), its resolution status and broader concepts;
``concept_label`` its labels and synonyms per language. Shared by all FDPs.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if postgres else sa.JSON()
    op.create_table(
        "concept",
        sa.Column("iri", sa.String(2048), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "resolved",
                "missing",
                "failed",
                "skipped",
                name="concept_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("broader", json_type, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_concept_status_due", "concept", ["status", "due_at"])
    op.create_table(
        "concept_label",
        sa.Column(
            "iri",
            sa.String(2048),
            sa.ForeignKey("concept.iri", ondelete="CASCADE", name="fk_concept_label_iri_concept"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("lang", sa.String(32), primary_key=True),
        sa.Column("label", sa.String(512), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("concept_label")
    op.drop_index("ix_concept_status_due", table_name="concept")
    op.drop_table("concept")
