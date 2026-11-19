# ADR-0006: Concept labels resolved from vocabularies

- Status: accepted
- Date: 2026-11-19

## Context

Harvested metadata names concepts by IRI: a MeSH descriptor or a Wikidata
item as `dct:subject`, an EDAM topic as `dcat:theme`, a ROR organisation as
publisher. Before this ADR, the Index had two sources of labels:
- labels the FDP itself published, such as `foaf:name` on a publisher;
- a bundled pack of EU authority tables and licences.

Other concepts showed as IRIs in facets and on resource pages, and no search
could find them by name. The search design (§3.5) planned a third source,
remote labels, off by default; FDPneo has the same for its own `/labels`.

Vocabularies name things in different ways. Labels come as `skos:prefLabel`,
`rdfs:label`, `dct:title`, `dc:title`, `schema:name`, `foaf:name` or SKOS-XL.
MeSH links to broader descriptors with its own `meshv:broaderDescriptor`.
Some services serve JSON only: ROR and the Ontology Lookup Service. OBO IRIs
dereference to HTML.

## Decision

1. **One list of naming predicates** (`fdpindex.rdf`): labels, synonyms and
   broader concepts. Harvested labels, resolved concepts and the SPARQL label
   lookup all read it. Before, search and SPARQL each kept a shorter list.
2. **A shared `concept` table.** IRI values of concept fields become rows,
   shared by all FDPs. Concept fields are the profile's IRI and agent fields
   that are facets or searched. Each row holds:
   - a status: pending, resolved, missing, failed or skipped;
   - its broader concepts;
   - when it is next due.

   Labels and synonyms per language go in `concept_label`. Concepts with a
   bundled label, and hosts not on the allow-list, are skipped and never
   requested.
3. **A background resolver** asks each vocabulary through what it serves
   best:
   - Wikidata's `wbgetentities`: 50 items per request, in the configured
     languages, with "subclass of" as broader;
   - ROR's JSON API;
   - EBI's OLS for OBO and EDAM terms;
   - any other allow-listed host, by RDF content negotiation.

   Requests go through `OutboundClient`, so private addresses stay blocked.
   The allow-list is checked on every redirect, a few requests per second
   are made, and responses are capped in size. Broader concepts are resolved
   too, two levels up by default. A resolved concept is refreshed after 30
   days; a miss or a failure is retried after 7.
4. **Use in search.**
   - Labels go into the full-text vectors at the field's weight, synonyms
     one weight lower, and broader concepts' labels at `D`. A search for
     "Neoplasms" thus finds a dataset tagged "Breast Neoplasms", below
     direct matches.
   - Facets, resource pages and the SPARQL labels endpoint show the labels.
   - The precedence for display is harvested, then bundled, then resolved,
     then a humanised IRI.
   - When a concept's names change, only the entries that use it, or a
     narrower concept, are projected again.
5. **Off by default** (`FDPINDEX_LABELS_RESOLVE`), because it sends requests
   to third parties. The default allow-list holds public vocabulary services:
   Wikidata, ROR, OBO, EDAM, MeSH, EU Publications Office, ORCID, NERC, the
   Library of Congress and GeoNames.
6. **Licensing.** SNOMED CT needs a licence, and ICD labels fall under WHO's
   API terms. Neither is on the default list. An operator who adds them is
   responsible for the terms.

## Consequences

- A projection version is now part of the indexer's hash. Changing what the
  projection emits re-projects every entry once.
- Resolved names make vectors longer: synonyms are capped at 12 per concept,
  and broader concepts give one label each.
- OLS provides no broader terms in the API used. Ontology hierarchies via
  OLS are a follow-up.
- Wikidata answers in the configured languages only
  (`FDPINDEX_LABELS_LANGUAGES`). Other vocabularies' labels are kept in those
  languages plus untagged ones.
