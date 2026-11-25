# Using the FDPneo Index

For people looking for data: what the index holds, and how to find things
in it. No account is needed for anything on this page.

*Also in [Nederlands](nl/user-guide.md), [Deutsch](de/user-guide.md),
[Français](fr/user-guide.md), [Español](es/user-guide.md),
[Italiano](it/user-guide.md) and [Português](pt/user-guide.md).*

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
| `title:registry` | *registry* in the title (also `description:`) |
| `keyword:asthma` | resources with that keyword |
| `theme:Health`, `theme:HEAL` | a theme by its name in any language, its code or its IRI |
| `publisher:"Alpha University"` | publishers whose name contains the words |
| `identifier:10.5072/abc` | a DOI or another identifier |
| `-keyword:mouse` | without that keyword |

Any field the index has works the same way (`format:csv`, `licence:…`,
`subject:…`). A prefix on a field acts as a filter: its values show ticked
in the filters at the side.

A search that finds nothing proposes a correction when one finds
something: *brest cancr* offers **Did you mean breast cancer**.

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
  **FAIR Data Point**, and more under **More filters**, among them
  **Spatial coverage**: the places the data cover. A country is one entry
  however an FDP names it (the EU's list, GeoNames or a code such as `NL`).
- **+ Add a filter**: any property found in the metadata, most used first
  (for example `dct:language` or `dct:conformsTo`). Added filters stay in
  the page's address, so a shared link carries them, and this browser
  remembers them. **Remove** takes one away.

Number filters, such as **Number of records**, count results in ranges
and also take a lowest and a highest value of your own. Yes/No filters say
whether a resource states something at all (for example whether it
concerns personal data, on indexes that use the HealthDCAT-AP filters).

**Area** finds data whose area overlaps a box you give as four numbers:
west, south, east, north (for the Netherlands, about `3.3, 50.7, 7.3,
53.6`). Only resources that state an area as coordinates have one.

**Dates** narrows by when a resource was issued or last modified. **Period
covered** finds data whose temporal coverage overlaps the years you give:
type a year (`2010`), a month (`2010-05`) or a date (`2010-05-31`), or
leave one side open. A dataset covering 2013 to 2025 matches 2012–2013;
one with no end date matches every later year. Resources that do not
state a temporal coverage are left out once this filter is set.

**Meets profile** keeps records that this index checked against a metadata
profile (DCAT-AP, HealthDCAT-AP …) and found without violations; a
dataset's result says which profiles it meets. **Meets what it declares**
keeps records that meet the profiles they name themselves
(`dct:conformsTo`), such as their FDP's own; the other profiles are checked
whether or not a record claims them. On indexes for health data,
**Quality certificate** keeps datasets whose holder states a quality
certificate; the certificate itself is on the dataset's page. Some indexes
show only records that meet a profile.

Very broad searches count their filters on a sample: those counts show
with ≈.

### Results

Each result shows its type, where it comes from (the FDP and the catalog),
a description, its themes, subjects and keywords (click one to filter on
it), the formats of its files and the period its data cover. When other
FDPs list the same resource, **Also in n other FDPs** opens the list; the
resource's page shows them under **Also published in**. **Group copies**
shows such a resource once, as the copy the search ranks first; the count
then says how many resources the groups stand for. **Open at source** goes to the
resource on its FDP. A resource's own page shows every value, its parents
and children, and its metadata as harvested (Turtle, JSON-LD or
N-Triples), and, under **Conformance**, how it fares against each profile
the index checks: its violations, which break a profile's rules, and its
warnings, which are recommendations.

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
- **Editor**: write SPARQL with suggestions that fit where you are:
  prefixes after `PREFIX`, the properties in the data where a property
  goes, classes after `a`, functions and variables inside `FILTER( )` and
  `BIND( )`, and `ORDER BY`, `LIMIT` and the like after the pattern.
  Mistakes are marked as you type; `{ }` blocks fold. Ctrl/Cmd+Enter runs
  the query.

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
the search page's address; [API examples](api.md) shows it, with the
DCAT-AP feed (`/index/catalog`) and OAI-PMH (`/index/oai`) for harvesting
everything. See `/docs` on the index for every path.

## Statistics

**Statistics** shows what the index holds and how that changes: FDPs listed
and active, records by type, the FDPs with most records, the share of records
meeting each profile, FDPs by month of registration, and datasets by the year
they were issued. It counts what visitors see, as the directory and the search
do. Counts over time start when the index first counts (it counts every hour,
and keeps the day's last count), so a new index shows a line only from its
second day. Every chart has a table under **Show as a table**, and **Open as
SPARQL query** asks the same question of SPARQL, where you can change it; SPARQL
counts by RDF type, so its numbers can differ a little.

Two maps show where the FDPs are (placed by an administrator's choice, the
FDP's own statement, its publisher, its domain or its server, in that order)
and where the data is about (the records' spatial coverage); choose a
country on the second to search its records.

## Languages

The interface speaks English, Dutch, German, French, Spanish, Italian and
Portuguese: the menu at the top picks one, and the index remembers it in
this browser (at first it follows your browser's languages). Titles,
descriptions and labels from the metadata follow the interface language when
an FDP provides them in it, then the index's fallback languages; **Metadata
in** on the search page picks another language for them.

## Registering your FDP

See the **Register an FDP** page: configure your FDP to ping the index, or
enter its address there to register it and see at once what the index
found. [FDPs and the index](data-holders.md) explains what happens next and
how to read your FDP's conformance report.
