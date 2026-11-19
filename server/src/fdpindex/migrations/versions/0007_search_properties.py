"""Captured properties for facets on any property (TASKS 9.2, 8.13).

The projection stores every property of a resource as ``search_value`` rows
whose ``field`` is ``p:`` and a hash of the property IRI; ``search_property``
maps those keys back to IRIs. Shared by all FDPs; rows are never removed (a
key without values is simply not listed).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_property",
        sa.Column("field", sa.String(64), primary_key=True),
        sa.Column("iri", sa.String(2048), nullable=False),
    )


def downgrade() -> None:
    op.execute("DELETE FROM search_value WHERE field LIKE 'p:%'")
    op.drop_table("search_property")
