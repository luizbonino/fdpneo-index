# API examples

Worked examples for programs that use an index. Every request below runs in
the index's test suite against a test index (`tests/test_docs.py`), so they
stay correct. Replace `https://index.example.org` with your index's address.
The full list of operations is in the [API reference](api-reference.md).

Everything public needs no credentials. Visitors' searches and SPARQL
queries are rate-limited per address (120 searches and 30 queries a minute by
default); a `429` answer says when to try again.

## Search

The faceted search as JSON:

```http
GET /index/search?q=registry&type=dataset&size=5
```

```sh
curl -s 'https://index.example.org/index/search?q=registry&type=dataset&size=5'
```

```python
import httpx

hits = httpx.get(
    "https://index.example.org/index/search",
    params={"q": "registry", "type": "dataset", "size": 5},
).json()["hits"]
for hit in hits:
    print(hit["title"], hit["iri"], hit["fdp"]["clientUrl"])
```

Filters are parameters named after the filter; the values are the ones
`facets` returns. Several values of one filter are alternatives; `!` excludes
a value:

```http
GET /index/search?type=dataset&theme=http://publications.europa.eu/resource/authority/data-theme/HEAL&facets=theme,conformance
```

**Records that meet a profile** this index checks (the "Meets profile"
filter; values are the profiles' specification IRIs):

```http
GET /index/search?type=dataset&conformance=https://semiceu.github.io/DCAT-AP/releases/3.0.1/
```

Field prefixes in `q` narrow words to one field: `title:cancer`,
`keyword:asthma`, `publisher:"Example University"`.

**Typed filters**: `POST /index/search` takes the same search as JSON with a
list of filters (see [Search and SPARQL](search-and-sparql.md)):

```http
POST /index/search
Content-Type: application/json

{"q": "", "types": ["dataset"], "filters": [{"field": "modified", "op": "gte", "value": "2020-01-01"}]}
```

**All results**: page with `page` and `size` (up to 100), or follow `cursor`
for a stable walk through everything:

```http
GET /index/search?type=dataset&size=2&sort=modified
```

**As RDF**: `output=ttl` (or `jsonld`, `nt`, `rdf`) returns the hits as a
DCAT catalogue page:

```http
GET /index/search?q=registry&output=ttl
```

## One resource

A hit's `id` names it in the index:

```http
GET /index/search/resources/{id}
```

The answer has its values with labels, its parents and children, and its
copies in other FDPs; `?format=ttl` returns the harvested triples.

## SPARQL

```http
GET /index/sparql?query=SELECT%20(COUNT(*)%20AS%20?n)%20WHERE%20%7B%20GRAPH%20?g%20%7B%20?d%20a%20dcat:Dataset%20%7D%20%7D
```

```sh
curl -s https://index.example.org/index/sparql \
  -H 'Accept: application/sparql-results+json' \
  --data-urlencode 'query=SELECT ?title WHERE { GRAPH ?g { ?d a dcat:Dataset ; dct:title ?title } } LIMIT 10'
```

Common prefixes (`dcat:`, `dct:`, `foaf:` …) are declared for you. Each FDP is
a named graph (its address); `fdpindex:entries` describes the FDPs
themselves. Read-only queries only, with a time and row budget.

## The catalogue feed

The harvested metadata as a DCAT-AP catalogue, page by page (follow
`hydra:next`):

```http
GET /index/catalog?size=100
Accept: text/turtle
```

and over OAI-PMH, for harvesters that speak it:

```http
GET /index/oai?verb=ListRecords&metadataPrefix=dcat_ap
```

## FDPs

The directory, in the Java FAIR Data Point index's format:

```http
GET /index/entries?page=0&size=20
```

An FDP's conformance report, when the index publishes reports:

```http
GET /index/conformance/entries/{uuid}
```

## Statistics

What the index holds now, FDPs by month of registration, datasets by year of
issue, and daily counts over the last `days` (365 by default), as the
Statistics page shows them:

```http
GET /index/stats?days=90
```

`history.series` has one value per day for each count (`fdps.total`,
`fdps.state.ACTIVE`, `records.kind.dataset` …), `null` where the index took no
count that day. Conformance shares are there only for those who may read
conformance reports.

## Search engines and browsers

An OpenSearch description lets browsers add the index as a search engine:

```http
GET /index/search/opensearch.xml
```
