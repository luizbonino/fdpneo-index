"""Two FDPs as Turtle pages, for the search projection and API tests.

Alpha is FDPneo-shaped (DCAT 3, ``fdp-o``, IRIs from the EU tables, some as
strings, multilingual titles, a numeric HealthDCAT-AP field, a MeSH subject). Beta is
Java-FDP-shaped (``r3d:Repository``, ``dct:isPartOf`` links, an LDP container, ``https``
Creative Commons IRIs, a data service, a distribution without a title, an
EDAM topic as ``dcat:theme`` and an EU data theme as ``dct:subject``).
"""

from __future__ import annotations

from typing import Protocol

PREFIXES = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix vcard: <http://www.w3.org/2006/vcard/ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix fdp-o: <https://w3id.org/fdp/fdp-o#> .
@prefix r3d: <http://www.re3data.org/schema/3-0#> .
@prefix ldp: <http://www.w3.org/ns/ldp#> .
@prefix healthdcatap: <http://healthdataportal.eu/ns/health#> .
@prefix time: <http://www.w3.org/2006/time#> .
@prefix gsp: <http://www.opengis.net/ont/geosparql#> .
@prefix locn: <http://www.w3.org/ns/locn#> .
"""

ALPHA = "https://fdp.alpha.example"
BETA = "https://fdp.beta.example"
EU = "http://publications.europa.eu/resource/authority/"

PAGES: dict[str, str] = {
    ALPHA: f"""
<{ALPHA}> a fdp-o:FAIRDataPoint, r3d:Repository ;
  dct:title "Alpha FDP"@en ;
  r3d:country <http://lexvo.org/id/iso3166/NL> ;
  dct:publisher <https://ror.org/alpha> ;
  fdp-o:metadataCatalog <{ALPHA}/catalog/c1> .
<https://ror.org/alpha> foaf:name "Alpha University" .
""",
    f"{ALPHA}/catalog/c1": f"""
<{ALPHA}/catalog/c1> a dcat:Catalog ;
  dct:title "Alpha health catalog"@en, "Alpha gezondheidscatalogus"@nl ;
  dct:publisher <https://ror.org/alpha> ;
  dcat:dataset <{ALPHA}/dataset/d1>, <{ALPHA}/dataset/d2> .
<https://ror.org/alpha> foaf:name "Alpha University" .
""",
    f"{ALPHA}/dataset/d1": f"""
<{ALPHA}/dataset/d1> a dcat:Dataset ;
  dct:title "Breast cancer registry"@en ;
  dct:description "Biopsies recorded nationwide since 2010."@en ;
  dcat:keyword "Cancer"@en, "registry"@en ;
  dcat:theme <{EU}data-theme/HEAL> ;
  dct:subject <http://id.nlm.nih.gov/mesh/D001943>, "oncology" ;
  dct:license "CC-BY-4.0" ;
  dct:accessRights <https://publications.europa.eu/resource/authority/access-right/PUBLIC> ;
  dct:language "en" ;
  dct:spatial <https://publications.europa.eu/resource/authority/country/NLD>,
    [ a dct:Location ; dcat:bbox
      "POLYGON((3.31 50.75, 7.23 50.75, 7.23 53.68, 3.31 53.68, 3.31 50.75))"^^gsp:wktLiteral ] ;
  dct:publisher <https://ror.org/alpha> ;
  dct:issued "2020-01-01"^^xsd:date ;
  dct:modified "2026-11-01T10:00:00Z"^^xsd:dateTime ;
  dct:temporal [ a dct:PeriodOfTime ;
    dcat:startDate "2010-01-01"^^xsd:date ; dcat:endDate "2015-12-31"^^xsd:date ] ;
  dcat:contactPoint [ vcard:fn "Data desk" ; vcard:hasEmail <mailto:desk@alpha.example> ] ;
  healthdcatap:numberOfRecords 2500 ;
  healthdcatap:numberOfUniqueIndividuals 1200 ;
  healthdcatap:minTypicalAge 18 ;
  healthdcatap:analytics <{ALPHA}/files/report.pdf> ;
  dcat:distribution <{ALPHA}/distribution/x1>, <{ALPHA}/distribution/x2> .
<https://ror.org/alpha> foaf:name "Alpha University" .
""",
    f"{ALPHA}/distribution/x1": f"""
<{ALPHA}/distribution/x1> a dcat:Distribution ;
  dct:title "CSV export"@en ;
  dcat:mediaType <https://www.iana.org/assignments/media-types/text/csv> ;
  dct:format <{EU}file-type/CSV> ;
  dct:license <http://creativecommons.org/licenses/by/4.0/> ;
  dcat:downloadURL <{ALPHA}/files/registry.csv> .
