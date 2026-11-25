# Changelog

All notable changes to the FDPneo Index. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). The server and the client are
released together, with one version.

## [Unreleased]

The first release will contain:

- **Directory**: the Java FAIR Data Point index's API (`POST /` pings,
  `/index/*`), entries, approvals, settings, webhooks with retries, and an
  importer from a Java FDP's index (ADR-0001).
- **Liveness checks** of registered FDPs (ADR-0004).
- **Harvest** of every FDP's metadata into PostgreSQL, with conditional
  requests, stale nodes kept on partial failures, and outbound safety
  (ADR-0002).
- **Faceted search** over all harvested metadata: full text in 16
  languages, facets including Theme and Subject, filters on any property,
  default facets set by administrators, "Show as SPARQL", measured at
  1 000 FDPs (ADR-0005, docs/search-performance.md).
- **Temporal coverage**: `dct:temporal` in its DCAT, schema.org, OWL-Time
  and literal forms, searched by overlap (`temporalFrom`/`temporalTo`, a
  year, month or day) and shown on results.
- **Spatial coverage**: a country is one filter value whether an FDP names
  it by the EU authority table, GeoNames, lexvo or an ISO code; continents
  are labelled in ten languages.
- **Number and Yes/No filters** from the profile, with ranges of your own,
  and a bundled **HealthDCAT-AP** extension to load in Settings.
- **Copies across FDPs**: a resource several FDPs list (the same DOI,
  identifier, IRI, or an `owl:sameAs` mirror) says where else it is, and
  **Group copies** shows it once.
- **Accessibility**: an axe-core and keyboard pass over the public pages
  (WCAG 2.2 AA), with fixes to contrast, heading order and the Subject tree.
- **Concept labels** from Wikidata, ROR, OLS and RDF vocabularies, off by
  default (ADR-0006).
- **SPARQL** endpoint and page: editor, builder, example library, charts.
- **Conformance packs**: the SHACL shapes of DCAT-AP 3.0.1, HealthDCAT-AP
  Release 8 (per access level) and Health-RI core v2.0.3, bundled as data
  with the EU vocabulary facts their checks need; operator packs from a
  directory; `fdpindex conformance validate` checks a file offline, and
  `GET /index/conformance/packs` lists them.
- **Conformance checks** of every harvested record in the background
  (worker processes; only changed records again), with a report on each
  FDP's page (most frequent problems, the records that have them), findings
  on each resource's page, and settings for the profiles and who may read
  reports (public by default).
- **Conformance in search and per surface**: a "Meets profile" filter and
  card line, and a minimum per surface (search, SPARQL, the list of FDPs,
  the catalogue feed) that shows visitors only conforming records.
- **Catalogue feed**: the harvested metadata republished as DCAT-AP 3 at
  `/index/catalog` (Hydra paging) and over OAI-PMH at `/index/oai`, one
  catalogue record per resource with links back to its FDP, for
  aggregators such as data.europa.eu.
- **EHDS deployment profile** (`FDPINDEX_DEPLOYMENT_PROFILE=ehds`):
  HealthDCAT-AP checked first, a feed of HealthDCAT-AP records only, and the
  HealthDCAT-AP search extension, now with quality certificates.
- **Seven interface languages**: English, Dutch, German, French, Spanish,
  Italian and Portuguese, chosen in the header and remembered; dates and
  numbers in the reader's language; metadata shown in the interface language
  by default; the search filters' names and saved searches' titles per
  language.
- **Documentation site** (MkDocs, built in CI and attached to releases),
  with guides per audience, a configuration reference, API examples that
  run in the tests, and a guide for AI agents through fdp-mcp's index mode.
- **Statistics** (`/statistics`, `GET /index/stats`): FDPs and records now
  and over time (daily counts, kept from the upgrade on), registrations by
  month, datasets by year of issue, conformance shares and the FDPs with most
  records, counted as visitors see the index; charts with tables and "Open as
  SPARQL query" links.
