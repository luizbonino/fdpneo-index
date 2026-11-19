"""Records: what one validation sees (conformance design §1).

A record is one harvested node's resource (an FDP root, catalogue, dataset,
dataset series or data service) with the blank nodes and inline resources its
node holds; a dataset or series also gets the nodes of its distributions
(``dcat:distribution``) when the same FDP serves them. Resources described in
other nodes, a publisher with its own page for instance, are not followed:
the record is what the FDP serves for the resource.

The kind is the search projection's (the profile's ``types``). A record's
content hash is the hash of its nodes' hashes, so it changes exactly when
one of its nodes does.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rdflib import RDF, Graph, URIRef

from fdpindex.search.project import find_subject, kind_classifier, public_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fdpindex.search.profile import Kind, Profile

DCAT_DISTRIBUTION = URIRef("http://www.w3.org/ns/dcat#distribution")
RECORD_KINDS: frozenset[str] = frozenset({"fdp", "catalog", "dataset", "series", "service"})
WITH_DISTRIBUTIONS: frozenset[str] = frozenset({"dataset", "series"})


@dataclass(frozen=True)
class Record:
    iri: str
    public_id: str
    kind: Kind
    nodes: tuple[str, ...]  # N-Triples of each node, the resource's own first
    content_hash: str


def content_hash(hashes: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


def assemble(
    entry_uuid: str, nodes: Iterable[tuple[str, str, str]], profile: Profile
) -> list[Record]:
    """The records of an entry from its nodes ``(node_iri, ntriples, content_hash)``."""
    kind_of = kind_classifier(profile)
    parsed: dict[str, tuple[Kind, str, str, list[str]]] = {}  # subject → kind, nt, hash, dists
    for node_iri, ntriples, digest in sorted(nodes):
        graph = Graph()
        try:
            graph.parse(data=ntriples, format="nt")
        except Exception:  # noqa: S112 - stored N-Triples parsed before; skip a bad one
            continue
        subject = find_subject(node_iri, graph)
        if subject is None or str(subject) in parsed:
            continue
        kind = kind_of(str(t) for t in graph.objects(subject, RDF.type))
        distributions = [
            str(o) for o in graph.objects(subject, DCAT_DISTRIBUTION) if isinstance(o, URIRef)
        ]
        parsed[str(subject)] = (kind, ntriples, digest, distributions)
    records: list[Record] = []
    for iri, (kind, ntriples, digest, distributions) in parsed.items():
        if kind not in RECORD_KINDS:
            continue
        texts, hashes = [ntriples], [digest]
        if kind in WITH_DISTRIBUTIONS:
            for dist in sorted(set(distributions)):
                found = parsed.get(dist)
                if found is not None and found[0] == "distribution":
                    texts.append(found[1])
                    hashes.append(found[2])
        records.append(
            Record(
                iri=iri,
                public_id=public_id(entry_uuid, iri),
                kind=kind,
                nodes=tuple(texts),
                content_hash=content_hash(hashes),
            )
        )
    return records


def record_graph(nodes: Iterable[str]) -> Graph:
    """One graph of a record's nodes; blank nodes stay apart (each parse gets fresh ones)."""
    graph = Graph()
    for ntriples in nodes:
        part = Graph()
        part.parse(data=ntriples, format="nt")
        for triple in part:
            graph.add(triple)
    return graph
