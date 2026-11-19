# Administering the FDPneo Index

For the people who run an index: approving FDPs, settings, webhooks and
the search. Installing and operating the service is in
[deploying.md](deploying.md) and [operations.md](operations.md).

## Signing in

Administrators sign in on the **Sign in** page, either through the
organisation's identity provider (OIDC; the `admin` role in the token, see
the README) or with an admin token. A token is created on the server:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm server fdpindex token new
```

It prints the token, which you keep, and its digest, which goes into
`FDPINDEX_ADMIN_TOKENS` (comma-separated for several). The index never
stores tokens, only digests.

Signed in, the menu gains **Approvals**, **Settings** and **Webhooks**, and
entry pages gain an administration panel.

## FDPs: approval, options, removal

How new FDPs are accepted is a setting (**Settings → Registration**):

- **Automatically**: listed and harvested as soon as they ping.
- **After a successful check**: accepted once the index has retrieved
  valid FDP metadata from them; until then they wait.
- **By an administrator**: they wait under **Approvals**.

**Approvals** lists the waiting FDPs; accept or reject one by one or in
bulk. Rejected FDPs stay registered but hidden, and their harvested
metadata is removed; accepting one later harvests it again.

An entry's page, for administrators, has:

- **Accept**, **Reject**, **Check now** (retrieve and harvest at once) and
  **Delete** (the entry, its events and its harvested metadata).
- **Harvest its metadata**: off makes the FDP *directory only*. It stays
  listed and checked, but is not harvested, and what was harvested leaves
  search and SPARQL.
- **Harvest again**: by default an FDP is harvested again after each ping
  and when a scheduled check finds its root metadata changed. An interval
  (12 hours, a day, a week) also harvests it on that schedule, for FDPs
  whose datasets change without their root changing.

Administrators can also add an FDP on the **Register** page, or with
`POST /index/entries` (`clientUrl`, optional `permit` and `options`).

## Settings

**Settings** changes apply at once. **Reset to defaults** restores them.

| Group | Setting | Default |
|---|---|---|
| Pings | An FDP stays active for | 7 days after a successful retrieval |
| | Pings accepted per address | 10 per 6 hours; more are answered with 429 |
| Metadata checks | Minimum time between checks of one FDP | 10 minutes |
| | Give up a request after | 1 minute |
| Liveness checks | Check registered FDPs regularly | on, every 24 hours, accepted FDPs |
| | Mark unreachable after | 3 failed checks in a row |
| | Harvest again when an FDP's metadata changed | on |
| FDPs out of contact | Mark FDPs as expired | off; after 90 days |
| | Hide expired FDPs from the public directory and the search | off |
| Deny list | Regular expressions matched against the whole address | `localhost` |

An expired FDP gets an *Expired* badge, an `Expiry` event and an
`EntryExpired` webhook, once. It revives as soon as the index reaches it
again, after a ping or a check. Hiding leaves it visible to
administrators, and its own page stays reachable by link.

The deny list refuses pings (403) from matching addresses; **Test an
address** shows which pattern matches.

## Search filters

**Settings → Search filters** sets the search page's filters:

- Their order (↑ ↓), and for each whether it is a **main** filter, **under
  more filters**, or **hidden**.
- **Add a property as a filter**: any property found in the harvested
  metadata, with its own label, shown to everyone.
- **Visitors may add filters on any property**: the "+ Add a filter"
  button. Properties listed under **Properties visitors may not use** (an
  IRI, or a namespace ending in `/` or `#`) are refused to visitors; the
  filters listed above stay open to everyone.

These are stored as `/index/search/settings` and need no re-indexing.

## Webhooks

**Webhooks** sends a signed POST to an address of yours when something
happens. Choose the events, and optionally the FDPs:

| Event | When |
|---|---|
| `NewEntry` | an FDP pings for the first time |
| `IncomingPing` | any ping |
| `EntryValid`, `EntryInvalid`, `EntryUnreachable` | after a ping-triggered retrieval; after a scheduled check only when the state changed |
| `EntryExpired` | the expiry policy marked an FDP |
| `AdminTrigger` | an administrator asked for a check |
| `WebhookPing` | **Test** on the webhook |

Deliveries carry the Java FAIR Data Point's signature (`X-Signature:
sha1=…`, a SHA-1 of the JSON body with the real secret in place of the
hidden one) and a standard one (`X-Signature-256`, an HMAC-SHA256 of the
delivered body keyed with the secret); check the latter in new receivers.
A delivery without a 2xx answer is tried again after 1, 5 and 30 minutes
and 2 hours. Every attempt is in the event log.

## Search and SPARQL maintenance

- `POST /index/search/reindex` projects every entry (or one, with
  `{"entry": "<uuid>"}`) into the search tables again; needed only after a
  manual change to the database.
- `GET /index/search/stats` reports, per FDP, the resources indexed, their
  average metadata completeness and when they were last projected, and how
  many concept labels were resolved.
- Concept labels from Wikidata, ROR, OLS and other vocabularies are off by
  default; `FDPINDEX_LABELS_RESOLVE=true` switches them on (ADR-0006).
- The SPARQL store is rebuilt from PostgreSQL when its volume is empty; see
  [search-and-sparql.md](search-and-sparql.md).