- **Where FDPs are, and where their data is about**: two maps on the
  Statistics page. An FDP is placed by an administrator's choice, its own
  `r3d:country`, its publisher's ROR record, its country-code domain or its
  server's address (DB-IP Lite, after redirects), in that order, and its page
  says which; records by the country their `dct:spatial` names (EU country
  codes, NUTS), each country a search. `fdpindex geoip download`; the image
  includes the month's database (`GEOIP_MONTH`).
- **`deploy/compose.proxy.yaml`**: the client joins the Docker network of a
  reverse proxy on the same host, such as Caddy.
- **Maps** on the Statistics page open on the world, with a view of each
  continent that has FDPs or records, and its count.
- **Metadata health** (TASKS 15.8): after every harvest, how far an FDP's
  metadata reaches (FDP → catalogue → dataset → distribution → data
  service), the pages that could not be read by kind, the index's own stops,
  findings against the FDP specification (records linked only through DCAT,
  children listed only in an LDP container, catalogues without datasets) and
  notes with DCAT's advice that do not count against the FDP. On the FDP's
  page, summed on the Statistics page with a table and CSV download, and at
  `GET /index/entries/{uuid}/health` and `GET /index/health`. Readable as
  conformance reports are. Migration 0019; FDPs harvested earlier get a
  report from the next sweep.
- **Operations**: Compose stack with an optional Keycloak profile, trusted
  reverse proxy, backup and restore (docs/operations.md).

### Fixed

- Registration answers no longer reveal the index's network. When a URL's
  host resolved to a private address, `POST /index/register` (open to
  anyone) answered with that address, so names inside the index's network
  could be looked up through it. The answer now names the host only; the
  address goes to the log.
- Large FDPs are harvested in full. A harvest stopped at 2,000 pages, so
  an FDP larger than that was never held whole (two FDPs on
  home.fairdatapoint.org). It is now a walk over runs of that size, a few
  minutes apart, up to 100,000 pages (50,000 until 2026-11-25:
  `fdp.lovd.nl` has about 69,000), and continues where it stopped; an FDP
  answering `429`, or `503` with `Retry-After`, is left alone for that
  long. Administrators can set an FDP's own budget and other sites it may
  read. Migration 0018. A page that fails but has a stored version still
  leads the harvest to the pages below it.
- One FDP, one entry. URLs that differ only in `http`/`https`, letter case
  in the host, a default port or a trailing slash were separate entries, so
  the FDP counted twice; home.fairdatapoint.org had ten such pairs. A ping
  for a variant now updates the known entry, and
  `GET /index/admin/duplicates` and `POST /index/admin/duplicates/merge`
  clean up the existing ones, keeping the entry that works. Migration 0017.
- Outbound requests: a connection kept open for one host name could carry a
  request for another name at the same address, without that name's
  certificate being checked. The index put the checked address in the URL,
  and connections are pooled by the URL's host. The address is now chosen
  where the socket is opened, and the URL keeps the name, so connections are
  per name and still kept open between requests to one FDP. A request
  without a checked address no longer connects at all. `HTTP(S)_PROXY` is
  ignored for outbound requests, as a proxy would resolve the name itself
  after the check. (Found while fixing F-01 of the 2026-11-23 audit in the
  FDPneo server.)
- SPARQL: one harvested node the store could not parse (an IRI such as
  `<;https://example.org/>`, which rdflib accepts) failed every sync pass, so
  no snapshot was published and SPARQL stayed empty or stale. Such nodes are
  now left out and logged (`sparql_nodes_rejected`), and one FDP's failure no
  longer stops the pass.
- The Register page was wider than a phone's screen (a long command in a
  copy field).
- A literal holding a character Python treats as a line break (U+0085,
  U+2028, form feed; seen in double-encoded UTF-8 on a live FDP) was cut in
  two when a node was stored, which broke the search projection of that
  FDP.
