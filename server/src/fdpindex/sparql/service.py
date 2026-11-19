"""The SPARQL service: what the endpoint, the query library and the UI call.

It owns the store, the worker pool and the sync loop (search design §2.2):

* ``query``: gate, then a worker; results in the negotiated format;
* ``validate``: the gate only, with a position for the editor;
* ``schema``: classes, properties and graphs with counts and labels, for
  autocomplete and the query builder;
* ``suggest``: values of a predicate (or instances of a class) matching some
  text, with labels, for parameter forms and the builder;
* the query library: list, expand, run.

The default graph is the union of the FDP graphs and the entries graph;
``GRAPH`` reaches those plus the VoID and example graphs. Only accepted FDPs
are in the store, so anonymous callers see what the directory shows them.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fdpindex.rdf import LABEL_PREDICATES as RDF_LABEL_PREDICATES
from fdpindex.sparql.examples import Example, Library, default_library, expand
from fdpindex.sparql.gate import CheckedQuery, QueryRejected, check_query, iri_term, string_term
from fdpindex.sparql.labels import KNOWN_LABELS, humanize
from fdpindex.sparql.prefixes import PREFIXES, compact, with_prefixes
from fdpindex.sparql.store import ENTRIES_GRAPH, EXAMPLES_GRAPH, VOID_GRAPH
from fdpindex.sparql.workers import GRAPH_FORMATS, MEDIA_TYPES, SOLUTION_FORMATS, QueryFailed

if TYPE_CHECKING:
    from fdpindex.sparql.store import SparqlStore
    from fdpindex.sparql.sync import SparqlSync
    from fdpindex.sparql.workers import QueryResult, WorkerPool

_ACCEPT: dict[str, str] = {
    "application/sparql-results+json": "json",
    "application/json": "json",
    "application/sparql-results+xml": "xml",
    "application/xml": "xml",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/n-triples": "ntriples",
    "application/ld+json": "jsonld",
    "application/rdf+xml": "rdfxml",
}
_FORMAT_NAMES = {name: name for name in MEDIA_TYPES} | {"ttl": "turtle", "nt": "ntriples"}

# The same predicates the search projection and the concept resolver read.
LABEL_PREDICATES = tuple(str(p) for p in RDF_LABEL_PREDICATES)


@dataclass(frozen=True)
class Limits:
    timeout: float
    admin_timeout: float
    max_rows: int
    max_bytes: int
    max_query_chars: int
    per_minute: int


class RateLimiter:
    """Per-key sliding window, in memory and per process (search design §2.2.3)."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, cost: int = 1) -> bool:
        if self._per_minute <= 0:
            return True
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) + cost > self._per_minute:
            return False
        hits.extend([now] * cost)
        if len(self._hits) > 10_000:
            # Forget the callers seen least recently, idle or not, so the table stays bounded.
            by_last = sorted(self._hits, key=lambda k: self._hits[k][-1] if self._hits[k] else 0.0)
            for old in by_last[:5000]:
                if old != key:
                    del self._hits[old]
        return True


def negotiate(form: str, fmt: str | None, accept: str | None) -> str:
    allowed = GRAPH_FORMATS if form in {"CONSTRUCT", "DESCRIBE"} else SOLUTION_FORMATS
    if form == "ASK":
        allowed = frozenset({"json", "xml"})
    if fmt:
        name = _FORMAT_NAMES.get(fmt.lower())
        if name in allowed:
            return name
        raise QueryFailed(f"Format {fmt!r} is not available for a {form} query.", status=406)
    for part in (accept or "").split(","):
        media = part.split(";")[0].strip().lower()
        name = _ACCEPT.get(media)
        if name in allowed:
            return name
    return "turtle" if form in {"CONSTRUCT", "DESCRIBE"} else "json"


