"""The SPARQL store: an embedded pyoxigraph store derived from ``harvested_node``.

Search design §2.2.1. Layout:

* one named graph per accepted FDP, named by its client URL, holding the
  union of its harvested nodes exactly as harvested;
* ``<urn:fdpindex:entries>``: one ``fdp-o:FAIRDataPoint`` per accepted FDP with
  its title, state, country, last retrieval and node count, so queries can
  join on FDP status without leaving SPARQL;
* ``<urn:fdpindex:void>``: VoID class and property partitions with counts,
  for the UI's autocomplete and for tools such as SIB's sparql-editor;
* ``<urn:fdpindex:sync>``: per FDP, the fingerprint of what was loaded.

The fingerprints live in the store rather than in PostgreSQL (a deviation
from the design's ``search_sparql_sync`` table): the store is a per-replica
cache, and keeping its sync state inside it means a wiped or fresh volume
resyncs everything by itself, with no table keyed by replica.

Only this process writes. Query workers open the directory read-only; a
read-only handle is a snapshot, so the store bumps ``generation`` after each
change and workers reopen when it moved (confirmed on pyoxigraph 0.5.13).
All methods are blocking: call them from a thread.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyoxigraph as ox

NS_INDEX = "urn:fdpindex:"
ENTRIES_GRAPH = ox.NamedNode("urn:fdpindex:entries")
VOID_GRAPH = ox.NamedNode("urn:fdpindex:void")
SYNC_GRAPH = ox.NamedNode("urn:fdpindex:sync")
EXAMPLES_GRAPH = ox.NamedNode("urn:fdpindex:examples")
SERVICE_GRAPHS = frozenset({ENTRIES_GRAPH, VOID_GRAPH, SYNC_GRAPH, EXAMPLES_GRAPH})

RDF_TYPE = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
XSD = "http://www.w3.org/2001/XMLSchema#"
DCT = "http://purl.org/dc/terms/"
VOID = "http://rdfs.org/ns/void#"
FDP_O = "https://w3id.org/fdp/fdp-o#"
R3D = "http://www.re3data.org/schema/3-0#"
FINGERPRINT = ox.NamedNode(NS_INDEX + "fingerprint")


def fingerprint(content_hashes: Iterable[str]) -> str:
    """Of an entry's harvested nodes; computable from PostgreSQL alone."""
    digest = hashlib.sha256()
    for item in sorted(content_hashes):
        digest.update(item.encode())
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class EntryFacts:
    """What the entries graph says about one accepted FDP."""

    client_url: str
    uuid: str
    state: str
    title: str | None
    country: str | None
    publisher: str | None
    last_retrieval: datetime | None
    nodes: int


