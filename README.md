<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/fdpneo-index-dark.svg">
    <img src="docs/assets/fdpneo-index.svg" width="48" height="48" alt="" align="absmiddle">
  </picture>
  FDPneo Index
</h1>

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

## Statuses

Every FDP has a status, from its last metadata retrieval (a ping or a
scheduled check). The values are the Java index's, so existing clients keep
working; the web client shows the labels below and explains them behind the
ⓘ button.

| API value | Shown as | Meaning |
|---|---|---|
| `ACTIVE` | Active | Answered with valid FAIR Data Point metadata recently |
| `UNREACHABLE` | Unreachable | Did not answer (down, timeout, server error); after three failed checks in a row |
| `INVALID` | Invalid metadata | Answered, but not with usable FDP metadata |
| `INACTIVE` | Not seen recently | Valid metadata, but nothing retrieved within the valid period (7 days). With liveness checks on, an FDP the index checks does not end up here; one it does not check, such as a pending one, can |
| `UNKNOWN` | Not checked yet | Registered; the first retrieval has not finished |

Administrators also see the approval status: `ACCEPTED` (listed, checked,
harvested), `PENDING` (waiting, hidden) and `REJECTED` (hidden, not harvested).

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
| `GET /index/search` | anyone† | Faceted search over all harvested metadata: words, phrases, `or`, `-word`; every facet field (`theme`, `keyword`, `publisher`, `format`, `license`, `accessRights`, …) as a repeatable filter, `!value` to exclude; `type`, `fdp`, date ranges, `fdpState=ACTIVE`, `sort`, `page`, `size`, `facets` |
| `GET /index/search/facets`, `GET …/resources/{id}`, `GET …/landing`, `GET …/labels` | anyone† | All values of one facet; one resource as JSON or its harvested triples (Turtle, JSON-LD, N-Triples); counters and facet descriptions; labels for IRIs |
| `GET /index/search/sparql` | anyone† | The same search as a SPARQL query (same parameters), with the parts SPARQL can only approximate (full text, ranking, day-precision dates) listed and commented in the query |
| `GET /index/search/profile`, `POST …/reindex`, `GET …/stats` | admin | The search profile; re-project harvested metadata; rows and completeness per FDP |
| `GET` / `POST /index/sparql` | anyone† | SPARQL 1.1 Protocol over all harvested metadata; without `query`, the service description |
| `POST /index/sparql/validate`, `GET …/schema`, `GET …/suggest`, `POST …/labels` | anyone† | Editor support: validation with line and column, classes and properties with counts, value suggestions, labels |
| `GET /index/sparql/examples`, `POST …/{id}/expand`, `POST …/{id}/run` | anyone† | The query library |
| `GET /index/config`, `/healthz`, `/readyz` | anyone | Client configuration, probes |

\* Anonymous callers see accepted entries only. † Rate limited per address
for anonymous callers; only accepted FDPs are in the SPARQL store. The
OpenAPI document is at `/openapi.json`, interactive documentation at `/docs`.

## SPARQL

The web client's **SPARQL** page has three ways in:

- **Questions**: ready-made queries with a form for their parameters. No
  SPARQL is needed.
- **Builder**: pick a type, add conditions (with suggestions from the data),
  and choose a list or a count. The generated SPARQL updates live.
- **Editor**: SPARQL with autocomplete of the classes, properties and
  prefixes in the data, inline validation, and Ctrl/Cmd+Enter to run.

Results show as a table with labelled links, as a chart when they have a
category and a number, or raw. They can be downloaded as CSV, TSV, JSON or
Turtle. Recent queries stay in the browser, and every query has a shareable
link.

Each accepted FDP's metadata is a named graph, named by its address. The
default graph is the union of those graphs. The graph `urn:fdpindex:entries`
describes the FDPs themselves:

- `fdpindex:state` (`ACTIVE`, …)
- `dct:title`
- `r3d:country`
- `fdpindex:lastRetrieval`
- `fdpindex:harvestedNodes`

```bash
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  http://localhost:8090/index/sparql
```

