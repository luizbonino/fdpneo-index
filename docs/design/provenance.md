# Harvest provenance (TASKS 15.2, 15.3)

Status: proposal, for review (2026-11-24). Nothing here is built yet.

## 1. Why

The index republishes what it harvests: through search, SPARQL, the catalogue
feed and OAI-PMH. Someone who finds a record there can tell which FDP it came
from, but not when the index fetched it, whether the copy is current, or what
the index did to get it. FAIR principle R1.2 asks for "(meta)data associated
with detailed provenance"; for an aggregator, the provenance that matters is
that of its copy.

The facts already exist. Every harvest is an event (`Harvest`, with start and
end times and a report: nodes fetched, added, changed, removed, not modified,
failed, limits reached). Every harvested node keeps when it was fetched, its
content hash, the FDP's ETag and Last-Modified, and whether it is stale (kept
from an earlier harvest because the last fetch failed). Events are never pruned
while the entry exists. They are admin-only today, because they also hold
callers' addresses and raw HTTP bodies.

## 2. Proposal

Publish the safe part of those facts in PROV-O.

```turtle
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix dct:  <http://purl.org/dc/terms/> .

# The index, once
<https://index.example.org/> a prov:SoftwareAgent ;
    dct:title "FDPneo Index" ;
    dct:hasVersion "0.2.0" .

# One harvest
<https://index.example.org/index/harvests/6f1c…> a prov:Activity, fdpindex:Harvest ;
    prov:used <https://fdp.example.org/> ;                 # the FDP (its client URL)
    prov:wasAssociatedWith <https://index.example.org/> ;
    prov:startedAtTime "2026-11-24T08:12:03Z"^^xsd:dateTime ;
    prov:endedAtTime   "2026-11-24T08:12:41Z"^^xsd:dateTime ;
    fdpindex:outcome "complete" ;                          # complete | partial | failed
    fdpindex:nodesFetched 412 ; fdpindex:nodesAdded 3 ; fdpindex:nodesChanged 1 ;
    fdpindex:nodesRemoved 0 ; fdpindex:nodesNotModified 408 ; fdpindex:nodesFailed 0 .

# The FDP's harvested graph: what the latest harvest produced
<https://fdp.example.org/> prov:wasGeneratedBy <…/index/harvests/6f1c…> .

# One record as the index holds it
<https://fdp.example.org/dataset/42>
    prov:wasDerivedFrom <https://fdp.example.org/dataset/42> ;   # see §3, question 3
    fdpindex:fetchedAt "2026-11-20T10:01:00Z"^^xsd:dateTime ;
    fdpindex:contentHash "sha256:…" ;
    fdpindex:staleSince "2026-11-24T08:12:41Z"^^xsd:dateTime .  # only when stale
```

Where it shows:

- **SPARQL**: a provenance graph, `urn:fdpindex:provenance`, beside the
  entries graph, rewritten after each harvest.
- **Resolvable**: `GET /index/harvests/{id}` in Turtle, JSON-LD and HTML (the
  harvest's IRI is that URL), and `GET /index/entries/{uuid}/harvests` for an
  FDP's list. Public.
- **Catalogue feed and OAI-PMH**: each `dcat:CatalogRecord` gains
  `prov:wasGeneratedBy` its harvest, beside the `dct:source` and
  `dct:modified` it has.
- **Resource and FDP pages**: "Harvested from … on …, by harvest …", and
  "kept from an earlier harvest" for stale records.
- **The index's own description** (VoID, the feed's catalogue): the index as
  the `prov:SoftwareAgent`.

What stays private: callers' addresses, request and response bodies, ping
events, admin triggers (who asked for a harvest), webhook deliveries.

## 3. Questions for review

1. **How much history?** (a) only the latest harvest per FDP; (b) every
   harvest, as long as the events are kept (they are never pruned today);
   (c) the last N per FDP. *Recommendation: (b) for `/index/harvests/{id}` and
   the per-FDP list, since the events exist anyway; only the latest in the SPARQL
   provenance graph, so it stays small.*
2. **Public or admin-only?** *Recommendation: public, as the harvested
   metadata and conformance reports are; only the fields in §2.*
3. **Record identity.** The index keeps the FDP's IRIs for the records it
   holds, so `<x> prov:wasDerivedFrom <x>` says nothing useful. Options: (a)
   describe the record's *copy* through its catalogue-feed record (`dcat:CatalogRecord`
   `prov:wasDerivedFrom` the FDP's IRI), which already has its own IRI;
   (b) give each copy an IRI of its own (`/index/search/resources/{id}`).
   *Recommendation: (a): the feed's CatalogRecord is the index's statement
   about its copy; per-record fetch times and staleness go there.*
4. **Vocabulary for the counts.** PROV-O has no terms for "nodes added"; the
   proposal uses an `fdpindex:` namespace. A public namespace IRI
   (`https://w3id.org/fdpindex#`, registered with w3id.org) would make them
   resolvable. *Recommendation: yes, through the same w3id.org redirect
   service the testbed already runs (pid-relay).*
5. **Also record failures?** A failed harvest (FDP unreachable) is provenance
   too: it explains why records are stale. *Recommendation: yes, with
   `fdpindex:outcome "failed"` and the reason in words (no response bodies).*

## 4. Tasks

- 15.2 This design, reviewed.
- 15.3 Build: the provenance graph, `/index/harvests/{id}` and the per-FDP
  list, the feed's `prov:wasGeneratedBy`, the page lines, the namespace
  document, tests, and the data-holders guide.
