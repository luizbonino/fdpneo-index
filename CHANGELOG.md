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
- **Operations**: Compose stack with an optional Keycloak profile, trusted
  reverse proxy, backup and restore (docs/operations.md).
