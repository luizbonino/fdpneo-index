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

import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

LABEL_PREDICATES = (
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "http://www.w3.org/2000/01/rdf-schema#label",
    "http://purl.org/dc/terms/title",
    "http://xmlns.com/foaf/0.1/name",
)


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
        if len(self._hits) > 10_000:  # forget idle callers
            for stale in [k for k, v in self._hits.items() if not v or v[-1] < now - 60][:5000]:
                del self._hits[stale]
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
        sync: SparqlSync,
        limits: Limits,
        library: Library | None = None,
    ) -> None:
        self.store = store
        self.pool = pool
        self.sync = sync
        self.limits = limits
        self.library = library or default_library()
        self.rate = RateLimiter(limits.per_minute)
        self._schema_cache: tuple[int, dict[str, Any]] | None = None

    # --- graphs -----------------------------------------------------------------

    def default_graphs(self) -> tuple[str, ...]:
        return (*self.store.snapshot_graphs, ENTRIES_GRAPH.value)

    def named_graphs(self) -> tuple[str, ...]:
        return (*self.default_graphs(), VOID_GRAPH.value, EXAMPLES_GRAPH.value)

    # --- queries ----------------------------------------------------------------

    def check(self, text: str) -> CheckedQuery:
        return check_query(text, max_chars=self.limits.max_query_chars)

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
    ) -> QueryResult:
        checked = self.check(text)
        chosen = negotiate(checked.form, fmt, accept)
        for iri in [*(default_graph_uri or []), *(named_graph_uri or [])]:
            iri_term(iri)  # validates
        return await self.pool.run(
            checked,
            fmt=chosen,
            snapshot=str(self.store.snapshot),
            timeout=timeout or (self.limits.admin_timeout if admin else self.limits.timeout),
            max_rows=min(max_rows or self.limits.max_rows, self.limits.max_rows),
            max_bytes=self.limits.max_bytes,
            default_graphs=tuple(default_graph_uri or ()) or self.default_graphs(),
            named_graphs=tuple(named_graph_uri or ()) or self.named_graphs(),
        )

    def validate(self, text: str) -> dict[str, Any]:
        try:
            checked = self.check(text)
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
        generation = self.store.generation
        if self._schema_cache and self._schema_cache[0] == generation:
            return self._schema_cache[1]
        classes, properties = self.store.void_partitions()
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
        self._schema_cache = (generation, result)
        return result

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
        if predicate:
            pattern = f"?s {iri_term(predicate)} ?value ."
        elif type_iri:
            pattern = f"?value a {iri_term(type_iri)} ."
        else:
            raise QueryRejected("Give a predicate or a type.")
        needle = string_term(text.strip().lower()[:100])
        labels = " ".join(iri_term(p) for p in LABEL_PREDICATES)
        query = f"""SELECT ?value (SAMPLE(?l) AS ?label) (COUNT(*) AS ?count) WHERE {{
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
        values = " ".join(iri_term(i) for i in wanted)
        predicates = " ".join(iri_term(p) for p in LABEL_PREDICATES)
        rows = await self._select(
            f"""SELECT ?s ?p ?l WHERE {{ VALUES ?s {{ {values} }} VALUES ?p {{ {predicates} }}
                 ?s ?p ?l FILTER(isLiteral(?l)) }} LIMIT 5000""",
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

    async def _select(self, query: str, *, timeout: float) -> list[dict[str, Any]]:
        import json

        result = await self.query(query, fmt="json", admin=True, timeout=timeout, max_rows=1000)
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
    <http://www.w3.org/ns/formats/N-Triples>, <http://www.w3.org/ns/formats/JSON-LD> ;
  sd:feature sd:UnionDefaultGraph ;
  sd:defaultDataset [
    a sd:Dataset ;
{graphs}    sd:defaultGraph [ a sd:Graph ; void:inDataset <urn:fdpindex:dataset> ]
  ] .
"""
