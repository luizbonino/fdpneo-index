# Metadata health and complete harvests (TASKS 15.6–15.11)

Status: reviewed (2026-11-24); the decisions are in §5. 3.1, 3.2, 3.3 and
3.7 are built (TASKS 15.7, 15.8, 15.12, 15.13). Updated with ERDERA's onboarding benchmark (3.3,
3.4) on 2026-11-24, and with what an FDP promised (3.7) on 2026-11-25.

## 1. Why

[FDPcrawleR](https://github.com/vodor001/FDPcrawleR) (Vodorezova, Cámara,
Benis, Waagmeester, Wilkinson and Cornet, *FDPcrawleR: A Lightweight R
Framework for Auditing FAIR Data Points and FAIR Virtual Platforms*,
SWAT4HCLS 2026) crawls every FDP in the ERDERA Virtual Platform's index and
shows how far each one's metadata reaches: FDP → catalogue → dataset →
distribution → data service. Of the 25 FDPs it found, only 2 reached data
services, and the Virtual Platform finds its Beacon endpoints only there. The
other 23 are "structurally valid yet functionally invisible". The authors plan
"a real-time online FDP monitoring system composed of FDPCrawleR and a
metadata validator", which tells data stewards when an update breaks an FDP
and tests new FDPs before they are registered.

