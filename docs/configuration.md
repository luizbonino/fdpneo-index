# Configuration

The server reads its configuration from `FDPINDEX_*` environment variables
(with Compose: `deploy/.env`). Everything else, from rate limits and
durations to the deny list, approvals, liveness checks, search filters and
conformance profiles, is an **index setting** that administrators change
at run time on the Settings page (see [Administering](administering.md)).

A test checks that every variable the server reads is listed here.

## Basics

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_DATABASE_URL` | `postgresql+asyncpg://fdpindex:fdpindex@localhost:5432/fdpindex` | The PostgreSQL database |
| `FDPINDEX_ENVIRONMENT` | `production` | `development` logs at debug level, as console text instead of JSON |
| `FDPINDEX_PUBLIC_URL` | — | The public base URL: absolute IRIs in the VoID description, the catalogue feed's record IRIs, links in responses. Set it behind a proxy |
| `FDPINDEX_AUTO_PERMIT` | `true` | The initial approval setting for new FDPs (then a setting) |
| `FDPINDEX_DEPLOYMENT_PROFILE` | `default` | `ehds` for a European Health Data Space index: HealthDCAT-AP and DCAT-AP checked, a catalogue feed of HealthDCAT-AP records only, the HealthDCAT-AP search extension. Defaults only: every setting stays the administrator's ([Administering](administering.md#an-index-for-the-european-health-data-space)) |
| `FDPINDEX_CORS_ORIGINS` | — | Comma-separated origins allowed to call the API from a browser |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is trusted. The per-address rate limits rely on it: the proxy must overwrite the header with the address that connected (the bundled nginx does), never append to what the client sent |
| `FDPINDEX_TRUSTED_PROXY` (client container) | — | Address or CIDR range(s) of a reverse proxy in front of the stack, whose `X-Forwarded-For` nginx trusts |
| `FDPINDEX_PROXY_NETWORK` (`compose.proxy.yaml`) | — | The Docker network of a reverse proxy on the same host, which the client joins ([Deploying](deploying.md#behind-a-proxy-in-docker)) |
| `FDPINDEX_PROXY_ALIAS` (`compose.proxy.yaml`) | `fdpneo-index-client` | The client's name on that network, which the proxy forwards to |

## Administrators

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_ADMIN_TOKENS` | — | SHA-256 digests of static admin tokens (`fdpindex token new`), comma-separated |
| `FDPINDEX_OIDC_ISSUER` | — | OIDC sign-in for administrators: the issuer |
| `FDPINDEX_OIDC_AUDIENCE` | — | The audience tokens must carry; set it, or any client of the realm is accepted |
| `FDPINDEX_OIDC_CLIENT_ID` | — | The public client the web app signs in with (handed to the browser) |
| `FDPINDEX_OIDC_DISCOVERY_URL` | the issuer's `/.well-known/openid-configuration` | Where the server reads the issuer's configuration, when it cannot reach the issuer's public address |
| `FDPINDEX_OIDC_ROLES_CLAIM` | `realm_access.roles` | Where in the token the roles are |
| `FDPINDEX_OIDC_ADMIN_ROLE` | `admin` | The role that makes an administrator |

## Fetching from FDPs

Every request the index makes goes through one guarded client (ADR-0002).

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_OUTBOUND_ALLOW_PRIVATE` | `false` | Allow fetching private and loopback addresses (local test stacks only) |
| `FDPINDEX_OUTBOUND_MAX_BYTES` | `5242880` | The largest response read |
| `FDPINDEX_OUTBOUND_MAX_REDIRECTS` | `5` | Redirects followed; each hop is checked again |
| `FDPINDEX_USER_AGENT` | `fdpneo-index (+https://github.com/luizbonino/fdpneo-index)` | The `User-Agent` of every request |
| `FDPINDEX_HARVEST_MAX_NODES` | `2000` | Most pages one harvest run reads; a larger FDP continues in the next run |
| `FDPINDEX_HARVEST_MAX_BYTES` | 50 MiB | Most N-Triples kept per run |
| `FDPINDEX_HARVEST_MAX_SECONDS` | `900` | Longest run |
| `FDPINDEX_HARVEST_WALK_MAX_NODES` | `100000` | Most pages one harvest reads over all its runs |
| `FDPINDEX_HARVEST_CONTINUE_SECONDS` | `600` | Wait before a harvest's next run (longer when the FDP sends `Retry-After`) |
| `FDPINDEX_HARVEST_NODE_TIMEOUT_SECONDS` | `20` | Time budget per harvested node (the root uses the retrieval timeout setting) |

## Background work

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_WORKER_CONCURRENCY` | `4` | Parallel background jobs (retrievals, harvests, webhook deliveries) |
| `FDPINDEX_HEALTH_SWEEP_SECONDS` | `60` | How often due liveness checks are started |
| `FDPINDEX_HEALTH_BATCH` | `100` | Liveness checks started per sweep |
| `FDPINDEX_STATS_SNAPSHOT_SECONDS` | `3600` | How often the day's statistics are counted again for the history on the Statistics page; `0` keeps no history |
| `FDPINDEX_EVENT_BODY_MAX_CHARS` | `65536` | Request and response bodies kept in the event log, at most |

## Search

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_SEARCH_ENABLED` | `true` | The faceted search and its page (needs the `unaccent` and `pg_trgm` extensions, which migration 0003 creates) |
| `FDPINDEX_SEARCH_RATE_PER_MINUTE` | `120` | Searches per minute per anonymous address |
| `FDPINDEX_SEARCH_TIMEOUT_SECONDS` | `5` | Statement timeout of a visitor's search |
| `FDPINDEX_SEARCH_ADMIN_TIMEOUT_SECONDS` | `30` | Statement timeout of an administrator's search |
| `FDPINDEX_VOCAB_FILE` | the bundled `search/vocab.json` | Another label pack, written by `fdpindex vocab update --output …`: labels of EU authority table codes, SPDX licences and DCAT/DC/FOAF/ADMS terms, and other names of each country |
| `FDPINDEX_SEARCH_EMBEDDINGS` | `off` | `local` turns on semantic search (hybrid ranking by meaning and words). Needs the `semantic` extra, pgvector and a model; see [Semantic search](semantic-search.md) |
| `FDPINDEX_EMBEDDINGS_MODEL_DIR` | — | Where `fdpindex embeddings download` put the model |
| `FDPINDEX_EMBEDDINGS_THREADS` | all cores | Threads for the model |
| `FDPINDEX_EMBEDDINGS_MAX_DISTANCE` | `0.6` | How close (cosine distance) a hit found by meaning alone must be |

## Concept labels

Labels, synonyms and broader concepts of the IRIs metadata uses, fetched
from their vocabularies (ADR-0006). Off by default: it sends requests to
third-party hosts.

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_LABELS_RESOLVE` | `false` | Fetch labels of concept IRIs (subjects, themes, publishers …) for filters and search |
| `FDPINDEX_LABELS_HOSTS` | Wikidata, ROR, OBO, EDAM, MeSH, EU Publications Office, ORCID, NERC, Library of Congress, GeoNames | Hosts that may be asked (comma-separated); checked on every redirect |
| `FDPINDEX_LABELS_LANGUAGES` | `en,nl,de,fr,es,it,pt,sv,da,fi` | Languages of labels kept |
| `FDPINDEX_LABELS_REFRESH_DAYS` | `30` | When a resolved concept is fetched again |
| `FDPINDEX_LABELS_RETRY_DAYS` | `7` | When a concept that could not be resolved is tried again |
| `FDPINDEX_LABELS_BROADER_DEPTH` | `2` | Levels of broader concepts resolved |
| `FDPINDEX_LABELS_INTERVAL_SECONDS` | `60` | How often the resolver looks for work |
| `FDPINDEX_LABELS_REQUESTS_PER_SECOND` | `2` | Requests per second, across all hosts |

## Where FDPs are

FDPs are placed on the Statistics page's map by a country an administrator
set, the country the FDP states, its publisher's ROR record (with label
resolution on), its country-code domain, or where its server is, in that
order ([Administering](administering.md#where-fdps-are)).

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_GEOIP_DIR` | — (the image sets `/app/geoip`) | A folder with DB-IP Lite's country database (`fdpindex geoip download <folder>`), to locate the server that answered each retrieval. Without it, servers are not located |
| `FDPINDEX_LABELS_MAX_BYTES` | `2097152` | The largest vocabulary response read |

## SPARQL

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_SPARQL_ENABLED` | `true` | The SPARQL endpoint and page |
| `FDPINDEX_SPARQL_DIR` | `data/sparql` | Store and snapshots (a volume; rebuilt from PostgreSQL when empty) |
| `FDPINDEX_SPARQL_WORKERS` | `2` | Query worker processes |
| `FDPINDEX_SPARQL_TIMEOUT_SECONDS` | `10` | Time budget of a visitor's query |
| `FDPINDEX_SPARQL_ADMIN_TIMEOUT_SECONDS` | `60` | Time budget of an administrator's query |
| `FDPINDEX_SPARQL_MAX_ROWS` | `10000` | Rows returned, at most |
| `FDPINDEX_SPARQL_MAX_BYTES` | 10 MiB | Serialised results, at most |
| `FDPINDEX_SPARQL_MAX_QUERY_CHARS` | `20000` | Query length, at most |
| `FDPINDEX_SPARQL_RATE_PER_MINUTE` | `30` | Queries per minute per anonymous address |
| `FDPINDEX_SPARQL_WORKER_MAX_MB` | `1024` | Memory ceiling per worker (Linux) |
| `FDPINDEX_SPARQL_VOID_INTERVAL_SECONDS` | `60` | The VoID description is regenerated at most this often |

## Conformance

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_CONFORMANCE_ENABLED` | `true` | Check harvested records against SHACL profiles in the background, with reports per FDP and per record |
| `FDPINDEX_CONFORMANCE_WORKERS` | CPUs less one, at most 4 | Processes running the checks; `0` runs them in the server's process |
| `FDPINDEX_CONFORMANCE_PACKS` | — | A directory of your own SHACL profile packs (`<id>/<version>/pack.yaml`), beside the bundled DCAT-AP 3.0.1, HealthDCAT-AP Release 8 and Health-RI core v2.0.3 ([Administering](administering.md#conformance)) |

## The catalogue feed

| Variable | Default | Meaning |
|---|---|---|
| `FDPINDEX_CATALOG_ENABLED` | `true` | The harvested metadata republished as DCAT-AP at `/index/catalog` and over OAI-PMH at `/index/oai` (needs search) |
| `FDPINDEX_CATALOG_TITLE` | `FDPneo Index` | The catalogue's `dct:title`, and the OAI-PMH repository name |
| `FDPINDEX_CATALOG_DESCRIPTION` | a sentence | The catalogue's `dct:description` |
| `FDPINDEX_CATALOG_PUBLISHER` | the title | The catalogue publisher's name |
| `FDPINDEX_CATALOG_PUBLISHER_URL` | — | The publisher's IRI, when it has one |
| `FDPINDEX_CATALOG_CONTACT_EMAIL` | — | The OAI-PMH `adminEmail`; set it before registering the feed with an aggregator |
