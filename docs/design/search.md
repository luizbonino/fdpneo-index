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
   any time without network access. No triple store is introduced for the
   core feature (ADR-0002 stands).
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
8. **Expert access in three steps**: a JSON filter body on `POST /index/search`
   (named fields and arbitrary predicate paths, typed operators), field
   prefixes inside `q` (`title:`, `keyword:`, `publisher:`), and, later and
   opt-in, a read-only SPARQL endpoint over a derived pyoxigraph store with
   hard resource limits. Semantic (pgvector) search is designed for but not
   built in the first phases; it must never depend on an external embeddings
   API by default.
9. **The index is itself a harvestable DCAT catalog**: search results are
   available as JSON-LD (a `dcat:Catalog` of the hits that points at the
   original IRIs), `/index/catalog` lists every accepted FDP's catalogs, and
   an OpenSearch description advertises the search.
10. **Protect the database from anonymous queries**: per-request
    `statement_timeout`, page-depth and parameter caps, a per-IP rate limit,
    cached facet counts, SPARQL off by default.

## 1. User-facing capabilities

Priorities: **MVP** (Phase 6), **next** (Phase 7–8), **later** (Phase 9 or
when demand shows). Each line names the predicates it relies on; the exact
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

### 2.1 Structured filters (Phase 6 for named fields, Phase 8 for paths)

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

### 2.2 SPARQL — opt-in, later, bounded

Recommendation: **not in the core, not in the MVP; an optional feature in
Phase 9**, documented as such in ADR-0005.

- rdflib's in-memory SPARQL over the whole corpus (tens of millions of
  triples) is too slow and too memory-hungry to expose publicly. Ruled out.
- SPARQL-to-SQL over `search_value` would cover only a fragment of SPARQL;
  the effort is not worth a half-endpoint. Ruled out.
- **pyoxigraph** (RocksDB-backed `Store(path)`, SPARQL 1.1, MIT/Apache-2,
  manylinux x86_64 and aarch64 wheels) is the only candidate that fits a
  single-process Python service. It becomes a *derived* store, rebuilt from
  `harvested_node` (one named graph per entry, the entry's client URL as
  graph IRI), kept up to date by the same projection job. It is disabled
  unless `FDPINDEX_SPARQL_ENABLED=true`.
