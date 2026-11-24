# Moving from a Java FAIR Data Point's index

A Java FAIR Data Point (the reference implementation) can run as an index
(`FDP_INDEX=true`), as `home.fairdatapoint.org` does. The FDPneo Index
replaces that mode: it accepts the same pings and serves the same
`/index/*` API, so FDPs and existing API clients keep working, and it adds
liveness checks, harvesting, search and SPARQL.

This is guidance only: the Java FDP is not changed.

## 1. Deploy the FDPneo Index

As in [deploying.md](deploying.md). Leave its settings at their defaults
for now; the import brings the Java index's settings across.

## 2. Import the Java index

The importer reads the Java FDP's PostgreSQL tables and writes them into
the new index, keeping identifiers, so links to entries keep working:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm server \
  fdpindex import-java postgresql://fdp:…@old-host:5432/fdp --dry-run
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm server \
  fdpindex import-java postgresql://fdp:…@old-host:5432/fdp
```

| Comes across | Notes |
|---|---|
| Entries, with their identifiers, states and permits | entries already in the new index are skipped |
| The event log | unfinished events are closed, so old webhook deliveries are not replayed; `--no-events` leaves the log behind |
| Settings, including the deny list | only while the new index still runs on its defaults |
| Webhooks, with their secrets and filters | |

The harvested metadata is not copied: the new index harvests every
accepted entry again on its first checks, within minutes.

Run it again, without `--dry-run`, just before switching over: entries
registered in the meantime are added; the others are skipped.

A Java index keeps the same FDP under several URLs when they differ only
in `http`/`https` or a trailing slash, and the importer copies them as they
are. Merge them afterwards (Administering, *FDPs registered more than
once*).

## 3. Switch over

Pick one:

- **Keep the address.** Point the old index's address (DNS or the reverse
  proxy) at the new index. FDPs keep pinging the address they know.
- **New address.** Ask FDP operators to add the new ping endpoint. A Java
  FDP lists its endpoints in `application.yml`:
  ```yaml
  ping:
    endpoints:
      - https://index.example.org/
  ```
  An FDPneo server uses `FDP_INDEX_PING_TARGETS` or its admin API
  ([with-fdpneo.md](with-fdpneo.md)).

Then set `FDP_INDEX=false` on the Java FDP, so it serves as an FDP only.

## What changes for FDP operators

Nothing is required. They may notice that:

- the index now checks their FDP itself (once a day by default), so it stays
  *Active* while it answers, even between pings;
- their metadata becomes searchable on the index and through SPARQL;
- the Register page can register an FDP and show what the index found.