""",
    f"{ALPHA}/distribution/x2": f"""
<{ALPHA}/distribution/x2> a dcat:Distribution ;
  dct:title "Parquet export"@en ;
  dct:format "parquet" .
""",
    f"{ALPHA}/dataset/d2": f"""
<{ALPHA}/dataset/d2> a dcat:Dataset ;
  dct:title "Lung function tests"@en, "Longfunctietesten"@nl ;
  dcat:keyword "cancer"@en, "lungs"@en ;
  dcat:theme <{EU}data-theme/HEAL>, <{EU}data-theme/TECH> ;
  dct:publisher [ a foaf:Agent ; foaf:name "Pulmonology Lab" ] ;
  dct:accessRights <{EU}access-right/RESTRICTED> ;
  dct:temporal [ <http://schema.org/startDate> "2018"^^xsd:gYear ] ;
  dct:spatial <http://www.geonames.org/2750405/>, <https://sws.geonames.org/2759794/>,
    [ dcat:centroid "POINT(13.40 52.52)"^^gsp:wktLiteral ] ;
  healthdcatap:numberOfUniqueIndividuals "40"^^xsd:nonNegativeInteger ;
  <https://w3id.org/dpv#hasPersonalData> <https://w3id.org/dpv/dpv-pd#Age> ;
  dct:modified "2025-03-01"^^xsd:date .
""",
    BETA: f"""
<{BETA}> a r3d:Repository ;
  dct:title "Beta FDP" ;
  dct:hasVersion "1.17.2" ;
  r3d:dataCatalog <{BETA}/catalog/k> .
""",
    f"{BETA}/catalog/k": f"""
<{BETA}/catalog/k> a dcat:Catalog ;
  dct:title "Genomics catalogue" ;
  dct:isPartOf <{BETA}> ;
  dcat:dataset <{BETA}/dataset/g> ;
  dcat:service <{BETA}/api> .
<{BETA}/catalog/k/members> a ldp:DirectContainer ;
  ldp:membershipResource <{BETA}/catalog/k> ;
  ldp:contains <{BETA}/dataset/g>, <{BETA}/api> .
""",
    f"{BETA}/dataset/g": f"""
<{BETA}/dataset/g> a dcat:Dataset ;
  dct:title "Genome cohort" ;
  dct:description "Whole-genome sequencing of a Dutch cohort, recruited at the café." ;
  dcat:keyword "genomics" ;
  dcat:theme <http://edamontology.org/topic_0622> ;
  dct:subject <https://publications.europa.eu/resource/authority/data-theme/TECH> ;
  dct:license <https://creativecommons.org/licenses/by/4.0/> ;
  dct:isPartOf <{BETA}/catalog/k> ;
  dct:publisher [ foaf:name "Beta Institute" ] ;
  dct:spatial "NL",
    [ locn:geometry '{{"type": "Point", "coordinates": [4.89, 52.37]}}'^^gsp:geoJSONLiteral ] ;
  <http://www.w3.org/2002/07/owl#sameAs> <http://fdp.alpha.example/dataset/d2/> ;
  dct:temporal [ time:hasBeginning [ time:inXSDDate "2020-01-01"^^xsd:date ] ;
    time:hasEnd [ time:inXSDgYear "2022"^^xsd:gYear ] ] ;
  dcat:distribution <{BETA}/distribution/vcf> .
""",
    f"{BETA}/distribution/vcf": f"""
<{BETA}/distribution/vcf> a dcat:Distribution ;
  dct:format "VCF" ;
  dct:isPartOf <{BETA}/dataset/g> .
""",
    f"{BETA}/api": f"""
<{BETA}/api> a dcat:DataService ;
  dct:title "Beta Beacon" ;
  dcat:endpointURL <{BETA}/beacon> ;
  dcat:servesDataset <{BETA}/dataset/g> .
""",
}


class Server(Protocol):
    def serve(self, url: str, body: str) -> None: ...


def serve(network: Server) -> None:
    for url, body in PAGES.items():
        network.serve(url, PREFIXES + body)


def nodes(fdp: str) -> list[tuple[str, str]]:
    """``(node IRI, N-Triples)`` per page of one FDP, without a harvest."""
    from rdflib import Graph

    out: list[tuple[str, str]] = []
    for url, body in PAGES.items():
        if url.startswith(fdp):
            graph = Graph()
            graph.parse(data=PREFIXES + body, format="turtle")
            out.append((url, graph.serialize(format="nt")))
    return out