- Limits that are non-negotiable, because pyoxigraph exposes no query
  timeout or cancellation from Python: the query runs in a **separate
  worker process** (`concurrent.futures.ProcessPoolExecutor`, one or two
  workers) that is killed on a wall-clock timeout (5 s anonymous, 30 s
  admin); result rows capped (10 000) and response bytes capped (5 MiB);
  query text capped (10 KiB); parsed with rdflib `prepareQuery` and rejected
  when it contains `SERVICE` or is not SELECT/ASK/CONSTRUCT/DESCRIBE
  (same gate as FDPneo's `shared/sparql_safety.py`); per-IP rate limit; read
  only (`Store.read_only()` in the worker). `GET/POST /index/sparql`,
  SPARQL 1.1 Protocol, results as JSON/XML/CSV, CONSTRUCT as Turtle.
- An alternative for most "expert" needs is cheaper: the `/data` endpoint per
  FDP, `/index/catalog`, and the structured filters. Offer SPARQL when
  users ask for it, not before.

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
| `theme` | IRI | `dcat:theme`, `dct:subject` (IRI) | yes | label → B | labels via §3.5 |
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
  theme:   { kind: iri, paths: [dcat:theme, dct:subject], facet: true, weight: B, labels: [bundled, harvested] }
  …
extensions:               # administrator additions; same shape as fields
  ejprdResourceType: { kind: iri, paths: [ejprd:resourceType], facet: true, label: "Resource type (EJP RD)" }
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

Decision: reserve the seam, decide after Phase 8 with a real corpus.
Schema reserved as a separate table `search_embedding(resource_id, model,
embedding vector(384))` with an HNSW index, feature flag
`FDPINDEX_SEARCH_EMBEDDINGS={off,local,remote}` (default `off`), and an
`Embedder` protocol with a local ONNX implementation first; a remote API
implementation is a deployment choice, never the default. Phase 9.4.

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
| `GET` / `POST /index/sparql` | anyone when enabled | Phase 9 (§2.2) |

`GET /index/search` parameters (all optional):

| Parameter | Meaning |
|---|---|
| `q` | websearch syntax: words, `"phrase"`, `or`, `-word`; field prefixes `title:`, `keyword:`, `publisher:`, `theme:`, `identifier:` (Phase 8); max 512 characters |
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
| `collapse` | `true` to collapse cross-FDP duplicates into one hit with `duplicates` (Phase 8) |
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
- **Facet sidebar**: groups in the order theme, publisher, format, access
  rights, language, spatial, FDP, country, then extension facets; each
  shows top buckets as checkboxes with counts and labels (IRI tail as the
  label fallback, full IRI in `title`), a "Show more" opening a searchable
  dialog over `/index/search/facets`, and a clear-group link. Date ranges
  (issued, modified) and temporal coverage are two-field inputs; "Only
  reachable FDPs" is a toggle (`fdpState=ACTIVE`). Active filters repeat as
  removable chips above the results.
- **Result cards** (`ResourceCard.vue`), one design with per-type accents:
  type badge; title (link to `/search/r/{publicId}`, a detail page); FDP
  chip with the FDP's title and `StateBadge` (link to `/entries/{uuid}`);
  publisher; snippet or the first 240 characters of the description; theme
  chips; keyword chips (max 6); for datasets a distribution strip
  (format labels, "n distributions"); issued/modified as relative time with
  absolute `title` (existing `format.ts`); "Open at source" external link;
  a quiet "sparse metadata" hint under 0.4 completeness; a muted "FDP
  unreachable since …" line when the state is not active.
- **Detail page** `/search/r/{publicId}`: every field in the requested
  language with the others behind a language switch, all values with labels
  and IRIs (copyable, `CopyField.vue`), the parent chain (FDP › catalog ›
  dataset), distributions and services, raw triples (Turtle, collapsed), and
  "find similar" shortcuts (same publisher, same theme).
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
| Abuse by anonymous users | per-IP token bucket (60 requests/min search, 300/min suggest; in memory, per process), parameter caps (`q` 512 chars, 20 filter values per facet, page depth 10 000), `statement_timeout`, SPARQL off by default and process-isolated when on, no `ts_headline` on more than 20 hits per request |
| Reindex storms (many pings in a burst, profile changes) | projection is per changed node only; profile re-projection is one resumable job with batches; harvest already has the per-entry lock and the worker pool bound |
| Metadata quality variance (strings for IRIs, missing titles, wrong types, catalogs typed as datasets) | normalisation map, fallbacks and flags (§3.8); per-FDP completeness in admin stats; kind precedence makes a node typed both `dcat:Catalog` and `dcat:Dataset` a catalog |
| Language detection wrong for untagged literals | untagged text goes to `simple` only (no stemming damage); the user's language configuration is applied only to the query side plus tagged literals |
| `ts_headline` output is not XSS-safe by PostgreSQL's own warning | the server returns only the markers `<em>`/`</em>` and escapes everything else before sending; the client renders the snippet from a tokenised form, never as raw HTML |
| Extension availability (`unaccent`, `pg_trgm`) on managed PostgreSQL | both are *trusted* extensions since PostgreSQL 13, so `CREATE EXTENSION IF NOT EXISTS` works for the database owner; the migration fails loudly otherwise, and the operator documentation says so |
| SQLite/PostgreSQL divergence in tests | all SQL through SQLAlchemy expressions; the dialect switch lives in one repository module with explicit "PostgreSQL only" marks; integration tests cover ranking and FTS |
| Stale search after a harvest that found nothing | the harvest keeps the previous nodes (service docstring), so the projection keeps the previous rows; `indexing.pending` and `harvestedAt` make staleness visible |
| Duplicate content across mirrors inflating counts | `dedup_key` grouping and `collapse=true`; counts always say "resources", not "datasets" |

## 8. Implementation plan

Numbering continues `TASKS.md`. Each task is small enough for one PR with
its tests. "Done" as defined in `TASKS.md`.

### Phase 6 — Search over harvested metadata: foundation

- [ ] 6.1 ADR-0005: search as a projection in PostgreSQL (amends ADR-0002), the profile, SPARQL deferred and opt-in, the compatibility rule for new paths
- [ ] 6.2 Migration `0003_search`: `search_resource`, `search_value`, `search_label`, `search_facet_cache`, `unaccent` + `pg_trgm`, the `fdpindex_*` text search configurations; SQLite variants for unit tests
- [ ] 6.3 Profile model (Pydantic) and bundled default `profile.default.yaml` covering §3.3; `profile_hash`; unit tests for CURIE expansion and validation
- [ ] 6.4 Projection (`search/project.py`): kind detection, fields, paths up to depth 2, multilingual titles/descriptions, dates, identifiers, `completeness`, `public_id`; pure function with fixture graphs from a Java FDP and from FDPneo (DCAT 2 and DCAT 3)
- [ ] 6.5 Incremental harvest storage: upsert `harvested_node` by `(entry_uuid, node_iri)` with change sets; projection job for changed/new/removed nodes; deletion on reject/delete; `search_reindex` event for resumable full re-projection
- [ ] 6.6 Text search write path: weighted `tsv_lang` / `tsv_simple` per §3.6, language → configuration map; integration tests for stemming and unaccent
- [ ] 6.7 Roll-up pass: distribution formats, media types, licences onto datasets; dataset counts onto catalogs
- [ ] 6.8 Labels: harvested labels during projection; bundled vocabulary pack (EU authority tables, SPDX, IANA, DCAT terms) and `fdpindex vocab update`; `GET /index/search/labels`
- [ ] 6.9 Search repository: filters (named fields, dates, fdp, fdpState), text condition, ranking per §4, sorting, offset paging, facet counts with the facet cache, `statement_timeout`; unit tests on SQLite, ranking and FTS tests on PostgreSQL
- [ ] 6.10 `GET` / `POST /index/search` (JSON), `GET /index/search/resources/{id}`, `GET /index/search/facets`; parameter validation and caps; anonymous visibility; OpenAPI snapshot and contract tests
- [ ] 6.11 Rate limit for anonymous search and suggest (in-memory token bucket, trusted proxy IP as for pings)
- [ ] 6.12 Admin: `GET`/`PUT`/`DELETE /index/search/profile`, `POST /index/search/reindex`, `GET /index/search/stats` (rows, lag, completeness per FDP)
- [ ] 6.13 Client: `/search` route, `SearchView.vue`, `SearchBox.vue`, `FacetGroup.vue`, `ResourceCard.vue`, URL state, type tabs, mobile bottom sheet, states; `useSearch()` composable; unit tests; "Search this FDP's metadata" on the entry page
- [ ] 6.14 Client: resource detail page `/search/r/{id}` with language switch, labels and IRIs, parent chain, distributions, raw triples
- [ ] 6.15 Performance check on a synthetic corpus (1 000 entries × 2 000 nodes, generated from the fixture graphs): `EXPLAIN ANALYZE` of the main query shapes, documented numbers and the `maintenance_work_mem` advice in the operator documentation
- [ ] 6.16 Operator documentation: extensions, profile, reindex, limits; README API table extended

### Phase 7 — Richer discovery

- [ ] 7.1 Snippets with `ts_headline` (`highlight=true`), server-side escaping, client rendering from tokens
- [ ] 7.2 Autocomplete `GET /index/search/suggest` (prefix on `search_value`, trigram on titles) and the combobox in the client
- [ ] 7.3 Temporal coverage: `dct:temporal` with `dcat:`, `schema:` and `time:` forms, literal ranges; `temporalFrom`/`temporalTo`; range inputs in the client
- [ ] 7.4 Spatial facet from `dct:spatial` IRIs with labels (EU country/place/continent, GeoNames via the bundled pack)
- [ ] 7.5 Remote label resolution through `OutboundClient` with a host allow-list, off by default, background with cached misses
- [ ] 7.6 Multilingual display: `lang` / `Accept-Language` pick for titles, descriptions and labels; language switch in the client; index default language in settings
- [ ] 7.7 Cross-FDP duplicates: `dedup_key` (DOI, identifiers, `owl:sameAs`, landing page), `duplicates` on hits, `collapse=true`, "also in n other FDPs" on cards
- [ ] 7.8 Client polish: active-filter chips, "show more" facet dialog, copy link, empty and busy states, keyboard and screen-reader pass

### Phase 8 — Expert and machine access

- [ ] 8.1 Field prefixes in `q` (`title:`, `keyword:`, `publisher:`, `theme:`, `identifier:`) parsed before `websearch_to_tsquery`
- [ ] 8.2 Raw predicate capture (`capture.iri_objects`) into `search_value` as `p:<path>`, `path` filters with typed operators on `POST /index/search`, predicate frequencies in `/index/search/stats`
- [ ] 8.3 Extension facets from the profile (`extensions`), `f.<field>` parameters, re-projection on `PUT`, facet labels in the client
- [ ] 8.4 Keyset `cursor` paging for non-relevance sorts
- [ ] 8.5 JSON-LD / Turtle search results as a `dcat:Catalog` of hits with `Link` paging; `format=` and `Accept` negotiation
- [ ] 8.6 `GET /index/catalog`: the index as a DCAT catalog of every accepted FDP's catalogs and services; validated with the DCAT-AP SHACL shapes in a test
- [ ] 8.7 `GET /index/search/opensearch.xml`; `totalResults`/`startIndex`/`itemsPerPage` in responses; `<link rel="search">` in the client's `index.html`
- [ ] 8.8 Bounding-box filter: WKT/GeoJSON parsing into the four columns, `bbox=` parameter, a simple map-free "within these coordinates" input (a map widget only if a deployment asks)
- [ ] 8.9 Approximate facet counts above a match threshold; facet response cache for anonymous requests

### Phase 9 — Optional engines (each behind a flag, each its own ADR addendum)

- [ ] 9.1 pyoxigraph derived store: build from `harvested_node` (named graph per entry), incremental update from the projection job, rebuild command
- [ ] 9.2 `GET`/`POST /index/sparql` (SPARQL 1.1 Protocol, read only): structural gate (no `SERVICE`, query forms only), process-isolated execution with wall-clock timeout, result and byte caps, rate limit; off by default
- [ ] 9.3 SPARQL in the client: a minimal query page with examples (only when enabled, from `/index/config`)
- [ ] 9.4 Semantic search evaluation: `search_embedding` table, `Embedder` protocol with a local ONNX model, hybrid ranking by reciprocal rank fusion; go/no-go documented with measurements on the real corpus
- [ ] 9.5 "Did you mean" from trigram similarity on zero-hit queries
- [ ] 9.6 Saved queries for administrators and featured searches on the search page

## 9. Sources

Primary sources read for this design (2026-11-18):

- W3C, *Data Catalog Vocabulary (DCAT) — Version 3*, https://www.w3.org/TR/vocab-dcat-3/ (§6.3–6.16: property lists, what DCAT 2 and DCAT 3 added, `dcat:startDate`/`endDate`, `dcat:bbox`/`centroid`, `locn:geometry`, `dcat:DatasetSeries`, `dcat:version`).
- SEMIC, *DCAT-AP 3.0.0*, https://semiceu.github.io/DCAT-AP/releases/3.0.0/ (mandatory and recommended properties, controlled vocabularies and their base IRIs, deprecated DCAT-AP 2 URIs).
- PostgreSQL 16 documentation: *Controlling Text Search* (`websearch_to_tsquery`, `setweight`, `ts_rank`/`ts_rank_cd` weights and normalisation, `ts_headline`), *Preferred Index Types for Text Search* (GIN), *pg_trgm* (operators, `gin_trgm_ops`), *unaccent* (configuration with `unaccent` as a filtering dictionary), https://www.postgresql.org/docs/16/.
- CKAN 2.11 API, `package_search` (`q`, `fq`, `facet.field`, `facet.limit`, `sort`, `rows`/`start`, the default `rows_max`), https://docs.ckan.org/en/2.11/api/.
- data.europa.eu data provider manual (search API entry point, `page`/`limit`, UI query syntax), https://dataeuropa.gitlab.io/data-provider-manual/. The hub-search OpenAPI document itself was not retrievable; the facet set named in §1 (catalog, category, country, format, licence, publisher) is from the portal's UI, not its API reference.
- pyoxigraph 0.5.13 on PyPI and its documentation (`Store(path)`, `Store.read_only()`, `query()` signature without a timeout; manylinux x86_64/aarch64 wheels; MIT/Apache-2), https://pypi.org/project/pyoxigraph/ and https://pyoxigraph.readthedocs.io/.
- OpenSearch 1.1 Draft 6, https://github.com/dewitt/opensearch/blob/master/opensearch-1-1-draft-6.md.
- FDPneo server (`../server/src/fdpneo_server`): `metadata/search/` (tsvector model, extraction, ranked and visibility-gated query), `metadata/labels.py` and `metadata/external_labels.py` (label precedence and cached remote resolution), `shared/sparql_safety.py` (structural SPARQL gate), `profiles/default/schemas/*.ttl` (the DCAT 3 shapes FDPneo serves), ADR-0021 (FAIR Discovery tiers and the accept-and-flag posture).
- This repository: `harvest.py`, `rdf.py`, `models.py`, `service.py` (`_harvest_once`, `list_entries`), ADR-0001–0004.
