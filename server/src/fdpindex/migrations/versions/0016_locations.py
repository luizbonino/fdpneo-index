"""Where FDPs are (TASKS 15.4).

``index_entry`` gains ``country`` (set by an administrator), ``served_from``
and ``server_country`` (the host that answered the last valid retrieval and
its country); ``concept`` gains ``country`` (an organisation's, from ROR).
Filled from the next retrieval and label refresh on.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("index_entry") as batch:
        batch.add_column(sa.Column("country", sa.String(2), nullable=True))
        batch.add_column(sa.Column("served_from", sa.String(255), nullable=True))
        batch.add_column(sa.Column("server_country", sa.String(2), nullable=True))
    with op.batch_alter_table("concept") as batch:
        batch.add_column(sa.Column("country", sa.String(2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("concept") as batch:
        batch.drop_column("country")
    with op.batch_alter_table("index_entry") as batch:
        batch.drop_column("server_country")
        batch.drop_column("served_from")
        batch.drop_column("country")
