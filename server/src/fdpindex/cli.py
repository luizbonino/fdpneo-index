"""``fdpindex`` command line: database migrations, admin tokens, serving."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING

import typer
from alembic import command
from alembic.config import Config

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fdpindex.conformance.packs import Pack
    from fdpindex.outbound import OutboundClient

app = typer.Typer(help="FDPneo Index administration.", no_args_is_help=True)
db_app = typer.Typer(help="Database.", no_args_is_help=True)
token_app = typer.Typer(help="Static admin tokens (ADR-0003).", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(token_app, name="token")
vocab_app = typer.Typer(help="The bundled vocabulary pack (labels).", no_args_is_help=True)
app.add_typer(vocab_app, name="vocab")
embeddings_app = typer.Typer(
    help="Semantic search (off by default): the local sentence model.", no_args_is_help=True
)
app.add_typer(embeddings_app, name="embeddings")
conformance_app = typer.Typer(
    help="SHACL profile packs and validation (conformance design).", no_args_is_help=True
)
app.add_typer(conformance_app, name="conformance")


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


@app.command("openapi")
def openapi(output: str = typer.Argument("-", help="File to write, or - for stdout")) -> None:
    """Print the OpenAPI document (the client generates its types from it)."""
    import json
    from pathlib import Path

    from fdpindex.config import Settings
    from fdpindex.main import create_app

    document = create_app(Settings(), configure_logs=False).openapi()
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if output == "-":
        typer.echo(text, nl=False)
    else:
        Path(output).write_text(text, encoding="utf-8")


@app.command("import-java")
def import_java_command(
    source: str = typer.Argument(
        ..., help="Database URL of the Java FDP, e.g. postgresql://fdp:…@host/fdp"
    ),
    events: bool = typer.Option(True, help="Import the event log too"),
    dry_run: bool = typer.Option(False, help="Read and validate everything, write nothing"),
) -> None:
    """Import entries, events, settings and webhooks from a Java FAIR Data Point's index."""
    import asyncio

    from fdpindex.config import get_settings
    from fdpindex.db import make_engine, make_session_factory
    from fdpindex.importer import async_url, import_java

    settings = get_settings()

    async def run() -> None:
        engine = make_engine(settings.database_url)
        try:
            report = await import_java(
                async_url(source),
                make_session_factory(engine),
                events=events,
                dry_run=dry_run,
                auto_permit_default=settings.auto_permit,
            )
        finally:
            await engine.dispose()
        prefix = "would import" if dry_run else "imported"
        typer.echo(
            f"{prefix}: {report.entries} entries ({report.entries_skipped} already present), "
            f"{report.events} events ({report.events_closed} unfinished, now closed), "
            f"{report.webhooks} webhooks, settings: {'yes' if report.settings else 'no'}"
        )

    asyncio.run(run())


@vocab_app.command("update")
def vocab_update(
    output: str = typer.Option(
        "", help="Where to write the pack; default: the package's search/vocab.json"
    ),
    languages: str = typer.Option(
        "en,nl,de,fr,es,it,pt,sv,da,fi", help="Languages of the labels kept"
    ),
) -> None:
    """Fetch labels from the EU authority tables, SPDX and the DCAT/DC/FOAF/ADMS terms."""
    import asyncio
    from importlib import resources
    from pathlib import Path

    import httpx

    from fdpindex.config import get_settings
    from fdpindex.outbound import OutboundClient
    from fdpindex.search.vocab_update import VocabFetcher, write_pack

    settings = get_settings()
    target = (
        Path(output) if output else Path(str(resources.files("fdpindex.search"))) / "vocab.json"
    )

    async def run() -> None:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            client = OutboundClient(
                http,
                allow_private=False,
                max_bytes=8 * 1024 * 1024,
                max_redirects=5,
                user_agent=settings.user_agent,
            )
            fetcher = VocabFetcher(client, languages=[x for x in languages.split(",") if x])
            report = await fetcher.run()
        write_pack(target, fetcher.labels, report, aliases=fetcher.aliases, codes=fetcher.codes)
        for source, count in sorted(report.sources.items()):
            typer.echo(f"{source:45} {count:6}")
        typer.echo(
            f"{report.labels} IRIs labelled, {report.aliases} aliases, {report.codes} codes; "
            f"written to {target}"
        )
        for failure in report.failed[:20]:
            typer.echo(f"failed: {failure}", err=True)
        if report.failed:
            typer.echo(f"{len(report.failed)} requests failed", err=True)

    asyncio.run(run())


