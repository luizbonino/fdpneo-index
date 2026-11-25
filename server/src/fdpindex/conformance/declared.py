"""What a record declares it conforms to (TASKS 15.12; design note metadata-health 3.7).

A record names what it meets with ``dct:conformsTo``. The index checks it
against that, not against a profile the FDP never promised. Each declared
IRI resolves to one of:

* **a pack**: the IRI is the specification of an installed pack (DCAT-AP
  3.0.1, HealthDCAT-AP Release 8 …), compared without scheme or trailing
  slash;
* **the FDP's own shapes**: the IRI is a W3C Profiles Vocabulary profile, as
  the FDP software serves at ``/profile/{uuid}``. Its resources with the role
  ``prof:role/Validation`` name SHACL documents (``/metadata-schemas/{uuid}``);
  the profiles it builds on (``prof:isProfileOf``) add theirs. A document that
  is SHACL itself is used as it is;
* **unresolved**, with the reason: on a site the FDP's harvest may not read
  and not a known specification, not a profile, no shapes, or not fetched.

Only the FDP's own site, and the origins an administrator allowed for it, are
fetched, under the outbound rules (ADR-0002): a declared IRI is chosen by the
FDP, so it is as untrusted as any page it serves. Resolved profiles are kept
in ``declared_profile`` for a day.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import urldefrag, urlsplit

import structlog
from rdflib import RDF, Graph, Namespace, URIRef

from fdpindex.db import utcnow
from fdpindex.models import DeclaredProfile
from fdpindex.outbound import OutboundError
from fdpindex.rdf import ACCEPT, RdfParseError, parse_rdf

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.conformance.packs import Pack
    from fdpindex.outbound import OutboundClient

log = structlog.get_logger(__name__)

DCT_CONFORMS_TO = "http://purl.org/dc/terms/conformsTo"
PROF = Namespace("http://www.w3.org/ns/dx/prof/")
SH = Namespace("http://www.w3.org/ns/shacl#")
VALIDATION_ROLE = URIRef("http://www.w3.org/ns/dx/prof/role/Validation")
FRESH = timedelta(days=1)
MAX_DEPTH = 3  # profiles followed through prof:isProfileOf
MAX_DOCUMENTS = 10  # SHACL documents per declared profile
MAX_DECLARED = 5  # declared IRIs per record that are resolved
TIMEOUT_SECONDS = 20.0

Kind = Literal["pack", "shapes", "unresolved"]
_LINE = re.compile(r"^<([^>]*)>\s+<" + re.escape(DCT_CONFORMS_TO) + r">\s+<([^>]*)>\s*\.\s*$")


@dataclass(frozen=True)
class Target:
    """A declared IRI as the index resolved it."""

    iri: str
    kind: Kind
    pack_key: str | None = None
    shapes: str | None = None  # N-Triples
    digest: str | None = None
    reason: str | None = None


def _key(iri: str) -> str:
    parts = urlsplit(urldefrag(iri.strip())[0])
    return f"{(parts.hostname or '').lower()}{parts.path.rstrip('/')}"


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        port = None
    return (parts.scheme, (parts.hostname or "").lower(), port)


def declared(ntriples: str, root: str) -> list[str]:
    """The IRIs the record's root declares with ``dct:conformsTo``, in order, at most five."""
    roots = {root, root.rstrip("/"), root.rstrip("/") + "/"}
    found: list[str] = []
    for line in ntriples.split("\n"):
        match = _LINE.match(line)
        if match and match.group(1) in roots and match.group(2) not in found:
            found.append(match.group(2))
    return sorted(found)[:MAX_DECLARED]


def has_shapes(graph: Graph) -> bool:
    return (
        any(graph.subjects(RDF.type, SH.NodeShape))
        or any(graph.subjects(RDF.type, SH.PropertyShape))
        or any(graph.subjects(SH.targetClass, None))
        or any(graph.subjects(SH.property, None))
    )


def shapes_text(graph: Graph) -> tuple[str, str]:
    """Sorted N-Triples and their digest."""
    lines = sorted(line for line in graph.serialize(format="nt").split("\n") if line.strip())
    text = "\n".join(lines) + "\n"
    return text, hashlib.sha256(text.encode()).hexdigest()