ERDERA has since made its requirements formal. Its **Onboarding Benchmark**
([FAIRsharing.2d49c0](https://fairsharing.org/FAIRsharing.2d49c0), in review
at FAIRsharing, maintained by Mark Wilkinson) is an OSTrails benchmark: a set
of metrics, each tested by a service on `tests.ostrails.eu`, run by FAIR
Champion. It measures a record's onboarding level, cumulatively:

| Level | Metric | Requires |
| --- | --- | --- |
| L1 | [FAIRsharing 8034](https://fairsharing.org/8034) | `w3id.org` terms, not the old EJP PURLs; `VPDiscoverable`; `dcat:theme`, `dcat:contactPoint`, `dct:description`, `dcat:keyword`, `dct:language`, `dct:license`, `dct:publisher` (a `foaf:Agent` with a `foaf:name`), `dct:title`, `dcat:landingPage` |
| L2 | [FAIRsharing 8035](https://fairsharing.org/8035) | L1; typed as `dcat:DataService`; a `dcat:landingPage` |
| Core quality | [FAIRsharing 9483](https://fairsharing.org/9483) | not public yet (in review) |

One of the L2 tests is FDPcrawleR itself, as the test
[`FDP_structural_completeness`](https://ostrails.github.io/assessment-component-metadata-records/test/FDP_structural_completeness.ttl)
(0.1.2): it "checks FDPs in an FDP Index for a structural completeness of
their metadata … (FDP -> Catalog -> Dataset -> Distribution -> Data
Service)". So a network now publishes what it requires in a public registry,
in a common vocabulary (FAIR Testing Resources, `ftr:`), with a generic
runner. The index can use that without knowing anything about ERDERA.

That is close to what this index already is. It harvests every accepted FDP,
keeps a report of each harvest, checks every record against SHACL profiles,
keeps daily statistics and sends webhooks. What it lacks is the view
FDPcrawleR gives: how complete each FDP's metadata is and where it breaks,
for the FDP's maintainer and summed over the network.

The index must not become an ERDERA tool. "Complete" differs per network:
ERDERA needs data services with an endpoint; another network may be content
with datasets that have a landing page. So the findings are generic, and what
a network expects is configuration.

## 2. What exists

- **Each harvest's report** (the `Harvest` event): pages fetched, added,
  changed, removed, not modified, failed (with the reason for each, the first
  20 kept), `limitReached`, `skippedOffOrigin`, the time taken. Admin-only,
  on the FDP's event timeline. Nothing sums it.
- **Each harvested page** keeps when it was fetched, its hash, ETag and
  Last-Modified, and whether it is stale (kept from an earlier harvest because
  the last fetch failed). The harvest sends conditional requests with them.
- **Removals only after a complete walk**: a record missing from a harvest
  that stopped early or had failures is kept.
- **Profile checks** (conformance packs): every record against DCAT-AP,
  HealthDCAT-AP and the others an administrator activates; per FDP and in
  search. Whether visitors may read the reports is a setting (`reports`,
  public by default).
- **Approval of new FDPs**, as in the reference: `autoPermit` accepts every
  ping; otherwise a new FDP waits for an administrator, or for valid metadata
  with `verifiedPermit`.
- **Webhooks** on new entries, pings and state changes (valid, invalid,
  unreachable, expired).
- **Statistics**: records by type, FDPs by state, conformance shares, daily
  history.
- Fixed while preparing this note: one FDP is one entry whatever variant of
  its URL it is registered under (migration 0017).

### The harvest's limits today

Per FDP and per harvest: 2,000 pages, 50 MiB of metadata, 15 minutes, 20 s
per page, 5 MiB per response, and only pages on the FDP's own site (scheme,
host and port). They came from the security review of 2026-11-18: a ping is
unauthenticated, so without them a site generating endless pages could hold a
worker forever, and a registered "FDP" could make the index crawl someone
else's site.

On the copy of home.fairdatapoint.org's FDPs (71 harvested), 2 hit the
2,000-page limit and are held only in part: `fdp.lovd.nl` (1,997 pages after
474 s and still going, about 4 pages a second) and
`ehdsfdp.healthdataportal.eu` (1,972 pages and 28 refused with HTTP 403). 5
FDPs had 20 links to other sites left out, and 11 had pages that failed.

### How the harvest walks an FDP

As the FDP specification says: through each page's LDP containers
(`ldp:contains`). A page without containers falls back to the navigation
properties of earlier FDP versions (`r3d:dataCatalog`, `fdp-o:metadataCatalog`,
`dcat:dataset`, `dcat:distribution`). DCAT links that are not part of the FDP's
navigation are not followed (decision 6).

## 3. Design

### 3.1 Complete harvests at a bounded cost

The limits should stop abuse, not cut real FDPs short. A harvest becomes a
**walk** that may take several **runs**: each run is bounded, the walk is not.

- **Each run has a budget** (pages, bytes, time; today's limits). When it is
  spent, the run stores the pages it read and the queue of pages still to
  read. The next run carries on from that queue.
- **A walk that is not finished continues soon**: its next run is due a few
  minutes later (default 10), not after the FDP's harvest interval. It queues
  behind FDPs that are due, so a large FDP never holds up small ones. A
  finished walk waits for the interval, as now.
- **Revisiting is cheap.** The index sends `If-None-Match` and
  `If-Modified-Since`; an unchanged page answers `304 Not Modified` with no
  body. A large FDP costs its full size once; after that mostly 304s.
- **Breadth first**: the FDP, its catalogues and their datasets are read
  before distributions and data services, so the levels most people search
  are the freshest when a walk is in progress.
- **Polite to the FDP's server**: one request at a time per FDP, and a
  `429` or `503` with `Retry-After` ends the run there and sets when the next
  one may start.
- **Removals still wait for a complete walk**, over all its runs. A page that
  fails keeps its stored version and is retried by the next walk; one failed
  page no longer holds up removals elsewhere in the FDP.
- **A ceiling per walk** stays, so a site generating pages endlessly cannot
  keep a worker forever: 100,000 pages by default (raised from 50,000 on
  2026-11-25: `fdp.lovd.nl` has about 69,000). An administrator can raise
  the budget per run or the ceiling for an accepted FDP, or allow it another
  site (for example a separate host for distributions), on its page, like the
  harvest interval today. FDPs that only pinged keep the defaults.
- **The index's own stops are reported as such**: "walk in progress: 6,000
  pages so far, continues at 14:10", "a link to another site was not
  followed", "the walk reached its ceiling". Never as the FDP's fault.

`fdp.lovd.nl`, if it has 10,000 pages, is then complete after five runs over
about an hour and a half the first time, and later walks are mostly 304s.

### 3.2 Built in: what the index found

Computed after each walk from the pages it read, for every deployment.
Nothing here depends on a project.

What is shown:

| Part | Meaning |
| --- | --- |
| Reach | The deepest level reached, per FDP (FDPcrawleR's drop-off), and pages per level by the `rdf:type` of the page's subject |
| Failures | Pages that could not be read, by kind: not found, other HTTP errors, timed out, not readable as RDF, too large, refused by the index's rules, connection failed |
| Stops | The index's own: links to other sites not followed, pages left unread at the walk's ceiling |

**Findings** rest on what every FDP claims by registering as one, the FDP
specification; they make an FDP's status "has findings":

| Finding | Meaning |
| --- | --- |
| Linked only in DCAT | A DCAT link (`dcat:dataset`, `dcat:distribution`, `dcat:service`, `dcat:accessService` …) to a page on the FDP's site in no LDP container and not described inline: the harvest does not follow it, so the record is not in the index |
| Listed only in an LDP container | A child in an LDP container that its parent does not link with the container's member relation (`dcat:dataset`, `fdp-o:metadataCatalog`, or the FDP's own, such as `adms:sample`) |
| Catalogue without datasets or services | A dead end |

**Notes** are DCAT's advice, which an FDP never promised (DCAT makes
distributions optional). They are shown with their fix, and counted on the
Statistics page, but do not change the FDP's status (decision 7):

| Note | Meaning |
| --- | --- |
| Dataset without distributions | No `dcat:distribution` and no distribution among its children |
| Distribution without access | No `dcat:accessURL`, `dcat:downloadURL` or `dcat:accessService` |

An empty LDP container is neither: FDP software makes a container for every
kind of child a page may have (on 70 FDPs on 2026-11-25, 56 had one).

On those 70 FDPs (2026-11-25): 46 with nothing found and 24 with findings
(all of them children listed only in a container); notes on 24 (datasets
without distributions) and 7 (distributions without access).

Each finding comes with a sentence on how to fix it, written for the FDP's
maintainer (the data holders' guide gets a section with the same text).

**What is public.** The harvest reads only what any visitor can read, so the
findings say nothing a visitor could not find out. They are public, under the
same `reports` setting as conformance, for deployments that want them
admin-only. Failures are shown by kind only. The full error text stays in the
admin-only event: it can describe the index's own network, for example the
address a name resolves to (see §4).

### 3.3 Configured: a network profile

A network states what it requires in a **network profile**: one document with
a fixed structure, so every network presents its requirements the same way.
The documentation ships the template, its JSON Schema, a generic default and
ERDERA's as a worked example. An administrator loads a profile on a settings
page (stored in the database, like the search profile) or from a file at
deployment. Visitors see the active profile on an "Expectations" page, so a
maintainer knows what the index checks before registering.

```yaml
# network-profile.yaml: illustrative; the real values come from ERDERA's metadata schema
profile: 1                          # template version
network:
  name: ERDERA Virtual Platform
  homepage: https://…
  contact: mailto:…
  description: Rare-disease resources found by the Virtual Platform.

reach: dataservice                  # fdp | catalog | dataset | distribution | dataservice

levels:                             # per level: properties required and recommended
  catalog:
    requires: [dct:title, dct:publisher]
  dataset:
    types: [dcat:Dataset, ejp:Biobank, ejp:PatientRegistry]   # what counts as this level
    requires: [dct:title, dcat:theme]
  distribution:
    requires: [dcat:accessURL]
  dataservice:
    requires: [dcat:endpointURL]
    recommends: [dcat:endpointDescription]

shapes:                             # record rules in SHACL: conformance packs
  - https://…/erdera-shapes.ttl

vocabularies:                       # optional: where values must come from
  dcat:theme: [http://www.orpha.net/ORDO/]

benchmarks:                         # the network's own verdict, run outside the index (3.4)
  - https://ostrails.github.io/assessment-component-metadata-records/benchmark/ERDERA_Onboard.ttl
```

- `reach` and `levels` become the health report's requirements ("does not
  reach data services"; "3 data services have no `dcat:endpointURL`");
  `recommends` become recommendations, which do not count. As built they
  are checked on the stored pages with the findings, not turned into SHACL:
  cheaper, and the same for every record.
- `shapes` are conformance packs, as DCAT-AP is one today.
- `benchmarks` name FAIR Testing Resources benchmarks (or single tests) by
  IRI. The index runs them as 3.4 says and shows their verdict; it does not
  read their rules.
- The default profile expects nothing: no reach, no requirements, no
  benchmarks (as built, 2026-11-25; the first draft had `reach: dataset`,
  which the FDP specification does not ask for).
- The EHDS deployment profile, which is code today, could move to this form
  later.

**Rules in the profile, verdict in the benchmark.** ERDERA's L1 is the same
kind of rule as `levels.requires`, so the two could repeat each other. They
do different jobs:

- The profile's rules (`reach`, `levels`, `shapes`) run inside the index, on
  every record of every FDP after each walk, at no cost to anyone else. They
  feed the findings on the FDP's page and the sums on the Statistics page,
  and the check before registering.
- A benchmark is the network's authoritative verdict, run by the network's
  own services. It is called per FDP, at most daily (3.4), not per record and
  harvest: thousands of records a walk would load those services, and send
  them every record's IRI.

To write a rule once, a network publishes it as SHACL and lists the file
under `shapes`; its benchmark's tests can use the same file. Where a network
has only a benchmark, its profile may hold just `benchmarks` and the
index's built-in findings still apply.

### 3.4 Triggering other tools

A FAIR assessment, or a network's own tests, is a separate tool. The index
starts it and shows its result; it does not contain it.

**First case: a FAIR Testing Resources benchmark.** The network profile
names a benchmark (3.3), such as ERDERA's Onboarding Benchmark. The index
reads its description (a `ftr:Benchmark`, with `ftr:supportedBy` naming the
runner, here FAIR Champion), asks the runner to apply it to an FDP, and
keeps the result. Nothing in this is specific to ERDERA: any community with
a benchmark in FAIRsharing plugs in the same way. How a runner is called
(its API, and whether it takes the FDP's URL or a record's) is confirmed
with FAIR Champion when 15.10 starts.

Two things the published records do not give yet, which the index needs:

- The benchmark's metrics are listed only in its description text, not as
  triples, so a machine cannot follow from the benchmark to its metrics and
  tests. Until they are, the runner is the only reader of that link.
- ERDERA's levels are per record (a dataset, a data service), while the
  index triggers per FDP. Which records the runner tests for one FDP (the
  root, every record, a sample) is part of the same conversation.

**Other tools**, such as a FAIR assessment service:

- An administrator registers a tool: its name, the URL the index calls, when
  (a new FDP, a walk that changed something, a schedule, by hand) and a
  secret. The list is empty by default, as each call sends the FDP's URL to
  that tool.
- The index calls the tool with the FDP's URL and entry. The tool answers at
  once, or later at a callback URL with a result: outcome (pass, fail,
  indeterminate, or a score), summary, link to the full report, time. FAIR
  Testing Resources results (`ftr:TestResult`) are read as they are, so
  OSTrails tests fit without an adapter.
- A call that failed (HTTP error, timeout) is kept apart from a test that
  failed, as FDPcrawleR does. At most one call per day per FDP and tool.
- Results show on the FDP's page beside the findings; who may see them is
  set per tool.

The webhooks already tell other services about new and changed FDPs; a
"walk finished, metadata changed" event is added to them too, for tools
that only listen.

### 3.5 Where it shows

- **The FDP's page**: a "Metadata health" section, combining the reach (as a
  small level-by-level line), the findings with their fixes, the profile
  results and the external tools, and the walk's progress or the index's
  stops.
- **Statistics**: FDPs by reach (the drop-off as a flow or stacked bar), FDPs
  that are fine, have findings or cannot be read, counts by finding, failures
  by level, and their trend from the daily snapshots. A table of FDPs with
  their findings, sortable, with CSV download.
- **API**: `GET /index/entries/{uuid}/health`, and the aggregate in
  `GET /index/stats`.
- **Check before registering**: the same checks on a URL without adding it to
  the index, with a smaller budget (200 pages). Who may run it follows the
  approval policy, which an administrator can override:
  - with `autoPermit` (anyone's FDP is accepted), anyone, under the ping
    rate limit;
  - with approval required, administrators, from the page of a pending FDP,
    so they review an FDP with its health in front of them.

### 3.6 Provenance

The provenance proposal (`provenance.md`) publishes each harvest's report in
PROV-O. The health findings are computed from the same reports, so the two
share their storage: the walk's record holds what was read and what failed;
the health view is derived from it.

### 3.7 What an FDP promised: declared, required, and a lens

The conformance packs (TASKS 11.x) check every record against the profiles an
administrator activates, and DCAT-AP 3.0.1 is active by default. So the index
judges FDPs against a profile none of them promised to meet. Most FDPs do say
what they meet: on the copy of 70 FDPs (60,117 pages, 2026-11-25), 61 FDPs
declare `dct:conformsTo` on their records (about 10,000 pages), most often
their own FDP profile (`/profile/{uuid}`), a few DCAT-AP 3.0.1, HealthDCAT-AP
release 6 or the SPHN schema. An FDP profile is a W3C Profiles Vocabulary
profile (`prof:Profile`, `prof:isProfileOf` its core profile) whose resource
with role `prof:role/Validation` is the FDP's own SHACL
(`/metadata-schemas/{uuid}`); on `fdp.healthdata.nl` that is Health-RI core,
plateau 2.

Three judgements, kept apart, each saying what it rests on:

1. **Declared** (the default when the administrator chose nothing). Each
   record is checked against what it declares: for an FDP profile, the SHACL
   its validation resources name, its core profile's included, fetched from
   the FDP's own site (or an origin allowed for it) under the outbound rules
   and cached by URL and ETag; for a known specification IRI, the bundled
   pack. A record that declares nothing "declares no profile": neutral.
2. **Required by this index**: the packs and network profile (3.3) an
   administrator sets, shown on the Expectations page. A record that misses
   them "does not meet this index's requirements", not "does not conform":
   the FDP chose to join a network whose rules are public.
3. **A visitor's lens**: on an FDP's page, in search and in the Statistics,
   a visitor may look through another profile, among the packs the
   administrator activated (computed in the background), worded "checked
   against X, which this FDP does not claim". Checking any profile on
   request is left out: it means validating up to 100,000 records.

DCAT-AP becomes a pack an administrator activates, not the default. The
testbed shows DCAT-AP results today, so visitors there will see declared
results instead.

## 4. Changes to what exists

- **Data-service links.** On 2026-11-24 (`72cff8f`) the fallback for pages
  without LDP containers gained `dcat:service` and `dcat:accessService`. They
  are not part of the FDP's navigation; per decision 6 they are taken out
  again and reported as "linked only in DCAT". On the real-data copy all 64
  such links were reached through LDP anyway.
- **Error texts.** `POST /index/register` returns the retrieval's error to
  anyone, and the text of a refused address names the address: registering
  a name that resolves to a private address answers "… resolves to a
  non-public address (10.…)". That lets anyone look up names in the index's
  network. The public answer gets the kind only ("resolves to a non-public
  address"); the address stays in the log and the admin-only event.

## 5. Decisions (review of 2026-11-24)

1. **Findings are public.** The metadata is public, so the findings are too;
   failures by kind, not raw error text. The `reports` setting can still make
   them admin-only.
2. **Complete at a bounded cost**: walks that continue over budgeted runs,
   conditional requests, breadth first, polite per FDP, a ceiling per walk
   (3.1).
3. **A network profile template** (3.3), loaded on a settings page or from a
   file, shown to visitors. It may name the network's FAIR Testing Resources
   benchmarks (added 2026-11-24, after ERDERA's Onboarding Benchmark).
4. **The index triggers other tools; it does not contain them** (3.4). The
   FAIR test is a separate tool, built later. The first case is running a
   network's benchmark through its runner (FAIR Champion).
5. **Check before registering follows the approval policy** (3.5).
6. **The harvest follows the FDP specification: LDP.** DCAT links outside it
   are reported, not followed.
7. **Findings rest on what an FDP promised** (2026-11-25): the FDP
   specification. DCAT's advice (datasets without distributions,
   distributions without access) is shown as notes, which do not count
   against the FDP.
8. **Conformance is checked against what a record declares by default**
   (3.7); DCAT-AP and the other packs are opt-in.
9. **Visitors may look through another profile**, among the packs the
   administrator activated (3.7).

## 6. Tasks

- 15.6 This design, reviewed. Done.
- 15.7 Walks that continue over budgeted runs, and per-FDP budgets (3.1);
  the data-service fallback taken out (§4). Done.
- 15.8 The built-in findings, per FDP and summed (3.2, 3.5), with the data
  holders' guide. Done.
- 15.9 Network profiles: the template, its schema, the settings page, the
  "Expectations" page, a generic default and an ERDERA example that names
  its Onboarding Benchmark (3.3). Done.
- 15.10 Triggering other tools and showing their results (3.4), starting
  with a profile's benchmarks run through FAIR Champion; the call agreed
  with its maintainers first.
- 15.11 Check before registering (3.5). Done: the sample takes at most 5
  children of each page, so 200 pages reach every level; a pending FDP's
  page offers it to administrators.
- 15.12 Declared conformance (3.7): follow `dct:conformsTo` to the FDP's
  profile and SHACL, or to a bundled pack; the default when no pack is
  chosen; DCAT-AP opt-in; "declares no profile". Done.
- 15.13 Lenses (3.7): visitors choose among the activated packs on an FDP's
  page, in search and in the Statistics, worded as not claimed. Done: a
  pack is required or a lens (the default).
- The error-text fix (§4) goes first, on its own. Done.
