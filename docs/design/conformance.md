# Conformance and republication — design

- Status: **proposed, for review** (TASKS 11.1). Becomes ADR-0007 once agreed.
- Date: 2026-11-19
- Scope: Phase 11 of `TASKS.md`. Validating harvested records against SHACL
  profile packs (11.2, 11.3), how strict each surface is (11.4), republishing
  the harvested metadata as a DCAT-AP catalogue (11.5, which replaces 9.6),
  and the EHDS deployment profile (11.6). The MCP server (11.7) and the
  language fallback order (11.8) are out of this note's scope.
- Sources: FDPneo ADR-0021 §2 and §4 and its 2026-11-19 amendment;
  `docs/architecture/discovery.md` §3 and §4 in fdpneo-server; this
  repository's ADR-0002 (harvested data in PostgreSQL) and ADR-0005 (search
  and SPARQL).

## 0. Decisions in one page

1. **A record is a dataset with what describes it.** Validation runs per
   *record*: a catalog, dataset, dataset series or data service, with its
   distributions and the blank nodes and inline resources its nodes hold
   (publisher, contact point, temporal and spatial coverage). FDP roots are
   validated too, as records of their own. A record's input is assembled
   from `harvested_node`, so validation needs no network.
2. **Profile packs are data.** A pack is a folder: a manifest (id, version,
   title, source, licence, which record kinds it applies to) and SHACL shapes
   in Turtle. The index bundles DCAT-AP 3, HealthDCAT-AP and Health-RI core
   v2; an operator adds packs from a directory. A deployment lists the packs
   it validates against, in order. Several packs coexist, so a model
   transition (HealthDCAT-AP R5 → R6) runs both until the old one is dropped.
