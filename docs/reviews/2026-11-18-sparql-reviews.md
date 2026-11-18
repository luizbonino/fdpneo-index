# Reviews of the SPARQL feature, 2026-11-18

Two independent reviews (Fable 5.1) of the code at `9d3b919`: one for code
quality and correctness, one for security. Every finding was fixed in
`8dce62c` (server) and `799ea8d` (client and deployment), except those
listed under "Deferred". Each fix has a regression test where one is possible.

## Code quality and correctness

| # | Finding | Resolution |
|---|---|---|
| H1 | Prefixes after `/` in property paths were not detected, so valid queries got a 400 | Detection fixed in the server and the editor; tests for sequence and inverse paths |
| H2 | A cancelled query lost its worker; two cancellations hung the pool | The worker is replaced on any abnormal exit; test cancels twice, then queries |
| M1 | `/index/sparql/labels` returned 500 for about 270 long IRIs | Batches of 100; internal queries bypass the user-facing size cap; 400, not 500 |
| M2 | Blank nodes made unchanged nodes "changed" on every harvest | Canonical blank nodes, scoped per node; test harvests twice and expects no change |
| M3 | A failed sync pass dropped pending work | A failure re-queues a full reconcile, which is cheap because fingerprints are compared |
| M4 | The rdflib gate ran on the event loop; `/validate` was not rate limited | The gate runs in a thread; `/validate` has its own budget |
| M5 | The schema cache mixed the live store and the snapshot | One snapshot, used as the cache key |
| M6 | The entries graph was rewritten and a snapshot published on every retrieval | Unchanged facts are skipped; no flush per write; a periodic tick handles time-based states |
| L1–L3 | Stale snapshot after a long queue wait; `close()` during a query; an idle death blamed on the next query | Snapshot chosen after acquiring a worker; bounded queue wait; safe removal; idle deaths replaced quietly |
| L4–L7 | `format` form field; unrestricted dataset graphs; XML `xsd:string`; inflated suggestion counts; subquery `LIMIT` | All fixed |
| L8–L12 | History validation; builder row keys; accessibility; chart kind; unused export and prefix drift | All fixed |
| L13–L14 | Unbounded limiter table; unreliable `idle` | Least-recent eviction; `idle` ignores pending VoID refreshes |

## Security

| # | Sev. | Finding | Resolution |
|---|---|---|---|
| 1 | High | `X-Forwarded-For` spoofable: rate limits bypassed, addresses forged | nginx overwrites the header with `$remote_addr`; re-probed: 30 requests, then 429 |
| 2 | High | Gate parsing on the event loop; `/validate` unlimited | Gate in a thread; `/validate` rate limited |
| 3 | High | A malicious FDP can freeze the loop and fill storage; `/data` re-parses per request | Parsing and serialisation in threads; harvest capped by bytes (50 MiB), time (15 min) and per-node timeout (20 s); `/data` streams stored N-Triples, renders other formats in a thread, rate limited; the store loads node by node |
| 4 | Med | One address saturates the worker pool | One in-flight query per anonymous address; one worker kept free for administrators and internal lookups |
| 5 | Med | `javascript:` / `data:` IRIs became links; no CSP | Only `http(s)` IRIs are links; CSP on the app pages; the security headers now actually reach the app locations |
| 6 | Med | DNS-rebinding window; 100.64.0.0/10 not blocked | Connections pinned to the vetted address (Host and SNI kept); 100.64.0.0/10 and 192.0.0.0/24 blocked |
| 7 | Med | `SERVICE` blocked by one parser only, and pyoxigraph federates | A second, independent check in the worker, after `\u` unescaping |
| 8 | Low | Open redirect after sign-in (`//evil`) | Same-origin check by URL resolution (`//evil`, `/\evil` refused) |
| 9 | Low | Admin names in anonymous event outcomes | "Triggered by an administrator" |
| 10 | Low | `Host` reflected in the service description and config | Uses `FDPINDEX_PUBLIC_URL` when set |
| 11 | Low | `/labels` 500 | As M1 |
| 12 | Low | CSV/TSV formula injection | Client downloads neutralise formula-like cells; the API stays spec-conformant |
| 13 | Low | Limiter table unbounded | As L13 |
| 16 | Low | `/readyz` leaked database errors | Fixed message, detail logged; no longer proxied publicly |
| 17 | Info | No request body limit without nginx | 1 MiB limit in the app |
| 19 | Info | Sync graph readable via `default-graph-uri`; LIKE wildcards | Graphs restricted; wildcards escaped |

## Deferred

- **Security 3, partly.** The number of entries created per address per day is
  not capped. With auto-permit on, which is the default, anyone can register
  FDPs. Turning auto-permit off is the operator's control; a per-origin cap is
  a candidate for TASKS 4.6.
- **Security 4, partly.** The rate limit counts requests, not seconds spent.
  The one-at-a-time rule and the reserved worker cover the measured attack.
- **Security 7, partly.** Worker processes are not sandboxed from the network
  (`unshare -n` or seccomp). The two independent checks stand in for that.
- **Security 14.** There is no separate job pool for harvests. The new
  per-harvest time budget and per-node timeout bound how long slow FDPs can
  hold a slot.
- **Security 15.** Requests with an unknown OIDC `kid` can force a JWKS
  refetch on every request. There is now a warning when no audience is set.
- **Security 18.** CI actions and base images are pinned by tag, not by
  digest.
- **Security 19.** Webhook secrets are stored in plain text; the
  reference-compatible SHA-1 signature is kept.
- **Quality test gaps.** There are no component tests for the editor, the
  combobox, or the page's URL state. The pure functions and the end-to-end
  flow are tested.
