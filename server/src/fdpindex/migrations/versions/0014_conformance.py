"""Conformance results (TASKS 11.3, conformance design §4).

``conformance_result`` holds one row per record and pack (status and complete
counts), ``conformance_finding`` up to 50 SHACL results per row, and
``conformance_entry_state`` what validation last saw of each entry. Every
accepted entry is validated in the background after the upgrade.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIG = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _entry(table: str, **kwargs: bool) -> sa.Column[object]:
    return sa.Column(
        "entry_uuid",
        sa.Uuid(),
        sa.ForeignKey(
            "index_entry.uuid", ondelete="CASCADE", name=f"fk_{table}_entry_uuid_index_entry"
        ),
        **kwargs,
    )


def upgrade() -> None:
    op.create_table(
        "conformance_result",
        sa.Column("id", BIG, primary_key=True, autoincrement=True),
        _entry("conformance_result", nullable=False),
        sa.Column("record_iri", sa.String(2048), nullable=False),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("pack", sa.String(64), nullable=False),
        sa.Column("pack_version", sa.String(64), nullable=False),
        sa.Column("pack_digest", sa.String(64), nullable=False),
        sa.Column("variant", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("violations", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.Integer(), nullable=False),
        sa.Column("infos", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "entry_uuid", "record_iri", "pack", name="uq_conformance_result_record"
        ),
    )
    op.create_index(
        "ix_conformance_result_entry_pack",
        "conformance_result",
        ["entry_uuid", "pack", "status"],
    )
    op.create_index("ix_conformance_result_public_id", "conformance_result", ["public_id"])
    op.create_table(
        "conformance_finding",
        sa.Column("id", BIG, primary_key=True, autoincrement=True),
        sa.Column(
            "result_id",
            BIG,
            sa.ForeignKey(
                "conformance_result.id",
                ondelete="CASCADE",
                name="fk_conformance_finding_result_id_conformance_result",
            ),
            nullable=False,
        ),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("focus_node", sa.String(2048), nullable=False),
        sa.Column("path", sa.String(2048), nullable=True),
        sa.Column("component", sa.String(128), nullable=False),
        sa.Column("shape", sa.String(2048), nullable=True),
        sa.Column("message", sa.String(512), nullable=True),
        sa.Column("value", sa.String(256), nullable=True),
    )
    op.create_index("ix_conformance_finding_result", "conformance_finding", ["result_id"])
    op.create_table(
        "conformance_entry_state",
        _entry("conformance_entry_state", primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("settings_hash", sa.String(64), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conformance_entry_state")
    op.drop_index("ix_conformance_finding_result", table_name="conformance_finding")
    op.drop_table("conformance_finding")
    op.drop_index("ix_conformance_result_public_id", table_name="conformance_result")
    op.drop_index("ix_conformance_result_entry_pack", table_name="conformance_result")
    op.drop_table("conformance_result")
