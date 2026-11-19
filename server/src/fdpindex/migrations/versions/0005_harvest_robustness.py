"""Harvested nodes keep conditional-request validators and a stale mark.

A harvest that cannot fetch a node, or does not reach it because a limit
stopped the walk, keeps the stored version and marks it stale (TASKS 1.14);
each node's ``ETag`` and ``Last-Modified`` let the next harvest ask for it
conditionally (TASKS 1.16). Harvests themselves are recorded as ``Harvest``
events; event types are plain strings, so that needs no schema change.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("harvested_node") as batch:
        batch.add_column(sa.Column("http_etag", sa.String(512), nullable=True))
        batch.add_column(sa.Column("http_last_modified", sa.String(64), nullable=True))
        batch.add_column(sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("fetch_error", sa.String(512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("harvested_node") as batch:
        batch.drop_column("fetch_error")
        batch.drop_column("stale_since")
        batch.drop_column("http_last_modified")
        batch.drop_column("http_etag")
