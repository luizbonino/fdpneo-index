# Using the FDPneo Index

For people looking for data: what the index holds, and how to find things
in it. No account is needed for anything on this page.

## What is in the index

FAIR Data Points (FDPs) publish metadata about data: catalogs, datasets,
their distributions (files and APIs) and data services. They register with
the index by pinging it. The index then checks each FDP regularly and
harvests its metadata, so you can search all of them in one place.

- **Directory** (the home page): every FDP the index lists, with its state.
  *Active* means the index reached it recently; *Unreachable* and *Invalid*
  mean the last checks failed; *Inactive* means it has not been checked
  for a while.
- **Search**: the harvested metadata of every listed FDP.
- **SPARQL**: the same metadata as a knowledge graph, for precise questions.

## Searching

Type words in the search box. The search looks in titles, descriptions,
keywords, publishers and the names of subjects, in the metadata's own
languages, and finds other forms of a word (searching *registry* finds *registries*).

| You type | It finds |
|---|---|
| `breast cancer` | resources with both words |
| `"lung function"` | that exact phrase |
| `cancer or tumour` | either word |
| `cancer -lung` | *cancer* without *lung* |

When the index resolves subject names (an administrator's choice), a search
also finds subjects by their names in other languages and by broader
terms: *borstkanker* or *Brustkrebs* find datasets tagged with the Wikidata
item for breast cancer, and *neoplasms* finds those tagged *Breast
Neoplasms*.

The tabs under the box narrow the results to datasets, catalogs, data
services or FDPs.

### Filters

The filters at the side count, for each value, how many results it would
give. Ticking two values of one filter shows resources with either; ticks in
different filters must all hold. Every active filter shows as a chip above
the results; click it to remove it.

- **Theme**: the 13 broad EU data themes, such as Health.
- **Subject**: specific topics from vocabularies like MeSH, Wikidata or
  EDAM. The same topic from two vocabularies is one entry when a
  vocabulary says they match (Wikidata's *dementia* is MeSH's *Dementia*).
  **Browse Subject as a tree** shows each subject under its broader ones;
  ticking one there finds it and everything narrower (*Neoplasms by Site*
  includes *Breast Neoplasms*).
- **Keyword**, **Publisher**, **Format**, **Licence**, **Access rights**,
  **FAIR Data Point**, and more under **More filters**.
- **+ Add a filter**: any property found in the metadata, most used first
  (for example `dct:language` or `dct:conformsTo`). Added filters stay in
  the page's address, so a shared link carries them, and this browser
  remembers them. **Remove** takes one away.

**Dates** narrows by when a resource was issued or last modified. **Period
covered** finds data whose temporal coverage overlaps the years you give:
type a year (`2010`), a month (`2010-05`) or a date (`2010-05-31`), or
leave one side open. A dataset covering 2013 to 2025 matches 2012–2013;
one with no end date matches every later year. Resources that do not
state a temporal coverage are left out once this filter is set.

Very broad searches count their filters on a sample: those counts show
with ≈.

### Results

Each result shows its type, where it comes from (the FDP and the catalog),
a description, its themes, subjects and keywords (click one to filter on
it), the formats of its files and the period its data cover. **Open at source** goes to the
resource on its FDP. A resource's own page shows every value, its parents
and children, and its metadata as harvested (Turtle, JSON-LD or
N-Triples).

**Copy link** copies the address of the search with its filters. **Show as
SPARQL** opens the same search as a SPARQL query, to refine it; the query
says where SPARQL can only approximate the search (word matching and
ranking, for instance).

## SPARQL

The SPARQL page offers three ways in:

- **Questions**: ready-made queries with a form for their parameters, such
  as "Datasets by theme", "Datasets by licence" or "FDPs by country". No SPARQL needed.
- **Builder**: choose a type, add conditions with suggestions from the
  data, choose a list or a count; the SPARQL updates as you go.
- **Editor**: write SPARQL with autocompletion of the classes, properties
  and prefixes in the data, and inline validation. Ctrl/Cmd+Enter runs it.

Results show as a table, as a chart when they have a category and a
number, or raw, and download as CSV, TSV, JSON or Turtle. Recent queries
stay in your browser; every query has a link you can share.

### The data

Each listed FDP's metadata is a named graph, named by the FDP's address;
the default graph is all of them together. The graph
`urn:fdpindex:entries` describes the FDPs themselves: `fdpindex:state`,
`dct:title`, `r3d:country`, `fdpindex:lastRetrieval`,
`fdpindex:harvestedNodes`. Common prefixes (`dcat:`, `dct:`, `foaf:`,
`fdp-o:`, …) need no declaration.

```sparql
# Datasets per FDP
SELECT ?fdp (COUNT(?dataset) AS ?datasets)
WHERE { GRAPH ?fdp { ?dataset a dcat:Dataset } }
GROUP BY ?fdp
ORDER BY DESC(?datasets)
```

```sparql
# Datasets about a MeSH descriptor, with their publisher
SELECT ?dataset ?title ?publisher WHERE {
  ?dataset a dcat:Dataset ;
           dct:subject <http://id.nlm.nih.gov/mesh/D001943> ;
           dct:title ?title .
  OPTIONAL { ?dataset dct:publisher/foaf:name ?publisher }
}
```

```sparql
# Active FDPs and their countries
SELECT ?fdp ?title ?country WHERE {
  GRAPH <urn:fdpindex:entries> {
    ?fdp fdpindex:state "ACTIVE" ; dct:title ?title .
    OPTIONAL { ?fdp r3d:country ?country }
  }
}
```

### From a program

The endpoint follows the SPARQL 1.1 Protocol at `/index/sparql`:

```sh
curl -H 'Accept: text/csv' --data-urlencode \
  'query=SELECT ?fdp (COUNT(?d) AS ?n) WHERE { GRAPH ?fdp { ?d a dcat:Dataset } } GROUP BY ?fdp' \
  https://index.example.org/index/sparql
```

Results come as SPARQL JSON, XML, CSV or TSV; CONSTRUCT and DESCRIBE as
Turtle, N-Triples or JSON-LD. Only queries are accepted (no updates, no
`SERVICE`). Each query has a time budget and a row cap, and anonymous
callers a rate limit per minute; `GET /index/config` tells you this
index's values.

The search has an API too: `GET /index/search` takes the same parameters as
the search page's address. See `/docs` on the index for every path.

## Registering your FDP

See the **Register an FDP** page: configure your FDP to ping the index, or
enter its address there to register it and see at once what the index
found.
