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
