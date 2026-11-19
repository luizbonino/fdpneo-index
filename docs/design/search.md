# Search over harvested metadata — design

- Status: proposed (to become ADR-0005; amends the "querying is out of scope"
  line of ADR-0002)
- Date: 2026-11-18
- Scope: search across the DCAT metadata the index harvests from every
  accepted FDP; server (`server/`), client (`client/`), operations.

## 0. Decisions in one page

1. **Search is a projection, not a second store.** Harvested N-Triples
   (`harvested_node`) stay the source of truth. A background step projects
   each node into PostgreSQL search tables; the projection can be rebuilt at
   any time without network access. The faceted search needs no triple
   store; the SPARQL endpoint (decision 8) runs over a *derived, disposable*
   embedded store filled from the same table, so ADR-0002's "one database to
   back up" still holds.
2. **Two tables, not one.** `search_resource` holds one row per DCAT resource
   with typed columns, the display title/description in every language, two
   `tsvector`s and the dates/bbox used for sorting and ranges.
   `search_value` holds every multi-valued, filterable value
   (`(resource, field, value)`): facets, filters and facet counts all run on
   it, and it doubles as the "generic triple table" for extension
   predicates. Labels for IRIs live in `search_label`.
3. **A search profile maps predicates to fields.** A bundled default profile
   covers DCAT 2/3, DCAT-AP, FDP-O, r3d, FOAF, vCard, ODRL, PROV. An
   administrator can extend it at runtime (new facets from extension
   predicates, value normalisation) under `/index/search/profile`; a profile
   change re-projects from `harvested_node`.
4. **PostgreSQL full-text does the ranking.** Weighted `tsvector`
   (title A, keyword B, description C, everything else D), one vector
   stemmed per literal language and one accent-folded `simple` vector so
   every query matches regardless of language; `websearch_to_tsquery` for
   user input; `pg_trgm` for autocomplete and fuzzy title matching.
   `unaccent` and `pg_trgm` are trusted extensions, created by the migration.
5. **Result unit is the resource, default scope is "things you can find data
   in"**: datasets, dataset series, catalogs, data services and FDPs.
   Distributions are rolled up into their dataset (formats, media types,
   licences become dataset facets) and are listed under the dataset card;
   they are searchable as a type on request.
6. **New paths only.** Everything lives under `/index/search/*` and
   `/index/catalog`; `/index/entries*` keeps its shape and its ILIKE `q`
   (CLAUDE.md compatibility rule). Anonymous callers see accepted entries
   only, as everywhere else.
7. **FDP liveness is a ranking signal and a flag, not a filter by default.**
   Resources of `UNREACHABLE` / `INVALID` FDPs are shown with a flag and
   demoted; `fdpState=ACTIVE` hides them. Rejected and deleted entries have
   no search rows at all (cascade).
8. **SPARQL is a core feature, on by default for anonymous users, bounded.**
   `GET`/`POST /index/sparql` (SPARQL 1.1 Protocol, read only) runs over an
   embedded **pyoxigraph** store (RocksDB on disk, one named graph per FDP,
   plus a small graph describing the FDPs themselves), kept in sync by the
   same job that projects harvested nodes. Every query passes a structural
   gate (parsed; no `SERVICE`, no updates), runs in a separate worker process
   that is killed on a wall-clock timeout, and is capped in rows, bytes and
   rate. The **SPARQL UI** (`/sparql`) is built for people who do not write
   SPARQL: a library of parameterised queries with form fields, a visual
   query builder that shows and lets you edit the generated SPARQL, a
   CodeMirror 6 editor with prefixes, autocomplete from the harvested data
   and inline validation, labelled result tables, charts, downloads, history
   and shareable URLs, and a "show this search as SPARQL" bridge from the
   faceted search (§2.2). Other expert access: a JSON filter body on
   `POST /index/search` and field prefixes inside `q`. Semantic (pgvector)
   search is designed for but not built in the first phases; it must never
   depend on an external embeddings API by default.
9. **The index is itself a harvestable DCAT catalog**: search results are
   available as JSON-LD (a `dcat:Catalog` of the hits that points at the
   original IRIs), `/index/catalog` lists every accepted FDP's catalogs, and
   an OpenSearch description advertises the search.
10. **Protect the database from anonymous queries**: per-request
    `statement_timeout`, page-depth and parameter caps, a per-IP rate limit,
    cached facet counts; SPARQL isolated in killable worker processes with
    its own caps (§2.2.3).
11. **Borrow from the SPHN FAIR Data Point what its users liked** (§8):
    counters and a plain-language hierarchy explainer on the landing page,
    an ⓘ help per facet (the pattern this client already uses for statuses),
    numeric-range and boolean facets and per-type "key fields" on result
    cards, all configured in the profile, and Turtle / JSON-LD download on
    every resource page.

## 1. User-facing capabilities

Priorities: **MVP** (Phase 6), **SPARQL** (Phase 7), **next** (Phase 8–9),
**later** (Phase 10 or when demand shows). Each line names the predicates it relies on; the exact
mapping is in §3.3.

| Capability | Priority | Notes |
|---|---|---|
| Free-text search with relevance, phrases, `or`, `-not` | MVP | `websearch_to_tsquery` never raises on user input; title > keyword > description weighting |
| Result types: dataset, dataset series, catalog, data service, FDP, distribution, other | MVP | type from `rdf:type` with a precedence list; unknown types kept as `other` with the IRI shown |
| Facets with counts: resource type, theme (`dcat:theme`), keyword (`dcat:keyword`), publisher (`dct:publisher`), licence (`dct:license`), format (`dct:format`) and media type (`dcat:mediaType`) rolled up from distributions, access rights (`dct:accessRights`), language (`dct:language`), FDP (entry), FDP country (`r3d:country`) | MVP | OR within a facet, AND across facets; top 20 buckets with "more" |
| Date ranges on `dct:issued`, `dct:modified` (fallback `fdp-o:metadataModified`), and on harvest time | MVP | |
| Search within one FDP (`fdp=<uuid>`), entry point from the entry page | MVP | |
| Origin and status on every hit (FDP title, state badge, link to the entry and to the resource at its source) | MVP | |
| Filters in the URL, shareable; "copy link" | MVP | the URL *is* the saved query; no server-side saved queries in MVP |
| ⓘ help on every facet group and every status badge; counters per resource type and a plain-language hierarchy explainer on the landing page | MVP | SPHN pattern (§8); help texts come from the profile and `en.ts` |
| Numeric-range facets (bucketed) and yes/no facets from the profile, e.g. `healthdcatap:numberOfRecords`, "has a distribution" | next | §3.4; SPHN pattern |
| "Key fields" per resource type on result cards (two or three domain values chosen in the profile) | MVP | SPHN pattern; defaults: publisher + formats for datasets, dataset count for catalogs |
| Turtle / JSON-LD / N-Triples download on every resource page | MVP | the harvested node's triples, served from `harvested_node` |
| SPARQL endpoint (read only, bounded) and a SPARQL UI with a query library, a form-based query builder, an editor with autocomplete and validation, labelled results, charts, downloads, history and shareable URLs | **Phase 7** (right after the foundation) | §2.2 |
| Multilingual literals: display in the user's language (`lang` or `Accept-Language`), fall back to untagged, then `en`, then any; search matches every language | MVP | |
| Label resolution for IRIs (themes, licences, publishers, formats, access rights, languages, countries) | MVP for harvested + bundled vocabularies; **next** for remote dereferencing (off by default) | §3.5 |
| Highlighted snippets | next | `ts_headline`, escaped client-side |
| Autocomplete: keywords, publishers, titles, themes (labelled) | next | `pg_trgm` and prefix indexes on `search_value` |
| Temporal coverage (`dct:temporal` → `dcat:startDate`/`dcat:endDate`) overlap filter | next | also `schema:startDate`/`endDate` (DCAT-AP 1.x) and `time:hasBeginning`/`hasEnd` |
| Spatial: `dct:spatial` IRIs as a facet; bounding-box filter from `dcat:bbox` / `dcat:centroid` / `locn:geometry` WKT | next (facet) / later (bbox) | bbox without PostGIS: four numeric columns and an overlap test; PostGIS only if a deployment asks |
| Cross-FDP duplicates: group hits that share `dct:identifier`, `adms:identifier`/`skos:notation`, `owl:sameAs`, `dcat:landingPage` or a DOI | next | grouped ("also in 2 other FDPs"), not suppressed; `collapse=true` to collapse |
| Field prefixes in `q` (`title:"…"`, `keyword:x`, `publisher:x`, `theme:<iri>`) | next | |
| Extension facets from FDP-specific predicates, configured by the administrator | next | §3.4 |
| "Did you mean" from trigram similarity when a query has zero hits | later | |
| Saved queries (named, per administrator) and "featured searches" on the search page | later | the URL already covers sharing |
| Semantic / cross-lingual search (pgvector) | later, optional | §3.7 |

What is deliberately **not** offered: searching metadata of pending or
rejected FDPs (not harvested); editing or enriching metadata in the index
(the source stays authoritative, FDPneo ADR-0021).

## 2. Advanced and machine access

### 2.1 Structured filters (Phase 6 for named fields, Phase 9 for paths)

`POST /index/search` takes a JSON body; `GET /index/search` takes the same
model as query parameters (§5). The body form is canonical for expert use:

```json
{
  "q": "cardiovascular \"patient registry\" -mouse",
  "types": ["dataset", "series"],
  "lang": "nl",
  "filters": [
    {"field": "theme", "op": "in", "values": ["http://publications.europa.eu/resource/authority/data-theme/HEAL"]},
    {"field": "modified", "op": "gte", "value": "2024-01-01"},
    {"field": "temporal", "op": "overlaps", "from": "2010", "to": "2020"},
    {"field": "fdp", "op": "in", "values": ["7d1c…"]},
    {"path": "dct:conformsTo", "op": "eq", "value": "https://w3id.org/ejp-rd/…"},
    {"path": "dcat:contactPoint/vcard:hasEmail", "op": "exists"}
  ],
  "sort": "relevance",
  "page": 0, "size": 20,
  "facets": ["type", "theme", "publisher", "format", "fdp"]
}
```

- `field` names come from the search profile (built-in or extension).
- `path` is a predicate or a two-step path, as a CURIE from the profile's
  prefix map or a full IRI. Path filters run on `search_value`; whether a
  predicate is captured there is decided by the profile (§3.4).
- Operators: `eq`, `in`, `prefix`, `exists`, `missing`, `contains` (text
  ILIKE on literal values), `gte`, `lte`, `between`, `overlaps` (temporal),
  `intersects` (bbox). Unknown field, path or operator → 400 with the
  offending filter in `errors`.

### 2.2 SPARQL API and UI — core, on by default, bounded

#### 2.2.1 Engine: an embedded pyoxigraph store derived from `harvested_node`

Options weighed:

| Option | Verdict |
|---|---|
| rdflib in-memory graph of the whole corpus | Ruled out: tens of millions of triples, slow evaluator, no isolation |
| SPARQL-to-SQL over `search_value` | Ruled out: covers a fragment of SPARQL; users would hit the edges immediately |
| Sidecar triple store (Fuseki, GraphDB, Oxigraph server) in Compose | Workable (SPHN runs Fuseki as its public read-only endpoint, §8) but adds a second service to run, back up and secure, and a JVM or a second image; the index's selling point is one process + PostgreSQL (ADR-0001/0002) |
| **pyoxigraph** embedded (RocksDB `Store(path)`, SPARQL 1.1 query, MIT/Apache-2, manylinux x86_64 and aarch64 wheels, 0.5.x) | **Chosen.** No extra service; the store is a derived cache on a volume, rebuilt from PostgreSQL at any time; the same wheels run in CI |

