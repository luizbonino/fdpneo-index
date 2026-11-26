"""Benchmark runs (TASKS 15.10).

``benchmark_definition`` caches the benchmarks a network profile names, with
their runner, scoring algorithm and the classes they apply to;
``benchmark_result`` keeps the latest result per FDP, record and benchmark, and
is the queue of calls to make; ``benchmark_plan`` records when an FDP's records
were last compared with their results.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "benchmark_definition",
        sa.Column("iri", sa.String(2048), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column("landing_page", sa.String(2048), nullable=True),
        sa.Column("runner", sa.String(2048), nullable=True),
        sa.Column("algorithm", sa.String(2048), nullable=True),
        sa.Column("applies_to", _JSON, nullable=False),
        sa.Column("error", sa.String(512), nullable=True),
    )
    op.create_table(
        "benchmark_result",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey("index_entry.uuid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("benchmark_iri", sa.String(2048), nullable=False),
        sa.Column("record_iri", sa.String(2048), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("priority", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_try_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner", sa.String(2048), nullable=True),
        sa.Column("algorithm", sa.String(2048), nullable=True),
        sa.Column("incomplete", sa.Boolean(), nullable=False),
        sa.Column("conditions", _JSON, nullable=True),
        sa.Column("tests", _JSON, nullable=True),
        sa.Column("result_set", sa.LargeBinary(), nullable=True),
        sa.Column("error", sa.String(512), nullable=True),
        sa.UniqueConstraint("entry_uuid", "key", name="uq_benchmark_result_entry_key"),
    )
    op.create_index("ix_benchmark_result_state", "benchmark_result", ["state", "next_try_at"])
    op.create_table(
        "benchmark_plan",
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey("index_entry.uuid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("walk_finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("benchmark_plan")
    op.drop_index("ix_benchmark_result_state", table_name="benchmark_result")
    op.drop_table("benchmark_result")
    op.drop_table("benchmark_definition")
