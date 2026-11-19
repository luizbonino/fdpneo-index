# Sources beyond FDPs — design

- Status: **proposed, for review** (TASKS 12.1 to 12.3).
- Date: 2026-11-19
- Context: FDPneo `docs/architecture/discovery.md` §1 and §2 (the
  connector protocol, the source record), adapted to this index: PostgreSQL
  with `harvested_node` as the store, one graph per source, the reference
  index's wire contract kept.

## 0. Decisions in one page

1. **A source has a kind.** `index_entry` gains `kind`: `fdp` (today's
   entries, the default), `dcat` and `ckan`. FDPs still arrive by ping;
   other sources are registered by an administrator, or through the Register
   page with the kind chosen.
2. **One connector protocol**, as discovery.md §2.1 describes it:
   `probe` (liveness and self-description), `enumerate` (every record with
   a cheap change hint), `fetch` (one record as RDF). The harvest service
   drives a connector and keeps everything it does today: change sets,
   conditional requests, stale nodes kept on failure, the outbound safety
   rules. **The FDP walk becomes the first connector**, with no change in
   behaviour.
3. **Records land as nodes.** Every connector yields node-sized graphs
   (one subject with its blank nodes) into `harvested_node`, so search,
   SPARQL, labels, copies and conformance need no change.
4. **DCAT sources** (12.2): a dereferenceable catalogue or a dump file, in
   any RDF format the index parses.
5. **CKAN** (12.3) goes through its DCAT endpoint where the portal has
   `ckanext-dcat`, which most do. Otherwise the CKAN API, mapped to DCAT by
   a documented mapping, with the mapped records marked as derived.
6. **Molgenis** EMX2 publishes an FDP of its own: the `fdp` connector
   harvests it. Older Molgenis versions are out of scope unless asked for.
7. **The wire contract holds.** `/index/entries` keeps its shape; non-FDP
   sources carry `kind`, which reference clients ignore. Pings stay
   FDP-only.

## 1. The connector protocol

```python
class Connector(Protocol):
    kind: str  # "fdp", "dcat", "ckan"

    async def probe(self, source: IndexEntry) -> Probe: ...
    # liveness and self-description, for registration and liveness checks

    def enumerate(self, source: IndexEntry) -> AsyncIterator[RemoteRef]: ...
    # every record the source exposes: (iri, url, change hint)

    async def fetch(self, ref: RemoteRef) -> list[NodeGraph]: ...
    # one record as one or more node graphs
```

- **Egress**: a connector fetches under the source's origin only, through
  `OutboundClient` (ADR-0002). A source may list extra hosts (a dump on a
  CDN), set by an administrator.
- **Liveness checks** (ADR-0004) call `probe`; states and expiry stay as
  they are.
- **Per-source options** (4.6 to 4.9: directory only, own interval) apply
  to every kind.

## 2. The FDP connector

Today's walk, unchanged: the root, then the child predicates
(`ldp:contains`, `dcat:dataset`, `fdp-o:metadataCatalog` …), with
conditional requests per node. Which predicates lead to children becomes
**data** (a small navigation file, overridable like the search profile)
instead of a constant in code, as discovery.md asks ("traversal is data,
not code"). Navigation SHACL as the reference FDP uses it is a possible
later format (§6, question 2).

## 3. DCAT sources (12.2)

**A dereferenceable catalogue.** The registered URL answers RDF with a
`dcat:Catalog`.

- `enumerate` lists its `dcat:dataset`, `dcat:service`, `dcat:catalog`
  and `dcat:record` objects. It follows paging when the document has it:
  Hydra (`hydra:next`, `hydra:nextPage`, `hydra:view`), which data
  portals use for large catalogues.
- `fetch` dereferences a resource when it has its own URL on the source's
  origin. Otherwise it takes the resource's description from the catalogue
  document itself: the subject, its blank nodes, and its distributions
  described inline.

**A dump file.** The registered URL is one RDF file, optionally gzipped.

- It is fetched with a conditional request; unchanged means nothing to do.
- It is split into nodes by subject.
- A size limit per source protects the index (default 200 MB, raised by an
  administrator).

Both forms give the catalogue root the role an FDP root has today, so a
DCAT source appears in the directory like an FDP.

## 4. CKAN (12.3)

- **Probe**: `GET /api/3/action/status_show` gives the CKAN version and its
  extensions.
- **With `ckanext-dcat`** (`dcat` in the extensions): the DCAT connector on
  the portal's catalogue endpoint (`/catalog.ttl`, paged with Hydra). This
  is the portal's own DCAT, and the preferred path.
- **Without it**: `package_search` with `start` and `rows`, each package
  mapped to DCAT:

  | CKAN | DCAT |
  |---|---|
  | `title`, `notes` | `dct:title`, `dct:description` |
  | `tags[].name` | `dcat:keyword` |
  | `groups[]` | `dcat:theme` (as literals) |
  | `organization` | `dct:publisher` (`foaf:name`) |
  | `license_url` / `license_id` | `dct:license` |
  | `metadata_created`, `metadata_modified` | `dct:issued`, `dct:modified` |
  | `url` | `dcat:landingPage` |
  | `resources[]` | `dcat:distribution` (`dcat:accessURL`, `dct:format`, `dcat:mediaType`) |

  The package's IRI is its CKAN URL (`/dataset/<name>`). The mapped nodes
  carry `prov:wasDerivedFrom` the API URL, so search and the conformance
  report can tell them from published DCAT.

## 5. Registration and the client

- **Register page**: a choice of kind (FAIR Data Point, DCAT catalogue or
  dump, CKAN portal). An FDP is still registered by its ping. Any kind may
  be registered here, and the immediate probe (4.9) shows what the index
  found.
- **Directory and FDP pages**: the kind as a badge. The page title stays
  "FAIR Data Points" while every source is an FDP, and becomes "Sources"
  once others exist.
- **Search**: the FDP facet becomes "Source"; its values carry the kind.

## 6. Questions for review

1. **Kinds in the first round**: `dcat` (endpoint and dump) and `ckan` now;
   anything else (Dataverse, Zenodo communities, OAI-PMH sources)
   later, on request. Agreed?
2. **Traversal as data**: a small YAML navigation file (child predicates per
   type) now, navigation SHACL only if FDPs that need it turn up?
3. **CKAN without DCAT**: worth the mapping, or DCAT-only CKAN support?
4. **Self-service for non-FDP sources**: registered by anyone (with the
   approval policy deciding), or by administrators only?
5. **Naming**: should the product's user-facing wording move from "FAIR
   Data Points" to "sources" once non-FDP sources exist, or keep FDPs
   first and list other sources separately?
