"""Downloading packs and building vocabulary sets (``fdpindex conformance fetch``).

Maintainers run these before a release and commit the result, as with
``fdpindex vocab update``; an operator runs them on their own packs. Every
request goes through :class:`~fdpindex.outbound.OutboundClient`, restricted
to the host of the manifest's ``download`` URL. The sources are pinned to a
commit, so a download changes nothing unless the manifest does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from rdflib import Graph

from fdpindex.conformance.packs import SUMS, Pack, sums
from fdpindex.conformance.vocabularies import VocabularyManifest, extract
from fdpindex.outbound import OutboundError

if TYPE_CHECKING:
    from fdpindex.outbound import OutboundClient

TIMEOUT = 120.0


class FetchError(RuntimeError):
    pass


async def _download(client: OutboundClient, url: str) -> str:
    host = urlsplit(url).hostname or ""
    try:
        response = await client.request("GET", url, timeout_seconds=TIMEOUT, allowed_hosts=[host])
    except OutboundError as err:
        raise FetchError(f"{url}: {err.message}") from err
    if response.status != 200 or response.truncated:
        raise FetchError(
            f"{url}: HTTP {response.status}{' (too large)' if response.truncated else ''}"
        )
    return response.body


async def fetch_pack(client: OutboundClient, pack: Pack) -> list[str]:
    """Download the pack's files from its source and rewrite ``SHA256SUMS``."""
    source = pack.manifest.source
    if source is None:
        raise FetchError(f"{pack.key}: the manifest names no source")
    written: list[str] = []
    for file in pack.manifest.files:
        body = await _download(client, source.download + file.path)
        target = pack.directory / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        written.append(file.path)
    (pack.directory / SUMS).write_text(sums(pack.directory, written), encoding="utf-8")
    return written


def _format(name: str) -> str:
    return "xml" if name.endswith((".rdf", ".owl", ".xml")) else "turtle"


async def build_vocabulary(
    client: OutboundClient, manifest: VocabularyManifest, directory: Path
) -> tuple[Path, int]:
    """Download the sources, keep the manifest's predicates, write the output file."""
    graphs: list[Graph] = []
    for name in manifest.files:
        body = await _download(client, manifest.source.download + name)
        graph = Graph()
        await asyncio.to_thread(graph.parse, data=body, format=_format(name))
        graphs.append(graph)
    data = extract(graphs, manifest.keep)
    target = directory / manifest.output
    target.write_bytes(data)
    return target, sum(len(g) for g in graphs)
