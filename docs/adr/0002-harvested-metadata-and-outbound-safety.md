# ADR-0002: Harvested metadata in PostgreSQL; restricted outbound requests

- Status: accepted
- Date: 2026-11-17

## Context

The reference index stores every harvested node in the FDP's triple store,
in a named graph per source FDP, and serves it back at
`/index/entries/{uuid}/data`. Its harvest is wipe-and-recrawl: on every ping
of an accepted entry it deletes the source's graph and walks the FDP again,
following `ldp:contains` from LDP containers, or, when a node has none, the
predicates of a default navigation SHACL (`r3d:dataCatalog`,
`fdp:metadataCatalog`, `dcat:dataset`, `dcat:distribution`,
`dct:isPartOf`).

Every retrieval and harvest is triggered by an unauthenticated ping, and the
reference follows whatever URL it is given, including redirects, without
limits on size or depth.

## Decision

### Storage

Harvested metadata lives in PostgreSQL, in `harvested_node`: one row per
visited node (entry, node IRI, N-Triples, content hash, harvest time). The
service needs no triple store. A harvest replaces the entry's rows in one
transaction, so readers never see a half-replaced graph. `/data` serialises
the union of an entry's nodes as Turtle, N-Triples or JSON-LD.

Querying the harvested corpus (SPARQL, search) was out of scope here. ADR-0005
added both, as stores derived from this table.

### Outbound requests

All outbound HTTP (metadata retrieval, harvest, webhook delivery) goes
through one client that:

- refuses URLs matching the deny list (default: `localhost`, as in the
  reference);
- resolves the host and refuses loopback, private, link-local, multicast and
  reserved addresses unless `FDPINDEX_OUTBOUND_ALLOW_PRIVATE=true` (needed for
  a local evaluation stack);
- connects to the address it checked, not to a fresh DNS answer, so a host
  that changes its answer in between (DNS rebinding) cannot point the
  request inward. The pinning happens where the socket is opened: the URL,
  `Host`, the TLS name and the certificate check keep the host name, and
  connections are kept per name, so a connection checked for one name never
  carries another name's request (2026-11-24);
- follows at most 5 redirects, re-checking each target;
- stops reading a response after `FDPINDEX_OUTBOUND_MAX_BYTES`
  (default 5 MiB);
- applies the retrieval timeout from the index settings.

Parsing never makes requests of its own: JSON-LD that names a context by URL
(`@context` or `@import`) is refused, because rdflib would fetch it itself,
past these checks. Inline contexts are accepted. (Found on 2026-11-19.)

The harvester only follows IRIs on the entry's own origin (scheme, host and
port of the client URL), or on other origins an administrator allowed for
that FDP, and runs one harvest per entry at a time. It follows the FDP's own
navigation (LDP containers, or the reference's navigation properties where a
page has none), not other DCAT links.

A harvest is a walk over one or more runs (TASKS 15.7). Each run reads at
most `FDPINDEX_HARVEST_MAX_NODES` pages (default 2000), 50 MiB and 15
minutes, then stores its queue; the next run starts
`FDPINDEX_HARVEST_CONTINUE_SECONDS` later, or when the FDP's `Retry-After`
says. A walk reads at most `FDPINDEX_HARVEST_WALK_MAX_NODES` pages (default
100,000; 50,000 until 2026-11-25). An administrator can raise both for one
FDP. Records are removed
only by the run that ends a walk that reached everything below the pages it
read. Until 2026-11-24 a harvest was a single run, so an FDP larger than the
limit was never held in full.

## Consequences

- One database to back up; the service is cheap to run.
- An FDP whose records live on another origin than its client URL is
  harvested only partly, unless an administrator allows that origin for it.
  That matches how FDPs are deployed; the limit is logged per harvest.
- Local evaluation needs `FDPINDEX_OUTBOUND_ALLOW_PRIVATE=true`; production
  keeps the default.
