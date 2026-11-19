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

### Fixed

- A literal holding a character Python treats as a line break (U+0085,
  U+2028, form feed; seen in double-encoded UTF-8 on a live FDP) was cut in
  two when a node was stored, which broke the search projection of that
  FDP.
- **Operations**: Compose stack with an optional Keycloak profile, trusted
  reverse proxy, backup and restore (docs/operations.md).
