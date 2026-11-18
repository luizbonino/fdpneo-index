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

## Phase 2 — Admin client (UI/UX)

- [x] 2.1 Scaffold: FAIR Ecosystem tokens (shared with fdpneo-client), light/dark, i18n (en), routing, OIDC sign-in
- [x] 2.2 Directory: state summary, searchable / filterable / sortable table, entry detail (metadata card, event timeline, harvested data preview and download)
- [x] 2.3 Administration: approval queue with bulk accept / reject, entry actions (check now, delete), settings with human-readable durations, deny-list editor with a live URL test, webhooks with a test delivery
- [x] 2.4 Accessibility (keyboard, focus, contrast), responsive layout, empty and error states
- [x] 2.5 Tests: unit (Vitest) and an end-to-end smoke test (Playwright, `npm run test:e2e` against a running stack)

## Phase 3 — Packaging and operations

- [x] 3.1 Server image (uv, non-root, migrations on start); client image (static, runtime config)
- [~] 3.2 Compose stack: PostgreSQL, server, client; optional Keycloak profile (stack done, Keycloak profile open)
- [~] 3.3 Operator documentation: configuration reference, reverse proxy (client IP for rate limiting), backup (configuration in README; backup open)
- [x] 3.4 Importer: entries, events, settings and webhooks from a Java FAIR Data Point database (index tables)
- [ ] 3.5 Release process (tag-driven, signed multi-arch images, release metadata) once the GitHub repository exists

## Phase 4 — Active liveness checks (new)

Instead of only waiting for each FDP's periodic ping, the index checks
registered FDPs itself. See [ADR-0004](docs/adr/0004-active-liveness-checks.md).

- [x] 4.1 ADR-0004 accepted (settle the open questions)
- [x] 4.2 Scheduler: periodic sweep, per-entry due time with jitter, bounded concurrency, respects the retrieval rate limit
- [x] 4.3 `HealthCheck` events; consecutive-failure threshold before an entry becomes Unreachable; webhooks only on state transitions
- [x] 4.4 Settings: enabled, interval, failure threshold, permits checked, re-harvest when the metadata changed
- [x] 4.5 UI: last check, liveness history, "check now"
- [ ] 4.6 Optional policy for entries unreachable for a long time (notify, or hide from the public list)

## Phase 5 — Separation from the FAIR Data Point

- [ ] 5.1 FDPneo evaluation stack: optional `index` profile running fdpindex, FDPneo pings it
- [ ] 5.2 fdpneo-docs: FDPneo Index pages (deploying, registering an FDP, administering)
- [ ] 5.3 Guidance for Java FDP deployments running `FDP_INDEX=true`: migrate with the importer, then switch the mode off

## Phases 6–9 — Search over harvested metadata

Design: [docs/design/search.md](docs/design/search.md) (proposed; becomes ADR-0005).
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

