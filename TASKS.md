# FDPneo Index — tasks

The FDPneo Index as a standalone service, separated from the FAIR Data Point.
Status marks: `[ ]` open · `[~]` in progress · `[x]` done.

Done means: code merged on `main`, the quality gate is green (server: `ruff`,
`pyright` strict, `pytest` unit + integration; client: lint, typecheck, unit
tests, build), and the public surface is documented.

Reference: the index of the Java FAIR Data Point (`FAIRDataTeam/FAIRDataPoint`,
package `org.fairdatateam.fairdatapoint.index`, `FDP_INDEX=true`) and its client
(`FAIRDataTeam/FAIRDataPoint-client`, views `IndexDetail`, `IndexSettings`,
`IndexPing`, component `FairDataPoints`). Decisions are recorded in
[`docs/adr/`](docs/adr/).

## Summary

87 of 115 tasks are done and 3 are in progress (19 November 2026). "Done"
counts `[x]`, and "partly" counts `[~]`. Small leftovers noted under a done
task are listed in the last column.

| Phase | What it delivers | Done | Partly | Open | Status | Still open |
|---|---|---|---|---|---|---|
| 0 Foundations | Repository, ADRs 1–4, tooling, CI | 8/8 | – | – | ✅ Done | – |
| 1 Server: port of the reference index | Ping intake, retrieval, harvest, entries and admin API, webhooks, auth, outbound safety | 17/17 | – | – | ✅ Done | – |
| 2 Admin client | Directory, entry pages, administration, accessibility, tests | 5/5 | – | – | ✅ Done | – |
| 3 Packaging and operations | Images, Compose stack, operator docs, importer, releases | 5/5 | – | – | ✅ Done | – |
| 4 Active liveness checks and registry | Scheduled checks, health events, settings, UI; per-FDP options and registration | 9/9 | – | – | ✅ Done | – |
| 5 Separation from the FAIR Data Point | Running next to FDPneo, documentation here, guidance for Java index deployments, ADR-0021 update | 4/4 | – | – | ✅ Done | – |
| 6 Search: foundation | Projection, full text, facets, ranking, search API, search and resource pages, performance check | 16/16 | – | – | ✅ Done | – |
| 7 SPARQL API and UI | Store, workers, endpoint, schema, query library, editor, builder, charts, history, "Show as SPARQL" | 14/14 | – | – | ✅ Done | Leftovers: `fdpindex sparql rebuild`, editing the query library, `POST /index/sparql/share`, "Open in YASGUI" |
| 8 Richer discovery | Snippets, suggestions, temporal and spatial facets, multilingual display, duplicates, subjects, chosen facets, polish | 13/13 | – | – | ✅ Done | – (a pass with a real screen reader is still worth doing) |
| 9 Expert and machine access | Field prefixes, raw predicates, extension facets, cursors, DCAT/OpenSearch output, bounding box, approximate counts | 8/9 | – | 1 | ✅ Done | 9.6, moved to 11.5 |
| 10 Optional engines and extras | Semantic search, "did you mean", saved queries, SPARQL grammar | 4/4 | – | – | ✅ Done | – |
| 11 Conformance and republication | Profile validation, conformance reports, DCAT-AP republication, EHDS profile, MCP | 1/8 | 1 | 6 | 🔹 Started | Design note (11.1) awaiting review; 11.2 to 11.7 |
| 12 Sources beyond FDPs | Connector protocol, DCAT-AP sources, CKAN and Molgenis | 0/3 | – | 3 | ⬜ Not started | All three |
| **Total** | | **104/115** | **1** | **10** | | |

## Phase 0 — Foundations

- [x] 0.1 Repository skeleton: `server/`, `client/`, `deploy/`, `docs/adr/`, licence, DCO, README, CLAUDE.md
- [x] 0.2 ADR-0001 standalone service, wire and API compatibility
- [x] 0.3 ADR-0002 storage of harvested metadata and outbound-request safety
- [x] 0.4 ADR-0003 administrator authentication
- [x] 0.5 ADR-0004 active liveness checks (proposed; implemented in Phase 4)
- [x] 0.6 Server tooling: uv, ruff, pyright strict, pytest (unit with SQLite, integration with testcontainers)
- [x] 0.7 Client tooling: Vite, Vue 3, TypeScript, ESLint, Prettier, Vitest
- [x] 0.8 CI workflows prepared (`.github/workflows`); they run once the GitHub repository exists

## Phase 1 — Server: port of the reference index