class DeclaredResolver:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        outbound: OutboundClient | None,
        packs: Callable[[], dict[str, Pack]],
    ) -> None:
        self._session_factory = session_factory
        self._outbound = outbound
        self._packs = packs
        self._locks: dict[str, asyncio.Lock] = {}

    def _pack_for(self, iri: str) -> Pack | None:
        wanted = _key(iri)
        for pack in self._packs().values():
            if _key(pack.manifest.specification) == wanted:
                return pack
        return None

    async def resolve(self, iris: Iterable[str], origins: Iterable[str]) -> dict[str, Target]:
        """Each IRI as a :class:`Target`; ``origins`` are the sites that may be fetched."""
        allowed = {_origin(o) for o in origins}
        out: dict[str, Target] = {}
        for iri in iris:
            out[iri] = await self._one(iri, allowed)
        return out

    async def _one(self, iri: str, allowed: set[tuple[str, str, int | None]]) -> Target:
        pack = self._pack_for(iri)
        if pack is not None:
            return Target(iri, "pack", pack_key=pack.key, digest=pack.digest)
        if urlsplit(iri).scheme not in {"http", "https"}:
            return Target(iri, "unresolved", reason="not a web address")
        if _origin(iri) not in allowed:
            return Target(
                iri,
                "unresolved",
                reason="on another site, and not a specification the index has shapes for",
            )
        lock = self._locks.setdefault(iri, asyncio.Lock())
        async with lock:
            async with self._session_factory() as session:
                row = await session.get(DeclaredProfile, iri)
            if row is None or utcnow() - row.fetched_at > FRESH:
                row = await self._fetch(iri, allowed)
        return _target(row)

    async def _fetch(self, iri: str, allowed: set[tuple[str, str, int | None]]) -> DeclaredProfile:
        status, shapes, digest, sources, error = "failed", None, None, [], None
        try:
            graph, sources = await self._collect(iri, allowed)
            if has_shapes(graph):
                status = "shapes"
                shapes, digest = shapes_text(graph)
            else:
                status, error = "no-shapes", "no SHACL shapes found for this profile"
        except _UnresolvedError as err:
            status, error = err.status, err.reason
        row = DeclaredProfile(
            iri=iri,
            fetched_at=utcnow(),
            status=status,
            shapes=shapes,
            digest=digest,
            sources=sources,
            error=error[:512] if error else None,
        )
        async with self._session_factory() as session, session.begin():
            await session.merge(row)
        log.info("declared_profile_resolved", iri=iri, status=status, sources=len(sources))
        return row

    async def _collect(
        self, iri: str, allowed: set[tuple[str, str, int | None]]
    ) -> tuple[Graph, list[str]]:
        """The SHACL of a profile and the profiles it builds on, and the documents read."""
        shapes = Graph()
        read: list[str] = []
        profiles: list[tuple[str, int]] = [(iri, 0)]
        seen: set[str] = set()
        documents: list[str] = []
        while profiles:
            current, depth = profiles.pop(0)
            if current in seen or _origin(current) not in allowed:
                continue
            seen.add(current)
            try:
                graph = await self._get(current)
            except _UnresolvedError:
                if current == iri:
                    raise
                continue  # a parent profile that does not answer (the core profile often)
            read.append(current)
            if has_shapes(graph):
                shapes += graph
                continue
            subject = URIRef(current)
            for resource in graph.objects(subject, PROF.hasResource):
                if (resource, PROF.hasRole, VALIDATION_ROLE) not in graph:
                    continue
                for artifact in graph.objects(resource, PROF.hasArtifact):
                    if isinstance(artifact, URIRef) and str(artifact) not in documents:
                        documents.append(str(artifact))
            if depth < MAX_DEPTH:
                for parent in graph.objects(subject, PROF.isProfileOf):
                    if isinstance(parent, URIRef):
                        profiles.append((str(parent), depth + 1))
        for document in documents[:MAX_DOCUMENTS]:
            if _origin(document) not in allowed:
                continue
            try:
                shapes += await self._get(document)
                read.append(document)
            except _UnresolvedError:
                continue
        return shapes, read

    async def _get(self, url: str) -> Graph:
        if self._outbound is None:
            raise _UnresolvedError("failed", "the index makes no outbound requests here")
        try:
            response = await self._outbound.request(
                "GET", url, timeout_seconds=TIMEOUT_SECONDS, headers={"Accept": ACCEPT}
            )
        except OutboundError as err:
            status = "refused" if err.kind in {"blocked", "denied"} else "failed"
            raise _UnresolvedError(status, err.message) from err
        if not 200 <= response.status < 300:
            raise _UnresolvedError("failed", f"HTTP {response.status}")
        if response.truncated:
            raise _UnresolvedError("failed", "response too large")
        try:
            return await asyncio.to_thread(parse_rdf, response.body, response.content_type, url)
        except RdfParseError as err:
            raise _UnresolvedError("failed", f"cannot parse: {err}") from err


class _UnresolvedError(Exception):
    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _target(row: DeclaredProfile) -> Target:
    if row.status == "shapes" and row.shapes:
        return Target(row.iri, "shapes", shapes=row.shapes, digest=row.digest)
    return Target(row.iri, "unresolved", reason=row.error or row.status)
