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

Only this process writes, to ``<dir>/store``. Opening that directory
read-only while it is written is undefined behaviour (pyoxigraph's own
warning; in tests a reader hit SST files that compaction had just deleted),
so query workers never touch it. After each sync pass the writer
``publish``es a checkpoint, ``<dir>/snapshots/<generation>``, made with
hard links (cheap on one file system), and workers query the newest one.
Each published snapshot is a consistent view of a whole sync pass. Old
snapshots are removed once no query can still be reading them: they are kept
for ``keep_seconds`` (longer than the longest query budget).
All methods are blocking: call them from a thread.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
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
    def __init__(self, path: Path, *, keep_seconds: float = 300.0) -> None:
        self.path = path
        self._snapshots = path / "snapshots"
        (path / "store").mkdir(parents=True, exist_ok=True)
        # No worker runs yet: snapshots from an earlier run are stale.
        shutil.rmtree(self._snapshots, ignore_errors=True)
        self._snapshots.mkdir(parents=True)
        self._store = ox.Store(str(path / "store"))
        self._lock = threading.Lock()
        self._generation = 0
        self._keep_seconds = keep_seconds
        self._published: list[tuple[float, Path]] = []
        self._published_graphs: tuple[str, ...] = ()
        self._entries: list[EntryFacts] | None = None
        self.publish()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def snapshot(self) -> Path:
        """The newest published snapshot: what queries read."""
        return self._published[-1][1]

    @property
    def snapshot_graphs(self) -> tuple[str, ...]:
        """The FDP graphs in the newest snapshot (so the default graph matches what is read)."""
        return self._published_graphs

    def publish(self) -> Path:
        """Checkpoint the store for readers, if it changed since the last checkpoint."""
        with self._lock:
            target = self._snapshots / f"{self._generation:010d}"
            if self._published and self._published[-1][1] == target:
                return target
            self._store.flush()
            self._store.backup(str(target))
            self._published_graphs = tuple(sorted(self.fdp_graphs()))
            now = time.monotonic()
            self._published.append((now, target))
            keep: list[tuple[float, Path]] = []
            for created, snapshot in self._published[:-1]:
                # A snapshot stops being "newest" when the next one appears; queries that
                # started on it finish within their budget, well inside keep_seconds.
                if now - created > self._keep_seconds:
                    shutil.rmtree(snapshot, ignore_errors=True)
                else:
                    keep.append((created, snapshot))
            self._published = [*keep, self._published[-1]]
            return target

    def _changed(self) -> None:
        # No flush here: publish() flushes once per pass, before the checkpoint.
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
        with self._lock:
            if self._store.contains_named_graph(graph):
                self._store.clear_graph(graph)
            # Node by node, so an entry's harvest is never held in memory twice. Blank
            # node labels are scoped per node by the harvester, so documents cannot merge.
            for document in ntriples:
                self._store.load(document.encode(), format=ox.RdfFormat.N_TRIPLES, to_graph=graph)
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

    def write_entries(self, entries: Iterable[EntryFacts]) -> bool:
        """Rewrite the entries graph; skipped (False) when nothing changed since the last write."""
        facts = list(entries)
        if facts == self._entries:
            return False
        quads: list[ox.Quad] = []
        for entry in facts:
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
        self._entries = facts
        return True

    def write_examples(self, quads: Iterable[ox.Quad]) -> None:
        with self._lock:
            self._store.clear_graph(EXAMPLES_GRAPH)
            self._store.extend(list(quads))
            self._changed()

    def write_void(self, endpoint: str) -> None:
        """Regenerate the VoID description from the store itself (whole-store partitions)."""
        dataset = ox.NamedNode(NS_INDEX + "dataset")
        # Count the FDP graphs and the entries graph only, never the service graphs
        # (an earlier VoID description would otherwise count itself).
        graphs: list[ox.NamedNode | ox.BlankNode | ox.DefaultGraph] = [
            ox.NamedNode(g) for g in sorted(self.fdp_graphs())
        ]
        graphs.append(ENTRIES_GRAPH)
        classes = self._store.query(
            "SELECT ?c (COUNT(*) AS ?n) WHERE { ?s a ?c } GROUP BY ?c", default_graph=graphs
        )
        properties = self._store.query(
            "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p", default_graph=graphs
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