Common prefixes (`dcat:`, `dct:`, `foaf:`, `fdp-o:`, …) work without
declarations. Limits apply: a time budget per query, a row cap, and a
per-address rate limit. They are configurable, and administrators get more
(see [ADR-0005](docs/adr/0005-search-and-sparql.md)).

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
| `FDPINDEX_HARVEST_MAX_BYTES`, `FDPINDEX_HARVEST_MAX_SECONDS` | 50 MiB, `900` | Most N-Triples and time per harvest |
| `FDPINDEX_HARVEST_NODE_TIMEOUT_SECONDS` | `20` | Time budget per harvested node (the root retrieval uses the retrieval timeout) |
| `FDPINDEX_WORKER_CONCURRENCY` | `4` | Parallel background jobs |
| `FDPINDEX_HEALTH_SWEEP_SECONDS`, `FDPINDEX_HEALTH_BATCH` | `60`, `100` | How often due liveness checks are started, and how many per sweep |
| `FDPINDEX_CORS_ORIGINS` | — | Comma-separated origins allowed to call the API |
| `FDPINDEX_SEARCH_ENABLED` | `true` | The faceted search and its page (needs the `unaccent` and `pg_trgm` extensions, which migration 0003 creates) |
| `FDPINDEX_SEARCH_RATE_PER_MINUTE` | `120` | Searches per minute per anonymous address |
| `FDPINDEX_SEARCH_TIMEOUT_SECONDS`, `…_ADMIN_TIMEOUT_SECONDS` | `5`, `30` | Statement timeout per search |
| `FDPINDEX_SPARQL_ENABLED` | `true` | The SPARQL endpoint and page |
| `FDPINDEX_SPARQL_DIR` | `data/sparql` | Store and snapshots (a volume; rebuilt from PostgreSQL when empty) |
| `FDPINDEX_SPARQL_WORKERS` | `2` | Query worker processes |
| `FDPINDEX_SPARQL_TIMEOUT_SECONDS`, `…_ADMIN_TIMEOUT_SECONDS` | `10`, `60` | Time budget per query |
| `FDPINDEX_SPARQL_MAX_ROWS`, `…_MAX_BYTES`, `…_MAX_QUERY_CHARS` | `10000`, 10 MiB, `20000` | Result and query caps |
| `FDPINDEX_SPARQL_RATE_PER_MINUTE` | `30` | Queries per minute per anonymous address |
| `FDPINDEX_SPARQL_WORKER_MAX_MB` | `1024` | Memory ceiling per worker (Linux) |
| `FDPINDEX_PUBLIC_URL` | — | Public base URL, for absolute IRIs in the VoID description |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is trusted. The per-address rate limits rely on it: the proxy must overwrite the header with the address that connected (the bundled nginx does), never append to what the client sent |

Search at scale (memory, migration 0004, the measured times at 1 000 FDPs):
[docs/search-performance.md](docs/search-performance.md).

Rate limits, durations, the deny list, auto-permit and the liveness checks
(interval, failure threshold, which permits) are index settings, changed at
runtime under `/index/settings` or on the Settings page.

## Moving from a Java FAIR Data Point's index

```bash
fdpindex import-java postgresql://fdp:…@old-host:5432/fdp --dry-run
fdpindex import-java postgresql://fdp:…@old-host:5432/fdp
```

Entries (with their identifiers), the event log, settings and webhooks come
across; harvested metadata is fetched again by the liveness checks. Then
point the old index's address at this one, or ask FDPs to ping the new
address, and switch `FDP_INDEX` off on the Java FDP.

## Development

```bash
cd server
uv sync --extra dev
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pytest -m unit           # fast, in-memory SQLite
uv run pytest -m integration    # PostgreSQL via testcontainers (needs Docker)
FDPINDEX_DATABASE_URL=sqlite+aiosqlite:///dev.db uv run fdpindex db migrate
```

To try the search by hand, `server/bench/seed_demo.py` fills an index with
synthetic FDPs. Their addresses are under `synthetic.example`, their titles
say "(synthetic)", and their liveness checks are switched off. Run it where
`FDPINDEX_DATABASE_URL` points to the index's database, then restart the
server so it indexes them:

```bash
docker cp server/bench/seed_demo.py fdpindex-server-1:/tmp/
docker exec fdpindex-server-1 python /tmp/seed_demo.py --fdps 150   # --remove takes them out
docker restart fdpindex-server-1
```

## Licence

MIT. Contributions need a [DCO](DCO) sign-off (`git commit -s`).