Store layout:

- One **named graph per FDP**, IRI = the entry's client URL, holding the
  union of that entry's `harvested_node` triples exactly as harvested. The
  default graph is the union of all named graphs (`use_default_graph_as_union`
  at query time), so plain queries "just work" and `GRAPH ?fdp { … }`
  gives per-FDP answers.
- A service graph `<urn:fdpindex:entries>` with one `fdp-o:FAIRDataPoint`
  per accepted entry: client URL as subject, `dct:title`, `dct:publisher`,
  `r3d:country`, `fdpindex:state` ("ACTIVE" …), `fdpindex:lastRetrieval`,
  `fdpindex:harvestedNodes`. Queries can join on FDP status and country
  without leaving SPARQL ("FDPs by country", "datasets of reachable FDPs").
- A VoID graph `<urn:fdpindex:void>` (`void:classPartition`,
  `void:propertyPartition` with counts, per named graph and overall),
  regenerated after each sync from the store itself. It feeds the UI's
  autocomplete and schema panel, and makes the endpoint usable in SIB's
  `sparql-editor` and in YASGUI with the same conventions.
- A service description at `GET /index/sparql` without `query`
  (`sd:Service`, `sd:endpoint`, `sd:namedGraph` list, `sd:supportedLanguage
  sd:SPARQL11Query`, links to the VoID graph and to the example-query graph).
- Example queries are also published as RDF (`sh:SPARQLExecutable` with
  `sh:select`, `rdfs:label`, `rdfs:comment`, `sh:prefixes`) in
  `<urn:fdpindex:examples>`, the convention `sparql-editor` reads.

Keeping it in sync (incremental, disposable):

- `search_sparql_sync(entry_uuid PK, graph_fingerprint char(64), synced_at)`;
  the fingerprint is `sha256` over the sorted `content_hash`es of the
  entry's nodes, so it is computable from PostgreSQL alone.
- The projection job (§3.2), after projecting an entry, compares the
  fingerprint; if different it replaces the graph in one write: `DROP GRAPH`
  then bulk-load the N-Triples with the graph name (`Store.bulk_load` /
  `Store.load` with `to_graph`), then updates the `entries` and `void`
  graphs for that FDP. A whole FDP is at most 2 000 nodes (~40 000 triples),
  so per-node diffs are not worth the complexity; the write takes well under
  a second. Rejection or deletion drops the graph.
