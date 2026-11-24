"""One FDP, one entry, whatever variant of its URL is sent.

``index_entry`` gains ``url_key``: the client URL without its scheme, letter
case in the host, default port, trailing slashes or fragment
(``fdpindex.urls``). Registration matches on it. Existing entries are filled
in here. Duplicates already in the table stay until an administrator merges
them (``POST /index/admin/duplicates/merge``), so the index is not unique.

The function is copied, not imported: a migration must keep doing what it
did when it was written.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-24
"""

from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _url_key(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host if port is None or port == _DEFAULT_PORTS.get(scheme) else f"{host}:{port}"
    key = f"//{netloc}{parts.path.rstrip('/')}"
    return f"{key}?{parts.query}" if parts.query else key


def upgrade() -> None:
    with op.batch_alter_table("index_entry") as batch:
        batch.add_column(sa.Column("url_key", sa.String(2048), nullable=True))
    entry = sa.table(
        "index_entry",
        sa.column("uuid", sa.Uuid()),
        sa.column("client_url", sa.String()),
        sa.column("url_key", sa.String()),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(entry.c.uuid, entry.c.client_url)).all()
    for uuid, client_url in rows:
        connection.execute(
            entry.update().where(entry.c.uuid == uuid).values(url_key=_url_key(client_url))
        )
    with op.batch_alter_table("index_entry") as batch:
        batch.alter_column("url_key", existing_type=sa.String(2048), nullable=False)
        batch.create_index("ix_index_entry_url_key", ["url_key"])


def downgrade() -> None:
    with op.batch_alter_table("index_entry") as batch:
        batch.drop_index("ix_index_entry_url_key")
        batch.drop_column("url_key")