- [x] 1.1 Configuration (environment), structured logging, error envelope, `/healthz`, `/readyz`, `/info`
- [x] 1.2 PostgreSQL schema and Alembic migrations: entries, events, settings (+ deny list), webhooks (+ events, entries), harvested nodes
- [x] 1.3 Index settings: defaults, get / put / reset, `isDefault`, ISO-8601 durations, deny-list patterns validated as regular expressions
- [x] 1.4 Ping intake `POST /`: validation, deny list, per-IP rate limit, recorded exchange, entry upsert, auto-permit, `newEntry`
- [x] 1.5 Metadata retrieval: fetch the client URL as Turtle, find the repository (`r3d:Repository`, `fdp-o:MetadataService`, legacy `fdp:o`), extract title, description, version, publisher, publisher name, country; states Valid / Invalid / Unreachable; retrieval rate limit and timeout
- [x] 1.6 Background jobs: in-process runner with bounded concurrency; unfinished events resumed at start-up
- [x] 1.7 Harvester: follow LDP containers, else the navigation predicates; same origin only; node and size limits; graph per entry; removed on reject and delete
- [x] 1.8 Entries API: list (state, permit, sort, page), `/all`, `/info`, detail with events, `PUT` permit (accept → retrieve + harvest; otherwise drop harvested data), `DELETE`, `/{uuid}/data` (Turtle, JSON-LD, N-Triples)
- [x] 1.9 Admin API: `trigger`, `trigger-all`, `ping-webhook`
- [x] 1.10 Webhooks: matching, delivery with the reference `X-Signature` (SHA-1) and an HMAC-SHA256 `X-Signature-256`, recorded exchanges; management API (new: the reference has none)
- [x] 1.11 Authentication: OIDC bearer tokens with an admin role; static admin tokens for scripts; anonymous read of accepted entries
- [x] 1.12 Outbound safety: deny list, no loopback / private / link-local targets unless allowed, redirects re-checked, response size cap
- [x] 1.13 OpenAPI snapshot and contract tests; integration tests against PostgreSQL and a fake FDP
- [x] 1.14 A harvest keeps what it could not fetch: a node that fails, or that a limit leaves unvisited, keeps its last version and is flagged stale; only a complete harvest deletes nodes the FDP no longer lists (FAIR Discovery design §2.3) — *done: 404 and 410 mean gone and are removed; any other failure, or a node a limit left unvisited, keeps its stored version with `stale_since` and `fetch_error` (migration 0005)*
- [x] 1.15 Harvest runs recorded (trigger, start and end, nodes fetched, added, changed, removed, failed, stale, limit reached, outcome) and shown to administrators on the entry page — *done: `Harvest` events, linked to the retrieval or check that caused them, with counters and a one-line summary in the entry's history; resumed after a restart*
- [x] 1.16 Conditional requests for every harvested node: ETag and Last-Modified stored per node; a 304 reuses the stored version — *done*
- [x] 1.17 Webhook deliveries retried with backoff, and resumed after a restart — *done: 1, 5, 30 and 120 minutes; each attempt in the delivery's payload*

## Phase 2 — Admin client (UI/UX)

- [x] 2.1 Scaffold: FAIR Ecosystem tokens (shared with fdpneo-client), light/dark, i18n (en), routing, OIDC sign-in
- [x] 2.2 Directory: state summary, searchable / filterable / sortable table, entry detail (metadata card, event timeline, harvested data preview and download)
- [x] 2.3 Administration: approval queue with bulk accept / reject, entry actions (check now, delete), settings with human-readable durations, deny-list editor with a live URL test, webhooks with a test delivery
- [x] 2.4 Accessibility (keyboard, focus, contrast), responsive layout, empty and error states
- [x] 2.5 Tests: unit (Vitest) and an end-to-end smoke test (Playwright, `npm run test:e2e` against a running stack)

## Phase 3 — Packaging and operations

- [x] 3.1 Server image (uv, non-root, migrations on start); client image (static, runtime config)
- [x] 3.2 Compose stack: PostgreSQL, server, client; optional Keycloak profile — *done: `--profile keycloak` with a realm (public PKCE client, `admin` role, an administrator who must change the password at first sign-in); `FDPINDEX_OIDC_DISCOVERY_URL` lets the server read the keys inside Compose while tokens name the public issuer; sign-in tested end to end in a browser*
- [x] 3.3 Operator documentation: configuration reference, reverse proxy (client IP for rate limiting), backup — *done: [docs/operations.md](docs/operations.md) (what holds state, reverse proxy, backup and restore tested on the local stack, upgrades, monitoring); `FDPINDEX_TRUSTED_PROXY` for the client's nginx behind another proxy*
- [x] 3.4 Importer: entries, events, settings and webhooks from a Java FAIR Data Point database (index tables)
- [x] 3.5 Release process (tag-driven, signed multi-arch images, release metadata) once the GitHub repository exists — *done: `release.yml` (tag = server = client version, CHANGELOG section, tag on main; server and client images for amd64 and arm64, signed keyless, provenance attested, signature verified; GitHub release from the CHANGELOG; PyPI behind the `PUBLISH_PYPI` variable), [docs/releasing.md](docs/releasing.md), CHANGELOG.md; no release cut yet*

