"""Bounding boxes (TASKS 9.8).

``search_resource.bbox_min_lon`` … ``bbox_max_lat`` hold the box around a
resource's spatial extent (``dct:spatial`` with ``dcat:bbox``, ``dcat:centroid``,
``locn:geometry`` or a WKT/GeoJSON literal). The projection version changes:
entries are re-projected in the background.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("bbox_min_lon", "bbox_min_lat", "bbox_max_lon", "bbox_max_lat")


def upgrade() -> None:
    with op.batch_alter_table("search_resource") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.Float(), nullable=True))
    op.create_index(
        "ix_search_resource_bbox",
        "search_resource",
        list(COLUMNS),
        postgresql_where=sa.text("bbox_min_lon IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_search_resource_bbox", table_name="search_resource")
    with op.batch_alter_table("search_resource") as batch:
        for name in reversed(COLUMNS):
            batch.drop_column(name)