- One writer: the main process owns the primary store (`FDPINDEX_SPARQL_DIR`,
  default `/var/lib/fdpindex/sparql`); query workers open a read-only /
  secondary instance that follows it (`Store.read_only` / `Store.secondary`
  per pyoxigraph's store API; the exact constructor is confirmed in task 7.1).
  With several replicas each replica keeps its own store directory, synced
  from PostgreSQL independently (the fingerprint table is per replica, keyed
  additionally by `replica_id`).
- At start-up the service compares fingerprints for every accepted entry and
  resyncs what lags; `fdpindex sparql rebuild` wipes and rebuilds. The store
  is never backed up: PostgreSQL is.
- `FDPINDEX_SPARQL_ENABLED` defaults to **true**; `/index/config` tells the
  client whether the page exists and which limits apply.

#### 2.2.2 Endpoint

- `GET /index/sparql?query=…` and `POST /index/sparql`
  (`application/sparql-query` or form `query=`), SPARQL 1.1 Protocol for
  queries only; `default-graph-uri` / `named-graph-uri` honoured.
- Results by `Accept` or `format=`: `application/sparql-results+json`
  (default), `+xml`, `text/csv`, `text/tab-separated-values`; CONSTRUCT and
  DESCRIBE as Turtle, N-Triples, JSON-LD. CORS as the rest of the API, so
  external tools (YASGUI, `sparql-editor`, Jupyter) can use it.
- `POST /index/sparql/validate` (`{"query": "…"}`) parses and gates without
  executing; returns `{"ok": true}` or `{"ok": false, "message": "…",
  "line": n, "column": n}` for the editor's inline errors.
- `GET /index/sparql/schema?lang=` returns classes and predicates with
  counts and labels (from the VoID graph plus `search_label`), namespaces
  and suggested prefixes, for autocomplete and the builder.
- `GET /index/sparql/examples` returns the query library (§2.2.4) as JSON;
  administrators edit it under `PUT /index/sparql/examples` (bundled default,
  overrides stored in settings).
- `GET /index/search/sparql?<same parameters as /index/search>` returns the
  SPARQL equivalent of a faceted search (text, `query`, `prefixes`), the
  "show this search as SPARQL" bridge. Full text is approximated with
  `CONTAINS(LCASE(STR(?title)), "…")` over title, description and keywords,
  and the response says so (`"approximation": ["fulltext"]`).

#### 2.2.3 Limits (non-negotiable)

pyoxigraph exposes no timeout or cancellation from Python, so isolation is
by process:

- A pool of `FDPINDEX_SPARQL_WORKERS` (default 2) long-lived worker
  subprocesses, each executing one query at a time against its read-only
  store; the parent kills and respawns a worker that exceeds the wall-clock
  budget (10 s anonymous, 60 s administrators). Queue depth 10 per replica,
  then `503` with `Retry-After`.
- Structural gate before any execution, as FDPneo's
  `shared/sparql_safety.py`: rdflib `prepareQuery`; reject anything but
  SELECT/ASK/CONSTRUCT/DESCRIBE, any `SERVICE`, `LOAD` or update form. The
  worker also opens the store read-only, so a gate bug cannot write.
- Caps: query text 20 KiB; result rows 10 000 (a missing `LIMIT` is
  appended as `LIMIT 1000` for the UI and `LIMIT 10000` for the API, with a
  `X-Fdpindex-Limit-Applied` header); response bytes 10 MiB; CONSTRUCT
  triples 100 000.
- Per-IP token bucket 30 queries/min (validate and schema calls 120/min),
  same trusted-proxy rules as the ping limiter; admins exempt.
- Memory: workers run with an RSS watchdog (killed above
  `FDPINDEX_SPARQL_WORKER_MAX_MB`, default 1024). RocksDB block cache sized
  by `FDPINDEX_SPARQL_CACHE_MB` (default 256).
- Every execution is logged (hash of the query, duration, rows, outcome);
  queries are not stored server-side unless shared (§2.2.4).

#### 2.2.4 The SPARQL UI (`/sparql`)

One page, three ways in, one result area. Everything the page needs comes
from the endpoints above; nothing is hard-coded in the client.

**Query library (the default tab).** Cards grouped by question ("Find
datasets", "Compare FDPs", "Quality"), each with a title, a one-line
description, and a **form** for its parameters; no SPARQL is visible until
"Show query". Bundled queries (administrators add more under
`/index/sparql/examples`):

| Query | Parameters (form control) |
|---|---|
| Datasets by theme | theme (autocomplete with labels from `search_value`) |
| Datasets by publisher | publisher (autocomplete) |
| Datasets by licence | licence (select, labelled) |
| Datasets per FDP (counts) | — ; optional FDP state |
| Recently modified datasets | since (date), limit |
| Distributions by media type / format | media type (autocomplete) |
| Datasets without a licence / without a description / without keywords | resource type (select) |
| Keyword co-occurrence (keywords used together with …) | keyword (autocomplete), minimum count |
| Catalogs and their dataset counts | FDP (select, optional) |
| FDPs by country | — |
| Datasets with temporal coverage overlapping … | from, to (year/date) |
| Data services and the datasets they serve | — |
| Everything about one resource (DESCRIBE) | IRI (paste or pick from search) |
| Predicate usage per FDP (what metadata do FDPs provide?) | FDP (select) |
| Resources conforming to a given schema (`dct:conformsTo`) | schema IRI (autocomplete) |

A query is a template with typed parameters:

```json
{"id": "datasets-by-theme", "title": {"en": "Datasets by theme"},
 "description": {"en": "Datasets tagged with a theme, with their FDP."},
 "parameters": [{"name": "theme", "type": "iri", "label": {"en": "Theme"},
                 "suggest": {"field": "theme"}, "required": true}],
 "query": "SELECT ?dataset ?title ?fdp WHERE { GRAPH ?fdp { ?dataset a dcat:Dataset ; dcat:theme ?_theme ; dct:title ?title } } ORDER BY ?title",
 "chart": {"type": "none"}}
```

Parameters are substituted server-side (`POST /index/sparql/examples/{id}/run`
with `{"parameters": {...}}`) by injecting a `VALUES ?_theme { <…> }`
clause, never by string concatenation: IRIs are validated against the IRI
grammar, literals escaped as SPARQL string literals, dates typed as
`xsd:date`. "Open in editor" shows the expanded query, editable.

**Builder tab.** A form-based builder for custom queries without typing
SPARQL:

1. *What are you looking for?* — resource type (Dataset, Catalog,
   Distribution, Data service, FDP, Any), optionally "in FDP …".
2. *Conditions* — rows of *property* (picker listing profile fields first,
   then every predicate seen in the data from `/index/sparql/schema`, with
   labels, counts and the IRI in a tooltip) · *operator* (is, is one of,
   contains text, exists, is missing, before/after/between for dates,
   greater/less for numbers) · *value* (autocomplete from
   `/index/search/suggest` and `search_value` for that predicate; labels
   shown, IRIs stored). Conditions on a related resource (its
   distribution's media type, its catalog's publisher, its FDP's country)
   via one nesting level.
3. *Show* — columns (title by default; any property; "count per …" turns the
   query into an aggregate with `GROUP BY`), sort, limit.
4. *Generated SPARQL* — a live, read-only preview that becomes editable with
   one click ("Edit as SPARQL" copies it to the editor tab; edits there do
   not flow back, and the UI says so).

The builder state is a small JSON model serialised into the URL; the
generator is our own (~300 lines of TypeScript producing a tree-shaped
basic graph pattern with `OPTIONAL`, `FILTER`, `VALUES`, `GRAPH` and
aggregates). It shares the field→predicate map with the server through
`/index/sparql/schema`, so an extension facet added in the profile appears
in the builder without a client release.

**Editor tab.** CodeMirror 6 with: SPARQL highlighting; prefix management
(the schema's prefix list; typing `dcat:` inserts the `PREFIX` line if
missing; a "Prefixes" popover to toggle them); autocomplete (`Ctrl+Space`
and as-you-type) for keywords, prefixes, classes, predicates and IRIs seen
in the data, with labels and counts; inline validation (debounced
`/index/sparql/validate`, error marker at line/column); `Ctrl+Enter` to
run; "Add LIMIT" hint when none; named graphs listed in a side panel
(click inserts `GRAPH <…> { }`); keyboard-accessible with the usual
CodeMirror 6 screen-reader support; theme from the FAIR tokens (light and
dark).

**Results.** One component for all three tabs: a table with sticky header,
column sort, client-side paging over at most 10 000 rows; IRIs rendered as
labelled links (labels batched from `/index/search/labels`; a harvested
resource links to its page `/search/r/{id}` and shows its FDP, an external
IRI opens in a new tab with the full IRI in `title`); literals with language
or datatype badges on hover; row count, duration, "limited to n" notice; a
raw response view (JSON/XML); download as CSV, TSV, JSON, and Turtle for
CONSTRUCT (re-requested from the server with the right `Accept`, so the
file is exactly what the API gives); copy as Markdown table. **Charts**:
when a result has one or two label columns and one numeric column, a
chart switch offers bar, horizontal bar, pie/donut and, for date-like keys,
a line; the table stays the accessible form and the chart carries an
`aria-label` summary. Every example query can declare its default chart.

**History and sharing.** The last 50 executed queries per browser in
`localStorage` (query, time, row count), with pin and clear; a shareable
URL `/sparql?q=<base64url-deflated query>` for anything under ~6 KB; above
that, `POST /index/sparql/share` stores the text (20 KiB cap, rate-limited,
anonymous, 1-year TTL) and returns `/sparql?s=<id>`. Example runs share as
`/sparql?example=datasets-by-theme&theme=<iri>`.

**Bridges.** The search page has "Show as SPARQL" (opens the editor with
`/index/search/sparql`'s output); a resource page has "Query this in
SPARQL" (DESCRIBE template); the builder has "Open in search" when the
query uses only profile fields; the SPARQL page links to the raw endpoint
and shows a `curl` example.

#### 2.2.5 Client libraries: assessment and choice

| Candidate | Facts (verified 2026-11-18) | Verdict |
|---|---|---|
| `@triply/yasgui` | MIT; repository archived 2026-04-20, last code release 2022 | No |
| `@zazuko/yasgui` / `yasqe` / `yasr` 4.6.2 (rdfjs/Yasgui) | MIT; maintained (last release 2026-08-18); CodeMirror **5**, jQuery + DataTables in YASR; no Vue binding; bundled prefix.cc list; a11y and dark mode undocumented | Not as the UI: two CodeMirror generations, jQuery and DataTables in a Vue 3 app, hard to theme with our tokens, and we would still build the library and builder around it. Kept as the *escape hatch*: "Open in YASGUI" link, and our endpoint follows its conventions |
| `@sib-swiss/sparql-editor` 0.2.15 | MIT; active (2026-11-04); light-DOM web component on `@zazuko/yasgui` + Comunica + mermaid; VoID-driven autocomplete, `sh:SPARQLExecutable` examples; Vue use seen in its history but undocumented | Not as a dependency (same reasons plus Comunica/mermaid weight), but its **data conventions are adopted** (VoID partitions, `sh:SPARQLExecutable` examples, prefixes in RDF) so the index works in it unchanged |
| **CodeMirror 6** (`@codemirror/state`, `view`, `language`, `autocomplete`, `lint`) + SPARQL `StreamParser` from `@codemirror/legacy-modes` | MIT; the legacy-modes repo moved from GitHub to the author's forge in 2026 but ships `mode/sparql`; CM6 is accessible (screen-reader mode, full keyboard), themeable with CSS variables, tree-shakeable (~150 kB gzip for the parts used) | **Chosen** for the editor. A Lezer grammar for SPARQL can replace the stream mode later for structural autocomplete; not needed now |
| `sparqljs` | MIT; archived 2026-02-17, deprecated for Traqula | Not used; validation is server-side (rdflib), generation is our own small generator |
| Sparnatural 14 | LGPL-3.0; TypeScript but jQuery, Bootstrap 5, select2, jstree, Leaflet; SHACL-configured; web component `<spar-natural>` | No: licence (LGPL in an MIT project is workable but a friction), heavy UI stack that fights the design tokens, config model built for ontologies rather than our profile. Its *interaction model* (type → property → value, tree-shaped only) is what the builder copies |
| Wikidata Query Builder | BSD-3; a Vue **2.6** application, not a library | No |
| Charts: Chart.js 4 (MIT, canvas, `vue-chartjs`), Apache ECharts (Apache-2, large), Observable Plot (ISC, SVG, needs d3) | | **Chart.js 4 + `vue-chartjs`**, lazy-loaded on the SPARQL page only; the table remains the accessible channel |

Risk fallback: if the CodeMirror 6 editor costs more than one task's worth
of effort, embed `@zazuko/yasqe` alone (editor only) behind the same Vue
component interface; the library, builder and results are independent of
the editor.

### 2.3 Machine-readable results and the index as a FAIR catalog

- `GET /index/search` with `Accept: application/ld+json` (or `format=jsonld`
  / `turtle`) returns the page as a `dcat:Catalog` whose `dcat:dataset` /
  `dcat:catalog` / `dcat:service` members are the hits, each with the
  original IRI as subject, the harvested description triples for that
  resource (from `harvested_node`), `dct:isPartOf` the FDP's catalog, and
  `prov:wasDerivedFrom` the source node. The catalog carries `dct:title`
  "Search results for …", `dct:modified` (newest harvest), and `Link`
  headers `rel="next"` / `rel="prev"` for paging. No vocabulary is invented
  for the result envelope: counts and facets are JSON-only.
- `GET /index/catalog` (Turtle, JSON-LD, N-Triples): a `dcat:Catalog` for
  the index itself, `dcat:catalog` → every root catalog of every accepted
  FDP (the IRIs at the source), `dcat:service` → the FDP services, `dct:publisher`
  → the index operator (from settings), `dcat:themeTaxonomy` ← the union of
  taxonomies seen. This is enough for a DCAT-AP harvester (data.europa.eu,
  a national catalogue, FAIR Discovery) to find everything; the full graph
  per FDP remains at `/index/entries/{uuid}/data`.
- `GET /index/search/opensearch.xml`: an OpenSearch 1.1 description
  (`ShortName`, `Description`, `Url type="text/html"` for the client's search
  page, `Url type="application/json"` for `/index/search`,
  `Url type="application/ld+json"`, `rel="suggestions"` for
  `/index/search/suggest`), with `{searchTerms}`, `{count}`, `{startPage}`,
  `{language}`. Browsers and portals can register the index as a search
  engine; `totalResults`, `startIndex`, `itemsPerPage` are echoed in the JSON
  response under the same names.
- CSW / OGC API Records are not offered: the audience is DCAT, and
  GeoNetwork-style spatial catalogues can harvest `/index/catalog`.

## 3. Index model

### 3.1 Tables (migration `0003_search`)

```sql
-- One row per DCAT resource (the subject whose IRI is the harvested node's IRI).
CREATE TABLE search_resource (
  id               bigserial PRIMARY KEY,
  entry_uuid       uuid NOT NULL REFERENCES index_entry(uuid) ON DELETE CASCADE,
  iri              text NOT NULL,                 -- node IRI, fragment and trailing slash normalised
  public_id        char(22) NOT NULL UNIQUE,      -- urlsafe base64 of sha256(entry_uuid || iri)[:16]; stable across reindexes and replicas
  kind             text NOT NULL,                 -- fdp | catalog | dataset | series | service | distribution | other
  type_iris        jsonb NOT NULL,                -- all rdf:type IRIs
  parent_iri       text,                          -- dct:isPartOf, else the harvest parent
  catalog_iri      text,                          -- nearest catalog ancestor
  dataset_iri      text,                          -- for distributions
  title            text,                          -- display title in the preferred language (see 3.6)
  titles           jsonb NOT NULL DEFAULT '{}',   -- {"en": "...", "nl": "...", "": "untagged"}
  description      text,
  descriptions     jsonb NOT NULL DEFAULT '{}',
  landing_page     text,
  issued           timestamptz,
  modified         timestamptz,                   -- dct:modified, else fdp-o:metadataModified
  temporal_start   date,
  temporal_end     date,
  bbox_min_lon     double precision, bbox_min_lat double precision,
  bbox_max_lon     double precision, bbox_max_lat double precision,
  completeness     real NOT NULL DEFAULT 0,       -- 0..1 quality score (3.8)
  dedup_key        text,                          -- normalised identifier for cross-FDP grouping
  tsv_lang         tsvector,                      -- stemmed per literal language, weighted
  tsv_simple       tsvector,                      -- unaccented 'simple', weighted
  content_hash     char(64) NOT NULL,             -- harvested_node.content_hash this row was built from
  profile_hash     char(64) NOT NULL,             -- the profile version that built it
  harvested_at     timestamptz NOT NULL,
  UNIQUE (entry_uuid, iri)
);
CREATE INDEX ON search_resource USING gin (tsv_lang);
CREATE INDEX ON search_resource USING gin (tsv_simple);
CREATE INDEX ON search_resource USING gin (title gin_trgm_ops);
CREATE INDEX ON search_resource (kind, modified DESC);
CREATE INDEX ON search_resource (entry_uuid, kind);
CREATE INDEX ON search_resource (dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX ON search_resource (temporal_start, temporal_end);

-- Every filterable / facetable value. One table serves named facets and raw predicates.
CREATE TABLE search_value (
  resource_id  bigint NOT NULL REFERENCES search_resource(id) ON DELETE CASCADE,
  field        text   NOT NULL,   -- profile field name ("theme", "keyword") or "p:" + predicate path for raw capture
  value        text   NOT NULL,   -- canonical IRI or literal lexical form
  lang         text,              -- literal language tag, NULL for IRIs
  PRIMARY KEY (resource_id, field, value)
);
CREATE INDEX ON search_value (field, value, resource_id);        -- filters and facet counts
CREATE INDEX ON search_value (field, lower(value) text_pattern_ops); -- prefix autocomplete

-- Human labels for IRIs.
CREATE TABLE search_label (
  iri        text NOT NULL,
  lang       text NOT NULL DEFAULT '',   -- '' = untagged
  label      text,                       -- NULL = cached miss (remote source only)
  source     text NOT NULL,              -- harvested | bundled | remote
  updated_at timestamptz NOT NULL,
  PRIMARY KEY (iri, lang, source)
);

-- Precomputed facet counts for the unfiltered and per-FDP views (3.9).
CREATE TABLE search_facet_cache (
  scope      text NOT NULL,   -- 'all' or an entry uuid
  field      text NOT NULL,
  value      text NOT NULL,
  count      integer NOT NULL,
  PRIMARY KEY (scope, field, value)
);
```

Why two tables instead of arrays + JSONB on one row: facet counting is a
plain `GROUP BY` on a b-tree index, multi-select filters are `EXISTS`
subqueries, extension predicates need no schema change, and the same SQL
runs unchanged on SQLite in unit tests (no `ARRAY`, no `@>`). The cost is
row count (≈10 values per resource) which PostgreSQL handles comfortably at
the target scale (§7).

Why not a true triple table of everything: 1 000 FDPs × 2 000 nodes ×
~20 triples ≈ 40 M rows, most of them literals nobody filters on. The
profile decides what is captured (§3.4); the N-Triples text is still there
for anything else.

### 3.2 Projection

```
harvested_node (entry, node_iri, ntriples, content_hash)
      │  parse with rdflib (already done once at harvest; re-parse is ~1 ms/node)
      ▼
project(node_graph, entry, profile) → ResourceRow + [ValueRow] + [LabelRow]
      │  pure function, no I/O: unit-tested on SQLite with fixture graphs
      ▼
upsert search_resource by (entry_uuid, iri); replace its search_value rows;
upsert search_label (source='harvested')
```

Rules of the projection (module `fdpindex/search/project.py`):

- **Resource subject**: the subject equal to the node IRI (modulo trailing
  slash and fragment), as `rdf.py` does for the repository. Other subjects in
  the node are reachable only through paths (blank nodes for
  `dcat:contactPoint`, `dct:temporal`, `dct:publisher` with inline
  `foaf:name`, `dct:spatial` with `dcat:bbox`), up to depth 2.
- **Kind** from `rdf:type`, first match in this order:
  `fdp` (`fdp-o:FAIRDataPoint`, `fdp-o:MetadataService`, `r3d:Repository`,
  legacy `https://w3id.org/fdp/o#` forms), `distribution` (`dcat:Distribution`),
  `service` (`dcat:DataService`), `series` (`dcat:DatasetSeries`, DCAT 3),
  `dataset` (`dcat:Dataset`, `fdp-o:Metadata` which FDPneo types as a
  dataset), `catalog` (`dcat:Catalog`), else `other`. LDP and PROV types are
  ignored for the decision, as FDPneo's extractor does.
- **Roll-up**: after an entry's nodes are projected, every `dataset` row
  receives `format`, `mediaType`, `license` (and `accessRights` if only
  distributions carry it) values from its distributions
  (`dcat:distribution` children, or distributions whose `dct:isPartOf` is the
  dataset). Catalog rows get the count of their datasets. This is a second
  pass per entry, inside the same transaction.
- **Identity of hits**: `public_id`, so a link to `/search?…` or to
  `/index/search/resources/{public_id}` survives reindexes.

Incremental re-indexing:

- `_harvest_once` currently deletes and re-inserts every node of the entry.
  Change it to an upsert keyed on `(entry_uuid, node_iri)`: rows whose
  `content_hash` is unchanged are left alone, changed rows are updated,
  nodes no longer reached are deleted (still one transaction, readers never
  see a half graph). The harvest result then carries the sets
  *changed*, *new*, *removed*.
- The projection job runs after the harvest for exactly those nodes
  (`search_resource.content_hash` ≠ `harvested_node.content_hash` or
  `profile_hash` ≠ current). Removed nodes cascade from `harvested_node`
  only if we add an FK; instead the job deletes `search_resource` rows whose
  `(entry_uuid, iri)` no longer exists in `harvested_node` for that entry.
- Rejecting or deleting an entry: `delete_harvested` already deletes the
  nodes; `search_resource` rows cascade from `index_entry`, `search_value`
  from `search_resource`; `search_facet_cache` rows for that scope are
  dropped by the same call.
- Profile change (admin `PUT /index/search/profile`): a full re-projection
  job walks `harvested_node` in batches of 500, no network. At 2 M nodes and
  ~1 ms per node this is under an hour on one core and runs in the existing
  `JobRunner`; a `search_reindex` row in the event table makes it resumable
  (CLAUDE.md: the event table is the durable work log).
- `POST /index/search/reindex` (admin, optional `entry`) triggers the same.

SQLite in unit tests: `TSVECTOR().with_variant(Text(), "sqlite")` as in
FDPneo; on SQLite the repository builds `tsv_simple` as plain concatenated
text and the text condition becomes `LIKE` (the same dialect switch
`list_entries` already makes). Projection, filters, facet counting, sorting,
visibility and incremental deletion are unit-tested; ranking,
`websearch_to_tsquery`, trigram and `ts_headline` are integration-tested on
PostgreSQL via testcontainers (`-m integration`).

### 3.3 Default profile: predicates to fields

Prefixes: `dcat: http://www.w3.org/ns/dcat#`, `dct: http://purl.org/dc/terms/`,
`fdp-o: https://w3id.org/fdp/fdp-o#`, `r3d: http://www.re3data.org/schema/3-0#`,
`foaf: http://xmlns.com/foaf/0.1/`, `vcard: http://www.w3.org/2006/vcard/ns#`,
`adms: http://www.w3.org/ns/adms#`, `skos: http://www.w3.org/2004/02/skos/core#`,
`locn: http://www.w3.org/ns/locn#`, `schema: http://schema.org/`,
`time: http://www.w3.org/2006/time#`, `odrl: http://www.w3.org/ns/odrl/2/`,
`prov: http://www.w3.org/ns/prov#`, `spdx: http://spdx.org/rdf/terms#`.

| Field | Kind | Paths (in order of preference) | Facet | Text weight | Notes |
|---|---|---|---|---|---|
| `title` | text, per language | `dct:title`, `rdfs:label`, `skos:prefLabel`, `foaf:name` | — | A | first non-empty per language |
| `description` | text, per language | `dct:description`, `rdfs:comment`, `dct:abstract` | — | C | |
| `keyword` | literal | `dcat:keyword`, `dct:subject` (literal) | yes | B | trimmed, case kept, lowercase for counting |
| `theme` | IRI | `dcat:theme`, `dct:subject` (IRI) in the EU data-theme namespace | yes | label → B | labels via §3.5; a short, stable list |
| `subject` | IRI | `dcat:theme`, `dct:subject` (IRI) outside the EU data-theme namespace | yes | label → B | MeSH, Wikidata, EDAM …; labels via §3.5 and the concept resolver (ADR-0006) |
| `type` | IRI | `rdf:type` (minus LDP/PROV), `dct:type` | yes (`kind` also) | — | |
| `publisher` | IRI or bnode | `dct:publisher` → value = IRI or, for a blank node, the `foaf:name` | yes | name → B | FDPneo and the Java FDP use `foaf:Agent` + `foaf:name` |
| `publisherName` | literal | `dct:publisher/foaf:name`, `dct:publisher/schema:name`, `dct:publisher/vcard:fn` | — | B | also used as the label of the publisher IRI |
| `creator` | IRI/bnode | `dct:creator` (+ `foaf:name`) | later | D | |
| `contactName`, `contactEmail` | literal | `dcat:contactPoint/vcard:fn`, `dcat:contactPoint/vcard:hasEmail`, `dcat:contactPoint/foaf:name`, `dcat:contactPoint/foaf:mbox` | — | D | email shown, not searched |
| `license` | IRI | `dct:license`, `odrl:hasPolicy` (IRI) | yes | — | string licences normalised (§3.8) |
| `rights` | IRI/literal | `dct:rights` | no | D | |
| `accessRights` | IRI | `dct:accessRights` | yes | — | DCAT-AP values `…/access-right/PUBLIC|RESTRICTED|NON_PUBLIC` |
| `language` | IRI | `dct:language` | yes | — | DCAT-AP: `http://publications.europa.eu/resource/authority/language/…`; ISO strings normalised to that |
| `format` | IRI/literal | `dct:format` | yes (rolled up) | — | DCAT-AP: `…/authority/file-type/…`; strings like `CSV` normalised |
| `mediaType` | IRI/literal | `dcat:mediaType` | yes (rolled up) | — | IANA `https://www.iana.org/assignments/media-types/text/csv` or the bare `text/csv` string; normalised to the string form for counting |
| `conformsTo` | IRI | `dct:conformsTo` | yes (extension-friendly) | — | the schema/profile a record claims; very useful across FDPs |
| `identifier` | literal | `dct:identifier`, `adms:identifier/skos:notation`, `fdp-o:metadataIdentifier` | no | D | feeds `dedup_key` |
| `sameAs` | IRI | `owl:sameAs`, `skos:exactMatch` | no | — | feeds `dedup_key` |
| `landingPage` | IRI | `dcat:landingPage`, `foaf:homepage`, `foaf:page` | — | — | single column |
| `accessUrl`, `downloadUrl` | IRI | `dcat:accessURL`, `dcat:downloadURL` | — | — | distributions |
| `endpointUrl` | IRI | `dcat:endpointURL`, `dcat:endpointDescription` | — | — | data services |
| `servesDataset` | IRI | `dcat:servesDataset` | no | — | links service ↔ dataset |
| `issued` | datetime | `dct:issued`, `fdp-o:metadataIssued`, `dct:created` | range | — | first parseable |
| `modified` | datetime | `dct:modified`, `fdp-o:metadataModified` | range | — | FDPs put *metadata* change dates in `fdp-o:*`; `dct:modified` is the data |
| `temporal` | period | `dct:temporal/dcat:startDate` + `/dcat:endDate` (DCAT 2+), `dct:temporal/schema:startDate` + `/schema:endDate` (DCAT 1, DCAT-AP 1.x), `dct:temporal/time:hasBeginning/time:inXSDDateTimeStamp`, or a literal `dct:temporal` "2010/2020" | range | — | gYear, gYearMonth, date, dateTime accepted |
| `spatial` | IRI | `dct:spatial` (IRI) | yes | — | EU `…/authority/country|place|continent/…`, GeoNames |
| `bbox` | geometry | `dct:spatial/dcat:bbox`, `dct:spatial/dcat:centroid`, `dct:spatial/locn:geometry`, literal `dct:spatial` WKT | bbox filter | — | WKT `POINT`/`POLYGON`/`ENVELOPE` parsed to 4 numbers; GeoJSON literals too |
| `version` | literal | `dcat:version` (DCAT 3), `dct:hasVersion` (Java FDP writes the version *string* here), `owl:versionInfo` | no | D | |
| `inSeries`, `hasVersion`, `previousVersion` | IRI | `dcat:inSeries`, `dcat:hasVersion`, `dcat:previousVersion`, `dct:isVersionOf` | no | — | series navigation |
| `accrualPeriodicity` | IRI | `dct:accrualPeriodicity` | later | — | EU `…/authority/frequency/…` |
| `country` (FDP) | literal/IRI | `r3d:country` on the repository | yes | — | re3data; shown as the FDP's country |
| `parent` | IRI | `dct:isPartOf` | — | — | hierarchy |
| `fdp` | entry | the entry the node came from | yes | — | always present |
| `harvestedAt` | datetime | `harvested_node.harvested_at` | range | — | |

`theme` and `subject` read the same two predicates and are split by
vocabulary, not by predicate (profile options `namespaces` and `exclude`).
DCAT-AP 3 and HealthDCAT-AP allow any vocabulary in `dcat:theme`, and some
providers put EU data themes in `dct:subject`; split by predicate, MeSH terms
would still mix with the thirteen EU themes. The search-as-SPARQL translation
drops a filter value outside a field's namespaces, as the index holds none.

DCAT 2 vs 3 and FDP specifics that matter:

- `dcat:DatasetSeries`, `dcat:inSeries`, `dcat:version`, `dcat:hasVersion`,
  `dcat:previousVersion`, `dcat:hasCurrentVersion`, `dcat:resource`,
  `spdx:checksum` are DCAT 3 only; `dcat:startDate`/`dcat:endDate`,
  `dcat:bbox`/`dcat:centroid`, `dcat:accessService`, `dcat:temporalResolution`,
  `dcat:spatialResolutionInMeters` arrived with DCAT 2. The Java FDP's
  default shapes are DCAT 2 with `dct:hasVersion` as a version string;
  FDPneo's are DCAT 3 (`dcat:version`). The profile lists both.
- DCAT-AP 3.0 deprecates `dct:hasVersion` → `dcat:hasVersion`,
  `dct:isVersionOf` → `dcat:isVersionOf`, `owl:versionInfo` → `dcat:version`.
  The projection accepts all and stores the canonical field.
- FDP-O: `fdp-o:MetadataService` / `fdp-o:FAIRDataPoint` (and the legacy
  `https://w3id.org/fdp/o#` namespace, FDPneo ADR-0026) mark the FDP root;
  `fdp-o:metadataCatalog` and `r3d:dataCatalog` link it to catalogs;
  `fdp-o:metadataIssued` / `fdp-o:metadataModified` are the record dates.
  `fdp-o:Metadata` records are datasets for search purposes.
- DCAT-AP mandates IRIs from the EU authority tables for theme, format,
  access rights, language, frequency and country; many FDPs use strings or
  other vocabularies instead. The profile's `normalise` map (§3.8) folds the
  common ones; everything else is shown as-is.

### 3.4 Extension predicates and the profile document

The profile is YAML/JSON, validated by a Pydantic model, bundled as
`fdpindex/search/profile.default.yaml`, overridable through index settings:

```yaml
version: 1
prefixes: { dcat: "http://www.w3.org/ns/dcat#", ejprd: "https://w3id.org/ejp-rd/vocabulary#", … }
fields:
  theme:   { kind: iri, paths: [dcat:theme, dct:subject], namespaces: ["eu-theme:"], facet: main, weight: B,
             help: { en: "The broad data themes of the EU vocabulary, such as Health or Environment." } }
  subject: { kind: iri, paths: [dcat:theme, dct:subject], exclude: ["eu-theme:"], facet: main, weight: B }
  …
extensions:               # administrator additions; same shape as fields
  ejprdResourceType: { kind: iri, paths: [ejprd:resourceType], facet: true, label: { en: "Resource type (EJP RD)" } }
  records:   { kind: number, paths: [healthdcatap:numberOfRecords], facet: true,
               buckets: [0, 100, 1000, 10000, 100000], label: { en: "Number of records" },
               help: { en: "As reported by the data provider; absent for most datasets." } }
  hasVisualisation: { kind: boolean, paths: [ejprd:hasInteractiveVisualisation], facet: true }
cards:                    # "key fields" shown in the footer of a result card, per kind
  dataset: [publisher, format, records]
  catalog: [publisher, datasetCount]
  service: [endpointUrl]
  fdp:     [country, datasetCount]
landing:
  counters: [fdp, catalog, dataset, distribution, service]
capture:                  # raw capture into search_value as "p:<path>"
  iri_objects: true       # every predicate with an IRI object on the resource subject (cheap, enables path filters and stats)
  literal_objects: false  # literals only when listed in fields/extensions
  max_values_per_predicate: 50
normalise:
  license:   { "CC-BY-4.0": "http://publications.europa.eu/resource/authority/licence/CC_BY_4_0", … }
  mediaType: { "https://www.iana.org/assignments/media-types/": "", "http://www.iana.org/assignments/media-types/": "" }  # strip to the bare type
  language:  { "en": "http://publications.europa.eu/resource/authority/language/ENG", … }
types:
  dataset: [dcat:Dataset, fdp-o:Metadata]
  …
```

- `GET /index/search/stats` (admin) reports predicate frequency per FDP from
  the raw capture (`field LIKE 'p:%'`), so an administrator sees which
  extension predicates occur before promoting one to a facet.
- A facet declared in `extensions` is immediately filterable by name; counts
  appear once the re-projection finished (the response says `"indexing":
  {"pending": 1234}` while it runs).
- Literal capture stays off by default: it is the one switch that can blow
  up `search_value` (free text in arbitrary predicates).
- Field kinds: `text`, `iri`, `literal`, `datetime`, `period`, `geometry`,
  **`number`** (facet buckets from `buckets`, stored as the bucket label in
  `search_value` plus the numeric value in a `value_num` column for range
  filters) and **`boolean`** (facet "yes/no", `true` when the predicate is
  present with a true literal or, with `presence: true`, when it is present
  at all). `help` texts appear behind the ⓘ of the facet group and in the
  builder's property picker; `cards` and `landing` drive the client without
  a release.

### 3.5 Labels for IRIs

Three sources, merged by `(iri, lang)` with precedence harvested → bundled →
remote, and language fallback requested → untagged → `en` → any:

1. **Harvested**: while projecting, every subject in the node that has
   `rdfs:label`, `skos:prefLabel`, `dct:title`, `foaf:name`, `schema:name`
   or `vcard:fn` yields `search_label` rows (`source='harvested'`). This
   covers inline publishers, contact points and FDPs that embed theme
   labels.
2. **Bundled**: a small vocabulary pack shipped with the server
   (`fdpindex/search/vocab/*.json`, regenerated by a `fdpindex vocab update`
   developer command): EU authority tables data-theme, file-type,
   access-right, language, frequency, licence, country and continent;
   SPDX licence list; IANA media types (name = IRI tail); DCAT/DCT class and
   property labels; common ontologies seen in FDPs (EJP RD vocabulary,
   NCIT and others can be added by the community). Loaded into
   `search_label` (`source='bundled'`) at start-up when the pack version
   changed.
3. **Remote** (off by default, `FDPINDEX_LABELS_REMOTE_HOSTS`): unresolved
   IRIs on allow-listed hosts are dereferenced through `OutboundClient`
   (content-negotiated RDF, ADR-0002 limits), in the background, misses
   cached with a shorter TTL. Mirrors FDPneo's `external_labels.py`.

`GET /index/search/labels?iri=…&iri=…&lang=nl` serves the client; search
responses already embed labels for facet buckets and hit fields, so the
client rarely needs it.

### 3.6 Multilingual literals and text search configurations

- Store every language of `title` and `description` (`titles`,
  `descriptions` JSONB). `title` / `description` columns hold the display
  pick for the index's default language (settings; `en`); the API re-picks
  per request from the JSONB when `lang` differs.
- `tsv_lang` = Σ over literals of `setweight(to_tsvector(cfg(lang), text), weight)`
  where `cfg` maps the literal's tag to a configuration:
  `en→fdpindex_english`, `nl→fdpindex_dutch`, `de→fdpindex_german`,
  `fr→fdpindex_french`, `es`, `it`, `pt`, `sv`, `da`, `fi`, `no`, `hu`, `ro`,
  `tr`, `ru` → the matching stock stemmer; anything else and untagged →
  `fdpindex_simple`. The `fdpindex_*` configurations are the stock ones
  with `unaccent` prepended (`ALTER TEXT SEARCH CONFIGURATION … ALTER
  MAPPING FOR hword, hword_part, word WITH unaccent, <stem>`), created in
  the migration. `tsvector` concatenation keeps all positions, so one
  column holds all languages.
- `tsv_simple` = the same text through `fdpindex_simple`, so "Übersicht" and
  "ubersicht" match, and a Dutch query still finds a Dutch title indexed as
  untagged.
- Query: `q_lang = websearch_to_tsquery(cfg(lang), q)`,
  `q_simple = websearch_to_tsquery('fdpindex_simple', q)`; condition
  `tsv_lang @@ q_lang OR tsv_simple @@ q_simple`; both are GIN-indexed.
- Keyword and label text go into the vectors with weight B; publisher,
  contact and identifier text with D.

### 3.7 Semantic search with pgvector: designed for, not built

Benefit: recall across languages and synonyms ("heart disease" ↔
"cardiovasculaire aandoeningen"), which stemming never gives. Costs:
`pgvector` must be installed in PostgreSQL (not always available on managed
services); an embedding model must run **inside the deployment** by default
(the index harvests public metadata, but the operator's query stream must
not leave the deployment unnoticed, and the service must work offline):
a multilingual sentence model via ONNX Runtime is ~100–500 MB on disk,
~1 GB RAM, ~10–30 ms per text on CPU, so a full index of 2 M resources is
a day of CPU and a re-projection is no longer "under an hour"; the image
grows accordingly; hybrid ranking (reciprocal rank fusion of BM25-like and
vector ranks) needs tuning with real data.

Decision: reserve the seam, decide after Phase 9 with a real corpus.
Schema reserved as a separate table `search_embedding(resource_id, model,
embedding vector(384))` with an HNSW index, feature flag
`FDPINDEX_SEARCH_EMBEDDINGS={off,local,remote}` (default `off`), and an
`Embedder` protocol with a local ONNX implementation first; a remote API
implementation is a deployment choice, never the default. Task 10.1.

### 3.8 Metadata quality: tolerate, normalise, score

- Missing title → last path segment of the IRI, flagged `"titleFallback": true`.
- Values that should be IRIs but are strings (`dct:license "CC BY 4.0"`,
  `dct:format "csv"`, `dct:language "en"`) go through the profile's
  `normalise` map (case-insensitive, with a handful of regexes for DOIs,
  SPDX ids and IANA media types); unmatched strings are kept as literal
  values and counted as their own facet bucket, labelled as typed.
- IRIs are canonicalised for counting: scheme `http`/`https` folded, trailing
  slash removed, fragment kept.
- `completeness` = share of {title, description, keyword or theme, publisher,
  licence, access rights, a distribution or an endpoint, issued or modified,
  contact} present. Shown as a quiet hint on the card ("sparse metadata"),
  used as a tie-breaker, and aggregated per FDP in the admin stats, which is
  the "accept-and-flag" posture of FDPneo ADR-0021 in a cheaper form.

### 3.9 Facet counts

- Filtered facet counts: for each requested facet, one query over the
  filtered resource set joined to `search_value` for that field, `GROUP BY
  value ORDER BY count DESC LIMIT facetSize`; the facet's own filter is
  excluded (disjunctive counts, so a user sees what adding a second theme
  would give). Per request at most 8 facets × 50 buckets.
- Unfiltered and per-FDP counts come from `search_facet_cache`, refreshed
  at the end of every projection job for the affected entry and for `all`
  (the `all` refresh is a single `GROUP BY` over `search_value`, run at most
  once per minute).
- All search SQL runs with `SET LOCAL statement_timeout = '5s'` (anonymous)
  / `'30s'` (admin); a timeout returns 503 with `Retry-After`.

## 4. Ranking

`score = text × boost(kind) × liveness × recency × language × quality`, all
computed in SQL and returned as `score` (0–1 normalised per page, the raw
value in `debug=true` for admins).

- `text` = `ts_rank_cd('{0.1, 0.2, 0.4, 1.0}', tsv_lang, q_lang, 1|32)`
  combined as `greatest(rank_lang, 0.9 × rank_simple)`; normalisation 1
  (log of document length) stops long descriptions from dominating, 32
  maps into 0–1. Plus an exact-title bonus: `+0.3` when
  `lower(unaccent(title)) = lower(unaccent(q))`, `+0.15` when
  `similarity(title, q) > 0.6` (pg_trgm).
- `boost(kind)`: dataset 1.0, series 1.0, catalog 0.9, service 0.9, fdp 0.8,
  distribution 0.7, other 0.6. With `type` set, the boost is 1.
- `liveness`: FDP `ACTIVE` 1.0, `INACTIVE` 0.9, `UNKNOWN` 0.9,
  `UNREACHABLE` 0.6, `INVALID` 0.6. The hit carries the state; the UI shows
  the badge. `fdpState=ACTIVE` filters instead. This keeps content
  discoverable through an outage without putting a dead link first.
- `recency`: `1 + 0.1 × exp(−age_days/365)` on `modified` (falls back to
  `harvested_at`), capped: a two-year-old dataset is not punished, a fresh
  one gets a nudge.
- `language`: `×1.05` when a title exists in the requested language.
- `quality`: `0.9 + 0.1 × completeness`.
- Without `q`: order by `modified DESC NULLS LAST, title`; `sort` offers
  `relevance` (default with `q`), `modified`, `issued`, `title`,
  `harvested`, each `,asc|,desc`.
- Ties: `completeness DESC`, then `id` so paging is stable.

## 5. API

All new; the reference `/index/*` contract is untouched. Anonymous callers
see resources of accepted entries only; administrators may pass
`permit=all` to search pending entries' metadata too — but pending entries
are not harvested today, so this is a no-op until TASKS 4.6 / a later
decision; the parameter is reserved, not implemented.

| Method, path | Who | Purpose |
|---|---|---|
| `GET /index/search` | anyone | search; parameters below; JSON, or JSON-LD/Turtle via `Accept`/`format` |
| `POST /index/search` | anyone | same, JSON body (§2.1); the only way to use `path` filters |
| `GET /index/search/suggest?q=&lang=&field=` | anyone | autocomplete: `{"keywords":[…],"titles":[…],"publishers":[…],"themes":[{"iri","label"}]}`; `q` ≥ 2 characters; 10 per group |
| `GET /index/search/labels?iri=…&lang=` | anyone | labels for up to 100 IRIs |
| `GET /index/search/resources/{publicId}` | anyone | one hit in full (all languages, all values, distributions, parent chain, FDP); `Accept: text/turtle` / `application/ld+json` returns the harvested node's triples |
| `GET /index/search/facets?field=theme&q=…&…` | anyone | one facet's full bucket list (paged) for the "more…" dialog |
| `GET /index/search/opensearch.xml` | anyone | OpenSearch description |
| `GET /index/catalog` | anyone | the index as a DCAT catalog (§2.3) |
| `GET` / `PUT` / `DELETE /index/search/profile` | admin | the search profile; `DELETE` resets to the bundled default; `PUT` validates and starts a re-projection |
| `POST /index/search/reindex` (`{"entry": uuid?}`) | admin | re-project one entry or all |
| `GET /index/search/stats` | admin | rows, lag (nodes not yet projected), predicate frequencies per FDP, completeness per FDP |
| `GET` / `POST /index/sparql` | anyone (on by default) | SPARQL 1.1 Protocol, query only, bounded (§2.2.2–2.2.3); service description without `query` |
| `POST /index/sparql/validate` | anyone | parse + gate, line/column errors |
| `GET /index/sparql/schema` | anyone | classes, predicates, counts, labels, prefixes, named graphs (from VoID + labels) |
| `GET /index/sparql/examples`, `POST /index/sparql/examples/{id}/run` | anyone | query library; parameterised run with safe substitution |
| `PUT` / `DELETE /index/sparql/examples` | admin | override / reset the library |
| `POST /index/sparql/share`, `GET /index/sparql/share/{id}` | anyone | long shared queries (20 KiB, TTL) |
| `GET /index/search/sparql?…` | anyone | a faceted search translated to SPARQL |
| `GET /index/search/landing` | anyone | counters per kind and per FDP state for the landing page (cached) |

`GET /index/search` parameters (all optional):

| Parameter | Meaning |
|---|---|
| `q` | websearch syntax: words, `"phrase"`, `or`, `-word`; field prefixes `title:`, `keyword:`, `publisher:`, `theme:`, `identifier:` (Phase 9); max 512 characters |
| `type` | repeatable: `dataset`, `series`, `catalog`, `service`, `fdp`, `distribution`, `other`; default all but `distribution` |
| `fdp` | repeatable entry UUID; "search within one FDP" |
| `theme`, `keyword`, `publisher`, `license`, `format`, `mediaType`, `accessRights`, `language`, `spatial`, `conformsTo`, `country` | repeatable; OR within, AND across; `!value` negates; values are canonical IRIs or literal strings as the facet bucket returned them |
| `f.<extensionField>` | repeatable; facets declared in the profile's `extensions` |
| `issuedFrom`, `issuedTo`, `modifiedFrom`, `modifiedTo`, `harvestedFrom`, `harvestedTo` | ISO dates or date-times |
| `temporalFrom`, `temporalTo` | overlap with the resource's temporal coverage; years, months or dates |
| `bbox` | `minLon,minLat,maxLon,maxLat`, intersects |
| `fdpState` | `ACTIVE` to hide resources of unreachable/invalid/inactive FDPs; default: all, demoted |
| `lang` | BCP-47 tag for display and stemming; default from `Accept-Language`, then the index default |
| `sort` | `relevance` (default with `q`), `modified`, `issued`, `title`, `harvested`; suffix `,asc` / `,desc` |
| `page`, `size` | offset paging, `size` ≤ 100, `page × size` ≤ 10 000 |
| `cursor` | keyset paging for `sort=modified|issued|title|harvested` (exhaustive iteration for harvesters); opaque, encodes the sort key + id |
| `facets` | which facets to count: default `type,theme,publisher,format,accessRights,fdp`; `none` to skip (cheaper); ≤ 8 |
| `facetSize` | buckets per facet, ≤ 50, default 20 |
| `highlight` | `true` to include `snippet` (ts_headline, `<em>` markers; the client escapes everything else) |
| `collapse` | `true` to collapse cross-FDP duplicates into one hit with `duplicates` (Phase 8, task 8.8) |
| `format` | `json` (default), `jsonld`, `turtle` |

Response (JSON):

```json
{
  "query": {"q": "registry", "types": ["dataset","series","catalog","service","fdp"], "filters": [...], "lang": "en", "sort": "relevance"},
  "totalResults": 1234, "startIndex": 0, "itemsPerPage": 20, "page": 0, "nextCursor": null,
  "took": 41,
  "indexing": {"pending": 0},
  "hits": [
    {
      "id": "Zk3…", "iri": "https://fdp.example.org/dataset/abc", "type": "dataset", "typeIris": ["http://www.w3.org/ns/dcat#Dataset"],
      "title": "Dutch Cardiac Registry", "titleLang": "en", "titleFallback": false,
      "description": "…", "snippet": "… the <em>registry</em> collects …",
      "score": 0.91, "completeness": 0.78,
      "fdp": {"uuid": "7d1c…", "clientUrl": "https://fdp.example.org", "title": "Example FDP", "state": "ACTIVE"},
      "catalog": {"iri": "https://fdp.example.org/catalog/1", "title": "Clinical catalog"},
      "publisher": [{"iri": "https://ror.org/…", "label": "Example University"}],
      "themes": [{"iri": "http://publications.europa.eu/resource/authority/data-theme/HEAL", "label": "Health"}],
      "keywords": ["cardiology", "registry"],
      "license": {"iri": "…/licence/CC_BY_4_0", "label": "CC BY 4.0"},
      "accessRights": {"iri": "…/access-right/RESTRICTED", "label": "Restricted"},
      "formats": [{"value": "text/csv", "label": "CSV"}],
      "languages": [{"iri": "…/language/NLD", "label": "Dutch"}],
      "issued": "2023-04-01T00:00:00Z", "modified": "2025-10-12T09:30:00Z",
      "temporal": {"start": "2010-01-01", "end": "2020-12-31"},
      "spatial": [{"iri": "http://publications.europa.eu/resource/authority/country/NLD", "label": "Netherlands"}],
      "landingPage": "https://…", "sourceUrl": "https://fdp.example.org/dataset/abc",
      "distributions": [{"iri": "…", "title": "CSV export", "format": "text/csv", "accessUrl": "…", "downloadUrl": null}],
      "duplicates": []
    }
  ],
  "facets": {
    "theme": {"buckets": [{"value": "…/data-theme/HEAL", "label": "Health", "count": 412}], "other": 23},
    "fdp": {"buckets": [{"value": "7d1c…", "label": "Example FDP", "count": 120, "state": "ACTIVE"}]}
  }
}
```

Error envelope and `errors` map as the rest of the API. The OpenAPI snapshot
and contract tests (TASKS 1.13) are extended; `npm run generate-api` gives
the client its types.

Compatibility notes: `/index/entries?q=` keeps its ILIKE semantics. The
client's directory page may *additionally* call `/index/search?type=fdp`
for a better FDP search, but the reference client is unaffected.

## 6. Client

Route `/search` (`SearchView.vue`), a "Search" item in the navigation next
to the directory; the entry page gets "Search this FDP's metadata"
(→ `/search?fdp=<uuid>`). The directory page stays the list of FDPs.

Layout (desktop ≥ 960 px): a search header spanning the page; below it, a
facet sidebar (280 px) and the results column.

- **Search box**: one input, submit on Enter, debounced suggestions (250 ms,
  as the directory does) in an ARIA `combobox`/`listbox` grouped by
  keywords / titles / publishers / themes; a hint line with the syntax
  (`"phrase"`, `or`, `-word`); type tabs under the box: All · Datasets ·
  Catalogs · Services · FAIR Data Points (counts from the `type` facet).
- **Landing strip** above the search box (only when no query is active):
  counters per kind (FDPs · catalogs · datasets · distributions · services,
  from `/index/search/landing`) and a one-paragraph explanation of the
  hierarchy ("An FDP publishes catalogs; a catalog groups datasets; a
  dataset is available as distributions …"), as the SPHN FDP does (§8).
- **Facet sidebar**: groups in the order theme, publisher, format, access
  rights, language, spatial, FDP, country, then extension facets; each
  group has an ⓘ button with the profile's `help` text (the `StatusHelp`
  pattern), shows top buckets as checkboxes with counts and labels (IRI tail
  as the label fallback, full IRI in `title`), a "Show more" opening a
  searchable dialog over `/index/search/facets`, a "Clear" per group and
  "Clear all" at the top; numeric facets show their buckets, boolean facets
  a yes/no pair; rarely used groups sit behind "More facets". Date ranges
  (issued, modified) and temporal coverage are two-field inputs; "Only
  reachable FDPs" is a toggle (`fdpState=ACTIVE`). Active filters repeat as
  removable chips above the results.
- **Result cards** (`ResourceCard.vue`), one design with per-type accents:
  type badge; title (link to `/search/r/{publicId}`, a detail page); FDP
  chip with the FDP's title and `StateBadge` (link to `/entries/{uuid}`);
  publisher; snippet or the first 240 characters of the description; theme
  chips; keyword chips (max 6); for datasets a distribution strip
  (format labels, "n distributions"); issued/modified as relative time with
  absolute `title` (existing `format.ts`); a **key-fields footer** with the
  two or three values the profile's `cards` section names for that kind
  (label: value · label: value); "Open at source" external link; a quiet
  "sparse metadata" hint under 0.4 completeness; a muted "FDP unreachable
  since …" line when the state is not active.
- **Detail page** `/search/r/{publicId}`: every field in the requested
  language with the others behind a language switch, all values with labels
  and IRIs (copyable, `CopyField.vue`), the parent chain (FDP › catalog ›
  dataset), distributions and services, raw triples (Turtle, collapsed),
  **download buttons** (Turtle, JSON-LD, N-Triples of the harvested node),
  "Query this in SPARQL", and "find similar" shortcuts (same publisher,
  same theme).
- **SPARQL page** `/sparql` (§2.2.4): library · builder · editor tabs over a
  shared results component; a "SPARQL" item in the navigation; the search
  page's "Show as SPARQL" lands here.
- **URL state**: every parameter of `GET /index/search` is a query parameter
  of the route (`?q=&type=&theme=&…&page=`), read and written through the
  same `param()`/`update()` pattern as `DirectoryView.vue`; back/forward and
  reload work; "Copy link" copies it. TanStack Query key
  `["search", params, session.isAdmin]`, `keepPreviousData` so facets do not
  flicker.
- **Mobile** (< 960 px): the sidebar becomes a bottom sheet opened by a
  "Filters (3)" button; type tabs scroll horizontally; cards stack; sticky
  search box.
- **States**: loading skeletons, "no results" with the active filters and a
  "clear filters" action (and "did you mean" later), 503 "the search is
  busy, try again" with retry, empty index ("nothing harvested yet").
- **Accessibility**: facets are real checkboxes in `fieldset`s with
  `legend`s, live region announces "1 234 results", focus returns to the
  results heading after a filter change, all colours from the FAIR tokens.
- i18n: all strings in `en.ts`; labels from the server already carry the
  user's language where available.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scale: 1 000 FDPs × 2 000 nodes = 2 M resources, ~20 M `search_value` rows, two GIN indexes on 2 M vectors (several GB in total) | Fits a single PostgreSQL node; GIN build needs `maintenance_work_mem` ≥ 256 MB (documented); facet counts for unfiltered views come from the cache; every query has a `statement_timeout`; `EXPLAIN` checks on a synthetic 2 M-row corpus are a Phase 6 task, not an afterthought |
| Facet counting on broad text queries (hundreds of thousands of matches) | `LIMIT` per facet, `facets=none` for machine clients, 8-facet cap, short-TTL in-process cache of `(normalised query → facets)` for anonymous requests, counts are "approximate" (documented) above 100 000 matches by sampling `TABLESAMPLE SYSTEM (10)` and scaling |
| Abuse by anonymous users | per-IP token bucket (60 requests/min search, 300/min suggest, 30/min SPARQL; in memory, per process), parameter caps (`q` 512 chars, 20 filter values per facet, page depth 10 000), `statement_timeout`, SPARQL process-isolated with kill-on-timeout, no `ts_headline` on more than 20 hits per request |
| SPARQL: a pathological query (cross product, `+` paths over the union graph) pins a worker | wall-clock kill and respawn, RSS watchdog, queue depth with `503`, auto-`LIMIT`, workers never exceed `FDPINDEX_SPARQL_WORKERS`; the PostgreSQL side is untouched because the store is separate |
| SPARQL store drift or corruption (crash during a write, volume lost) | the store is derived: fingerprints per entry resync at start-up, `fdpindex sparql rebuild` recreates it from PostgreSQL in minutes; the service runs without SPARQL (feature flag in `/index/config`) if the directory is unusable |
| Disk for the SPARQL store | ~40 M triples ≈ a few GB in RocksDB; documented, and `FDPINDEX_SPARQL_DIR` can be a separate volume |
| pyoxigraph API changes (0.x) | the store access is one module (`search/sparql_store.py`) with the three operations used (replace graph, query, open read-only); pinned minor version; integration test on the real wheel |
| Reindex storms (many pings in a burst, profile changes) | projection is per changed node only; profile re-projection is one resumable job with batches; harvest already has the per-entry lock and the worker pool bound |
| Metadata quality variance (strings for IRIs, missing titles, wrong types, catalogs typed as datasets) | normalisation map, fallbacks and flags (§3.8); per-FDP completeness in admin stats; kind precedence makes a node typed both `dcat:Catalog` and `dcat:Dataset` a catalog |
| Language detection wrong for untagged literals | untagged text goes to `simple` only (no stemming damage); the user's language configuration is applied only to the query side plus tagged literals |
| `ts_headline` output is not XSS-safe by PostgreSQL's own warning | the server returns only the markers `<em>`/`</em>` and escapes everything else before sending; the client renders the snippet from a tokenised form, never as raw HTML |
| Extension availability (`unaccent`, `pg_trgm`) on managed PostgreSQL | both are *trusted* extensions since PostgreSQL 13, so `CREATE EXTENSION IF NOT EXISTS` works for the database owner; the migration fails loudly otherwise, and the operator documentation says so |
| SQLite/PostgreSQL divergence in tests | all SQL through SQLAlchemy expressions; the dialect switch lives in one repository module with explicit "PostgreSQL only" marks; integration tests cover ranking and FTS |
| Stale search after a harvest that found nothing | the harvest keeps the previous nodes (service docstring), so the projection keeps the previous rows; `indexing.pending` and `harvestedAt` make staleness visible |
| Duplicate content across mirrors inflating counts | `dedup_key` grouping and `collapse=true`; counts always say "resources", not "datasets" |

## 8. Lessons from the SPHN FAIR Data Point

The SPHN Metadata Catalog (SIB Swiss Institute of Bioinformatics,
https://fdp.dcc.sib.swiss) is a customised deployment of the reference
FAIR Data Point, not a new product: the deployment repository
(https://gitlab.com/sphn/sphn-fair-data-point, GPL-3.0-or-later, created
June 2026) composes a modified `FAIRDataPoint` server
(https://git.dcc.sib.swiss/sphn-semantic-framework/FAIRDataPoint, MIT, branch
`sphn-dev`), a modified `FAIRDataPoint-client`
(https://git.dcc.sib.swiss/sphn-semantic-framework/FAIRDataPoint-client,
MIT, "modified for SPHN-specific use-cases"), MongoDB, **GraphDB as the
internal store** and **Apache Jena Fuseki as the public read-only SPARQL
endpoint** (`/store/fdp/sparql`), behind traefik; `fdp/application.yml`
carries no facet configuration, so the facets live in the forks and the
FDP's own search-filter settings (reference 1.15: "Extended search
(filters, simple and complex query)", "Add saved search queries"; 1.16:
"Add autocomplete widget"). Metadata follows HealthDCAT-AP plus the SPHN
Metadata Catalog Schema (https://sphn.gitlab.io/sphn-metacat-schema/,
CC BY 4.0; `sphn-metacat:` = `https://biomedit.ch/rdf/sphn-metacat/sphn/`).
Documentation: https://sphn.gitlab.io/sphn-fair-data-point/ (FAQ and
"Querying with SPARQL"), the framework page
https://sphn-semantic-framework.readthedocs.io/en/latest/sphn_framework/sphn_metadata_catalog.html,
the feature release post
https://sphn.ch/2026/09/02/release-of-new-sphn-metadata-catalog-features/
(a spring 2026 survey: "more than 80% … high satisfaction or satisfaction",
"77% … likely to use"; users "particularly appreciated detailed metadata and
concept- and terminology counts"), and the paper Witte et al., *The Swiss
Personalized Health Network Metadata Catalog*, JMIR Med Inform 2026;14:e90146,
https://doi.org/10.2196/90146 (the article body and the October 2026
preprint https://www.preprints.org/manuscript/202610.0874 were not
retrievable at the time of writing, so their usability findings are not
reflected here). Observations of the live UI come from the coordinator's
screenshots.

Worth adopting (and where it lands in this design):

| SPHN feature | Adopt | Where |
|---|---|---|
| Landing page: counters per DCAT level with icons and a one-paragraph plain-language explanation of Catalog → Dataset → Distribution | Yes; plus FDPs and services as levels | §6 landing strip, `GET /index/search/landing`, profile `landing` |
| Faceted "Explore" on the landing page itself: Clear All, collapsible groups, each with an ⓘ tooltip explaining the facet | Yes; ⓘ texts from the profile, same pattern as the client's status help | §3.4 `help`, §6 sidebar |
| Numeric facets bucketed into ranges (Number of Unique Individuals 101–1 000, 1 001–10 000 …) and boolean facets (Interactive Visualization yes/no) | Yes, as profile field kinds `number` (with `buckets`) and `boolean` | §3.4 |
| "Show 10 more" per group and "Show more facets" for rarely used groups | Yes | §6 |
| Result cards: title, full abstract, and a footer with two domain "key fields" (Number of Patients · Data Type) | Yes, configurable per kind in the profile (`cards`); abstract truncated with "more" rather than full, because our corpus is heterogeneous | §3.4, §6 |
| Resource page as a label/value table with IRIs rendered as labelled links; ttl and json-ld download buttons; children as cards with the same key-field footer | Yes | §6 detail page, `search_label` |
| Public SPARQL endpoint on a separate read-only engine, documented with a page of worked example queries (ten, from "List all Catalogs" to terminology hierarchies with `rdfs:subClassOf+`) | Yes: the example-query idea becomes the parameterised library; the separate-engine idea becomes the embedded derived store | §2.2 |
| Metadata counts as first-class content (VoID-style concept availability, code frequencies) and users' appreciation of them | Partly: the index does not compute data-level counts, but it exposes VoID partitions for the *metadata* and counts per FDP/catalog, and numeric facets surface counts FDPs publish (`healthdcatap:numberOfRecords`, `numberOfUniqueIndividuals`) | §2.2.1, §3.4 |
| Small-count suppression ("0 means fewer than 10") | Noted as the pattern for any future data-level counts | — |

Not adopting:

- The SPARQL page is plain YASGUI (tabs, endpoint field, prefilled prefixes
  and one example, share/run, Table/Response views, download, help): no
  library, no builder, no parameter forms. That is the floor, not the
  target (§2.2.4).
- A second triple store and MongoDB in the deployment; the index keeps one
  database and an embedded derived store.
- SPHN-specific fields (`sphn-metacat:hasDataProvider`, `hasConsentType`,
  `hasDataType`, `dcatap:applicableLegislation`, `healthdcatap:*`) are not
  built in; they are exactly what the profile's `extensions` are for, and a
  HealthDCAT-AP extension profile can be bundled as an example.
- Alphabetical catalog listing as the home page: at 1 000 FDPs the home
  page needs counters and search, not a list.

## 9. Implementation plan

Numbering continues `TASKS.md`. Each task is small enough for one PR with
its tests. "Done" as defined in `TASKS.md`. Phase 7 (SPARQL) can start as
soon as 6.5 (incremental harvest storage) has landed; it does not wait for
the rest of Phase 6.

### Phase 6 — Search over harvested metadata: foundation

- [ ] 6.1 ADR-0005: search as a projection in PostgreSQL (amends ADR-0002), the profile, SPARQL over an embedded derived store with process isolation (amends ADR-0001's "no SPARQL in this service"), the compatibility rule for new paths
- [ ] 6.2 Migration `0003_search`: `search_resource`, `search_value`, `search_label`, `search_facet_cache`, `unaccent` + `pg_trgm`, the `fdpindex_*` text search configurations; SQLite variants for unit tests
- [ ] 6.3 Profile model (Pydantic) and bundled default `profile.default.yaml` covering §3.3, with `help`, `cards`, `landing` and the `number`/`boolean` kinds; `profile_hash`; unit tests for CURIE expansion and validation
- [ ] 6.4 Projection (`search/project.py`): kind detection, fields, paths up to depth 2, multilingual titles/descriptions, dates, identifiers, `completeness`, `public_id`; pure function with fixture graphs from a Java FDP and from FDPneo (DCAT 2 and DCAT 3)
- [ ] 6.5 Incremental harvest storage: upsert `harvested_node` by `(entry_uuid, node_iri)` with change sets; projection job for changed/new/removed nodes; deletion on reject/delete; `search_reindex` event for resumable full re-projection
- [ ] 6.6 Text search write path: weighted `tsv_lang` / `tsv_simple` per §3.6, language → configuration map; integration tests for stemming and unaccent
- [ ] 6.7 Roll-up pass: distribution formats, media types, licences onto datasets; dataset counts onto catalogs
- [ ] 6.8 Labels: harvested labels during projection; bundled vocabulary pack (EU authority tables, SPDX, IANA, DCAT terms) and `fdpindex vocab update`; `GET /index/search/labels`
- [ ] 6.9 Search repository: filters (named fields, dates, fdp, fdpState), text condition, ranking per §4, sorting, offset paging, facet counts with the facet cache, `statement_timeout`; unit tests on SQLite, ranking and FTS tests on PostgreSQL
- [ ] 6.10 `GET` / `POST /index/search` (JSON), `GET /index/search/resources/{id}` (JSON, Turtle, JSON-LD, N-Triples), `GET /index/search/facets`, `GET /index/search/landing`; parameter validation and caps; anonymous visibility; OpenAPI snapshot and contract tests
- [ ] 6.11 Rate limit for anonymous search and suggest (in-memory token bucket, trusted proxy IP as for pings)
- [ ] 6.12 Admin: `GET`/`PUT`/`DELETE /index/search/profile`, `POST /index/search/reindex`, `GET /index/search/stats` (rows, lag, completeness per FDP)
- [ ] 6.13 Client: `/search` route, `SearchView.vue`, `SearchBox.vue`, `FacetGroup.vue` (with ⓘ help, Clear, Show more, numeric and boolean groups), `ResourceCard.vue` (key-fields footer), landing strip with counters and the hierarchy explainer, URL state, type tabs, mobile bottom sheet, states; `useSearch()` composable; unit tests; "Search this FDP's metadata" on the entry page
- [ ] 6.14 Client: resource detail page `/search/r/{id}` as a label/value table with language switch, labelled IRIs, parent chain, distributions, raw triples, Turtle / JSON-LD / N-Triples download buttons
- [ ] 6.15 Performance check on a synthetic corpus (1 000 entries × 2 000 nodes, generated from the fixture graphs): `EXPLAIN ANALYZE` of the main query shapes, documented numbers and the `maintenance_work_mem` advice in the operator documentation
- [ ] 6.16 Operator documentation: extensions, profile, reindex, limits; README API table extended

### Phase 7 — SPARQL API and UI

- [ ] 7.1 `search/sparql_store.py`: pyoxigraph store on `FDPINDEX_SPARQL_DIR`, named graph per entry, replace-graph from `harvested_node`, read-only open for workers (confirm `Store.read_only` / `Store.secondary` semantics on the pinned version), `search_sparql_sync` fingerprints, resync at start-up, `fdpindex sparql rebuild`; integration test on the real wheel
- [ ] 7.2 Sync from the projection job: fingerprint compare after each harvest, drop on reject/delete, the `<urn:fdpindex:entries>` service graph, the `<urn:fdpindex:void>` partitions regenerated per sync
- [ ] 7.3 Query workers: subprocess pool, one query at a time, wall-clock kill and respawn, RSS watchdog, queue depth and `503`; structural gate (rdflib parse; SELECT/ASK/CONSTRUCT/DESCRIBE only; no `SERVICE`/`LOAD`); auto-`LIMIT`; row, byte and triple caps; unit tests with a slow-query stub, integration test with a real cross product
- [ ] 7.4 `GET`/`POST /index/sparql` (SPARQL 1.1 Protocol, content negotiation for results and graphs, service description, CORS), `POST /index/sparql/validate`, per-IP rate limit, logging; `/index/config` advertises `sparql: {enabled, limits}`; OpenAPI and contract tests
- [ ] 7.5 `GET /index/sparql/schema`: classes, predicates, counts, labels, prefixes, named graphs from VoID + `search_label`, cached per sync
- [ ] 7.6 Query library: bundled `examples.default.yaml` (the fifteen queries of §2.2.4), typed parameters, safe substitution (`VALUES`), `GET /index/sparql/examples`, `POST …/{id}/run`, admin `PUT`/`DELETE`; also published as `sh:SPARQLExecutable` in `<urn:fdpindex:examples>`
- [ ] 7.7 `GET /index/search/sparql`: faceted-search parameters → SPARQL (named fields, dates, FDP, approximate full text), with `approximation` notes; tests per field kind
- [ ] 7.8 Client: `/sparql` route and `SparqlView.vue` with the shared `SparqlResults.vue` (table, labelled IRI links to `/search/r/{id}`, literal badges, sort, paging, raw view, CSV/TSV/JSON/Turtle download, copy as Markdown); labels batched from `/index/search/labels`
- [ ] 7.9 Client: query library tab, parameter forms per type (IRI autocomplete with labels, FDP select, date, number, text), "Show query", "Open in editor", example run URLs
- [ ] 7.10 Client: editor tab on CodeMirror 6 (`SparqlEditor.vue`): SPARQL highlighting, prefix insertion and popover, autocomplete from `/index/sparql/schema` and `/index/search/suggest`, inline validation via `/index/sparql/validate`, `Ctrl+Enter`, named-graph panel, light/dark from tokens, keyboard and screen-reader check
- [ ] 7.11 Client: builder tab (`QueryBuilder.vue` and friends): type → conditions (property picker with labels/counts, operators, value autocomplete, one nesting level) → columns/aggregates → live SPARQL preview; TypeScript generator with unit tests against golden queries; builder state in the URL; "Edit as SPARQL", "Open in search"
- [ ] 7.12 Charts in results (Chart.js 4 + `vue-chartjs`, lazy-loaded): bar, horizontal bar, pie/donut, line; chart defaults per example query; `aria-label` summaries
- [ ] 7.13 History (localStorage, pin/clear) and sharing: URL-embedded queries, `POST /index/sparql/share` for long ones, `/sparql?example=…&param=…` deep links; "Show as SPARQL" on the search page, "Query this in SPARQL" on resource pages, "Open in YASGUI" escape hatch
- [ ] 7.14 Documentation: SPARQL page in the operator docs (store directory, workers, limits, rebuild), a user guide with the example queries, `curl` samples; README API table

### Phase 8 — Richer discovery

- [ ] 8.1 Snippets with `ts_headline` (`highlight=true`), server-side escaping, client rendering from tokens
- [ ] 8.2 Autocomplete `GET /index/search/suggest` (prefix on `search_value`, trigram on titles) and the combobox in the client
- [ ] 8.3 Temporal coverage: `dct:temporal` with `dcat:`, `schema:` and `time:` forms, literal ranges; `temporalFrom`/`temporalTo`; range inputs in the client
- [ ] 8.4 Spatial facet from `dct:spatial` IRIs with labels (EU country/place/continent, GeoNames via the bundled pack)
- [ ] 8.5 Numeric-range and boolean facet kinds end to end (`value_num`, buckets, yes/no groups), with a bundled HealthDCAT-AP extension profile as the worked example
- [ ] 8.6 Remote label resolution through `OutboundClient` with a host allow-list, off by default, background with cached misses
- [ ] 8.7 Multilingual display: `lang` / `Accept-Language` pick for titles, descriptions and labels; language switch in the client; index default language in settings
- [ ] 8.8 Cross-FDP duplicates: `dedup_key` (DOI, identifiers, `owl:sameAs`, landing page), `duplicates` on hits, `collapse=true`, "also in n other FDPs" on cards
- [ ] 8.9 Client polish: active-filter chips, "show more" facet dialog, "more facets", copy link, empty and busy states, keyboard and screen-reader pass

### Phase 9 — Expert and machine access

- [ ] 9.1 Field prefixes in `q` (`title:`, `keyword:`, `publisher:`, `theme:`, `identifier:`) parsed before `websearch_to_tsquery`
- [ ] 9.2 Raw predicate capture (`capture.iri_objects`) into `search_value` as `p:<path>`, `path` filters with typed operators on `POST /index/search`, predicate frequencies in `/index/search/stats`
- [ ] 9.3 Extension facets from the profile (`extensions`), `f.<field>` parameters, re-projection on `PUT`, facet labels in the client and in the SPARQL builder's picker
- [ ] 9.4 Keyset `cursor` paging for non-relevance sorts
- [ ] 9.5 JSON-LD / Turtle search results as a `dcat:Catalog` of hits with `Link` paging; `format=` and `Accept` negotiation
- [ ] 9.6 `GET /index/catalog`: the index as a DCAT catalog of every accepted FDP's catalogs and services; validated with the DCAT-AP SHACL shapes in a test
- [ ] 9.7 `GET /index/search/opensearch.xml`; `totalResults`/`startIndex`/`itemsPerPage` in responses; `<link rel="search">` in the client's `index.html`
- [ ] 9.8 Bounding-box filter: WKT/GeoJSON parsing into the four columns, `bbox=` parameter, a simple map-free "within these coordinates" input (a map widget only if a deployment asks)
- [ ] 9.9 Approximate facet counts above a match threshold; facet response cache for anonymous requests

### Phase 10 — Optional engines and extras (each behind a flag)

- [ ] 10.1 Semantic search evaluation: `search_embedding` table, `Embedder` protocol with a local ONNX model, hybrid ranking by reciprocal rank fusion; go/no-go documented with measurements on the real corpus
- [ ] 10.2 "Did you mean" from trigram similarity on zero-hit queries
- [ ] 10.3 Saved queries for administrators and featured searches on the search page
- [ ] 10.4 Lezer grammar for SPARQL (structural autocomplete, bracket-aware folding) replacing the stream mode, if the editor's autocomplete proves too shallow

## 10. Sources

Primary sources read for this design (2026-11-18):

- SPHN FAIR Data Point: https://fdp.dcc.sib.swiss (UI observed by the
  coordinator), deployment repository
  https://gitlab.com/sphn/sphn-fair-data-point (README, `docker-compose.yml`
  services, `fdp/application.yml`), server and client forks on
  https://git.dcc.sib.swiss/sphn-semantic-framework/ (project pages only;
  their READMEs and changelogs were not reachable), documentation
  https://sphn.gitlab.io/sphn-fair-data-point/ (FAQ, "Querying with SPARQL"
  with ten example queries), schema https://sphn.gitlab.io/sphn-metacat-schema/,
  the framework page and the September 2026 release post cited in §8;
  reference FDP changelog https://github.com/FAIRDataTeam/FAIRDataPoint/blob/develop/CHANGELOG.md
  (search filters 1.15, autocomplete 1.16). Not retrievable: the JMIR
  article body (https://medinform.jmir.org/2026/1/e90146) and the October
  2026 preprint (https://www.preprints.org/manuscript/202610.0874).
- SPARQL UI libraries (GitHub repositories, package manifests and release
  pages; npm pages were not reachable): rdfjs/Yasgui (`@zazuko/*` 4.6.2),
  TriplyDB/Yasgui (archived 2026-04-20), sib-swiss/sparql-editor 0.2.15,
  codemirror/legacy-modes (`mode/sparql`), RubenVerborgh/SPARQL.js
  (archived, deprecated for Traqula), sparna-git/Sparnatural 14.0.0,
  wikimedia/wikidata-query-builder, chartjs/Chart.js, apache/echarts,
  observablehq/plot.

- W3C, *Data Catalog Vocabulary (DCAT) — Version 3*, https://www.w3.org/TR/vocab-dcat-3/ (§6.3–6.16: property lists, what DCAT 2 and DCAT 3 added, `dcat:startDate`/`endDate`, `dcat:bbox`/`centroid`, `locn:geometry`, `dcat:DatasetSeries`, `dcat:version`).
- SEMIC, *DCAT-AP 3.0.0*, https://semiceu.github.io/DCAT-AP/releases/3.0.0/ (mandatory and recommended properties, controlled vocabularies and their base IRIs, deprecated DCAT-AP 2 URIs).
- PostgreSQL 16 documentation: *Controlling Text Search* (`websearch_to_tsquery`, `setweight`, `ts_rank`/`ts_rank_cd` weights and normalisation, `ts_headline`), *Preferred Index Types for Text Search* (GIN), *pg_trgm* (operators, `gin_trgm_ops`), *unaccent* (configuration with `unaccent` as a filtering dictionary), https://www.postgresql.org/docs/16/.
- CKAN 2.11 API, `package_search` (`q`, `fq`, `facet.field`, `facet.limit`, `sort`, `rows`/`start`, the default `rows_max`), https://docs.ckan.org/en/2.11/api/.
- data.europa.eu data provider manual (search API entry point, `page`/`limit`, UI query syntax), https://dataeuropa.gitlab.io/data-provider-manual/. The hub-search OpenAPI document itself was not retrievable; the facet set named in §1 (catalog, category, country, format, licence, publisher) is from the portal's UI, not its API reference.
- pyoxigraph 0.5.13 on PyPI and its documentation (`Store(path)`, `Store.read_only()`, `query()` signature without a timeout; manylinux x86_64/aarch64 wheels; MIT/Apache-2), https://pypi.org/project/pyoxigraph/ and https://pyoxigraph.readthedocs.io/.
- OpenSearch 1.1 Draft 6, https://github.com/dewitt/opensearch/blob/master/opensearch-1-1-draft-6.md.
- FDPneo server (`../server/src/fdpneo_server`): `metadata/search/` (tsvector model, extraction, ranked and visibility-gated query), `metadata/labels.py` and `metadata/external_labels.py` (label precedence and cached remote resolution), `shared/sparql_safety.py` (structural SPARQL gate), `profiles/default/schemas/*.ttl` (the DCAT 3 shapes FDPneo serves), ADR-0021 (FAIR Discovery tiers and the accept-and-flag posture).
- This repository: `harvest.py`, `rdf.py`, `models.py`, `service.py` (`_harvest_once`, `list_entries`), ADR-0001–0004.
