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

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urldefrag, urlsplit

import structlog
from rdflib import Graph, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF

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


def serialise(graph: Graph) -> HarvestedGraph:
    lines = sorted(line for line in graph.serialize(format="nt").splitlines() if line.strip())
    text = "\n".join(lines) + ("\n" if lines else "")
    return HarvestedGraph(
        node_iri="", ntriples=text, content_hash=hashlib.sha256(text.encode()).hexdigest()
    )


async def harvest(
    outbound: OutboundClient, client_url: str, *, timeout_seconds: float, max_nodes: int
) -> HarvestResult:
    result = HarvestResult()
    origin = _origin(client_url)
    queue: deque[str] = deque([_normalise(client_url)])
    seen: set[str] = {_normalise(client_url)}
    while queue:
        if len(result.nodes) + len(result.failed) >= max_nodes:
            result.limit_reached = True
            break
        node = queue.popleft()
        try:
            response = await outbound.request(
                "GET", node, timeout_seconds=timeout_seconds, headers={"Accept": ACCEPT}
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
            graph = parse_rdf(response.body, response.content_type, node)
        except RdfParseError as err:
            result.failed[node] = f"cannot parse: {err}"
            continue
        harvested = serialise(graph)
        harvested.node_iri = node
        result.nodes.append(harvested)
        for child in children_of(graph, node):
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
