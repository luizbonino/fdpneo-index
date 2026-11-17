"""``fdpindex`` command line: database migrations, admin tokens, serving."""

from __future__ import annotations

from importlib.resources import files

import typer
from alembic import command
from alembic.config import Config

app = typer.Typer(help="FDP Index administration.", no_args_is_help=True)
db_app = typer.Typer(help="Database.", no_args_is_help=True)
token_app = typer.Typer(help="Static admin tokens (ADR-0003).", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(token_app, name="token")


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(files("fdpindex") / "alembic.ini"))
    if database_url:
        config.attributes["database_url"] = database_url
    return config


@db_app.command("migrate")
def db_migrate(revision: str = typer.Argument("head")) -> None:
    """Apply database migrations (to the latest revision by default)."""
    command.upgrade(alembic_config(), revision)
    typer.echo(f"migrations applied to {revision}")


@token_app.command("new")
def token_new() -> None:
    """Print a new admin token and the digest to add to FDPINDEX_ADMIN_TOKENS."""
    from fdpindex.auth import new_token

    token, digest = new_token()
    typer.echo(f"token:  {token}")
    typer.echo(f"digest: {digest}")
    typer.echo("Keep the token secret; put only the digest in FDPINDEX_ADMIN_TOKENS.")


@token_app.command("digest")
def token_digest(token: str = typer.Argument(..., help="An existing token")) -> None:
    """Print the digest of an existing token."""
    from fdpindex.auth import token_digest as digest_of

    typer.echo(digest_of(token))


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Interface to bind"),  # noqa: S104 - container default
    port: int = typer.Option(8000),
    migrate: bool = typer.Option(True, help="Apply migrations first"),
) -> None:
    """Apply migrations, then serve the API with uvicorn (proxy headers honoured)."""
    import uvicorn

    if migrate:
        command.upgrade(alembic_config(), "head")
    uvicorn.run(
        "fdpindex.main:app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=True,
        log_config=None,
    )
