# ADR-0004: The index checks registered FDPs itself

- Status: accepted (implemented with the proposed defaults; open questions below remain open)
- Date: 2026-11-17

## Context

The reference index only learns about an FDP when the FDP pings it. An
entry is `ACTIVE` when its last successful metadata retrieval is newer than
the valid duration (default 7 days), and a retrieval only happens after a
ping. An FDP that stops pinging (a misconfigured or removed ping, a firewall
on its outbound traffic) drifts to `INACTIVE` although it is up; an FDP that
goes down between pings stays `ACTIVE` until the valid duration lapses.
Directory users cannot tell the two apart.

## Decision

The index runs a scheduled **liveness check** of registered entries, in
addition to ping-triggered retrievals:

- A sweep runs every minute (`FDPINDEX_HEALTH_SWEEP_SECONDS`) and starts a
  check for each entry whose next check is due, up to `FDPINDEX_HEALTH_BATCH`
  per sweep. Every retrieval, scheduled or ping-triggered, sets the next check
  to now plus the check interval (default 24 hours), ±10% jitter. Entries
  with a check already running are skipped, and rows are claimed with
  `FOR UPDATE SKIP LOCKED`, so several replicas can sweep at once.
- A check is a metadata retrieval recorded as a `HealthCheck` event; it uses
  the outbound safety rules of ADR-0002 and the retrieval rate limit.
- An entry becomes `Unreachable` only after `failure_threshold` consecutive
  failed checks (default 3); one success makes it `Valid` again. Ping-triggered
  retrievals keep the reference behaviour.
- Webhooks for state changes fire on transitions only, so a long outage does
  not send a notification per check.
- When a successful check finds changed root metadata (SHA-256 of the
  response), an accepted entry is harvested again. Ping-triggered retrievals
  keep harvesting every time, as in the reference.
- Checks send `If-None-Match` / `If-Modified-Since` when the FDP sent
  validators; a `304` counts as reachable and unchanged.
- Settings (in the index settings, with defaults): enabled (on), interval
  (24 hours), failure threshold (3), which permits are checked (accepted), and
  re-harvest on change (on). Concurrency is the worker pool's
  (`FDPINDEX_WORKER_CONCURRENCY`).

The entry API gains `health` (`nextCheckTime`, `lastSuccessTime`,
`consecutiveFailures`); the events gain the `HealthCheck` type.

## Amendment (19 November 2026): registry policies (TASKS 4.6 to 4.9)

- **Expiry**, off by default: an accepted entry without a successful
  retrieval for `expiry.after` (90 days by default; counted from its
  registration when it was never reached) is marked once, with an `Expiry`
  event and an `EntryExpired` webhook. With `expiry.hide`, expired entries
  leave the public directory (lists and counts) and the search; the SPARQL
  store keeps them. A successful retrieval, after a ping or a check, clears
  the mark. The reference states are unchanged; entries gain `expiredTime`.
- **Per-FDP options**: `harvest: false` keeps an entry in the directory,
  checked, but never harvested (its harvested metadata is removed); a
  `harvestInterval` harvests it again at that interval, even when the root
  metadata did not change, at its checks.
- **Verified approval** (`verifiedPermit`): with auto-permit off, a new
  entry is accepted once a retrieval finds valid FDP metadata.
- **Registration**: `POST /index/register` is a ping whose retrieval runs
  before the answer, under the same deny list, rate limit and approval
  policy; `POST /index/entries` lets administrators add an FDP with a
  permit and options, applied before its first retrieval.

These settle the first open question below (hiding, off by default) and,
with `harvestInterval`, give the periodic re-harvest the third asks for, per
FDP.

## Open questions

- Should `ACTIVE` keep meaning "retrieved within the valid duration"? With
  checks enabled it effectively means "reachable recently", which is what
  directory users expect; the label stays, its documentation changes.
- Should entries unreachable for longer than a configurable period be hidden
  from the public list (not deleted)? Proposed: off by default.
- Change detection looks at the root metadata only; a change deeper in the
  tree (a new dataset) is picked up by the next ping-triggered harvest, or by
  a check when the root's modification date changes with it. A periodic full
  re-harvest (say weekly) would close the gap.
