# Search and SPARQL: notes for operators

How the search and the SPARQL endpoint are built from the harvested
metadata, what can be tuned, and what to do when something looks wrong.
The design is in [design/search.md](design/search.md) and ADR-0005;
measured performance in [search-performance.md](search-performance.md).

## Where the data comes from

PostgreSQL's `harvested_node` holds every FDP's metadata as harvested. Two
things are derived from it, in the background, after every harvest:

- **The search tables** (`search_resource`, `search_value`, `search_label`,
  `search_property`): one row per DCAT resource, its values per field, and
  full-text vectors.
- **The SPARQL store** (`FDPINDEX_SPARQL_DIR`, the `sparql-data` volume): an
  embedded Oxigraph store with one named graph per FDP, queried by worker
  processes from read-only snapshots.

Both are rebuilt from PostgreSQL. At start-up the index compares what each
holds with `harvested_node` and catches up, so a restart after a crash, a
restore, or an upgrade that changes the projection needs nothing else.

## The search profile

`server/src/fdpindex/search/profile.default.yaml` maps predicates to search
fields: which predicates feed titles, keywords, themes, subjects,
publishers, formats …, which fields are facets, their weights in the
ranking, and how common spellings fold into one value (`csv`, `text/csv`
and the EU file-type IRI are one format). It covers DCAT 2 and 3, DCAT-AP,
FDP-O, the Java FDP's shapes, FOAF and vCard.

Spatial coverage (`dct:spatial`) folds the names of one country into the
EU authority IRI: GeoNames (`https://sws.geonames.org/2750405/`, also
`www.geonames.org/…/netherlands.html`), lexvo, and ISO 3166 codes written
as text (`"NL"`, `"NLD"`). The mapping is part of the label pack, from
GeoNames' country list (CC BY 4.0). Other GeoNames places keep one form and
get their names from the concept resolver, as do the EU's 4 700 places.

Copies of a resource in several FDPs are recognised by one key per
dataset, series, service or catalog: the strongest identifier it states,
in this order: a DOI wherever it appears; a global identifier IRI or URN
(`dct:identifier`, `adms:identifier`); the target of `owl:sameAs`, so a
mirror that names its original groups with it; and its own IRI. Landing
pages and local identifiers ("dataset-1") are not used. Hits carry their
copies (`duplicates`, `duplicateCount`); `collapse=true` keeps the
best-placed copy of each group, `totalResults` then counts groups and
`resources` the resources.

Temporal coverage (`dct:temporal`) is read in every form FDPs use: a
period with `dcat:startDate`/`dcat:endDate` (DCAT 2 and 3),
`schema:startDate`/`schema:endDate` (DCAT 1, DCAT-AP 1.x), OWL-Time
`time:hasBeginning`/`time:hasEnd`, or a literal such as `"2010/2020"`. It
is stored as a first and a last day, which `temporalFrom`/`temporalTo`
compare by overlap. In **Show as SPARQL**, a period written as a literal is
not matched; the query says so.

The profile also captures **every property** one step from a resource (IRI
values and literals up to 100 characters), which is what lets visitors add a
filter on any property. `capture.properties: false` in the profile turns
that off; it roughly halves the size of `search_value`.

Administrators change the profile of their index with an *overlay* on the
bundled one (`PUT /index/search/profile`, JSON or YAML, or **Settings →
Search profile**): a field by name replaces or adds one, `null` removes
one, and `prefixes`, `types`, `normalise`, `cards`, `landing` and
`capture` can be changed too. Because it is an overlay, the index still
receives the bundled profile's improvements in later releases. For example,
a filter on the version an FDP declares:

```yaml
fields:
  fdpVersion:
    kind: literal
    paths: [dcat:version, dct:hasVersion]
    facet: more
    label: {en: FDP version}
```

Fields of kind `number` count their values in the `buckets` given and are
filtered by range too (`<field>Min`, `<field>Max`, for example
`recordsMin=1000`). Fields of kind `boolean` are a Yes/No filter; with
`presence: true`, Yes means the property is stated at all and No that it is
not.

