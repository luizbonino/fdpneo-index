"""Harvest an FDP: walk its metadata tree and keep every node's triples.

The walk follows the reference's ``HarvesterService``: a node that declares
LDP direct containers yields its children through ``ldp:contains``;
otherwise the children are the objects of the navigation predicates of the
reference's default navigation SHACL. Nodes that fail to load or parse are
skipped, as in the reference.

Additions (ADR-0002): only IRIs on the client URL's origin are followed,
fragments are dropped, and at most ``max_nodes`` nodes are visited.
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
    from fdpindex.outbound import OutboundClient

log = structlog.get_logger(__name__)

NAVIGATION_PREDICATES = (
    R3D.dataCatalog,
    FDP_O.metadataCatalog,
    FDP_LEGACY.metadataCatalog,
    DCAT.dataset,
    DCAT.distribution,
    DCTERMS.isPartOf,
)


@dataclass
class HarvestedGraph:
    node_iri: str
    ntriples: str
    content_hash: str


@dataclass
class HarvestResult:
    nodes: list[HarvestedGraph] = field(default_factory=lambda: [])
    failed: dict[str, str] = field(default_factory=lambda: {})
    skipped_off_origin: int = 0
    limit_reached: bool = False


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
    lines = sorted(line for line in scoped.serialize(format="nt").splitlines() if line.strip())
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


async def harvest(
    outbound: OutboundClient,
    client_url: str,
    *,
    timeout_seconds: float,
    max_nodes: int,
    max_bytes: int = 50 * 1024 * 1024,
    max_seconds: float = 900.0,
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
        try:
            response = await outbound.request(
                "GET",
                node,
                timeout_seconds=min(timeout_seconds, remaining),
                headers={"Accept": ACCEPT},
            )
        except OutboundError as err:
            result.failed[node] = err.message
            continue
        if not 200 <= response.status < 300:
            result.failed[node] = f"HTTP {response.status}"
            continue
        if response.truncated:
            result.failed[node] = "response too large"
            continue
        try:
            harvested, children = await asyncio.to_thread(
                _parse_and_serialise, response.body, response.content_type, node
            )
        except RdfParseError as err:
            result.failed[node] = f"cannot parse: {err}"
            continue
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
        skipped_off_origin=result.skipped_off_origin,
        limit_reached=result.limit_reached,
    )
    return result
