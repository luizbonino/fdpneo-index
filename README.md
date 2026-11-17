# FDP Index

A directory of FAIR Data Points, as a standalone service. FAIR Data Points
announce themselves with a ping; the index retrieves their metadata, tracks
whether they are reachable, harvests the metadata of accepted ones, and lets
administrators curate the list.

It replaces the index mode of the Java FAIR Data Point (`FDP_INDEX=true`)
and stays compatible with it on the wire: FDPs ping it the same way, and the
reference client works against its API (see
[ADR-0001](docs/adr/0001-standalone-index-service.md)).

| Part | What it is |
|---|---|
| [`server/`](server/) | The index API: Python, FastAPI, PostgreSQL |
| [`client/`](client/) | The directory and administration web client: Vue 3 |
| [`deploy/`](deploy/) | Compose stack |
| [`docs/adr/`](docs/adr/) | Design decisions |
| [`TASKS.md`](TASKS.md) | Implementation phases and status |

## How it works

1. An FDP sends `POST /` with `{"clientUrl": "https://its.base.url"}`.
   The index answers `204`, or `400` / `403` (deny list) / `429` (rate limit).
2. The index creates or updates the entry. New entries are `ACCEPTED` when
   auto-permit is on, otherwise `PENDING` until an administrator decides.
3. In the background it fetches the client URL as RDF, finds the repository
   (`fdp-o:MetadataService` or `r3d:Repository`) and records its title,
   description, publisher, version and country. The entry becomes `ACTIVE`,
   `INVALID` or `UNREACHABLE`; an `ACTIVE` entry turns `INACTIVE` when nothing
   was retrieved for the valid duration (7 days by default).
4. For accepted entries it then harvests the FDP's metadata tree, served at
   `/index/entries/{uuid}/data`.
5. Every day (configurable) the index also checks each accepted FDP itself,
   so an FDP that stopped pinging stays `ACTIVE` while it answers, and one
   that went down becomes `UNREACHABLE` after three failed checks in a row
   ([ADR-0004](docs/adr/0004-active-liveness-checks.md)).
6. Webhooks notify other systems of new entries, pings and state changes.

Every step is recorded as an event, with the HTTP exchange it involved.

## API

| Path | Who | Purpose |
|---|---|---|
| `POST /` | anyone | Ping |
| `GET /index/entries` | anyone* | Paged list: `state`, `permit`, `q`, `sort`, `page`, `size` |
| `GET /index/entries/all`, `/info` | anyone* | Unpaged list; counts per state |
| `GET /index/entries/{uuid}` | anyone* | Detail with metadata and recent events |
| `GET /index/entries/{uuid}/data` | anyone* | Harvested metadata (Turtle, JSON-LD, N-Triples, RDF/XML) |
| `PUT` / `DELETE /index/entries/{uuid}` | admin | Accept or reject; delete |
| `GET /index/entries/{uuid}/events`, `/index/events/{uuid}` | admin | Full event log with exchanges |
| `GET` / `PUT` / `DELETE /index/settings` | admin | Settings; `DELETE` resets |
| `POST /index/admin/trigger`, `/trigger-all` | admin | Retrieve (and harvest) now |
| `/index/webhooks`, `POST /index/admin/ping-webhook` | admin | Manage and test webhooks |
| `GET /index/config`, `/healthz`, `/readyz` | anyone | Client configuration, probes |

\* Anonymous callers see accepted entries only. The OpenAPI document is at
`/openapi.json`, interactive documentation at `/docs`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_DATABASE_URL` | `postgresql+asyncpg://fdpindex:fdpindex@localhost:5432/fdpindex` | Database |
| `FDPINDEX_AUTO_PERMIT` | `true` | Initial auto-permit setting |
| `FDPINDEX_OIDC_ISSUER`, `FDPINDEX_OIDC_AUDIENCE`, `FDPINDEX_OIDC_CLIENT_ID` | — | OIDC sign-in for administrators |
| `FDPINDEX_OIDC_ROLES_CLAIM`, `FDPINDEX_OIDC_ADMIN_ROLE` | `realm_access.roles`, `admin` | Where the admin role is |
| `FDPINDEX_ADMIN_TOKENS` | — | SHA-256 digests of static admin tokens (`fdpindex token new`) |
| `FDPINDEX_OUTBOUND_ALLOW_PRIVATE` | `false` | Allow fetching private / loopback addresses (local stacks only) |
| `FDPINDEX_OUTBOUND_MAX_BYTES` | `5242880` | Largest response read |
| `FDPINDEX_HARVEST_MAX_NODES` | `2000` | Most nodes harvested per FDP |
| `FDPINDEX_WORKER_CONCURRENCY` | `4` | Parallel background jobs |
| `FDPINDEX_HEALTH_SWEEP_SECONDS`, `FDPINDEX_HEALTH_BATCH` | `60`, `100` | How often due liveness checks are started, and how many per sweep |
| `FDPINDEX_CORS_ORIGINS` | — | Comma-separated origins allowed to call the API |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is trusted (needed for the per-IP rate limit) |

Rate limits, durations, the deny list, auto-permit and the liveness checks
(interval, failure threshold, which permits) are index settings, changed at
runtime under `/index/settings` or on the Settings page.

## Development

```bash
cd server
uv sync --extra dev
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -m unit           # fast, in-memory SQLite
uv run pytest -m integration    # PostgreSQL via testcontainers (needs Docker)
FDPINDEX_DATABASE_URL=sqlite+aiosqlite:///dev.db uv run fdpindex db migrate
```

## Licence

MIT. Contributions need a [DCO](DCO) sign-off (`git commit -s`).
