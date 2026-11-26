# Harvest provenance (TASKS 15.2, 15.3)

Status: reviewed (2026-11-26); questions 1–5 decided, question 6 open.
Nothing here is built yet (15.3).

## 1. Why

The index republishes what it harvests: through search, SPARQL, the catalogue
feed and OAI-PMH. Someone who finds a record there can tell which FDP it came
from, but not when the index fetched it, whether the copy is current, or what
the index did to get it. FAIR principle R1.2 asks for "(meta)data associated
with detailed provenance"; for an aggregator, the provenance that matters is
that of its copy.

The facts already exist. Since TASKS 15.7 an FDP is read in **runs** of up to
2 000 pages, a few minutes apart, which together make one **walk**: a
complete pass over its metadata (`fdp.lovd.nl`, about 69 000 pages, takes
some 35 runs). Every run is an event (`Harvest`, with start and end times and
a report: pages read, added, changed, removed, not modified, failed, limits
reached, the walk it belongs to). Every harvested page keeps when it was
fetched, its content hash, the FDP's ETag and Last-Modified, and whether it
is stale (kept from an earlier walk because the last fetch failed). Events
are kept as long as their FDP is in the index. They are admin-only today,
because they also hold callers' addresses and raw HTTP bodies. A walk itself
is not kept once it ends; only its summary for the metadata health report.

## 2. Decisions

1. **One harvest is one walk** (option b). The walk is the `prov:Activity`
   that generated an FDP's copy; its runs are activities that are part of
   it. A new table keeps each walk after it ends (about one row per walk
   and FDP), with the run events pointing to it.
2. **All history** (option b): every walk is resolvable, as long as its
   events are kept. The SPARQL provenance graph holds only the latest walk
   per FDP, so it stays small.
3. **Public** (option a), with only the fields of §3: times, outcome, counts,
   failure reasons in words. Callers' addresses, request and response
   bodies, pings, who asked for a harvest and webhook deliveries stay
   private.
4. **The copy is described by its catalogue-feed record** (option a): the
   feed's `dcat:CatalogRecord` is the index's statement about its copy,
   `prov:wasDerivedFrom` the FDP's IRI, with the copy's fetch time, content
   hash and staleness. The index keeps the FDP's IRIs for the records
   themselves.
5. **The terms extend the FAIR Data Point Ontology** (FDP-O,
   `https://w3id.org/fdp/fdp-o#`), not a namespace of the index's own (§4).
   PROV-O is used wherever it has a term.

Open:

- **Question 6: also record failed walks?** A walk that could read nothing
  (the FDP was unreachable) is provenance too: it explains why copies are
  stale. *Recommendation: yes, with the outcome "failed" and the reason in
  words (no response bodies).*

## 3. What is published

```turtle
@prefix prov:  <http://www.w3.org/ns/prov#> .
@prefix dct:   <http://purl.org/dc/terms/> .
@prefix dcat:  <http://www.w3.org/ns/dcat#> .
@prefix fdp-o: <https://w3id.org/fdp/fdp-o#> .

# The index, once
<https://index.example.org/> a fdp-o:FAIRDataPointIndex ;
    dct:title "FDPneo Index" ;
    dct:hasVersion "0.2.0" .

# One harvest: a walk
<https://index.example.org/index/harvests/6f1c…> a fdp-o:Harvest ;
    prov:used <https://fdp.example.org/> ;                  # the FDP (its client URL)
    prov:wasAssociatedWith <https://index.example.org/> ;
    prov:startedAtTime "2026-11-24T08:12:03Z"^^xsd:dateTime ;
    prov:endedAtTime   "2026-11-24T09:40:41Z"^^xsd:dateTime ;
    fdp-o:harvestOutcome fdp-o:HarvestComplete ;            # Complete | Partial | Failed
    fdp-o:pagesRead 69120 ; fdp-o:pagesAdded 3 ; fdp-o:pagesChanged 1 ;
    fdp-o:pagesRemoved 0 ; fdp-o:pagesNotModified 69116 ; fdp-o:pagesFailed 0 ;
    dct:hasPart <…/index/harvests/6f1c…/runs/1>, <…/runs/2> .

# One run of it
<…/index/harvests/6f1c…/runs/2> a fdp-o:HarvestRun ;
    dct:isPartOf <…/index/harvests/6f1c…> ;
    prov:startedAtTime "…"^^xsd:dateTime ; prov:endedAtTime "…"^^xsd:dateTime ;
    fdp-o:pagesRead 2000 .

# The index's copy of one record, through its catalogue-feed record
<https://index.example.org/index/catalog/records/…> a dcat:CatalogRecord ;
    foaf:primaryTopic <https://fdp.example.org/dataset/42> ;
    prov:wasDerivedFrom <https://fdp.example.org/dataset/42> ;
    prov:wasGeneratedBy <…/index/harvests/6f1c…> ;
    fdp-o:fetchedAt "2026-11-24T08:30:00Z"^^xsd:dateTime ;
    fdp-o:contentHash "sha256:…" ;
    fdp-o:staleSince "2026-11-24T09:40:41Z"^^xsd:dateTime .  # only when stale
```

