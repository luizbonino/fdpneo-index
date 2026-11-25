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
- **Harvest budget**: a harvest reads an FDP in runs of 2 000 pages, a few
  minutes apart, up to 100 000 pages in all (the deployment's defaults). For
  an FDP you trust you can set its own pages per run and per harvest, and
  **other sites** it may read (for example a separate host for its
  distributions, as `https://data.example.org`). While a harvest continues,
  the FDP's page shows how far it got and when the next run starts.

- **Country**: where the FDP is, for the Statistics page's map. Leave it at
  *From the other sources* unless those place it wrongly (below).

Administrators can also add an FDP on the **Register** page, or with
`POST /index/entries` (`clientUrl`, optional `permit` and `options`).

### FDPs registered more than once

The index counts URLs that differ only in `http` or `https`, letter case in
the host, a default port or a trailing slash as one FDP:
`https://fdp.example.org/` and `http://fdp.example.org` are the same entry.
A ping for a variant of a known URL updates that entry. The path, its case
and the query still count, so `https://example.org/fdp` and
`https://example.org/FDP` stay two entries.

Indexes that ran before this rule, and imports from a Java FDP index, can
hold such duplicates already. home.fairdatapoint.org had ten pairs. List
them, and the entry each would keep:

```sh
curl -H "Authorization: Bearer $TOKEN" https://index.example.org/index/admin/duplicates
```

The entry kept is the one that works (a valid FDP before one that failed),
then the one whose URL the FDP itself states, then `https`, then the oldest.
To delete the others:

```sh
curl -X POST -H "Authorization: Bearer $TOKEN" https://index.example.org/index/admin/duplicates/merge
```

A merge deletes the other entries as **Delete** does, with their events and
harvested metadata. It leaves a group alone when its entries differ in
approval or in an administrator's options (country, harvesting, interval),
and says why under `conflicts`: decide those on the entries' pages.

## Where FDPs are

The Statistics page maps the FDPs by country. FDPs rarely say where they
are, so each is placed by the first of these that knows, and the page says
which placed how many:

1. **Set by an administrator**: the *Country* option on the FDP's page.
2. **Stated by the FDP**: `r3d:country` in its own metadata (an ISO code,
   an EU country authority IRI or an English name).
3. **Its publisher**: the country of the publisher's ROR record, when label
   resolution is on (`FDPINDEX_LABELS_RESOLVE`).
4. **Its domain**: a country-code domain (`.nl`, `.de`) of the host that
   answered, after redirects. Domains used as generic ones (`.io`, `.co`,
   `.ai` …) do not count.
5. **Its server**: where the server that answered the last retrieval is,
   after redirects, from DB-IP Lite's country database. A w3id.org address
   counts as the server it redirects to. This is the hosting company's
   country: an FDP run in Leiden on a server in Germany is placed in Germany,
   unless one of the sources above places it first.

FDPs nothing can place (unreachable ones with a generic domain) are counted
apart. The server image includes DB-IP's database of the month it was built
in; rebuild monthly to keep it current:

```sh
GEOIP_MONTH=$(date +%Y-%m) docker compose -f deploy/compose.yaml --env-file deploy/.env build server
```

Without the database (`FDPINDEX_GEOIP_DIR` unset, or a build without network
access) servers are simply not located. The page credits DB-IP, as its
licence (CC BY 4.0) asks.

The page also maps **where the data is about**: records by the country their
`dct:spatial` names, from EU country codes and NUTS regions, each country
linking to a search for its records.

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
- **Default language of titles and labels**: for readers whose browser
  states no language.
- **Fallback languages**: when a title or label is not in the reader's
  language and has no untagged form, these languages in this order (for
  example `nl, en, de` for a Dutch index), then any.

These are stored as `/index/search/settings` and need no re-indexing.

**Save search** on the search page (administrators) keeps the search as
it stands under a title; **Feature it** lists it on the search page for
everyone, before they type anything. **Settings → Saved searches** lists
them to open, rename, feature or delete.

**Settings → Search profile (advanced)** changes which predicates feed
which fields, and can define new ones; a new field with `facet` is a new
filter. It re-indexes every FDP in the background. **Load the HealthDCAT-AP
example** fills the editor with ready filters for health metadata (numbers
of individuals, typical ages, health category, coding systems, and Yes/No
filters such as "Concerns personal data"); nothing changes until you save.
See [search-and-sparql.md](search-and-sparql.md#the-search-profile).

## Conformance

The index checks every harvested record against SHACL *profiles* in the
background, after each harvest, and only the records that changed. A
record is a catalogue, dataset, series or data service (or the FDP's own
description) with the blank nodes and inline descriptions of its page; a
dataset's record includes its distributions. A record **conforms** to a
profile when it has no violations; warnings are recommendations.

**What it declares** (on by default). Each record is checked against the
profiles it names itself with `dct:conformsTo`, not against one it never
promised:

- an FDP profile (the FDP software's `/profile/{uuid}`): the index reads its
  validation resources, the FDP's own SHACL (`/metadata-schemas/{uuid}`),
  and those of the profiles it builds on, from the FDP's own site (or a site
  you allowed for that FDP), and keeps them for a day;
- the specification of a profile below (DCAT-AP 3.0.1, for instance): that
  profile's shapes;
- anything else is *unresolved*, with the reason: on another site, not a
  profile, no shapes, not readable. A record that names nothing *declares no
  profile*, which is not counted against it.

**Profiles this index requires** (off by default; DCAT-AP 3.0.1 was on
until 2026-11-25). Every record is checked against them whether or not it
claims them, and the reports say they are this index's requirements:

| Profile | Applies to | Notes |
|---|---|---|
| `dcat-ap@3.0.1` | FDP roots, catalogues, datasets, series, services | SEMIC's shapes; recommended properties are warnings |
| `healthdcat-ap@release-8` | datasets, series | a draft release; the shapes follow the dataset's `dct:accessRights` (public, restricted, non-public) |
| `health-ri-core@2.0.3` | catalogues, datasets, series, services | the Dutch Health-RI v2 model |

**Settings → Conformance** turns the declared check on or off, chooses the
required profiles and who may read the reports: everyone (the default, like the metadata they describe) or
administrators only. Changing the profiles checks every FDP again in the
background; **Check every FDP again** forces it. The API
(`/index/conformance/settings`) also narrows a profile to some kinds of
record and chooses its files: the range checks (`range.ttl`) of DCAT-AP and
HealthDCAT-AP are off by default, because they need every value's class
and the EU vocabularies type their concepts as `skos:Concept` only, so
correct values would fail.

**What visitors see.** By default every harvested record is shown
everywhere, with its conformance as information: the search has a
**Meets profile** filter and result cards say which profiles a dataset
meets. **Settings → Conformance → What visitors see** can show visitors
only what conforms to one of the checked profiles, per place:

| Place | Strict means |
|---|---|
| Search | hits and filter counts are records with no violations of the profile; records not checked yet are left out too |
| SPARQL | the store leaves out the pages of records that were checked and do not conform (pages not checked yet, and distributions, stay) |
| List of FDPs | only FDPs whose own description conforms; an FDP's page stays reachable by link |
| Catalogue feed | only conforming records are republished (see below) |

Administrators always see everything.

**Reports.** An FDP's page has a **Conformance** section: per profile, its
records and how many conform, and the most frequent problems; **Show
records** lists the records with a problem, each linking to the
resource's page, whose own **Conformance** section lists its findings.
The API: `GET /index/conformance/entries/{uuid}`, `GET
/index/conformance/records?entry=…&pack=…&status=…` (and `path`,
`component`, `severity` for one problem), `GET
/index/conformance/records/{id}`.

What the checks do not judge: descriptions on other pages of the FDP (a
publisher with its own page is not part of the dataset's record), and EU
vocabulary concepts an FDP describes in passing.

**Check a file** against a profile without the index, the way the index
checks a harvested record:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm \
  -v "$PWD/dataset.ttl:/tmp/dataset.ttl:ro" server \
  fdpindex conformance validate /tmp/dataset.ttl --pack healthdcat-ap
```

It prints the violations, warnings and infos, and exits with 1 when there
are violations. **Your own profiles** go in a directory named by
`FDPINDEX_CONFORMANCE_PACKS`, one folder per pack and version
(`my-rules/1.0/pack.yaml` beside the shapes files; the bundled packs under
`server/src/fdpindex/conformance/packs/` are examples). `fdpindex conformance
packs` lists every pack and checks its files against its `SHA256SUMS`;
`fdpindex conformance fetch my-rules` downloads a pack's files again from the
`source.download` its manifest names.

## An index for the European Health Data Space

`FDPINDEX_DEPLOYMENT_PROFILE=ehds` starts the index with defaults for
health metadata, all of which stay settings you can change, and **Reset to
defaults** returns to:

- **Conformance**: HealthDCAT-AP Release 8 and DCAT-AP 3.0.1 checked, in
  that order; the catalogue feed republishes only records that conform to
  HealthDCAT-AP (Regulation (EU) 2025/327, Articles 77 and 79), the rest of
  the index shows everything.
- **Search**: the HealthDCAT-AP extension (the example under **Settings →
  Search profile**): health category, numbers of individuals, typical
  ages, coding systems, health data access body, and **Quality
  certificate**, a filter and a line on dataset cards for datasets whose
  holder states a `dqv:QualityCertificate`, with the certificate documents
  on the resource's page.

EHDS Article 78's quality and utility label is expected to be expressed as
such a certificate; until its implementing act is out, the index shows
what data holders state and does not check certificates.

## The catalogue feed

The index republishes what it harvested as a DCAT-AP catalogue, for
aggregators such as data.europa.eu:

- `GET /index/catalog`: a DCAT-AP 3 catalogue in Turtle, RDF/XML (`?format=rdf`),
  JSON-LD or N-Triples, 100 records a page (`size` up to 500), with Hydra
  paging (`hydra:next`, and a `Link: rel="next"` header);
- `GET /index/oai`: the same records over OAI-PMH 2.0, metadata format
  `dcat_ap`, which data.europa.eu's harvester prefers (`ListRecords`).

Each harvested catalogue, dataset, series and data service is one
`dcat:CatalogRecord` under the index's own URL
(`/index/catalog/records/<id>`, the id the search uses), pointing at the
resource under its own IRI (`foaf:primaryTopic`), at the FDP document it came
from (`dct:source`), at when the index last saw it change (`dct:modified`),
and at the checked profiles it conforms to (`dct:conformsTo`). The
resource's triples follow as the FDP published them, with its
distributions'; the index does not rewrite them, and a dataset several FDPs
list is several records. **Settings → Conformance → What visitors see →
Catalogue feed** republishes only what conforms to a profile.

Before registering the feed with an aggregator, set the catalogue's title,
description, publisher and contact address (`FDPINDEX_CATALOG_*`, see the
README) and `FDPINDEX_PUBLIC_URL`, which the record IRIs are built from.

## Languages

The interface is translated into Dutch, German, French, Spanish, Italian
and Portuguese; each visitor picks a language, or gets their browser's.
What administrators write is per language too: the labels and help of
search filters (**Settings → Search filters**, one text per language, and
the search profile's `label` and `help` maps, which the bundled profile
fills in all seven languages). A text missing in the reader's language
shows in English.

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
