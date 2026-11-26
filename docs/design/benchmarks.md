# Running a network's benchmarks (TASKS 15.10)

Status: reviewed (2026-11-26); decisions in §3. Built in TASKS 15.10.

The index triggers other tools; it does not contain them (metadata-health
design, decision 4). This note covers the first case: a network profile
names FAIR Testing Resources (FTR) benchmarks, such as ERDERA's Onboarding
Benchmark (FAIRsharing.2d49c0), and the index runs them on its FDPs through
the runner the benchmark names, FAIR Champion, and shows the results. It
replaces the "confirmed with FAIR Champion when 15.10 starts" of
metadata-health §3.4.

## 1. FAIR Champion, as found

From its code (`markwilkinson/FAIR-Champion`, Ruby and Sinatra, MIT,
version 1.1.22, last commit 2026-09-11) and one run against
`https://fdp.healthdata.nl` on 2026-11-26:

- **A benchmark is run through its scoring algorithm.** The benchmark's
  description names its runner and algorithm: `ERDERA_Onboard.ttl` has
  `ftr:supportedBy <https://w3id.org/FAIR-Champion>` and
  `ftr:scoringFunction <https://docs.google.com/spreadsheets/d/16F0…/edit>`.
  The algorithm (a public Google Sheet) lists the tests, their weights and
  the conditions; the Champion reads it on every run and finds each test's
  endpoint in the OSTrails FDP Index. So any FTR benchmark with a scoring
  function works the same way: nothing is specific to ERDERA.
- **One synchronous call:**
  `POST {runner}/assess/algorithm/d/{sheet id}` with
  `{"guid": "<record IRI>"}` and `Accept: application/json`. It answers when
  every test has run (ERDERA's three tests: 5.8 s; the server allows 180 s).
  No job id, no callback, no authentication. `w3id.org/FAIR-Champion`
  redirects to `https://tools.ostrails.eu/champion` with a 307, which keeps
  the POST. CESSDA runs its own instance.
- **The answer** is JSON: each test's verdict (`pass`, `fail`,
  `indeterminate`, `error`) with its log and weight; one sentence per
  condition, met or not ("Acceptable for VP onboarding: … Level 1"); guidance
  links for the conditions not met; the tests with their metrics; whether a
  test errored (HTTP 207); and the FTR result set (`ftr:TestResultSet`, a
  `prov:Collection` of `ftr:TestResult`) as a JSON-LD string. There is no
  single score.
- **The tests fetch the record themselves**, so the record must be public and
  serve RDF. FDP records are and do.
- **ERDERA's levels on an FDP**: run on `fdp.healthdata.nl`'s root, all three
  conditions were met, L2 included, because an FDP's root is a
  `dcat:DataService`. Run on a dataset, the L2 condition (which tests for a
  data service) is not met by design.

## 2. Proposal

- **Which benchmarks**: those of the network profile (`benchmarks`). The
  index reads each benchmark's description, finds its runner and scoring
  function, and keeps them for a day, as it keeps declared profiles. A
  benchmark whose runner the index does not know how to call is listed as
  "not run: unknown runner"; the index knows FAIR Champion's API today, and
  another runner is one more adapter.
- **Calls**: one at a time per runner, a pause between calls, a daily cap,
  each with the record's IRI only. A call that failed (the runner did not
  answer, HTTP error, timeout) is kept apart from tests that failed, and
  retried later; it never counts against the FDP.
- **Results**: per FDP, record and benchmark: the conditions with their
  sentences, the tests with their verdicts and guidance links, when, by which
  runner and algorithm version, and the FTR result set as it came. The FTR
  result set is republished at a public address of its own, so the results
  are FAIR too.
- **Where they show**: the FDP's page, under the metadata health, as "{the
  network}'s benchmark" with the conditions met or not and the guidance; the
  Statistics page, FDPs per condition met; the API,
  `GET /index/entries/{uuid}/benchmarks`; the Expectations page links each
  benchmark to its results.

## 3. Decisions