Where it shows:

- **SPARQL**: a provenance graph, `urn:fdpindex:provenance`, beside the
  entries graph, rewritten after each walk, with the latest walk per FDP.
- **Resolvable**: `GET /index/harvests/{id}` (a walk, with its runs) in
  Turtle, JSON-LD and HTML (the walk's IRI is that URL), and
  `GET /index/entries/{uuid}/harvests` for an FDP's walks. Public.
- **Catalogue feed and OAI-PMH**: each `dcat:CatalogRecord` gains
  `prov:wasDerivedFrom`, `prov:wasGeneratedBy` its walk, the fetch time,
  hash and staleness, beside the `dct:source` and `dct:modified` it has.
- **Resource and FDP pages**: "Harvested from … on …, by harvest …", and
  "kept from an earlier harvest" for stale records.
- **The index's own description** (VoID, the feed's catalogue): the index
  as a `fdp-o:FAIRDataPointIndex`.

## 4. The FDP-O extension

Proposed for FDP-O 2.1 (`FAIRDataTeam/FDP-O`). The index needs them only
for what PROV-O, DCAT and Dublin Core do not say.

| Term | Kind | Meaning |
| --- | --- | --- |
| `fdp-o:FAIRDataPointIndex` | class, ⊑ `prov:SoftwareAgent` | A service that registers FDPs and harvests their metadata |
| `fdp-o:Harvest` | class, ⊑ `prov:Activity` | One complete pass of an index over an FDP's metadata |
| `fdp-o:HarvestRun` | class, ⊑ `prov:Activity` | One budgeted part of a harvest (`dct:isPartOf` it) |
| `fdp-o:HarvestOutcome` | class | How a harvest ended; individuals `fdp-o:HarvestComplete`, `fdp-o:HarvestPartial` (some pages failed or a limit was reached), `fdp-o:HarvestFailed` (nothing could be read) |
| `fdp-o:harvestOutcome` | object property, Harvest → HarvestOutcome | |
| `fdp-o:pagesRead`, `pagesAdded`, `pagesChanged`, `pagesRemoved`, `pagesNotModified`, `pagesFailed` | datatype properties, `xsd:nonNegativeInteger` | Counts of a harvest or run |
| `fdp-o:failureReason` | datatype property, `xsd:string` | Why a harvest or page failed, in words |
| `fdp-o:fetchedAt` | datatype property, `xsd:dateTime` | When the index last fetched the record a catalogue record describes |
| `fdp-o:contentHash` | datatype property, `xsd:string` | The hash of the copy (`sha256:` and hex) |
| `fdp-o:staleSince` | datatype property, `xsd:dateTime` | Since when the copy is kept from an earlier harvest |

The build (15.3) uses these IRIs; they resolve once FDP-O 2.1 is released.

## 5. Tasks

- 15.2 This design, reviewed (question 6 open).
- 15.3 Build: the walk table, the provenance graph, `/index/harvests/{id}`
  and the per-FDP list, the feed's provenance, the page lines, tests, the
  data-holders guide; the FDP-O 2.1 additions as a change to FDP-O.
