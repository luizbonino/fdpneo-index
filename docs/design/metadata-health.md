# Metadata health and complete harvests (TASKS 15.6–15.11)

Status: proposal, for review (2026-11-24). Nothing in §3 is built yet.

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
  the last fetch failed).
- **Profile checks** (conformance packs): every record against DCAT-AP,
  HealthDCAT-AP and the others an administrator activates; per FDP and in
  search. Whether visitors may read the reports is a setting (`reports`).
- **Statistics**: records by type, FDPs by state, conformance shares, daily
  history.
- Fixed while preparing this note: one FDP is one entry whatever variant of
  its URL it is registered under (migration 0017), and the harvest follows
  data services linked only through DCAT.

### The harvest's limits today

Per FDP and per harvest: 2,000 pages, 50 MiB of metadata, 15 minutes, 20 s
per page, 5 MiB per response, and only pages on the FDP's own site (scheme,
host and port). They came from the security review of 2026-11-18: a ping is
unauthenticated, so without them a site generating endless pages could hold a
worker forever, and a registered "FDP" could make the index crawl someone
else's site.

On the copy of home.fairdatapoint.org's FDPs (71 harvested), 2 hit the
2,000-page limit and are held only in part: `fdp.lovd.nl` (1,997 pages after
474 s and still going) and `ehdsfdp.healthdataportal.eu` (1,972 pages and 28
refused with HTTP 403). 5 FDPs had 20 links to other sites left out, and 11
had pages that failed.

## 3. Proposal

### 3.1 Harvest budgets that resume, not caps

The limits should stop abuse, not cut real FDPs short.

- **A budget per run that resumes.** A run reads up to its budget (pages,
  bytes, time) and records where it stopped: the queue of pages not yet read.
  The next run starts there, then walks the rest again. Pages that have not
  changed answer `304 Not Modified` to the index's conditional requests, so
  walking again is cheap. A large FDP becomes complete over a few runs; its
  health report says "harvest in progress: N pages so far".
- **Trust per FDP.** An administrator can give an accepted FDP a larger
  budget, or allow it other sites (for example a separate host for
  distributions), on its page like the harvest interval today. FDPs that only
  pinged keep the defaults.
- **The index's own stops are reported as such**: "the index stopped at its
  budget", "a link to another site was not followed", never as the FDP's
  fault.

### 3.2 Built in: what the index found

Computed after each harvest from the pages it read, for every deployment.
Nothing here depends on a project.