3. **pySHACL validates, in the background, incrementally.** The validator is
   pySHACL (pure Python; FDPneo's server already depends on it) behind a
   small `Validator` protocol, so a faster engine can replace it. A record is
   validated again only when its content hash or the pack version changes.
   A job queue with bounded workers keeps validation off the request path,
   as the search projection does.
4. **Results live in PostgreSQL.** One row per record and pack (status,
   counts, hashes, time) and up to 50 findings per row (severity, focus node,
   path, constraint, shape, message). The report graph itself is not kept.
   This deviates from discovery.md §3 ("results stored in the record's meta
   graph"): the index has no meta graphs and its SPARQL store is derived
   (ADR-0005).
5. **Accept and flag; strictness is per surface.** Ingest never rejects a
   record for its metadata. Each *surface* (search, SPARQL, the catalogue
   feed, the directory) declares the minimum it shows: nothing, or "no
   violations" against a named pack. The default is lenient everywhere. A
   record not yet validated counts as not conforming on a strict surface.
6. **Data holders see why.** A conformance report per FDP, public by
   default like the metadata it describes: records per pack and status, the
   most frequent problems (shape, path, message) with example records, and
   per record its findings. An API and a tab on the FDP's page.
7. **The catalogue feed republishes, it does not rewrite.** `GET
   /index/catalog` is a DCAT-AP 3 catalogue of catalogue records: one
   `dcat:CatalogRecord` per harvested record, with `foaf:primaryTopic` the
   resource under its own IRI, `dct:source` the FDP's record and
   `dct:conformsTo` the packs it conforms to. The resource's triples are
   published as harvested. It is offered the two ways data.europa.eu
   harvests: **OAI-PMH** (`ListRecords`, its preferred protocol) and an RDF
   dump paged with Hydra. A strict feed shows only conforming records.
8. **The EHDS profile is a bundle of settings, not code.** HealthDCAT-AP and
   DCAT-AP packs, the EU vocabularies (the label pack has them), the
   HealthDCAT-AP search overlay (`search/examples/healthdcat-ap.yaml`), and a
   strict catalogue feed. Quality and utility labels (EHDS Art. 78) are
   harvested metadata: the index shows them and does not compute them.

## 1. Records

The harvest keeps one row per node (`harvested_node`: node IRI, N-Triples,
content hash). A record is assembled from nodes:

| Record kind | Root | Also included |
|---|---|---|
| fdp | the FDP's root node | nothing else |
| catalog | a `dcat:Catalog` | blank nodes and inline resources of its node |
| dataset, series | a `dcat:Dataset` / `dcat:DatasetSeries` | its distributions' nodes (`dcat:distribution`), inline nodes |
| service | a `dcat:DataService` | inline nodes |

"Inline resources" are IRI subjects described in the same node (an
`foaf:Agent` with its `foaf:name` inside the dataset's node), which DCAT-AP
shapes need to check a publisher's name. Resources described in *other*
nodes (a publisher with its own page on the FDP) are not followed: the
record is what the FDP serves for that resource. The kind is the search
projection's (profile `types`).

The record's **content hash** is the hash of its nodes' hashes. It decides
when a record is validated again.

## 2. Profile packs

```
packs/dcat-ap/3.0.1/
  pack.yaml        id, version, title, source URL, licence, applies-to kinds,
                   default severity, shapes files, vocabulary files
  shapes.ttl       the SHACL shapes as published
  vocabularies.ttl optional: the code lists sh:in / sh:class constraints need
```

- **Bundled packs** live in the package (`fdpindex/conformance/packs/`),
  copied unchanged from their publishers, with licence and source in the
  manifest. All three are CC BY 4.0. Proposed set:
  - **DCAT-AP 3.0.1** (SEMIC, released 2026-06-04;
    `github.com/SEMICeu/DCAT-AP`, `releases/3.0.1/html/shacl/`). The files
    split by concern: `shapes.ttl` (existence, cardinality, literal ranges),
    `range.ttl` (`sh:class` on objects), `mdr-vocabularies.shape.ttl` (values
    from the EU vocabularies), `shapes_recommended.ttl`, and
    `deprecateduris.ttl`. The pack uses the first three as violations and
    the recommended file as warnings.
  - **HealthDCAT-AP Release 8** (`code.europa.eu/healthdataeu/healthdcat-ap`,
    the release `data.health.europa.eu/healthdcat-ap/releases/latest/`
    points to; a draft specification dated 2026-05). Its shapes come per
    access level (`public`, `restricted`, `non-public`) plus recommended,
    range and vocabulary files, and import DCAT-AP **3.0.0**. The pack picks
    the shapes file by the record's `dct:accessRights` (§9, question 2).
  - **Health-RI core v2.0.3** (`github.com/Health-RI/health-ri-metadata`,
    `Formalisation(shacl)/Core/ValidationShape/HRI-Datamodel-shapes.ttl`).
    v2.1.0 is at release candidate 3; the pack follows when it is final.
- **Operator packs**: `FDPINDEX_CONFORMANCE_PACKS` names a directory of
  packs in the same layout.
- **Deployment setting** (admin API and Settings page): the ordered list of
  active packs, each with the kinds it applies to (narrower than the
  manifest's, never wider). Changing the list or a pack's version validates
  again in the background; results of a removed pack are deleted.
- **Severity**: SHACL's own (`sh:Violation`, `sh:Warning`, `sh:Info`). A
  record *conforms* to a pack when it has no violations; warnings and infos
  are reported, not counted against it.
- **Vocabularies**: the DCAT-AP and HealthDCAT-AP vocabulary shapes check
  `skos:inScheme` and `sh:class` on concepts of the EU vocabularies, so they
  pass only when those concepts' triples are loaded. A pack ships the
  vocabulary triples it needs (the EU tables are a few MB as N-Triples;
  `fdpindex vocab update` already fetches them for labels). They are loaded
  as pySHACL's `ont_graph`, next to the record, never stored with it.

## 3. Validation

- **When**: after a harvest changes a record (the indexer's change sets
  already name changed nodes), when a pack is added or its version changes,
  and on demand (`POST /index/conformance/validate`, admin).
- **How**: jobs in the event table, so they resume after a restart (the
  index's ground rule); a bounded worker pool (default: one less than the
  CPUs, at least one) runs pySHACL in processes, not threads, because SHACL
  validation is CPU-bound Python.
- **Cost**: to be measured in 11.3 on the 150-FDP synthetic corpus and the
  2-million-resource benchmark corpus. The expectation is tens of
  milliseconds per record for DCAT-AP 3 without inference: minutes for
  thousands of records, hours for millions on first validation, then only
  the changed records. If measured costs are too high, the `Validator`
  protocol allows a compiled engine (for example the Rust `rudof`) behind
  the same results.
- **No inference** (`inference="none"`): the shapes are written for
  asserted data, and RDFS inference multiplies the cost.
- **Errors** (a pack that does not parse, a record that times out after
  10 s) are a status of their own, `error`, never a silent pass.

## 4. Results

```sql
CREATE TABLE conformance_result (
  id            bigserial PRIMARY KEY,
  entry_uuid    uuid NOT NULL REFERENCES index_entry(uuid) ON DELETE CASCADE,
  record_iri    text NOT NULL,
  kind          text NOT NULL,
  pack          text NOT NULL,        -- "dcat-ap"
  pack_version  text NOT NULL,        -- "3.0.1"
  status        text NOT NULL,        -- conforms | violations | error
  violations    int  NOT NULL DEFAULT 0,
  warnings      int  NOT NULL DEFAULT 0,
  infos         int  NOT NULL DEFAULT 0,
  content_hash  char(64) NOT NULL,    -- the record's, at validation
  validated_at  timestamptz NOT NULL,
  UNIQUE (entry_uuid, record_iri, pack)
);
CREATE TABLE conformance_finding (
  result_id   bigint NOT NULL REFERENCES conformance_result(id) ON DELETE CASCADE,
  severity    text NOT NULL,          -- violation | warning | info
  focus_node  text NOT NULL,
  path        text,                   -- the property, when the shape has one
  component   text NOT NULL,          -- sh:MinCountConstraintComponent …
  shape       text,
  message     text,                   -- sh:resultMessage, 500 characters
  value       text                    -- the offending value, 200 characters
);
```

- At most 50 findings per result; the counts are complete.
- The search projection reads a record's statuses into `search_value`
  (field `conformance`, values like `dcat-ap` or `dcat-ap:violations`), so
  "conforms to DCAT-AP 3" is an ordinary facet and filter, and strict
  surfaces are an extra condition rather than a new query path.

## 5. Strictness per surface

| Surface | What "strict" means |
|---|---|
| search (page and API) | hits are records that conform to the named pack |
| SPARQL | the derived store leaves out the triples of nonconforming records (rebuilt when results change) |
| catalogue feed (§6) | catalogue records only for conforming records |
| directory | an FDP is listed only when its root record conforms |

A setting per surface: `minimum: none` (default) or `minimum: {pack:
"healthdcat-ap"}`. Admins always see everything, with the status shown.
Lenient surfaces show the status as a badge ("Conforms to DCAT-AP 3") and
a facet.

## 6. The catalogue feed (11.5, replacing 9.6)

`GET /index/catalog`, content-negotiated (Turtle, RDF/XML, JSON-LD), is a
DCAT-AP 3 catalogue that an aggregator such as data.europa.eu can harvest:

```turtle
<https://index.example.org/index/catalog> a dcat:Catalog ;
  dct:title "FDPneo Index of Example"@en ;
  dct:publisher <…the operator, from settings…> ;
  dcat:record <https://index.example.org/index/catalog/records/Zk3…> ;
  dcat:dataset <https://fdp.example.org/dataset/abc> .

<https://index.example.org/index/catalog/records/Zk3…> a dcat:CatalogRecord ;
  foaf:primaryTopic <https://fdp.example.org/dataset/abc> ;
  dct:source <https://fdp.example.org/dataset/abc> ;      # the FDP's record
  dct:issued "…"^^xsd:dateTime ;                           # first harvested
  dct:modified "…"^^xsd:dateTime ;                         # last change seen
  dct:conformsTo <http://data.europa.eu/r5r/> .            # packs it conforms to

<https://fdp.example.org/dataset/abc> … the triples as harvested …
```

- **Resources keep their IRIs.** The index mints IRIs only for its catalogue
  records (from the stable public id).
- **As harvested, not rewritten.** The FDP stays authoritative (FDPneo
  ADR-0021): no normalisation of literal licences into IRIs, no merged
  copies. Copies across FDPs (8.8) stay separate records.
- **Two ways to harvest it.** data.europa.eu's manual names OAI-PMH as the
  preferred protocol (it uses `ListRecords`), and also reads an RDF dump,
  recommending that a large one be split into pages "for example, by using
  the hydra core vocabulary", or a SPARQL endpoint. The index offers:
  - `GET /index/oai?verb=ListRecords&metadataPrefix=dcat_ap` (and
    `Identify`, `ListMetadataFormats`, `GetRecord`, resumption tokens over
    keyset pages, 9.4), one DCAT-AP RDF/XML record per catalogue record;
  - the paged dump at `GET /index/catalog` (Hydra `PartialCollectionView`:
    `hydra:first`, `hydra:next`, `hydra:totalItems`), in RDF/XML, Turtle or
    N-Triples.
  The index's SPARQL endpoint is a third way, but it holds every record,
  lenient or not, so it is not the feed.
- **Their requirements, met by the projection**: ISO 8601 dates, stable
  identifiers (the public id), themes from the EU data-theme vocabulary
  (the Theme facet already separates them).
- **Strict by default for this surface?** Proposed: lenient by default like
  the others, strict in the EHDS profile. See §9, question 4.
- **Validated in a test** with the DCAT-AP 3 shapes (what 9.6 asked for).

## 7. The EHDS deployment profile (11.6)

`FDPINDEX_DEPLOYMENT_PROFILE=ehds` sets, as defaults an administrator can
still change:

- packs: HealthDCAT-AP and DCAT-AP 3;
- the HealthDCAT-AP search overlay as the profile overlay;
- surfaces: catalogue feed strict on HealthDCAT-AP, others lenient;
- the EU vocabularies' labels (the bundled pack already has them).

**Quality and utility labels** (EHDS Art. 78) arrive as harvested metadata.
HealthDCAT-AP Release 8 has `dqv:hasQualityAnnotation` on a dataset, range
`dqv:QualityCertificate`, 0..*; it does not name Art. 78, so reading these
certificates as the label is an assumption to confirm (§9, question 5). The
index shows them as a badge on result cards and the resource page and as a
facet. It does not compute or verify them.

Regulation (EU) 2025/327: Art. 77 (dataset descriptions and catalogue),
Art. 78 (the label), Art. 79 (the EU dataset catalogue). The implementing
acts on the minimum elements of a dataset description (Art. 77(4)) and on
the label (Art. 78) are due by 26 March 2027. The strict EHDS pack tracks
the first; the label's display follows the second.

## 8. API and client

| Method and path | Who | What |
|---|---|---|
| `GET /index/conformance/packs` | anyone | bundled and operator packs, active ones marked |
| `GET`/`PUT /index/conformance/settings` | admin | active packs, surface minimums |
| `GET /index/conformance/entries/{uuid}` | anyone* | an FDP's report: per pack, counts by status; top problems |
| `GET /index/conformance/records?entry=…&pack=…&status=…` | anyone* | records with their status, paged |
| `GET /index/conformance/records/{publicId}` | anyone* | one record's findings per pack |
| `POST /index/conformance/validate` | admin | validate again (all, one FDP, one pack) |
| `GET /index/catalog`, `…/records/{id}` | anyone | the catalogue feed (paged RDF dump) |
| `GET /index/oai` | anyone | the catalogue feed over OAI-PMH |

\* Public by default, like the harvested metadata; a setting can make
reports admin-only.

Client: a **Conformance** tab on the FDP's page (summary per pack, top
problems with "show records", per record findings), a badge and a facet in
search, and a **Conformance** section in Settings (packs, surfaces).

## 9. Questions for review

1. **pySHACL as a new runtime dependency.** It is pure Python and already
   in FDPneo's server. The alternative is a compiled engine from the start.
   Proposal: pySHACL, measure, keep the protocol.
2. **Which HealthDCAT-AP release.** TASKS.md names R5; the current one is
   Release 8, still a draft specification, whose shapes import DCAT-AP
   3.0.0 while the DCAT-AP pack is 3.0.1. Proposal: bundle Release 8 with
   its own DCAT-AP 3.0.0 import, and add R5 only if FDPs in the field still
   publish it.
3. **Reports public or admin-only by default.** Proposal: public, because
   they describe public metadata and their purpose is feedback to data
   holders; a setting turns that off.
4. **Catalogue feed strict or lenient by default.** Proposal: lenient, with
   the conformance of each record stated (`dct:conformsTo`); strict in the
   EHDS profile.
5. **Quality and utility labels.** HealthDCAT-AP R8 offers
   `dqv:hasQualityAnnotation` → `dqv:QualityCertificate` without naming
   Art. 78. Proposal: show quality certificates now, and adopt the label's
   own encoding when the Art. 78 implementing act is out.
7. **OAI-PMH.** A protocol the index does not speak today, added because
   data.europa.eu prefers it. Proposal: build it with the feed (a small,
   read-only subset: four verbs, one metadata format).
6. **Records across nodes.** A publisher described on its own page of the
   FDP is not part of the dataset's record, so a shape requiring
   `foaf:name` on the publisher fails for that FDP. Proposal: accept that
   (it is what the FDP serves for the dataset), or follow `dct:publisher`
   one node deep as a pack option.
