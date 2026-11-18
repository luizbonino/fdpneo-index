"""Search projection (ADR-0005): resources, values, labels, entry state; text search set-up.

``unaccent`` and ``pg_trgm`` are trusted extensions, so the database owner
can create them. The ``fdpindex_*`` text search configurations are the stock
ones with ``unaccent`` in front of the stemmer; ``fdpindex_tsv`` builds a
weighted vector from a resource's ``text_segments``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LANGUAGES = (
    "english",
    "dutch",
    "german",
    "french",
    "spanish",
    "italian",
    "portuguese",
    "swedish",
    "danish",
    "finnish",
    "norwegian",
    "hungarian",
    "romanian",
    "turkish",
    "russian",
)

TSV_FUNCTION = """
CREATE FUNCTION fdpindex_tsv(segments jsonb, simple boolean) RETURNS tsvector
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT coalesce(
    fdpindex_tsv_agg(
      setweight(
        to_tsvector(
          (CASE WHEN simple THEN 'fdpindex_simple' ELSE s->>0 END)::regconfig,
          s->>2
        ),
        (s->>1)::"char"
      )
    ),
    ''::tsvector
  )
  FROM jsonb_array_elements(segments) AS s
$$
"""


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    postgres = _postgres()
    if postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("CREATE TEXT SEARCH CONFIGURATION fdpindex_simple (COPY = simple)")
        op.execute(
            "ALTER TEXT SEARCH CONFIGURATION fdpindex_simple "
            "ALTER MAPPING FOR hword, hword_part, word WITH unaccent, simple"
        )
        for language in LANGUAGES:
            op.execute(f"CREATE TEXT SEARCH CONFIGURATION fdpindex_{language} (COPY = {language})")
            op.execute(
                f"ALTER TEXT SEARCH CONFIGURATION fdpindex_{language} "
                f"ALTER MAPPING FOR hword, hword_part, word WITH unaccent, {language}_stem"
            )
        op.execute(
            "CREATE AGGREGATE fdpindex_tsv_agg(tsvector) "
            "(SFUNC = tsvector_concat, STYPE = tsvector, INITCOND = '')"
        )
        op.execute(TSV_FUNCTION)

    json_type = postgresql.JSONB() if postgres else sa.JSON()
    tsv_type = postgresql.TSVECTOR() if postgres else sa.Text()
    big_id = sa.BigInteger() if postgres else sa.Integer()

    op.create_table(
        "search_resource",
        sa.Column("id", big_id, primary_key=True, autoincrement=True),
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey(
                "index_entry.uuid",
                ondelete="CASCADE",
                name="fk_search_resource_entry_uuid_index_entry",
            ),
            nullable=False,
        ),
        sa.Column("iri", sa.String(2048), nullable=False),
        sa.Column("node_iri", sa.String(2048), nullable=False),
        sa.Column("public_id", sa.String(22), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("type_iris", json_type, nullable=False),
        sa.Column("parent_iri", sa.String(2048), nullable=True),
        sa.Column("catalog_iri", sa.String(2048), nullable=True),
        sa.Column("dataset_iri", sa.String(2048), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("titles", json_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("descriptions", json_type, nullable=False),
        sa.Column("landing_page", sa.String(2048), nullable=True),
        sa.Column("issued", sa.DateTime(timezone=True), nullable=True),
        sa.Column("modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completeness", sa.Float(), nullable=False),
        sa.Column("facts", json_type, nullable=False),
        sa.Column("text_segments", json_type, nullable=False),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("tsv_lang", tsv_type, nullable=True),
        sa.Column("tsv_simple", tsv_type, nullable=True),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("harvested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("public_id", name="uq_search_resource_public_id"),
        sa.UniqueConstraint("entry_uuid", "iri", name="uq_search_resource_entry_iri"),
    )
    op.create_index("ix_search_resource_kind_modified", "search_resource", ["kind", "modified"])
    op.create_index("ix_search_resource_entry_kind", "search_resource", ["entry_uuid", "kind"])
    op.create_index("ix_search_resource_parent", "search_resource", ["entry_uuid", "parent_iri"])
    if postgres:
        op.create_index(
            "ix_search_resource_tsv_lang", "search_resource", ["tsv_lang"], postgresql_using="gin"
        )
        op.create_index(
            "ix_search_resource_tsv_simple",
            "search_resource",
            ["tsv_simple"],
            postgresql_using="gin",
        )
        op.create_index(
            "ix_search_resource_title_trgm",
            "search_resource",
            ["title"],
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        )

    op.create_table(
        "search_value",
        sa.Column(
            "resource_id",
            big_id,
            sa.ForeignKey(
                "search_resource.id",
                ondelete="CASCADE",
                name="fk_search_value_resource_id_search_resource",
            ),
            primary_key=True,
        ),
        sa.Column("field", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(512), primary_key=True),
        sa.Column("lang", sa.String(32), nullable=True),
    )
    op.create_index(
        "ix_search_value_field_value", "search_value", ["field", "value", "resource_id"]
    )

    op.create_table(
        "search_label",
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey(
                "index_entry.uuid",
                ondelete="CASCADE",
                name="fk_search_label_entry_uuid_index_entry",
            ),
            primary_key=True,
        ),
        sa.Column("iri", sa.String(2048), primary_key=True),
        sa.Column("lang", sa.String(32), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
    )
    op.create_index("ix_search_label_iri", "search_label", ["iri"])

    op.create_table(
        "search_entry_state",
        sa.Column(
            "entry_uuid",
            sa.Uuid(),
            sa.ForeignKey(
                "index_entry.uuid",
                ondelete="CASCADE",
                name="fk_search_entry_state_entry_uuid_index_entry",
            ),
            primary_key=True,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("resources", sa.Integer(), nullable=False),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("search_entry_state")
    op.drop_index("ix_search_label_iri", table_name="search_label")
    op.drop_table("search_label")
    op.drop_index("ix_search_value_field_value", table_name="search_value")
    op.drop_table("search_value")
    op.drop_table("search_resource")
    if _postgres():
        op.execute("DROP FUNCTION IF EXISTS fdpindex_tsv(jsonb, boolean)")
        op.execute("DROP AGGREGATE IF EXISTS fdpindex_tsv_agg(tsvector)")
        for language in ("simple", *LANGUAGES):
            op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS fdpindex_{language}")
        # The extensions stay: other objects in the database may use them.