## Phase 4 — Active liveness checks (new)

Instead of only waiting for each FDP's periodic ping, the index checks
registered FDPs itself. See [ADR-0004](docs/adr/0004-active-liveness-checks.md).

- [x] 4.1 ADR-0004 accepted (settle the open questions)
- [x] 4.2 Scheduler: periodic sweep, per-entry due time with jitter, bounded concurrency, respects the retrieval rate limit
- [x] 4.3 `HealthCheck` events; consecutive-failure threshold before an entry becomes Unreachable; webhooks only on state transitions
- [x] 4.4 Settings: enabled, interval, failure threshold, permits checked, re-harvest when the metadata changed
- [x] 4.5 UI: last check, liveness history, "check now"
- [x] 4.6 Optional policy for entries unreachable for a long time (notify, or hide from the public list); the FAIR Discovery design's `EXPIRED` state after 90 days without contact, revived by a ping — *done: `expiry` setting (off; 90 days), `Expiry` event and `EntryExpired` webhook once, optional hiding from the public directory and search (not yet from SPARQL), revived by a successful retrieval; a flag (`expiredTime`), not a new state, to keep the reference states*
- [x] 4.7 Per-FDP options: directory only (registered and checked, not harvested), and its own harvest interval — *done: `options` on `PUT /index/entries/{uuid}`, entry page controls*
- [x] 4.8 A "verified" approval policy next to automatic and manual approval: approve automatically after a successful check of the FDP's root — *done: `verifiedPermit` setting; Settings page offers automatic, after a successful check, by an administrator*
- [x] 4.9 Self-service registration: the Register page submits an FDP and shows the result of an immediate check; administrators can add an FDP through the API — *done: `POST /index/register`, `POST /index/entries`*

## Phase 5 — Separation from the FAIR Data Point

Decisions of 19 November 2026:
- The FDPneo Index is the FAIR Discovery of FDPneo ADR-0021 (proposed),
  built as a standalone service rather than as `registry/` and `harvest/`
  contexts inside FDPneo.
- The repository stays at `luizbonino/fdpneo-index`.
- FDPneo's evaluation stack is not changed until further notice.
- The Index's documentation lives in this repository, not in fdpneo-docs.

- [x] 5.1 Running the Index next to FDPneo, documented here: the Index's own Compose stack, and FDPneo pinging it through its admin-managed index targets (FDPneo ADR-0025). FDPneo's evaluation stack stays as it is. — *done: [docs/with-fdpneo.md](docs/with-fdpneo.md): ping targets by environment or admin API, production and one-machine set-ups*
- [x] 5.2 Documentation in this repository (README and `docs/`): deploying, registering an FDP, administering, a user guide to search and SPARQL — *done: [docs/](docs/README.md): deploying, administering, operations, a user guide to search and SPARQL*
- [x] 5.3 Guidance for Java FDP deployments running `FDP_INDEX=true`: migrate with the importer, then switch the mode off (guidance only) — *done: [docs/java-fdp.md](docs/java-fdp.md): import, switch over, what changes*
- [x] 5.4 Record in FDPneo ADR-0021 that FAIR Discovery is built as this standalone service, and list what of its design is still open here — *done: FAIRDataTeam/fdpneo-server#52, merged 19 Nov*

## Phases 6–10 — Search and SPARQL over harvested metadata

Design: [docs/design/search.md](docs/design/search.md). Order of work: 6.1,
6.5, then the SPARQL server (7.1–7.6) and UI (7.8–7.10), then the rest of
Phase 6, then 7.7 and 7.11–7.14.

### Phase 6 — Search over harvested metadata: foundation

