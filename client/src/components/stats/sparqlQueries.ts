/**
 * SPARQL versions of the Statistics page's charts (TASKS 15.1), for the
 * "Open as SPARQL query" links. They count by RDF type over the harvested
 * graphs (one named graph per FDP) and the entries graph, so their numbers can
 * differ a little from the page's, which follow the search's record kinds.
 */

const PREFIXES = `PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
PREFIX fdpindex: <urn:fdpindex:>
`;

export const FDPS_BY_STATE = `${PREFIXES}
# FDPs in the index by state
SELECT ?state (COUNT(?fdp) AS ?fdps)
WHERE {
  GRAPH <urn:fdpindex:entries> { ?fdp fdpindex:state ?state }
}
GROUP BY ?state
ORDER BY DESC(?fdps)
`;

export const RECORDS_BY_TYPE = `${PREFIXES}
# Harvested records by DCAT type
SELECT ?type (COUNT(DISTINCT ?record) AS ?records)
WHERE {
  VALUES ?type { dcat:Catalog dcat:Dataset dcat:DatasetSeries dcat:DataService dcat:Distribution }
  GRAPH ?fdp { ?record a ?type }
}
GROUP BY ?type
ORDER BY DESC(?records)
`;

export const DATASETS_BY_YEAR = `${PREFIXES}
# Datasets by the year of their dct:issued
SELECT ?year (COUNT(DISTINCT ?dataset) AS ?datasets)
WHERE {
  GRAPH ?fdp { ?dataset a dcat:Dataset ; dct:issued ?issued }
  BIND (YEAR(?issued) AS ?year)
  FILTER (BOUND(?year))
}
GROUP BY ?year
ORDER BY ?year
`;

export const LARGEST_FDPS = `${PREFIXES}
# FDPs with the most harvested metadata nodes
SELECT ?fdp ?title ?nodes
WHERE {
  GRAPH <urn:fdpindex:entries> {
    ?fdp fdpindex:harvestedNodes ?nodes .
    OPTIONAL { ?fdp dct:title ?title }
  }
}
ORDER BY DESC(?nodes)
LIMIT 10
`;