1. **Every record the benchmark applies to** (option c), and an administrator
   can run it on any single record of an FDP. Which records a benchmark applies
   to comes from its description (`dpv:isApplicableFor`, ERDERA's
   `schema:Dataset`): classes DCAT and schema.org share map to the index's
   levels, so `schema:Dataset` means the FDP's datasets, the network profile's
   own dataset classes included; a benchmark that states nothing applies to
   every record. To keep the load on a shared service bounded, a record is
   assessed again only when its content changed since its last result (or the
   benchmark's algorithm changed); a walk that changed nothing costs no call.
   The first pass over a large FDP spreads over days under the daily cap.
2. **After a walk that changed an FDP's metadata, at most once a week per FDP,
   and on an administrator's request** (option b).
3. **Administrators start runs; results are visible as the health reports are**
   (option a).
4. **Off until an administrator switches it on** (option b), with the runner the
   benchmark names unless an administrator gives another instance. The setting
   has three positions: off, on request (administrators' runs only) and
   scheduled.
5. **The network's own verdict**, beside the metadata health and on the
   Statistics page, not in the health status (option a).
6. **The latest result per record and benchmark** (option a); the Statistics
   page keeps daily counts.
7. **Benchmarks only for now** (option b); the runner is an adapter.

The questions as they were put:

### Q1. Which records of an FDP are assessed?

- **(a)** The FDP's root only.
- **(b)** The root and a sample of the records the benchmark applies to (up
  to 10, taken as the check before registering takes its sample).
- **(c)** Every record the benchmark applies to.

*Consequences*: (a) is 70 calls for the testbed's 70 FDPs, but ERDERA's L1
is about datasets, biobanks and registries, which (a) never looks at. (b) is
up to some 700 calls, about an hour and a half at one call at a time with
pauses, and shows whether an FDP's records meet the level, by example. (c)
is exact but `fdp.lovd.nl` alone has some 23 000 datasets: about a day and a
half of a shared public service for one FDP.

*Recommendation: (b)*, and an administrator can run the benchmark on any
single record from its page.

### Q2. When do runs happen?

- **(a)** Only when an administrator asks.
- **(b)** After a walk that changed an FDP's metadata, at most once a week
  per FDP, and when an administrator asks.
- **(c)** On a fixed schedule (monthly).

*Consequences*: (a) leaves results old or missing unless someone remembers.
(b) follows the metadata as it changes, with a cap that keeps the load on
the runner small. (c) runs on unchanged FDPs and misses changes for weeks.

*Recommendation: (b).*

### Q3. Who may start a run, and who sees the results?

- **(a)** Administrators start runs; the results are visible as the metadata
  health reports are (public by default).
- **(b)** Anyone may start a run on a record, under a rate limit; results as
  in (a).
- **(c)** Administrators only, for both.

*Consequences*: (b) lets maintainers try fixes, but every visitor's click is
a call to someone else's shared service. (c) hides from maintainers what
their network asks of them. The results concern public metadata, as the
health findings do.

*Recommendation: (a).* Maintainers can run the benchmark themselves on FAIR
Champion's own page.

### Q4. On by default, and which runner instance?

- **(a)** On whenever the network profile names benchmarks, with the runner
  the benchmark names.
- **(b)** Off until an administrator switches it on; then the runner the
  benchmark names, unless the administrator gives another instance (a
  self-hosted Champion, as CESSDA runs).
- **(c)** Only a self-hosted runner.

*Consequences*: (a) sends record IRIs and load to a third party as soon as a
profile is loaded, which an administrator may not expect. (c) makes every
index run a Champion. (b) asks one decision of the administrator and keeps
the choice of instance open.

*Recommendation: (b).* Before the testbed runs benchmarks on a schedule,
OSTrails should know: it is their shared service.

### Q5. How do the results count?

- **(a)** As the network's own verdict, beside the metadata health: worded as
  theirs ("ERDERA Onboarding: Level 1 met"), counted on the Statistics page
  per condition, not in the health status.
- **(b)** A condition not met is a missed requirement, and the FDP's health
  status becomes "has findings".

*Consequences*: with (b), a run that fails for reasons outside the FDP (the
Champion, Google Sheets or the test service down; a test's own scope, as L2
on datasets) marks the FDP; and the network's verdict and the index's
findings blur. With (a), an administrator who wants the benchmark to count
also lists its rules as the profile's requirements or shapes.

*Recommendation: (a).*

### Q6. How much history?

- **(a)** The latest result per record and benchmark.
- **(b)** Every result.

*Consequences*: (b) shows progress over time but grows with every walk; the
FTR result sets are some tens of kilobytes each. (a) answers "does it meet
the level now".

*Recommendation: (a)*, with the date and runner of the result; the
Statistics page keeps the daily counts per condition, as it does for
health.

### Q7. Other tools now, or later?

- **(a)** Now: an administrator registers any tool (its address, when to
  call it, a secret) and the index calls it with an FDP and reads its result,
  at once or at a callback (metadata-health §3.4).
- **(b)** Later, when a second tool exists; 15.10 builds benchmarks only,
  with the runner as an adapter a second tool can follow.

*Consequences*: (a) designs a general interface from a single example, the
FAIR assessment tool that is not built yet. (b) ships the benchmark first
and shapes the general case on the second real tool.

*Recommendation: (b).*

## 4. To tell FAIR Champion's maintainers

Not needed to build this, but worth reporting:

- Asking for JSON-LD (`Accept: application/ld+json`) likely fails: the
  content-type match tests only its first pattern.
- The result set has no `ftr:BenchmarkScore`, though the code has a builder
  for it.
- Scoring a posted result set without a `guid` likely ends in an error.
- ERDERA's algorithm declares it applies to `schema:Dataset`, while its L2
  test asks for a `dcat:DataService`.
- The benchmark's metrics are listed in its description text, not as
  triples.

## 5. Tasks

- 15.10 Done (2026-11-26): `fdpindex.benchmarks`, migration 0021, the API
  under `/index/entries/{uuid}/benchmarks` and `/index/benchmark-results`, the
  FDP page section, the Statistics table, the settings card, the data
  holders' and administrators' guides. Checked against the public FAIR
  Champion with ERDERA's benchmark on one FDP's root.