- [x] 6.1 ADR-0005: search as a projection in PostgreSQL (amends ADR-0002), the profile, SPARQL over an embedded derived store with process isolation (amends ADR-0001's "no SPARQL in this service"), the compatibility rule for new paths
- [x] 6.2 Migration `0003_search`: `search_resource`, `search_value`, `search_label`, `search_facet_cache`, `unaccent` + `pg_trgm`, the `fdpindex_*` text search configurations; SQLite variants for unit tests — *done; `search_entry_state` instead of a reindex event, no facet cache (see 6.15)*
- [x] 6.3 Profile model (Pydantic) and bundled default `profile.default.yaml` covering §3.3, with `help`, `cards`, `landing` and the `number`/`boolean` kinds; `profile_hash`; unit tests for CURIE expansion and validation — *done; `help`, `cards`, `landing`, `number` buckets; `boolean` is modelled, untested end to end (8.5)*
- [x] 6.4 Projection (`search/project.py`): kind detection, fields, paths up to depth 2, multilingual titles/descriptions, dates, identifiers, `completeness`, `public_id`; pure function with fixture graphs from a Java FDP and from FDPneo (DCAT 2 and DCAT 3) — *done; whole entry at once, rows written when their hash changes*
- [x] 6.5 Incremental harvest storage: upsert `harvested_node` by `(entry_uuid, node_iri)` with change sets; projection job for changed/new/removed nodes; deletion on reject/delete; `search_reindex` event for resumable full re-projection — *done: upsert with change sets, listeners, deletion; the search projection job comes with 6.4*
- [x] 6.6 Text search write path: weighted `tsv_lang` / `tsv_simple` per §3.6, language → configuration map; integration tests for stemming and unaccent — *done; plus prefix matching of plain words*
- [x] 6.7 Roll-up pass: distribution formats, media types, licences onto datasets; dataset counts onto catalogs
- [x] 6.8 Labels: harvested labels during projection; bundled vocabulary pack (EU authority tables, SPDX, IANA, DCAT terms) and `fdpindex vocab update`; `GET /index/search/labels` — *done: `fdpindex vocab update` regenerates `search/vocab.json` (1 697 IRIs: EU themes, access rights, file types, frequencies, licences, countries, 47 languages, in ten languages; SPDX; DCAT, DC, FOAF and ADMS terms); labels in the reader's language, every language searchable, re-projection when the pack changes; IANA media types need no labels (the bare type is the label)*
- [x] 6.9 Search repository: filters (named fields, dates, fdp, fdpState), text condition, ranking per §4, sorting, offset paging, facet counts with the facet cache, `statement_timeout`; unit tests on SQLite, ranking and FTS tests on PostgreSQL — *done except the facet cache*
- [x] 6.10 `GET` / `POST /index/search` (JSON), `GET /index/search/resources/{id}` (JSON, Turtle, JSON-LD, N-Triples), `GET /index/search/facets`, `GET /index/search/landing`; parameter validation and caps; anonymous visibility; OpenAPI snapshot and contract tests — *done except `POST /index/search` (it carries the path filters of 9.2)*
- [x] 6.11 Rate limit for anonymous search and suggest (in-memory token bucket, trusted proxy IP as for pings)
- [x] 6.12 Admin: `GET`/`PUT`/`DELETE /index/search/profile`, `POST /index/search/reindex`, `GET /index/search/stats` (rows, lag, completeness per FDP) — *done: the profile is the bundled one plus an administrator's overlay (JSON or YAML; fields replaced, added or removed; prefixes, types, normalise, cards, landing, capture), validated as a whole, applied at run time with a re-projection; Settings → Search profile*
- [x] 6.13 Client: `/search` route, `SearchView.vue`, `SearchBox.vue`, `FacetGroup.vue` (with ⓘ help, Clear, Show more, numeric and boolean groups), `ResourceCard.vue` (key-fields footer), landing strip with counters and the hierarchy explainer, URL state, type tabs, mobile bottom sheet, states; `useSearch()` composable; unit tests; "Search this FDP's metadata" on the entry page — *done; suggestions in the search box come with 8.2*
- [x] 6.14 Client: resource detail page `/search/r/{id}` as a label/value table with language switch, labelled IRIs, parent chain, distributions, raw triples, Turtle / JSON-LD / N-Triples download buttons
- [x] 6.15 Performance check on a synthetic corpus (1 000 entries × 2 000 nodes, generated from the fixture graphs): `EXPLAIN ANALYZE` of the main query shapes, documented numbers and the `maintenance_work_mem` advice in the operator documentation — *done: `server/bench/search_bench.py`, results and advice in [docs/search-performance.md](docs/search-performance.md); fixes in migration 0004 and `search/query.py` (facets from `search_value` alone, matched-id facets with sampling above 50 000, bounded ranking, sort indexes, an in-process facet cache, `random_page_cost` per search). Open: first queries on a cold cache with phrases or very common words take several seconds*
- [x] 6.16 Operator documentation: extensions, profile, reindex, limits; README API table extended — *done: [docs/search-and-sparql.md](docs/search-and-sparql.md) (profile, capture, limits, reindex, labels, troubleshooting); README API table. Profile extensions wait for 6.12/9.3*

### Phase 7 — SPARQL API and UI

- [x] 7.1 `search/sparql_store.py`: pyoxigraph store on `FDPINDEX_SPARQL_DIR`, named graph per entry, replace-graph from `harvested_node`, read-only open for workers (confirm `Store.read_only` / `Store.secondary` semantics on the pinned version), `search_sparql_sync` fingerprints, resync at start-up, `fdpindex sparql rebuild`; integration test on the real wheel — *done except `fdpindex sparql rebuild` (a wiped volume already resyncs at start-up)*
- [x] 7.2 Sync from the projection job: fingerprint compare after each harvest, drop on reject/delete, the `<urn:fdpindex:entries>` service graph, the `<urn:fdpindex:void>` partitions regenerated per sync
- [x] 7.3 Query workers: subprocess pool, one query at a time, wall-clock kill and respawn, RSS watchdog, queue depth and `503`; structural gate (rdflib parse; SELECT/ASK/CONSTRUCT/DESCRIBE only; no `SERVICE`/`LOAD`); rows capped while iterating (instead of an auto-`LIMIT`); row, byte and triple caps; unit tests with a slow-query stub, integration test with a real cross product
- [x] 7.4 `GET`/`POST /index/sparql` (SPARQL 1.1 Protocol, content negotiation for results and graphs, service description, CORS), `POST /index/sparql/validate`, per-IP rate limit, logging; `/index/config` advertises `sparql: {enabled, limits}`; OpenAPI and contract tests
- [x] 7.5 `GET /index/sparql/schema`: classes, predicates, counts, labels, prefixes, named graphs from VoID + `search_label`, cached per sync — *labels are humanised local names until `search_label` exists (6.8)*
- [x] 7.6 Query library: bundled `examples.default.yaml` (the fifteen queries of §2.2.4), typed parameters, safe substitution (`VALUES`), `GET /index/sparql/examples`, `POST …/{id}/run`, admin `PUT`/`DELETE`; also published as `sh:SPARQLExecutable` in `<urn:fdpindex:examples>` — *done except admin `PUT`/`DELETE` and the `sh:SPARQLExecutable` graph*
- [x] 7.7 `GET /index/search/sparql`: faceted-search parameters → SPARQL (named fields, dates, FDP, approximate full text), with `approximation` notes; tests per field kind — *done: `search/to_sparql.py`; filter values matched in every raw form the profile normalises (case-insensitive, through `STR`), roll-ups through `dcat:distribution` and `^dct:isPartOf`, the index's kind precedence, `fdpState` through `<urn:fdpindex:entries>`; approximations also explained as comments in the query; a test runs 20 translated searches on pyoxigraph over the fixture FDPs and checks they find the same resources as the search*
- [x] 7.8 Client: `/sparql` route and `SparqlView.vue` with the shared `SparqlResults.vue` (table, labelled IRI links to `/search/r/{id}`, literal badges, sort, paging, raw view, CSV/TSV/JSON/Turtle download, copy as Markdown); labels batched from `/index/search/labels` — *done: table, labelled IRIs (`POST /index/sparql/labels`), describe action, raw view, downloads, Markdown copy; resource pages (6.14) pending*
- [x] 7.9 Client: query library tab, parameter forms per type (IRI autocomplete with labels, FDP select, date, number, text), "Show query", "Open in editor", example run URLs — *done*
- [x] 7.10 Client: editor tab on CodeMirror 6 (`SparqlEditor.vue`): SPARQL highlighting, prefix insertion and popover, autocomplete from `/index/sparql/schema` and `/index/search/suggest`, inline validation via `/index/sparql/validate`, `Ctrl+Enter`, named-graph panel, light/dark from tokens, keyboard and screen-reader check — *done except the named-graph side panel (an "insert FDP graph" menu instead) and a screen-reader pass*
- [x] 7.11 Client: builder tab (`QueryBuilder.vue` and friends): type → conditions (property picker with labels/counts, operators, value autocomplete, one nesting level) → columns/aggregates → live SPARQL preview; TypeScript generator with unit tests against golden queries; builder state in the URL; "Edit as SPARQL", "Open in search" — *done except "Open in search" (no search page yet) and multi-level nesting (one hop)*
- [x] 7.12 Charts in results (Chart.js 4 + `vue-chartjs`, lazy-loaded): bar, horizontal bar, pie/donut, line; chart defaults per example query; `aria-label` summaries — *done: bar, horizontal bar, pie, line*
- [x] 7.13 History (localStorage, pin/clear) and sharing: URL-embedded queries, `POST /index/sparql/share` for long ones, `/sparql?example=…&param=…` deep links; "Show as SPARQL" on the search page, "Query this in SPARQL" on resource pages, "Open in YASGUI" escape hatch — *done: history, pins, URL-embedded queries and examples; `POST /index/sparql/share`, and "Open in YASGUI" pending; "Show as SPARQL" done with 7.7*
- [x] 7.14 Documentation: SPARQL page in the operator docs (store directory, workers, limits, rebuild), a user guide with the example queries, `curl` samples; README API table — *done: operator part in [docs/search-and-sparql.md](docs/search-and-sparql.md) (store, workers, limits, rebuild), user guide with example queries and `curl` in [docs/user-guide.md](docs/user-guide.md); README API table*

### Phase 8 — Richer discovery

- [x] 8.1 Snippets with `ts_headline` (`highlight=true`), server-side escaping, client rendering from tokens — *done: title and the description's best fragments as `{text, match}` segments (stemmed on PostgreSQL, word prefixes elsewhere), control characters stripped so marks cannot be forged; `<mark>` in result cards*
- [x] 8.2 Autocomplete `GET /index/search/suggest` (prefix on `search_value`, trigram on titles) and the combobox in the client — *done: titles by trigram similarity (identical titles across FDPs folded into one phrase search), keywords by prefix, subjects, themes and publishers by harvested, resolved or bundled names in any language, with counts; an ARIA combobox (arrow keys, Enter, Escape) that applies a value as a filter*
- [x] 8.3 Temporal coverage: `dct:temporal` with `dcat:`, `schema:` and `time:` forms, literal ranges; `temporalFrom`/`temporalTo`; range inputs in the client — *done: a `period` field kind, `temporal_start`/`temporal_end` (migration 0010), overlap filter with years, months or days, "Show as SPARQL" (literal periods noted as not matched), "Period covered" inputs, chip, card line and detail row; the demo seed writes all four forms*
- [x] 8.4 Spatial facet from `dct:spatial` IRIs with labels (EU country/place/continent, GeoNames via the bundled pack) — *done: the pack adds the EU continents and, from GeoNames' `countryInfo.txt`, GeoNames and lexvo country IRIs as aliases and ISO 3166 codes; one country is one value however it is written (also in "Show as SPARQL"); GeoNames IRIs fold to `https://sws.geonames.org/<id>/`. The EU place table (4 700 cities) is left to the concept resolver rather than bundled. A place tree (city in country in continent) is not done*
- [x] 8.5 Numeric-range and boolean facet kinds end to end (`value_num`, buckets, yes/no groups), with a bundled HealthDCAT-AP extension profile as the worked example — *done: `search_value.value_num` (migration 0011), `<field>Min`/`<field>Max`, buckets in range order, Yes/No with absence stored as "false" for `presence` fields, both in "Show as SPARQL"; min/max inputs and Yes/No in the client; `search/examples/healthdcat-ap.yaml` (Release 8 IRIs) served at `/index/search/profile/examples/{name}` and loadable in Settings. EHDS vocabulary IRIs have no labels (their host serves empty RDF)*
- [x] 8.6 Remote label resolution through `OutboundClient` with a host allow-list, off by default, background with cached misses — *done (ADR-0006): labels, synonyms and broader concepts from Wikidata, ROR, OLS (OBO, EDAM) and RDF content negotiation; one shared list of naming predicates (`dct:title`, `dc:title`, `schema:name` … besides `rdfs:label`); used in search, facets and SPARQL labels; off by default*
- [x] 8.7 Multilingual display: `lang` / `Accept-Language` pick for titles, descriptions and labels; language switch in the client; index default language in settings — *done: "Metadata in" on the search page (remembered in the browser; search, suggestions and the tree follow it) and on resource pages; `defaultLanguage` in the search settings for readers whose browser states none; the label pack's ten languages offered*
- [x] 8.8 Cross-FDP duplicates: `dedup_key` (DOI, identifiers, `owl:sameAs`, landing page), `duplicates` on hits, `collapse=true`, "also in n other FDPs" on cards — *done: one key per resource (DOI, global identifier, `owl:sameAs` target, own IRI; migration 0012), `duplicates`/`duplicateCount` on hits and the resource page, `collapse=true` ranks within each group in the search's own order (`totalResults` counts groups, `resources` resources), "Group copies", "Also in n other FDPs" and "Also published in" in the client; the demo seed mirrors datasets. Landing pages are deliberately not keys (portals share them); the key is one identifier, not a union of all of them*
- [x] 8.9 Client polish: active-filter chips, "show more" facet dialog, "more facets", copy link, empty and busy states, keyboard and screen-reader pass — *done: the polish with 6.13; the pass adds `tests/e2e/a11y.spec.ts` (axe-core, WCAG 2.2 A/AA and best practices, on every public page in light and dark mode and in the open dialogs; skip link, combobox, filters and dialogs by keyboard, focus after paging). Fixed: light-mode contrast of the accent and success colours, heading order on the SPARQL page, the Subject tree as a disclosure rather than a half ARIA tree. Not done: a pass with a real screen reader*
- [x] 8.10 Theme and Subject facets split by vocabulary: Theme holds the EU data themes, Subject every other concept (MeSH, Wikidata, EDAM …), from `dcat:theme` and `dct:subject` alike; profile option `namespaces` / `exclude` — *done*
- [x] 8.11 One Subject entry per topic across vocabularies: merge concepts linked by `skos:exactMatch` or Wikidata's MeSH ID (P486) — *done (ADR-0006 amended): matches recorded by the resolver, groups by union-find, one representative per group in fields with `equivalents`, filters and SPARQL follow the group*
- [x] 8.12 Subject as a hierarchy in the client, from the broader concepts the resolver stores — *done: fields with `hierarchy` store each value with its broader concepts in `<field>^`; `GET /index/search/facets?field=subject&tree=true` nests them with counts that include everything narrower; `subject^=<IRI>` filters on "this or narrower" (SPARQL: the concept and those known below it); "Browse Subject as a tree" dialog*
- [x] 8.13 Facets chosen by readers and administrators: visitors add a filter on any property of the harvested metadata ("+ Add a filter", `prop:<IRI>`, kept in the URL and remembered in the browser); administrators set the default facets (order; main, more or hidden; labels), make properties default facets, and decide whether visitors may add filters and on which properties (`GET`/`PUT`/`DELETE /index/search/settings`, Settings page) — *done*

### Phase 9 — Expert and machine access

- [x] 9.1 Field prefixes in `q` (`title:`, `keyword:`, `publisher:`, `theme:`, `identifier:`) parsed before `websearch_to_tsquery` — *done: `title:`/`description:` restrict the words to that text (`ts_filter` on the weighted vectors), any IRI, agent or literal field of the profile is a prefix that becomes a filter on the values it names (IRI, code, label in any language; names by substring; DOIs in any spelling); `-field:x` excludes; echoed as `query.fields`; in "Show as SPARQL" too*
- [x] 9.2 Raw predicate capture (`capture.iri_objects`) into `search_value` as `p:<path>`, `path` filters with typed operators on `POST /index/search`, predicate frequencies in `/index/search/stats` — *done: capture with 8.13; two-step paths listed in `capture.paths`; `POST /index/search` with `eq`/`in`/`not`, `prefix`/`contains`/`exists`/`missing`, `gte`/`lte`/`between` (dates, numbers) and `overlaps` (temporal), 400 naming the filter; typed tests in "Show as SPARQL"; predicate frequency overall and per FDP in the stats. `intersects` waits for 9.8*
- [x] 9.3 Extension facets from the profile (`extensions`), `f.<field>` parameters, re-projection on `PUT`, facet labels in the client and in the SPARQL builder's picker — *done: overlay fields with 6.12 (a field with `facet` is a filter under its own name); `f.` prefixes were not needed; `/index/sparql/schema` marks properties with the profile fields they feed, and the builder lists them first under "Fields of this index" with the fields' labels and help*
- [x] 9.4 Keyset `cursor` paging for non-relevance sorts — *done: `cursor=*` then `nextCursor` (GET and POST), a strict order on (sort key, id) with missing dates last, for `modified`, `issued`, `title`, `harvested`; a cursor of another sort, relevance, or `page` with `cursor` answer 400*
- [x] 9.5 JSON-LD / Turtle search results as a `dcat:Catalog` of hits with `Link` paging; `format=` and `Accept` negotiation — *done: JSON-LD, Turtle, N-Triples, RDF/XML on GET and POST; the parameter is `output=` because `format` is the file-format filter; OpenSearch counts on the catalog; `Link` first/prev/next (or the next cursor); "Download" on the search page*
- [ ] 9.6 (see 11.5) `GET /index/catalog`: the index as a DCAT catalog of every accepted FDP's catalogs and services; validated with the DCAT-AP SHACL shapes in a test — *moved: 11.5 replaces it, after the Phase 11 design note*
- [x] 9.7 `GET /index/search/opensearch.xml`; `totalResults`/`startIndex`/`itemsPerPage` in responses; `<link rel="search">` in the client's `index.html` — *done: the description (search page, JSON, JSON-LD, Turtle with `pageOffset="0"`, suggestions as `application/x-suggestions+json`), the counts were already in responses; the client adds the link at start-up (works when the API is on another origin)*
- [x] 9.8 Bounding-box filter: WKT/GeoJSON parsing into the four columns, `bbox=` parameter, a simple map-free "within these coordinates" input (a map widget only if a deployment asks) — *done: a `geometry` field kind (`bbox` from `dct:spatial`: `dcat:bbox`, `dcat:centroid`, `locn:geometry`, WKT or GeoJSON literals; EPSG:4326 axis order), four columns (migration 0013), `bbox=` and POST `intersects`, `bbox` on hits and the resource page, an "Area" input with a chip; noted, not translated, in "Show as SPARQL"; the antimeridian is not handled*
- [x] 9.9 Approximate facet counts above a match threshold; facet response cache for anonymous requests — *done with 6.15: sampled counts above 50 000 matches, marked `approximate`; an in-process facet cache*

### Phase 10 — Optional engines and extras (each behind a flag)

- [x] 10.1 Semantic search evaluation: `search_embedding` table, `Embedder` protocol with a local ONNX model, hybrid ranking by reciprocal rank fusion; go/no-go documented with measurements on the real corpus — *done: built behind `FDPINDEX_SEARCH_EMBEDDINGS=local` (the `semantic` extra, pgvector, a pinned multilingual MiniLM int8 model), evaluated in `docs/semantic-search.md`: on 27 judged queries hybrid P@10 1.00 against 0.48 for full text, +40 ms per query, ~87 resources/s to embed. Go, off by default; measured on the synthetic corpus, so each deployment re-runs `bench/semantic_eval.py` on its own metadata first*
- [x] 10.2 "Did you mean" from trigram similarity on zero-hit queries — *done: on a search with words and no hits, words of titles (trigram `<%` on PostgreSQL, threshold 0.4 for that query) and keywords close to each word; offered as `didYouMean` only if the corrected search finds something; field prefixes kept; a link on the no-results state*
- [x] 10.3 Saved queries for administrators and featured searches on the search page — *done: `/index/search/saved` (admin; the search page's query string under a title, who saved it, featured or not; at most 200), featured searches in the landing data and as links on the search page; "Save search" on the search page and "Saved searches" in Settings. Shared by all administrators rather than per administrator*
- [x] 10.4 Lezer grammar for SPARQL (structural autocomplete, bracket-aware folding) replacing the stream mode, if the editor's autocomplete proves too shallow — *done at the user's request: a Lezer grammar of SPARQL 1.1 Query, complete for SELECT and ASK (CONSTRUCT and DESCRIBE too), case-insensitive keywords; highlighting, folding and indentation from the tree, syntax errors marked before the server's check; completion by position (prefixes after PREFIX, properties as predicates, classes after `a`, functions and variables in expressions, solution modifiers after the group); every bundled library query parses; the legacy stream mode is gone*

## Phases 11–12 — FAIR Discovery: conformance, republication, other sources

What FDPneo ADR-0021 and its design (`docs/architecture/discovery.md` in
fdpneo-server) ask for beyond Phases 1–10: the catalogue tier.

### Phase 11 — Conformance and republication

- [~] 11.1 Design note, as for search: profile packs, where results are stored, per-surface strictness, the DCAT-AP republication, the EHDS profile — *drafted in `docs/design/conformance.md`, awaiting review (seven questions in §9)*
- [ ] 11.2 SHACL profile packs as data (DCAT-AP 3, HealthDCAT-AP R5, Health-RI core), versioned, each for declared record types
- [ ] 11.3 Validation of every harvested record against the deployment's packs, results stored; a conformance report per FDP for data holders (API and page)
- [ ] 11.4 Minimum conformance per surface (search, SPARQL, the catalogue feed) as a deployment setting; accept-and-flag by default
- [ ] 11.5 The harvested metadata republished as a DCAT-AP catalogue with links back to each source (`dct:source`), harvestable by the EU dataset catalogue (replaces 9.6)
- [ ] 11.6 EHDS deployment profile: HealthDCAT-AP shapes, EU vocabularies, a strict feed; quality and utility labels (EHDS Art. 78) shown on search and resource pages
- [ ] 11.7 An MCP server for the index: find FDPs and datasets, run searches
- [x] 11.8 Configurable language fallback order for titles, descriptions and labels — *done: `languageFallback` in the search settings (default `en`): the reader's language, untagged text, then these languages in order, then any; for titles, descriptions, harvested, resolved and bundled labels; "Fallback languages" in Settings*

### Phase 12 — Sources beyond FDPs

- [ ] 12.1 A connector protocol (probe, enumerate, fetch) with the FDP tree walk as its first implementation; traversal declared as data (navigation SHACL) instead of code
- [ ] 12.2 DCAT-AP sources: a dereferenceable endpoint or a dump file
- [ ] 12.3 CKAN and Molgenis bridges