class SparqlStore:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._store = ox.Store(str(path))
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def _changed(self) -> None:
        self._store.flush()
        self._generation += 1

    # --- per-FDP graphs ---------------------------------------------------------

    def fingerprints(self) -> dict[str, str]:
        """Client URL → fingerprint of what is loaded."""
        return {
            quad.subject.value: quad.object.value
            for quad in self._store.quads_for_pattern(None, FINGERPRINT, None, SYNC_GRAPH)
            if isinstance(quad.subject, ox.NamedNode) and isinstance(quad.object, ox.Literal)
        }

    def replace_graph(self, client_url: str, ntriples: Iterable[str], fingerprint: str) -> int:
        """Replace the FDP's graph with ``ntriples`` (one document per node); return triples."""
        graph = ox.NamedNode(client_url)
        data = "\n".join(ntriples).encode()
        with self._lock:
            if self._store.contains_named_graph(graph):
                self._store.clear_graph(graph)
            self._store.load(data, format=ox.RdfFormat.N_TRIPLES, to_graph=graph)
            self._set_fingerprint(graph, fingerprint)
            self._changed()
        return sum(1 for _ in self._store.quads_for_pattern(None, None, None, graph))

    def drop_graph(self, client_url: str) -> None:
        graph = ox.NamedNode(client_url)
        with self._lock:
            if self._store.contains_named_graph(graph):
                self._store.remove_graph(graph)
            for quad in list(self._store.quads_for_pattern(graph, FINGERPRINT, None, SYNC_GRAPH)):
                self._store.remove(quad)
            self._changed()

    def fdp_graphs(self) -> set[str]:
        return {
            g.value
            for g in self._store.named_graphs()
            if isinstance(g, ox.NamedNode) and g not in SERVICE_GRAPHS
        }

    def _set_fingerprint(self, graph: ox.NamedNode, value: str) -> None:
        for quad in list(self._store.quads_for_pattern(graph, FINGERPRINT, None, SYNC_GRAPH)):
            self._store.remove(quad)
        self._store.add(ox.Quad(graph, FINGERPRINT, ox.Literal(value), SYNC_GRAPH))

    # --- service graphs ---------------------------------------------------------

    def write_entries(self, entries: Iterable[EntryFacts]) -> None:
        quads: list[ox.Quad] = []
        for entry in entries:
            subject = ox.NamedNode(entry.client_url)

            def add(
                predicate: str, value: ox.NamedNode | ox.Literal, subject: ox.NamedNode = subject
            ) -> None:
                quads.append(ox.Quad(subject, ox.NamedNode(predicate), value, ENTRIES_GRAPH))

            add(RDF_TYPE.value, ox.NamedNode(FDP_O + "FAIRDataPoint"))
            add(NS_INDEX + "entry", ox.Literal(entry.uuid))
            add(NS_INDEX + "state", ox.Literal(entry.state))
            add(
                NS_INDEX + "harvestedNodes",
                ox.Literal(str(entry.nodes), datatype=ox.NamedNode(XSD + "integer")),
            )
            if entry.title:
                add(DCT + "title", ox.Literal(entry.title))
            if entry.publisher:
                add(DCT + "publisher", ox.Literal(entry.publisher))
            if entry.country:
                add(R3D + "country", ox.Literal(entry.country))
            if entry.last_retrieval:
                add(
                    NS_INDEX + "lastRetrieval",
                    ox.Literal(
                        entry.last_retrieval.isoformat(), datatype=ox.NamedNode(XSD + "dateTime")
                    ),
                )
        with self._lock:
            self._store.clear_graph(ENTRIES_GRAPH)
            self._store.extend(quads)
            self._changed()

    def write_examples(self, quads: Iterable[ox.Quad]) -> None:
        with self._lock:
            self._store.clear_graph(EXAMPLES_GRAPH)
            self._store.extend(list(quads))
            self._changed()

    def write_void(self, endpoint: str) -> None:
        """Regenerate the VoID description from the store itself (whole-store partitions)."""
        dataset = ox.NamedNode(NS_INDEX + "dataset")
        classes = self._store.query(
            "SELECT ?c (COUNT(*) AS ?n) WHERE { ?s a ?c } GROUP BY ?c",
            use_default_graph_as_union=True,
        )
        properties = self._store.query(
            "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p",
            use_default_graph_as_union=True,
        )
        quads = [
            ox.Quad(dataset, RDF_TYPE, ox.NamedNode(VOID + "Dataset"), VOID_GRAPH),
            ox.Quad(
                dataset, ox.NamedNode(VOID + "sparqlEndpoint"), ox.NamedNode(endpoint), VOID_GRAPH
            ),
        ]
        triples = 0
        for kind, rows, key in (("class", classes, "c"), ("property", properties, "p")):
            for index, row in enumerate(rows):  # pyright: ignore[reportArgumentType]
                term, count = row[key], row["n"]
                if not isinstance(term, ox.NamedNode) or term.value.startswith(NS_INDEX):
                    continue
                if kind == "property":
                    triples += int(count.value)
                part = ox.NamedNode(f"{NS_INDEX}void/{kind}/{index}")
                quads += [
                    ox.Quad(dataset, ox.NamedNode(f"{VOID}{kind}Partition"), part, VOID_GRAPH),
                    ox.Quad(part, ox.NamedNode(VOID + kind), term, VOID_GRAPH),
                    ox.Quad(
                        part,
                        ox.NamedNode(VOID + ("entities" if kind == "class" else "triples")),
                        ox.Literal(count.value, datatype=ox.NamedNode(XSD + "integer")),
                        VOID_GRAPH,
                    ),
                ]
        quads.append(
            ox.Quad(
                dataset,
                ox.NamedNode(VOID + "triples"),
                ox.Literal(str(triples), datatype=ox.NamedNode(XSD + "integer")),
                VOID_GRAPH,
            )
        )
        with self._lock:
            self._store.clear_graph(VOID_GRAPH)
            self._store.extend(quads)
            self._changed()

    def void_partitions(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        """(classes, properties) with counts, from the VoID graph."""
        rows = self._store.query(
            """SELECT ?kind ?term ?n WHERE { GRAPH <urn:fdpindex:void> {
                 { ?part <http://rdfs.org/ns/void#class> ?term ; <http://rdfs.org/ns/void#entities> ?n BIND("c" AS ?kind) }
                 UNION
                 { ?part <http://rdfs.org/ns/void#property> ?term ; <http://rdfs.org/ns/void#triples> ?n BIND("p" AS ?kind) }
               } }"""
        )
        classes: list[tuple[str, int]] = []
        properties: list[tuple[str, int]] = []
        for row in rows:  # pyright: ignore[reportGeneralTypeIssues]
            target = classes if row["kind"].value == "c" else properties
            target.append((row["term"].value, int(row["n"].value)))
        return (
            sorted(classes, key=lambda item: -item[1]),
            sorted(properties, key=lambda item: -item[1]),
        )

    def counts(self) -> Mapping[str, int]:
        return {"graphs": len(self.fdp_graphs())}

    def close(self) -> None:
        with self._lock:
            self._store.flush()
        del self._store
