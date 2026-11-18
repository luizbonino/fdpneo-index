"""A small multi-FDP DCAT corpus for SPARQL tests: client URL → N-Triples per node.

Two FDPs with DCAT 3 metadata (one with a licence gap and a data service) and
one Java-FDP-style FDP using the legacy namespace, so the example queries are
exercised on the shapes the index really harvests.
"""

from __future__ import annotations

from rdflib import Graph

PREFIXES = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix fdp-o: <https://w3id.org/fdp/fdp-o#> .
@prefix r3d: <http://www.re3data.org/schema/3-0#> .
"""

FDPS: dict[str, dict[str, str]] = {
    "https://fdp.alpha.example": {
        "https://fdp.alpha.example": """
<https://fdp.alpha.example> a fdp-o:FAIRDataPoint, r3d:Repository ;
  dct:title "Alpha FDP"@en ; fdp-o:metadataCatalog <https://fdp.alpha.example/catalog/c1> .
""",
        "https://fdp.alpha.example/catalog/c1": """
<https://fdp.alpha.example/catalog/c1> a dcat:Catalog ; dct:title "Alpha health catalog"@en ;
  dct:modified "2026-10-01T10:00:00Z"^^xsd:dateTime ;
  dct:conformsTo <https://example.org/profiles/catalog> ;
  dcat:dataset <https://fdp.alpha.example/dataset/d1>, <https://fdp.alpha.example/dataset/d2> .
""",
        "https://fdp.alpha.example/dataset/d1": """
<https://fdp.alpha.example/dataset/d1> a dcat:Dataset ; dct:title "Breast cancer registry"@en ;
  dct:description "Biopsies recorded nationwide."@en ;
  dcat:keyword "cancer"@en, "registry"@en ; dcat:theme <http://publications.europa.eu/resource/authority/data-theme/HEAL> ;
  dct:license <http://creativecommons.org/licenses/by/4.0/> ;
  dct:publisher [ a foaf:Agent ; foaf:name "University Hospital Alpha" ] ;
  dct:modified "2026-11-01"^^xsd:date ;
  dct:temporal [ dcat:startDate "2017-01-01"^^xsd:date ; dcat:endDate "2023-12-31"^^xsd:date ] ;
  dcat:distribution <https://fdp.alpha.example/distribution/x1> .
""",
        "https://fdp.alpha.example/dataset/d2": """
<https://fdp.alpha.example/dataset/d2> a dcat:Dataset ; dct:title "Lung function tests"@en ;
  dcat:keyword "cancer"@en, "lungs"@en ; dcat:theme <http://publications.europa.eu/resource/authority/data-theme/HEAL> ;
  dct:publisher [ foaf:name "University Hospital Alpha" ] ;
  dct:modified "2025-03-01"^^xsd:date .
""",
        "https://fdp.alpha.example/distribution/x1": """
<https://fdp.alpha.example/distribution/x1> a dcat:Distribution ; dct:title "CSV export"@en ;
  dcat:mediaType <https://www.iana.org/assignments/media-types/text/csv> ;
  dct:license <http://creativecommons.org/licenses/by/4.0/> .
""",
    },
    "https://fdp.beta.example/": {
        "https://fdp.beta.example/": """
<https://fdp.beta.example/> a fdp-o:FAIRDataPoint ; dct:title "Beta FDP"@en ;
  fdp-o:metadataCatalog <https://fdp.beta.example/catalog/k> .
""",
        "https://fdp.beta.example/catalog/k": """
<https://fdp.beta.example/catalog/k> a dcat:Catalog ; dct:title "Beta genomics"@en ;
  dcat:dataset <https://fdp.beta.example/dataset/g> ; dcat:service <https://fdp.beta.example/api> .
""",
        "https://fdp.beta.example/dataset/g": """
<https://fdp.beta.example/dataset/g> a dcat:Dataset ; dct:title "Genome cohort"@en ;
  dcat:keyword "genomics"@en ; dct:license <https://opensource.org/licenses/MIT> ;
  dct:publisher [ foaf:name "Beta Institute" ] ;
  dcat:distribution <https://fdp.beta.example/distribution/vcf> .
""",
        "https://fdp.beta.example/distribution/vcf": """
<https://fdp.beta.example/distribution/vcf> a dcat:Distribution ; dct:format "VCF" .
""",
        "https://fdp.beta.example/api": """
<https://fdp.beta.example/api> a dcat:DataService ; dct:title "Beta Beacon"@en ;
  dcat:endpointURL <https://fdp.beta.example/beacon> ; dcat:servesDataset <https://fdp.beta.example/dataset/g> .
""",
    },
}


def ntriples(turtle: str) -> str:
    graph = Graph()
    graph.parse(data=PREFIXES + turtle, format="turtle")
    return graph.serialize(format="nt")


def corpus() -> dict[str, list[str]]:
    """Client URL → N-Triples documents, one per node."""
    return {url: [ntriples(doc) for doc in nodes.values()] for url, nodes in FDPS.items()}
