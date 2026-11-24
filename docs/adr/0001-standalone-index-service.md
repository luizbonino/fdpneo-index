# ADR-0001: The FDPneo Index as a standalone service, compatible on the wire

- Status: accepted
- Date: 2026-11-17

## Context

In the Java FAIR Data Point, the index is a mode of the FDP itself
(`FDP_INDEX=true`). An index deployment therefore carries a full metadata
repository, its triple store, users, schemas and resource definitions, although
it only needs a list of registered FDPs, their state and their harvested
metadata. The two products also share a release cycle: a fix to the index
means releasing the FDP.

FDPneo implements only the sending side: it pings the indexes it is configured
with (FDPneo ADR-0025). FDPneo ADR-0021 names the eventual product
**FAIR Discovery** and keeps "index" as the protocol vocabulary; it describes
two tiers, a directory tier (registration, ping intake, reachability, a
browsable directory) and a harvest tier (validated, incremental harvesting
with search). This service is the directory tier, plus the reference index's
harvest of each accepted FDP.

## Decision

1. **A standalone service** in its own repository, `fdpneo-index`, with a server
   (`server/`, Python, FastAPI) and an administration client (`client/`, Vue),
   on the same stack and conventions as FDPneo. It does not depend on FDPneo
   packages: it only speaks HTTP to FDPs.
2. **Wire compatible with every FDP.** Ping intake stays
   `POST /` with `{"clientUrl": "…"}`, `204` when accepted, `400` when the
   body is invalid, `403` when the URL is on the deny list, `429` when
   rate-limited. FDPneo and Java FDPs register without any change.
3. **API compatible with the reference client.** The administration API keeps
   the reference paths and JSON shapes: `/index/entries` (paged as Spring
   pages: `content`, `totalElements`, `totalPages`, `number`, `size`),
   `/index/entries/all`, `/index/entries/info`, `/index/entries/{uuid}`,
   `/index/entries/{uuid}/data`, `/index/settings`, `/index/admin/trigger`,
   `/index/admin/trigger-all`, `/index/admin/ping-webhook`. Durations are
   ISO-8601 strings, written the way Java's `Duration.toString()` writes them
   (`PT168H`). Additions are new paths or new fields, never changed ones.
4. **Behaviour ported faithfully where it is observable**: entry states
   (`UNKNOWN`, `ACTIVE`, `INACTIVE`, `UNREACHABLE`, `INVALID`), permits
   (`PENDING`, `ACCEPTED`, `REJECTED`), auto-permit, the deny list, the per-IP
   ping rate limit, the retrieval rate limit, the extracted repository
   metadata, the event log, and webhook payloads and signatures.
5. **Deliberate differences**, each documented where it applies:
   - Outbound requests are restricted (ADR-0002): the reference fetches any
     URL a ping names, which makes an unauthenticated ping a server-side
     request forgery vector.
   - The harvester stays on the FDP's own origin and has limits (ADR-0002).
   - Webhooks gain a management API and an HMAC-SHA256 signature next to the
     reference SHA-1 one.
   - Administrators authenticate with OIDC or static tokens (ADR-0003); the
     index has no user database of its own.
   - Repository detection also accepts the FDP-O namespace FDPneo moved to
     and the legacy one (FDPneo ADR-0026).
   - One FDP is one entry: a ping for a URL that differs from a known one
     only in `http`/`https`, letter case in the host, a default port or a
     trailing slash updates that entry, where the reference adds another
     (`fdpindex.urls`; 2026-11-24).

## Consequences

- A Java FDP running in index mode can be replaced by fdpindex without touching
  the FDPs that ping it. An importer moves its entries, events, settings and
  webhooks (TASKS 3.4).
- The reference client keeps working against fdpindex; the new client
  replaces it with a better administration experience.
- Harvested metadata was at first meant to be searched elsewhere, in the
  harvest tier of FAIR Discovery (FDPneo ADR-0021). ADR-0005 brought faceted
  search and SPARQL into this service. Since 19 November 2026 this service is
  FAIR Discovery, built standalone instead of inside FDPneo (TASKS 5.4).
