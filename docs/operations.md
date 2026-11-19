# Operating the FDPneo Index

For operators running the Compose stack in `deploy/` (or the same images
elsewhere). Configuration variables are in the [README](../README.md#configuration).

## What holds state

| Where | What | Back up? |
|---|---|---|
| PostgreSQL | Entries, events, settings, webhooks, harvested metadata (`harvested_node`), resolved concept labels | **Yes**: the only source of truth |
| PostgreSQL, search tables | `search_resource`, `search_value`, `search_label`, `search_entry_state`, `search_property` | No: projected again from `harvested_node` at start-up |
| `sparql-data` volume | The SPARQL store and its snapshots | No: rebuilt from PostgreSQL when empty |
| `deploy/.env` | Database password, admin token digests, OIDC settings | Yes, as a secret, apart from the data |

## Behind a reverse proxy

The client container (nginx) is the only published service: it serves the
app and passes the API and FDP pings (`POST /`) to the server. Most
deployments put a TLS-terminating proxy in front of it.

- **Client addresses.** Ping, search and SPARQL rate limits are per client
  address. Behind another proxy, nginx sees only that proxy's address, and
  every client would share one limit. Set `FDPINDEX_TRUSTED_PROXY` to the
  proxy's address or CIDR range (comma-separated for several). nginx then
  takes the client's address from the proxy's `X-Forwarded-For`, and passes
  only that address on. The proxy must set the header from the address that
  connected to it, not append to one the client sent.
- **Public origin.** Set `FDPINDEX_PUBLIC_ORIGIN` (for example
  `https://index.example.org`) when the browser sees another origin than
  the container's. OIDC redirect URIs use it.
- **Public URL.** Set `FDPINDEX_PUBLIC_URL` for absolute IRIs in the SPARQL
  service description.
- **Binding.** `FDPINDEX_BIND` stays `127.0.0.1` when the proxy runs on the
  same host. FDPs must reach the public address, since they ping it.
- **Bodies.** nginx caps request bodies at 256 KiB and the server at 1 MiB;
  pings, settings and queries are far smaller.

## Backup

Back up the database; leave out the data of the search tables, which are
rebuilt anyway and make up most of its size. Write the dump outside the
repository: a dump in the working tree is one `git add` away from being
published.

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env exec -T postgres \
  pg_dump -U fdpindex -d fdpindex -Fc \
    --exclude-table-data=search_resource --exclude-table-data=search_value \
    --exclude-table-data=search_label --exclude-table-data=search_entry_state \
  > /backups/fdpindex-$(date +%F).dump
```

With 150 FDPs and 34 000 harvested nodes, the dump is 5 MB. Keep
`deploy/.env` with the backups, encrypted: without the admin token digests
and the database password, a restore needs new ones.

## Restore

Into an empty database of the same or a newer release:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env stop server
docker compose -f deploy/compose.yaml --env-file deploy/.env exec -T postgres \
  dropdb -U fdpindex fdpindex
docker compose -f deploy/compose.yaml --env-file deploy/.env exec -T postgres \
  createdb -U fdpindex fdpindex
docker compose -f deploy/compose.yaml --env-file deploy/.env exec -T postgres \
  pg_restore -U fdpindex -d fdpindex --no-owner < /backups/fdpindex-2026-11-19.dump
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d server
```

At start-up the server applies newer migrations, projects every entry into
the search tables again, and rebuilds the SPARQL store if its volume is
empty. Until that finishes, the search page says that FDPs are still being
indexed: a few minutes for hundreds of FDPs.

This procedure was checked by restoring a dump of the local stack into a
scratch database: entries, events and harvested nodes came back, the
search tables were empty, and `alembic_version` matched.

## Upgrades

- Read the release notes, back up, then pull or build the new images and
  `up -d`. Migrations run when the server starts (`fdpindex serve`); with
  `--no-migrate`, run `fdpindex db migrate` first.
- A release that changes the search profile or the projection projects
  every entry again in the background. Search keeps answering from the old
  rows until each entry's new rows are written.

## Monitoring

- `GET /healthz`: the process is up (the image's health check).
- `GET /readyz`: the database answers; 503 otherwise.
- `GET /index/search/stats` (administrators): rows per FDP, completeness,
  when each was last projected, concept labels resolved.
- Logs are JSON lines on standard output (`structlog`). Worth alerting on:
  `job_failed`, `search_index_failed`, `health_sweep_failed`,
  `unhandled_error`, `readiness_check_failed`, and bursts of
  `token_rejected` or `ping_rate_limited`. Each harvest logs `harvested`
  with its `outcome` and counts of stale and removed nodes.

## Removing an FDP's data

Rejecting an entry (Approvals, or `PUT /index/entries/{uuid}` with a new `permit`)
removes it from search and SPARQL; deleting the entry removes its harvested
metadata too. A backup taken before still holds it: keep backups only as
long as your retention policy allows.
