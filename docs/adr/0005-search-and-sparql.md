# ADR-0005: Search and SPARQL over the harvested metadata

- Status: accepted
- Date: 2026-11-18
- Amends: ADR-0002 ("querying the harvested metadata is out of scope")
- Design: [docs/design/search.md](../design/search.md)

## Context

The index harvests the metadata of every accepted FDP into PostgreSQL
(ADR-0002) but offers nothing to query it with beyond a per-FDP download.
Finding data across FDPs is the point of a directory. Users need faceted
search, and a SPARQL endpoint and UI are a requirement, not an extra.

## Decision

1. **PostgreSQL stays the source of truth.** `harvested_node` holds what was
   harvested. Harvests upsert by node IRI and report what was added, changed
   or removed, so derived stores update incrementally.
2. **SPARQL runs on an embedded pyoxigraph store derived from PostgreSQL.**
   - It has one named graph per accepted FDP, named by its client URL.
   - `<urn:fdpindex:entries>` describes the FDPs themselves: state, title and
     country.
   - `<urn:fdpindex:void>` holds VoID partitions with counts.
   - A background loop syncs the store per FDP by fingerprint. A fresh volume
     catches up at start-up. The store is never backed up.
   - The sync fingerprints are kept inside the store rather than in a
     PostgreSQL table, which differs from the design. This makes the store a
     self-contained per-replica cache.
3. **The endpoint is on by default for anonymous callers, and bounded.**
   - Every query passes a structural gate (rdflib). Only SELECT, ASK,
     CONSTRUCT and DESCRIBE are accepted; `SERVICE` is rejected, and update
     forms do not parse.
   - Queries execute in killable worker processes, because pyoxigraph cannot
     cancel a query.
   - Workers never open the store being written. Opening it read-only while
     another process writes is undefined behaviour: in testing, a reader hit
     files that compaction had just deleted. After each sync pass the writer
     publishes a hard-linked checkpoint (`Store.backup`), and workers read the
     newest one. Each checkpoint is a consistent view of a whole pass. Old
     checkpoints are removed after a grace period longer than any query
     budget.
   - Limits: a wall-clock budget (10 s anonymous, 60 s for administrators), a
     row cap, a byte cap, a memory ceiling, and a per-address rate limit.
4. **Parameters in the query library are bound with a `VALUES` block** in the
   outermost group, never pasted into the text. The expanded query is plain
   SPARQL that runs in any endpoint.
5. **Faceted search is a projection into PostgreSQL**: tables, full-text
   vectors and facet values (design §3). It is built after the SPARQL
   endpoint.
6. **Only new paths are added**: `/index/sparql*` now, `/index/search*`
   later. The reference `/index/*` contract is unchanged.

## Consequences

- The server needs a writable directory for the store (`FDPINDEX_SPARQL_DIR`,
  a volume in Compose). It holds `store/` and `snapshots/`, which must be on
  the same file system so that checkpoints are hard links. Only one process
  may open the store for writing, so run one server process per volume.
- Memory rises with the corpus: RocksDB caches, plus the workers' ceilings.
- The SPARQL view lags PostgreSQL by the sync debounce (about a second), and
  the VoID counts lag by up to a minute.
