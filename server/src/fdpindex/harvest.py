"""Harvest an FDP: walk its metadata tree and keep every node's triples.

The walk follows the reference's ``HarvesterService``: a node that declares
LDP direct containers yields its children through ``ldp:contains``;
otherwise the children are the objects of the navigation predicates of the
reference's default navigation SHACL. Nodes that fail to load or parse are
skipped, as in the reference.

Additions (ADR-0002): only IRIs on the client URL's origin are followed,
fragments are dropped, and at most ``max_nodes`` nodes are visited. Without
LDP containers, data services are followed too (``dcat:service``,
``dcat:accessService``): an FDP that links them only through DCAT would
otherwise keep them out of the index. On home.fairdatapoint.org every
service linked that way was also in an LDP container (2026-11-24).

A node that answers 404 or 410 is gone; any other failure is temporary
(TASKS 1.14: the stored version is kept).

Every node is asked for conditionally when an earlier harvest stored its
``ETag`` or ``Last-Modified`` (TASKS 1.16): a ``304`` reuses the stored
triples, and the walk continues through the children they list.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urldefrag, urlsplit

import structlog
from rdflib import BNode, Graph, URIRef
from rdflib.compare import to_canonical_graph
from rdflib.namespace import DCAT, DCTERMS, RDF
from rdflib.term import Node

from fdpindex.outbound import OutboundError
from fdpindex.rdf import ACCEPT, FDP_LEGACY, FDP_O, LDP, R3D, RdfParseError, parse_rdf

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fdpindex.outbound import OutboundClient

log = structlog.get_logger(__name__)

NAVIGATION_PREDICATES = (
    R3D.dataCatalog,
    FDP_O.metadataCatalog,
    FDP_LEGACY.metadataCatalog,
    DCAT.dataset,
    DCAT.distribution,
    DCTERMS.isPartOf,
    # Data services, which the reference's navigation shapes leave out: a catalogue's
    # dcat:service and a dataset's or distribution's dcat:accessService (2026-11-24).
    DCAT.service,
    DCAT.accessService,
)


@dataclass
class HarvestedGraph:
    node_iri: str
    ntriples: str
    content_hash: str
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


@dataclass(frozen=True)
class KnownNode:
    """A node as an earlier harvest stored it."""

    ntriples: str
    content_hash: str
    etag: str | None
    last_modified: str | None


@dataclass
class HarvestResult:
    nodes: list[HarvestedGraph] = field(default_factory=lambda: [])
    failed: dict[str, str] = field(default_factory=lambda: {})
    # Listed, but answered 404 or 410: the FDP says the node does not exist.
    gone: set[str] = field(default_factory=lambda: set())
    skipped_off_origin: int = 0
    limit_reached: bool = False

    @property
    def complete(self) -> bool:
        """Every node the walk found was fetched (or confirmed unchanged)."""
        return not self.failed and not self.limit_reached

    @property
    def not_modified(self) -> int:
        return sum(1 for node in self.nodes if node.not_modified)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return (parts.scheme, (parts.hostname or "").lower(), port)


def _normalise(iri: str) -> str:
    return urldefrag(iri)[0]


def children_of(graph: Graph, node: str) -> list[str]:
    containers = [s for s in graph.subjects(RDF.type, LDP.DirectContainer) if isinstance(s, URIRef)]
    found: list[str] = []
    if containers:
        for container in containers:
            found.extend(
                str(o) for o in graph.objects(container, LDP.contains) if isinstance(o, URIRef)
            )
    else:
        subjects = {URIRef(node), URIRef(node.rstrip("/")), URIRef(node.rstrip("/") + "/")}
        for predicate in NAVIGATION_PREDICATES:
            for subject in subjects:
                found.extend(
                    str(o) for o in graph.objects(subject, predicate) if isinstance(o, URIRef)
                )
    return list(dict.fromkeys(found))


def serialise(graph: Graph, node_iri: str) -> HarvestedGraph:
    """Sorted N-Triples with stable blank-node labels, so unchanged metadata hashes the same.

    rdflib labels blank nodes afresh on every parse; canonicalising them makes
    the hash (and the change sets of ADR-0005) depend on content only. Labels
    are then scoped by the node's IRI, because an FDP's nodes are later loaded
    as one document, where equal labels would merge distinct blank nodes.
    """
    scope = hashlib.sha256(node_iri.encode()).hexdigest()[:12]
    scoped = Graph()
    for triple in to_canonical_graph(graph):
        scoped.add(tuple(_scoped(term, scope) for term in triple))  # pyright: ignore[reportArgumentType]
    # Split on "\n" only: N-Triples ends lines with it, while str.splitlines() also breaks
    # on characters a literal may hold raw (U+0085, U+2028, form feed), which cut the
    # literal in two (found on a live FDP whose text was double-encoded UTF-8).
    lines = sorted(line for line in scoped.serialize(format="nt").split("\n") if line.strip())
    text = "\n".join(lines) + ("\n" if lines else "")
    return HarvestedGraph(
        node_iri=node_iri, ntriples=text, content_hash=hashlib.sha256(text.encode()).hexdigest()
    )


def _scoped(term: Node, scope: str) -> Node:
    return BNode(f"n{scope}x{term}") if isinstance(term, BNode) else term


def _parse_and_serialise(
    body: str, content_type: str, node: str
) -> tuple[HarvestedGraph, list[str]]:
    graph = parse_rdf(body, content_type, node)
    return serialise(graph, node), children_of(graph, node)


def _stored_children(ntriples: str, node: str) -> list[str]:
    graph = Graph()
    graph.parse(data=ntriples, format="nt")
    return children_of(graph, node)


def _header(response_headers: dict[str, list[str]], name: str) -> str | None:
    value = (response_headers.get(name) or [None])[0]
    return value[:512] if value else None


async def harvest(
    outbound: OutboundClient,
    client_url: str,
    *,
    timeout_seconds: float,
    max_nodes: int,
    max_bytes: int = 50 * 1024 * 1024,
    max_seconds: float = 900.0,
    known: Mapping[str, KnownNode] | None = None,
) -> HarvestResult:
    """Walk an FDP's metadata tree.

    Bounded so that one FDP cannot hold the index (security review, 2026-11-18):
    at most ``max_nodes`` nodes and ``max_bytes`` of N-Triples in total, at most
    ``max_seconds`` overall, and each node fetch within ``timeout_seconds``.
    Parsing runs in a thread: a large document takes seconds of pure CPU.
    """
    result = HarvestResult()
    origin = _origin(client_url)
    queue: deque[str] = deque([_normalise(client_url)])
    seen: set[str] = {_normalise(client_url)}
    deadline = time.monotonic() + max_seconds
    total_bytes = 0
    while queue:
        if len(result.nodes) + len(result.failed) >= max_nodes:
            result.limit_reached = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0 or total_bytes >= max_bytes:
            result.limit_reached = True
            break
        node = queue.popleft()
        stored = (known or {}).get(node)
        headers = {"Accept": ACCEPT}
        if stored is not None and stored.etag:
            headers["If-None-Match"] = stored.etag
        if stored is not None and stored.last_modified:
            headers["If-Modified-Since"] = stored.last_modified
        try:
            response = await outbound.request(
                "GET",
                node,
                timeout_seconds=min(timeout_seconds, remaining),
                headers=headers,
            )
        except OutboundError as err:
            result.failed[node] = err.message
            continue
        if response.status == 304 and stored is not None:
            try:
                children = await asyncio.to_thread(_stored_children, stored.ntriples, node)
            except Exception as err:  # stored triples written by an earlier harvest
                result.failed[node] = f"cannot reuse the stored version: {err}"
                continue
            harvested = HarvestedGraph(
                node_iri=node,
                ntriples=stored.ntriples,
                content_hash=stored.content_hash,
                etag=_header(response.headers, "etag") or stored.etag,
                last_modified=_header(response.headers, "last-modified") or stored.last_modified,
                not_modified=True,
            )
        elif response.status in (404, 410):
            result.gone.add(node)
            continue
        elif not 200 <= response.status < 300:
            result.failed[node] = f"HTTP {response.status}"
            continue
        elif response.truncated:
            result.failed[node] = "response too large"
            continue
        else:
            try:
                harvested, children = await asyncio.to_thread(
                    _parse_and_serialise, response.body, response.content_type, node
                )
            except RdfParseError as err:
                result.failed[node] = f"cannot parse: {err}"
                continue
            harvested.etag = _header(response.headers, "etag")
            harvested.last_modified = _header(response.headers, "last-modified")
        total_bytes += len(harvested.ntriples)
        result.nodes.append(harvested)
        for child in children:
            child = _normalise(child)
            if child in seen:
                continue
            seen.add(child)
            if _origin(child) != origin:
                result.skipped_off_origin += 1
                continue
            queue.append(child)
    log.info(
        "harvest_completed",
        client_url=client_url,
        nodes=len(result.nodes),
        failed=len(result.failed),
        not_modified=result.not_modified,
        skipped_off_origin=result.skipped_off_origin,
        limit_reached=result.limit_reached,
    )
    return result