class SparqlService:
    def __init__(
        self,
        store: SparqlStore,
        pool: WorkerPool,
        sync: SparqlSync | None,
        limits: Limits,
        library: Library | None = None,
    ) -> None:
        self.store = store
        self.pool = pool
        self.sync = sync
        self.limits = limits
        self.library = library or default_library()
        self.rate = RateLimiter(limits.per_minute)
        # The editor validates on every pause in typing: a larger, separate budget,
        # so editing does not use up the queries.
        self.validate_rate = RateLimiter(limits.per_minute * 4)
        self._schema_cache: tuple[str, dict[str, Any]] | None = None
        self._inflight: dict[str, int] = {}

    # --- graphs -----------------------------------------------------------------

    def default_graphs(self) -> tuple[str, ...]:
        return (*self.store.snapshot_graphs, ENTRIES_GRAPH.value)

    def named_graphs(self) -> tuple[str, ...]:
        return (*self.default_graphs(), VOID_GRAPH.value, EXAMPLES_GRAPH.value)

    # --- queries ----------------------------------------------------------------

    async def check(self, text: str, *, internal: bool = False) -> CheckedQuery:
        """The gate, off the event loop (rdflib parsing takes up to half a second).

        ``internal`` queries are the service's own (labels, suggestions, schema):
        their size is bounded by construction, not by the user-facing cap.
        """
        max_chars = 2_000_000 if internal else self.limits.max_query_chars
        return await asyncio.to_thread(check_query, text, max_chars=max_chars)

    def _restrict(self, requested: list[str] | None) -> tuple[str, ...] | None:
        """Requested dataset graphs, limited to the ones the endpoint offers."""
        if not requested:
            return None
        offered = set(self.named_graphs())
        for iri in requested:
            iri_term(iri)  # validates
            if iri not in offered:
                raise QueryRejected(f"Unknown graph: {iri[:120]}")
        return tuple(requested)

    async def query(
        self,
        text: str,
        *,
        fmt: str | None = None,
        accept: str | None = None,
        admin: bool = False,
        default_graph_uri: list[str] | None = None,
        named_graph_uri: list[str] | None = None,
        timeout: float | None = None,
        max_rows: int | None = None,
        internal: bool = False,
    ) -> QueryResult:
        checked = await self.check(text, internal=internal)
        chosen = negotiate(checked.form, fmt, accept)
        default = self._restrict(default_graph_uri)
        named = self._restrict(named_graph_uri)
        return await self.pool.run(
            checked,
            fmt=chosen,
            snapshot=lambda: str(self.store.snapshot),
            timeout=timeout or (self.limits.admin_timeout if admin else self.limits.timeout),
            max_rows=min(max_rows or self.limits.max_rows, self.limits.max_rows),
            max_bytes=self.limits.max_bytes,
            default_graphs=(lambda: default) if default else self.default_graphs,
            named_graphs=(lambda: named) if named else self.named_graphs,
            privileged=admin,
        )

    async def query_for(self, caller_key: str | None, text: str, **options: Any) -> QueryResult:
        """``query`` for an anonymous caller (``caller_key``: their address): one at a time.

        The rate limit counts requests; without this, a few slow queries in flight
        from one address would hold every worker for their whole time budget.
        """
        if caller_key is None:
            return await self.query(text, **options)
        if self._inflight.get(caller_key, 0) >= 1:
            raise QueryFailed(
                "One query at a time, please: wait for the previous one to finish.", status=429
            )
        self._inflight[caller_key] = self._inflight.get(caller_key, 0) + 1
        try:
            return await self.query(text, **options)
        finally:
            left = self._inflight[caller_key] - 1
            if left:
                self._inflight[caller_key] = left
            else:
                del self._inflight[caller_key]

    async def validate(self, text: str) -> dict[str, Any]:
        try:
            checked = await self.check(text)
        except QueryRejected as err:
            return {"ok": False, "message": err.message, "line": err.line, "column": err.column}
        return {"ok": True, "form": checked.form, "hasLimit": checked.has_limit}

    # --- query library ----------------------------------------------------------

    def example(self, example_id: str) -> Example:
        for example in self.library.examples:
            if example.id == example_id:
                return example
        raise KeyError(example_id)

    def expand_example(self, example_id: str, values: dict[str, str]) -> str:
        return with_prefixes(expand(self.example(example_id), values))

    # --- schema and suggestions -------------------------------------------------

    async def schema(self) -> dict[str, Any]:
        # Everything comes from one snapshot, and the cache is keyed by it.
        snapshot = str(self.store.snapshot)
        if self._schema_cache and self._schema_cache[0] == snapshot:
            return self._schema_cache[1]
        classes, properties = await self._partitions()
        graphs = await self._graph_titles()
        result = {
            "prefixes": {p: ns for p, ns in PREFIXES.items() if p != "dcterms"},
            "classes": [
                {"iri": iri, "curie": compact(iri), "label": humanize(iri), "count": n}
                for iri, n in classes
            ],
            "properties": [
                {"iri": iri, "curie": compact(iri), "label": humanize(iri), "count": n}
                for iri, n in properties
            ],
            "graphs": graphs,
            "defaultGraph": "union of the FDP graphs and urn:fdpindex:entries",
        }
        self._schema_cache = (snapshot, result)
        return result

    async def _partitions(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        rows = await self._select(
            """SELECT ?kind ?term ?n WHERE { GRAPH <urn:fdpindex:void> {
                 { ?part <http://rdfs.org/ns/void#class> ?term ;
                         <http://rdfs.org/ns/void#entities> ?n BIND("c" AS ?kind) }
                 UNION
                 { ?part <http://rdfs.org/ns/void#property> ?term ;
                         <http://rdfs.org/ns/void#triples> ?n BIND("p" AS ?kind) }
               } }""",
            timeout=10,
            max_rows=10_000,
        )
        classes = [(r["term"], int(r["n"])) for r in rows if r["kind"] == "c"]
        properties = [(r["term"], int(r["n"])) for r in rows if r["kind"] == "p"]
        return sorted(classes, key=lambda i: -i[1]), sorted(properties, key=lambda i: -i[1])

    async def _graph_titles(self) -> list[dict[str, Any]]:
        rows = await self._select(
            """SELECT ?fdp ?title ?state WHERE { GRAPH <urn:fdpindex:entries> {
                 ?fdp <urn:fdpindex:state> ?state . OPTIONAL { ?fdp <http://purl.org/dc/terms/title> ?title } } }
               ORDER BY ?title""",
            timeout=5,
        )
        return [
            {"iri": r["fdp"], "title": r.get("title") or r["fdp"], "state": r.get("state")}
            for r in rows
        ]

    async def suggest(
        self, *, predicate: str | None, type_iri: str | None, text: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Values of ``predicate`` (or instances of ``type_iri``) whose value or label contains ``text``."""
        # ?s is what is counted: resources using the value, or the instance itself.
        if predicate:
            pattern = f"?s {iri_term(predicate)} ?value ."
        elif type_iri:
            pattern = f"?value a {iri_term(type_iri)} . BIND(?value AS ?s)"
        else:
            raise QueryRejected("Give a predicate or a type.")
        needle = string_term(text.strip().lower()[:100])
        labels = " ".join(iri_term(p) for p in LABEL_PREDICATES)
        query = f"""SELECT ?value (SAMPLE(?l) AS ?label) (COUNT(DISTINCT ?s) AS ?count) WHERE {{
              {pattern}
              OPTIONAL {{ VALUES ?lp {{ {labels} }} ?value ?lp ?l }}
              FILTER(CONTAINS(LCASE(STR(?value)), {needle}) || CONTAINS(LCASE(STR(COALESCE(?l, ""))), {needle}))
            }} GROUP BY ?value ORDER BY DESC(?count) LIMIT {max(1, min(limit, 50))}"""
        rows = await self._select(query, timeout=5)
        return [
            {
                "value": r["value"],
                "kind": r["_kinds"]["value"],
                "label": r.get("label")
                or (humanize(r["value"]) if r["_kinds"]["value"] == "uri" else r["value"]),
                "count": int(r.get("count") or 0),
            }
            for r in rows
        ]

    async def labels(self, iris: list[str]) -> dict[str, str]:
        """Labels for IRIs: bundled vocabulary labels, else labels found in the data.

        Prefers ``skos:prefLabel`` over ``rdfs:label``, ``dct:title`` and
        ``foaf:name``, and English or untagged literals over other languages.
        IRIs without a label are left out; the client shortens those itself.
        """
        found: dict[str, str] = {}
        wanted: list[str] = []
        for iri in dict.fromkeys(iris[:500]):
            if iri in KNOWN_LABELS:
                found[iri] = KNOWN_LABELS[iri]
            else:
                try:
                    iri_term(iri)
                except QueryRejected:
                    continue
                wanted.append(iri)
        if not wanted:
            return found
        predicates = " ".join(iri_term(p) for p in LABEL_PREDICATES)
        rows: list[dict[str, Any]] = []
        for start in range(0, len(wanted), 100):
            values = " ".join(iri_term(i) for i in wanted[start : start + 100])
            rows += await self._select(
                f"""SELECT ?s ?p ?l WHERE {{ VALUES ?s {{ {values} }} VALUES ?p {{ {predicates} }}
                     ?s ?p ?l FILTER(isLiteral(?l)) }} LIMIT 2000""",
                timeout=5,
            )
        best: dict[str, tuple[int, int, str]] = {}
        for row in rows:
            rank = LABEL_PREDICATES.index(row["p"]) if row["p"] in LABEL_PREDICATES else 9
            lang = row.get("_lang", "")
            lang_rank = 0 if lang in {"", "en"} or lang.startswith("en-") else 1
            candidate = (lang_rank, rank, row["l"])
            if row["s"] not in best or candidate < best[row["s"]]:
                best[row["s"]] = candidate
        found.update({iri: label for iri, (_, _, label) in best.items()})
        return found

    async def _select(
        self, query: str, *, timeout: float, max_rows: int = 2000
    ) -> list[dict[str, Any]]:
        import json

        result = await self.query(
            query, fmt="json", admin=True, timeout=timeout, max_rows=max_rows, internal=True
        )
        data = json.loads(result.body)
        rows: list[dict[str, Any]] = []
        for binding in data["results"]["bindings"]:
            row: dict[str, Any] = {name: term["value"] for name, term in binding.items()}
            row["_kinds"] = {name: term["type"] for name, term in binding.items()}
            row["_lang"] = next(
                (t.get("xml:lang", "") for t in binding.values() if "xml:lang" in t), ""
            )
            rows.append(row)
        return rows

    # --- description ------------------------------------------------------------

    def service_description(self, endpoint: str) -> str:
        graphs = "".join(f"    sd:namedGraph [ sd:name <{g}> ] ;\n" for g in self.named_graphs())
        return f"""@prefix sd: <http://www.w3.org/ns/sparql-service-description#> .
@prefix void: <http://rdfs.org/ns/void#> .

<{endpoint}> a sd:Service ;
  sd:endpoint <{endpoint}> ;
  sd:supportedLanguage sd:SPARQL11Query ;
  sd:resultFormat <http://www.w3.org/ns/formats/SPARQL_Results_JSON>,
    <http://www.w3.org/ns/formats/SPARQL_Results_XML>, <http://www.w3.org/ns/formats/SPARQL_Results_CSV>,
    <http://www.w3.org/ns/formats/SPARQL_Results_TSV>, <http://www.w3.org/ns/formats/Turtle>,
    <http://www.w3.org/ns/formats/N-Triples>, <http://www.w3.org/ns/formats/JSON-LD>,
    <http://www.w3.org/ns/formats/RDF_XML> ;
  sd:feature sd:UnionDefaultGraph ;
  sd:defaultDataset [
    a sd:Dataset ;
{graphs}    sd:defaultGraph [ a sd:Graph ; void:inDataset <urn:fdpindex:dataset> ]
  ] .
"""
