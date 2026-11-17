# ADR-0004: The index checks registered FDPs itself

- Status: proposed
- Date: 2026-11-17

## Context

The reference index only learns about an FDP when the FDP pings it. An
entry is `ACTIVE` when its last successful metadata retrieval is newer than
the valid duration (default 7 days), and a retrieval only happens after a
ping. An FDP that stops pinging (a misconfigured or removed ping, a firewall
on its outbound traffic) drifts to `INACTIVE` although it is up; an FDP that
goes down between pings stays `ACTIVE` until the valid duration lapses.
Directory users cannot tell the two apart.

## Decision (proposed)

The index runs a scheduled **liveness check** of registered entries, in
addition to ping-triggered retrievals:

- A sweep runs every minute and picks entries whose next check is due. Each
  entry's next check is its last retrieval plus the check interval
  (default 24 hours), with random jitter so checks spread out.
- A check is a metadata retrieval recorded as a `HealthCheck` event; it uses
  the outbound safety rules of ADR-0002 and the retrieval rate limit.
- An entry becomes `Unreachable` only after `failure_threshold` consecutive
  failed checks (default 3); one success makes it `Valid` again. Ping-triggered
  retrievals keep the reference behaviour.
- Webhooks for state changes fire on transitions only, so a long outage does
  not send a notification per check.
- When a successful check finds changed metadata (by content hash), an
  accepted entry is harvested again.
- Settings (in the index settings, with defaults): enabled, interval, failure
  threshold, concurrency, and which permits are checked (default: accepted).

## Open questions

- Should `ACTIVE` keep meaning "retrieved within the valid duration"? With
  checks enabled it effectively means "reachable recently", which is what
  directory users expect; the label stays, its documentation changes.
- Should entries unreachable for longer than a configurable period be hidden
  from the public list (not deleted)? Proposed: off by default.
- Should the check use `HEAD` or a conditional `GET`? Proposed: conditional
  `GET` with `If-None-Match` / `If-Modified-Since` when the FDP sent
  validators; a `304` counts as success without re-parsing.