| Finding | Meaning |
|---|---|
| Pages read and failed, by level | FDP, catalogue, dataset, distribution, data service; failures with their reason (HTTP status, timeout, cannot parse, too large) |
| Shape | How many catalogues, datasets, distributions and data services; how many datasets have no distribution, distributions no access URL or service |
| Reach | The deepest level reached, per FDP (FDPcrawleR's drop-off) |
| Listed only in LDP | A child in an LDP container that its parent does not link with the DCAT or FDP-O property |
| Linked only in DCAT | A DCAT link to a page in no LDP container |
| Empty container | An LDP container with no members |
| Dead end | A catalogue without datasets, a dataset without distributions |
| Not followed | Links to other sites, pages beyond the budget |

Each finding comes with a sentence on how to fix it, written for the FDP's
maintainer (the data holders' guide gets a section with the same text).

### 3.3 Configured: what a network expects

A deployment states what it counts as complete, as data. Two parts:

1. **Required reach**: the level an FDP must reach, and what that level must
   have. ERDERA's, as an example:

   ```yaml
   # expectations.yaml
   complete:
     reach: dataservice          # fdp | catalog | dataset | distribution | dataservice
     dataservice:
       requires: [dcat:endpointURL]
   ```

   The default deployment ships `reach: dataset`, with no requirements.
2. **Record rules**: SHACL, through the conformance packs that exist. A
   network's minimal metadata becomes a pack, as DCAT-AP is one.

ERDERA's settings would be a file an operator installs, or an example in the
documentation, not code. The EHDS deployment profile, which is code today,
could move to the same form later.

### 3.4 Optional: external FAIR tests

Some networks have their own test services: ERDERA runs its L1 and L2 tests of
its Virtual Platform metadata on OSTrails (`tests.ostrails.eu`). Those
services answer in the FAIR Testing Resources vocabulary (`ftr:TestResult`,
with `prov:value` pass, fail or indeterminate, a summary and a log), so one
generic client fits all of them:

- an operator lists test endpoints in the settings; the list is empty by
  default, as each call sends the FDP's URL to that service;
- the index calls each one with the FDP's root URL after a harvest that
  changed something, at most once a day per FDP and test;
- it keeps the outcome, summary, log and time, and keeps a failed call (HTTP
  error, timeout) apart from a failed test, as FDPcrawleR does.

### 3.5 Where it shows

- **The FDP's page**: a "Metadata health" section, combining the reach (as a
  small level-by-level line), the findings with their fixes, the profile
  results and the external tests, and the harvest's progress or the index's
  stops.
- **Statistics**: FDPs by reach (the drop-off as a flow or stacked bar), FDPs
  that are fine, have findings or cannot be read, counts by finding, failures
  by level, and their trend from the daily snapshots. A table of FDPs with
  their findings, sortable, with CSV download.
- **API**: `GET /index/entries/{uuid}/health`, and the aggregate in
  `GET /index/stats`.
- **Check before registering**: the Register page can run the same checks on
  a URL without adding it to the index (budgeted, rate-limited as pings are),
  so a maintainer can fix problems first. FDPcrawleR's authors list this as
  future work.

### 3.6 Provenance

The provenance proposal (`provenance.md`) publishes each harvest's report in
PROV-O. The health findings are computed from the same reports, so the two
share their storage: the harvest record holds what was read and what failed;
the health view is derived from it.

## 4. Left out

- Guessing a record's type from its URL (FDPcrawleR's ERDERA profile treats
  pages under `/biobank/` as datasets): the index reads `rdf:type`.
- Saved crawl runs: the daily snapshots cover history.

## 5. Questions for review

1. **Who sees an FDP's findings?** (a) everyone; (b) administrators only;
   (c) as the `reports` setting says for conformance. *Recommendation: (c), so
   one setting decides whether problems are public. The FDP's maintainer has
   no sign-in today, so with (b) they would not see their own findings.*
2. **Resumable budgets: default sizes?** *Recommendation: keep 2,000 pages and
   15 minutes per run, and resume; let administrators raise them per FDP.
   `fdp.lovd.nl` is then complete after as many runs as its size needs,
   instead of never. Its size is unknown: it lists a dataset per gene.*
3. **Expectations: YAML file or a settings page?** (a) a file mounted into
   the container; (b) the administration settings, stored in the database,
   like the search profile. *Recommendation: (b), with (a) as the way to
   install a network's ready-made settings.*
4. **External tests: build now or later?** They are the one part only some
   networks use. *Recommendation: later, after 3.1–3.3 and 3.5; the design
   stays as above.*
5. **Check before registering: open to everyone?** It makes the index fetch
   an arbitrary site on request. *Recommendation: yes, under the outbound
   rules and the ping rate limit, with a smaller budget (200 pages).*
6. **Union of LDP and DCAT links.** Today a page with LDP containers is walked
   through them only, and its DCAT links are ignored; the findings in 3.2 need
   both. *Recommendation: read both for the findings, and follow both, under
   the same origin rule and budget.*

## 6. Tasks

- 15.6 This design, reviewed.
- 15.7 Resumable harvest budgets and per-FDP trust (3.1).
- 15.8 The built-in findings, per FDP and summed (3.2, 3.5), with the data
  holders' guide.
- 15.9 Expectations as configuration, with an ERDERA example (3.3).
- 15.10 External FAIR tests (3.4), if wanted.
- 15.11 Check before registering (3.5).