**HealthDCAT-AP** comes as a ready overlay
(`server/src/fdpindex/search/examples/healthdcat-ap.yaml`, Release 8 IRIs):
numbers of individuals and typical ages, health category and theme, coding
systems, the health data access body, applicable legislation, and Yes/No
filters for an analytics distribution and for personal data. Load it under
**Settings → Search profile → Load the HealthDCAT-AP example**, review it,
and save; or `GET /index/search/profile/examples/healthdcat-ap` and `PUT`
its `overlay`.

A change, in a release or by an administrator, re-projects every entry
once in the background; search answers from the old rows meanwhile, and
new filters fill in as entries are done. `DELETE /index/search/profile`
restores the bundled profile. Which filters the page shows, and in what
order, is a separate setting: [administering.md](administering.md#search-filters).

## Limits

| What | Setting | Default |
|---|---|---|
| Searches per minute per anonymous address | `FDPINDEX_SEARCH_RATE_PER_MINUTE` | 120 |
| Time per search (visitors, administrators) | `FDPINDEX_SEARCH_TIMEOUT_SECONDS`, `…_ADMIN_TIMEOUT_SECONDS` | 5 s, 30 s |
| Results reachable by paging | fixed | 10 000 |
| Facet counts on a sample | above 50 000 matches | about 10 000 sampled, shown with ≈ |
| Ranking of very broad searches | above 50 000 matches | the 5 000 most recently changed |
| SPARQL queries per minute per anonymous address | `FDPINDEX_SPARQL_RATE_PER_MINUTE` | 30 |
| Time per SPARQL query (visitors, administrators) | `FDPINDEX_SPARQL_TIMEOUT_SECONDS`, `…_ADMIN_TIMEOUT_SECONDS` | 10 s, 60 s |
| Rows, bytes, query length | `FDPINDEX_SPARQL_MAX_ROWS`, `…_MAX_BYTES`, `…_MAX_QUERY_CHARS` | 10 000, 10 MiB, 20 000 |
| SPARQL worker processes, memory each | `FDPINDEX_SPARQL_WORKERS`, `…_WORKER_MAX_MB` | 2, 1 024 MB |

A search that hits its time limit answers 503 ("busy"). A SPARQL query
over its time budget answers 408, one over its worker's memory 413, and
when every worker is taken a query answers 503; one address runs one query
at a time (429 otherwise).
SPARQL accepts queries only: updates, `LOAD` and `SERVICE` are refused
before they reach a worker, and workers open the store read-only.

## Concept labels

With `FDPINDEX_LABELS_RESOLVE=true`, a background resolver fetches labels,
synonyms and broader concepts of the concepts the metadata names (MeSH,
Wikidata, EDAM, ROR …) from an allow-list of vocabulary services, a few
requests per second, and re-projects the entries that use them (ADR-0006).
Name the deployment and a contact in `FDPINDEX_USER_AGENT`: Wikimedia
throttles anonymous clients. `GET /index/search/stats` counts resolved,
missing and failed concepts.

## When something looks wrong

- **Search results lag behind a harvest**: projection runs in the
  background; the search page says how many FDPs are still being indexed.
  `GET /index/search/stats` shows when each FDP was last projected.
- **An FDP's resources are missing from search**: check that the entry is
  accepted, not directory only, and was harvested (its page shows the
  harvest). Expired entries are hidden when the expiry policy says so.
- **Force a re-projection**: `POST /index/search/reindex` (all) or with
  `{"entry": "<uuid>"}` (one).
- **Rebuild the SPARQL store**: stop the server, remove the `sparql-data`
  volume, start the server; the store is loaded again from PostgreSQL.
  ```sh
  docker compose -f deploy/compose.yaml --env-file deploy/.env stop server
  docker volume rm fdpindex_sparql-data
  docker compose -f deploy/compose.yaml --env-file deploy/.env up -d server
  ```
- **Slow searches at scale**: see the memory and index notes in
  [search-performance.md](search-performance.md).