@embeddings_app.command("download")
def embeddings_download(
    target: str = typer.Argument(
        ..., help="Directory for the model (FDPINDEX_EMBEDDINGS_MODEL_DIR)"
    ),
) -> None:
    """Fetch the pinned sentence model (Apache 2.0) for semantic search (TASKS 10.1)."""
    from pathlib import Path

    import httpx

    from fdpindex.config import get_settings
    from fdpindex.search.semantic import MODEL_REPO, download

    settings = get_settings()

    def fetch(url: str) -> bytes:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=300,
            headers={"User-Agent": settings.user_agent},
        )
        response.raise_for_status()
        return response.content

    digests = download(Path(target), fetch=fetch)
    typer.echo(f"{MODEL_REPO} in {target}:")
    for name, digest in digests.items():
        typer.echo(f"  {name}  sha256:{digest[:16]}…")


def _outbound_run(work: Callable[[OutboundClient], Awaitable[None]]) -> None:
    import asyncio

    import httpx

    from fdpindex.config import get_settings
    from fdpindex.outbound import OutboundClient

    settings = get_settings()

    async def run() -> None:
        async with httpx.AsyncClient(follow_redirects=False) as http:
            client = OutboundClient(
                http,
                allow_private=False,
                max_bytes=64 * 1024 * 1024,
                max_redirects=5,
                user_agent=settings.user_agent,
            )
            await work(client)

    asyncio.run(run())


def _packs() -> dict[str, Pack]:
    from fdpindex.config import get_settings
    from fdpindex.conformance.packs import discover

    return discover(get_settings().conformance_packs)


def _pack(name: str) -> Pack:
    """A pack by ``id@version``, or by ``id`` when one version is installed."""
    packs = _packs()
    if name in packs:
        return packs[name]
    matches = [p for p in packs.values() if p.id == name]
    if len(matches) != 1:
        known = ", ".join(sorted(packs))
        raise typer.BadParameter(f"no single pack '{name}'; installed: {known}")
    return matches[0]


@conformance_app.command("packs")
def conformance_packs() -> None:
    """List the bundled and operator packs, and check their files against SHA256SUMS."""
    from fdpindex.conformance.packs import verify

    for key, pack in sorted(_packs().items()):
        problems = verify(pack)
        state = "ok" if not problems else f"{len(problems)} file(s) differ"
        origin = "bundled" if pack.bundled else "operator"
        kinds = ",".join(pack.manifest.applies_to)
        typer.echo(f"{key:28} {origin:9} {kinds:36} {state}")
        for problem in problems:
            typer.echo(f"  {problem}", err=True)


@conformance_app.command("fetch")
def conformance_fetch(
    packs: list[str] = typer.Argument(None, help="Packs (id or id@version); default: all"),
) -> None:
    """Download packs' files from their pinned sources and rewrite SHA256SUMS."""
    from fdpindex.conformance.fetch import fetch_pack

    chosen = [_pack(name) for name in packs] if packs else list(_packs().values())

    async def work(client: OutboundClient) -> None:
        for pack in chosen:
            files = await fetch_pack(client, pack)
            typer.echo(f"{pack.key}: {len(files)} files in {pack.directory}")

    _outbound_run(work)


@conformance_app.command("vocabularies")
def conformance_vocabularies(
    manifest: str = typer.Argument("", help="A vocabulary manifest; default: the bundled eu.yaml"),
) -> None:
    """Rebuild a vocabulary set (the triples shapes need about EU concepts)."""
    from pathlib import Path

    from fdpindex.conformance.fetch import build_vocabulary
    from fdpindex.conformance.vocabularies import BUNDLED, load_manifest

    path = Path(manifest) if manifest else BUNDLED / "eu.yaml"
    loaded = load_manifest(path)

    async def work(client: OutboundClient) -> None:
        target, read = await build_vocabulary(client, loaded, path.parent)
        typer.echo(f"{loaded.id}: {read} triples read, written to {target}")

    _outbound_run(work)


@conformance_app.command("validate")
def conformance_validate(
    file: str = typer.Argument(..., help="An RDF file (Turtle, RDF/XML, JSON-LD, N-Triples)"),
    pack: str = typer.Option(..., "--pack", help="id or id@version"),
    root: str = typer.Option("", help="The record's root IRI (picks the pack's variant)"),
) -> None:
    """Validate a file against a pack, as the index validates a harvested record."""
    from pathlib import Path

    from rdflib import Graph

    from fdpindex.conformance.validator import ShaclValidator

    chosen = _pack(pack)
    graph = Graph()
    graph.parse(Path(file))
    outcome = ShaclValidator().validate(graph, chosen, root=root or None)
    variant = f" ({outcome.variant})" if outcome.variant else ""
    typer.echo(
        f"{chosen.key}{variant}: {outcome.status}; {outcome.violations} violations, "
        f"{outcome.warnings} warnings, {outcome.infos} infos"
    )
    if outcome.error:
        typer.echo(outcome.error, err=True)
    for finding in outcome.findings:
        typer.echo(
            f"  {finding.severity:9} {finding.focus_node}  {finding.path or '-'}  "
            f"{finding.component}: {finding.message or ''}"
        )
    raise typer.Exit(0 if outcome.conforms else 1)
