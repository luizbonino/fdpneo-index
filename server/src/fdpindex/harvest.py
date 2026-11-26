"""Harvest an FDP: walk its metadata tree and keep every node's triples.

The walk follows the reference's ``HarvesterService``: a node that declares
LDP direct containers yields its children through ``ldp:contains``;
otherwise the children are the objects of the navigation predicates of the
reference's default navigation SHACL. That is the FDP specification's
navigation: DCAT links outside it (a catalogue's ``dcat:service``, a
distribution's ``dcat:accessService``) are not followed (design note
metadata-health, decision 6).

Additions (ADR-0002): only IRIs on the client URL's origin, or on origins an
administrator allowed for the FDP, are followed; fragments are dropped.

One call is one **run** of a **walk** (design note metadata-health, 3.1): it
reads breadth first, one request at a time, until the queue is empty or its
budget (pages, bytes, time) is spent, and returns the pages still queued. The
caller stores them and starts the next run from there (``queue`` and
``seen``). An FDP that answers ``429``, or ``503`` with ``Retry-After``, ends
the run; the page goes back to the front of the queue.

A node that answers 404 or 410 is gone; any other failure is temporary
(TASKS 1.14: the stored version is kept). A failed node with a stored version
still yields the children that version lists, so the walk goes on below it.

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
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
    from collections.abc import Collection, Iterable, Mapping

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

# The longest wait a Retry-After may ask for; a longer one waits this long.
MAX_RETRY_AFTER_SECONDS = 24 * 3600


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
    # Failed with no stored version: what lies below them was not reached.
    unreached_below: set[str] = field(default_factory=lambda: set())
    skipped_off_origin: int = 0
    # The run's budget ran out with pages still queued (``queue``).
    limit_reached: bool = False
    # Pages still to read, in order: the next run starts here.
    queue: list[str] = field(default_factory=lambda: [])
    # Children left out by ``per_page`` (a sample, TASKS 15.11).
    sampled_out: set[str] = field(default_factory=lambda: set())
    # The FDP asked the index to wait (429, or 503 with Retry-After): seconds, or
    # 0 when it gave no time.
    retry_after: float | None = None

    @property
    def finished(self) -> bool:
        """The queue is empty: every page this walk found was asked for."""
        return not self.queue

    @property
    def complete(self) -> bool:
        """Every node the walk found was fetched (or confirmed unchanged)."""
        return self.finished and not self.failed

    @property
    def not_modified(self) -> int:
        return sum(1 for node in self.nodes if node.not_modified)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        port = None
    return (parts.scheme, (parts.hostname or "").lower(), port)


def _retry_after(headers: Mapping[str, list[str]]) -> float:
    """Seconds from a ``Retry-After`` header (seconds or an HTTP date); 0 if absent."""
    value = ((headers.get("retry-after") or [""])[0] or "").strip()
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return 0.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


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
    queue: Iterable[str] | None = None,
    seen: Collection[str] = (),
    origins: Iterable[str] = (),
    per_page: int | None = None,
) -> HarvestResult:
    """Run one budgeted run of a walk over an FDP's metadata tree.

    Bounded so that one FDP cannot hold the index (security review, 2026-11-18):
    at most ``max_nodes`` nodes and ``max_bytes`` of N-Triples per run, at most
    ``max_seconds`` overall, and each node fetch within ``timeout_seconds``.
    Parsing runs in a thread: a large document takes seconds of pure CPU.

    ``queue`` continues a walk (default: the client URL alone); ``seen`` holds
    the pages earlier runs of the walk already asked for. ``origins`` are other
    sites an administrator allowed for this FDP. ``per_page`` reads a sample:
    at most that many new children of each page are queued, so a small budget
    still goes down to the lower levels (the check before registering, TASKS
    15.11); the others are listed in ``sampled_out``.
    """
    result = HarvestResult()
    allowed = {_origin(client_url), *(_origin(o) for o in origins)}
    start = [_normalise(iri) for iri in queue] if queue is not None else [_normalise(client_url)]
    pending: deque[str] = deque(dict.fromkeys(start))
    visited: set[str] = {*seen, *pending}
    deadline = time.monotonic() + max_seconds
    total_bytes = 0

    def stored_children(node: str, stored: KnownNode | None, reason: str) -> list[str]:
        """Record a failure; a stored version still says where the walk goes on."""
        result.failed[node] = reason
        if stored is None:
            result.unreached_below.add(node)
            return []
        try:
            return _stored_children(stored.ntriples, node)
        except Exception:  # stored triples written by an earlier harvest
            result.unreached_below.add(node)
            return []

    while pending:
        if len(result.nodes) + len(result.failed) >= max_nodes:
            result.limit_reached = True
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0 or total_bytes >= max_bytes:
            result.limit_reached = True
            break
        node = pending.popleft()
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
            _enqueue(
                stored_children(node, stored, err.message),
                pending,
                visited,
                allowed,
                result,
                per_page,
            )
            continue
        if response.status == 429 or (
            response.status == 503 and response.headers.get("retry-after")
        ):
            # The FDP asks the index to wait: stop here and read this page first next time.
            pending.appendleft(node)
            result.retry_after = _retry_after(response.headers)
            break
        if response.status == 304 and stored is not None:
            try:
                children = await asyncio.to_thread(_stored_children, stored.ntriples, node)
            except Exception as err:  # stored triples written by an earlier harvest
                result.failed[node] = f"cannot reuse the stored version: {err}"
                result.unreached_below.add(node)
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
            reason = f"HTTP {response.status}"
            _enqueue(
                stored_children(node, stored, reason), pending, visited, allowed, result, per_page
            )
            continue
        elif response.truncated:
            reason = "response too large"
            _enqueue(
                stored_children(node, stored, reason), pending, visited, allowed, result, per_page
            )
            continue
        else:
            try:
                harvested, children = await asyncio.to_thread(
                    _parse_and_serialise, response.body, response.content_type, node
                )
            except RdfParseError as err:
                reason = f"cannot parse: {err}"
                _enqueue(
                    stored_children(node, stored, reason),
                    pending,
                    visited,
                    allowed,
                    result,
                    per_page,
                )
                continue
            harvested.etag = _header(response.headers, "etag")
            harvested.last_modified = _header(response.headers, "last-modified")
        total_bytes += len(harvested.ntriples)
        result.nodes.append(harvested)
        _enqueue(children, pending, visited, allowed, result, per_page)
    result.queue = list(pending)
    log.info(
        "harvest_completed",
        client_url=client_url,
        nodes=len(result.nodes),
        failed=len(result.failed),
        not_modified=result.not_modified,
        skipped_off_origin=result.skipped_off_origin,
        limit_reached=result.limit_reached,
        queued=len(result.queue),
        retry_after=result.retry_after,
    )
    return result


def _enqueue(
    children: Iterable[str],
    pending: deque[str],
    visited: set[str],
    allowed: set[tuple[str, str, int | None]],
    result: HarvestResult,
    per_page: int | None = None,
) -> None:
    taken = 0
    for child in children:
        child = _normalise(child)
        if child in visited:
            continue
        if _origin(child) not in allowed:
            visited.add(child)
            result.skipped_off_origin += 1
            continue
        if per_page is not None and taken >= per_page:
            result.sampled_out.add(child)
            continue
        visited.add(child)
        taken += 1
        pending.append(child)
