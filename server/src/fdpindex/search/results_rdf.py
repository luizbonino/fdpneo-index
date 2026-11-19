"""Search results as RDF (TASKS 9.5): a ``dcat:Catalog`` of the hits.

``GET`` and ``POST /index/search`` answer JSON-LD, Turtle, N-Triples or
RDF/XML when asked (``format=`` or ``Accept``). The graph is built from the
JSON result, so it holds what the JSON holds:

* the search as a ``dcat:Catalog`` (its IRI is the request URL) with the
  OpenSearch counts ``os:totalResults``, ``os:startIndex``, ``os:itemsPerPage``;
* each hit under its own IRI, linked by kind (``dcat:dataset``,
  ``dcat:service``, ``dcat:catalog``, else ``dcat:resource``), with its types,
  title and description in their language, dates, landing page, temporal
  coverage, and the values of the profile's fields under the predicate each
  field reads first (``dcat:theme``, ``dcat:keyword``, ``dct:publisher`` …);
* where it comes from: ``dct:isPartOf`` its catalog, ``prov:hadPrimarySource``
  its FDP, ``foaf:page`` its page in this index.

Number buckets and Yes/No values are left out: they are the index's grouping,
not the publisher's statements. Paging is in the ``Link`` header.
"""

from __future__ import annotations

from typing import Any

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS, FOAF, PROV, RDF, XSD

from fdpindex.search.profile import COLUMN_FIELDS, Profile

OS = Namespace("http://a9.com/-/spec/opensearch/1.1/")
DCAT_RESOURCE = URIRef("http://www.w3.org/ns/dcat#resource")  # DCAT 3; not in rdflib's DCAT
LINKS = {
    "dataset": DCAT.dataset,
    "series": DCAT.dataset,
    "service": DCAT.service,
    "catalog": DCAT.catalog,
    "fdp": DCAT.catalog,
}
_IRI_PREFIXES = ("http://", "https://", "urn:")


def results_graph(result: dict[str, Any], url: str, page_base: str, profile: Profile) -> Graph:
    graph = Graph()
    for prefix, namespace in (("dcat", DCAT), ("dct", DCTERMS), ("os", OS), ("prov", PROV)):
        graph.bind(prefix, namespace)
    search = URIRef(url)
    query = result.get("query", {})
    graph.add((search, RDF.type, DCAT.Catalog))
    title = f"Search results for “{query['q']}”" if query.get("q") else "Search results"
    graph.add((search, DCTERMS.title, Literal(title, lang="en")))
    graph.add((search, OS.totalResults, Literal(int(result["totalResults"]))))
    graph.add((search, OS.startIndex, Literal(int(result["startIndex"]))))
    graph.add((search, OS.itemsPerPage, Literal(int(result["itemsPerPage"]))))
    predicates = _predicates(profile)
    for hit in result["hits"]:
        node = URIRef(hit["iri"])
        graph.add((search, LINKS.get(hit["type"], DCAT_RESOURCE), node))
        for type_iri in hit.get("typeIris", []):
            graph.add((node, RDF.type, URIRef(type_iri)))
        graph.add((node, DCTERMS.title, Literal(hit["title"], lang=hit.get("titleLang") or None)))
        if hit.get("description"):
            graph.add((node, DCTERMS.description, Literal(hit["description"])))
        for name, predicate in (("issued", DCTERMS.issued), ("modified", DCTERMS.modified)):
            if hit.get(name):
                graph.add((node, predicate, Literal(hit[name], datatype=XSD.dateTime)))
        if hit.get("landingPage"):
            graph.add((node, DCAT.landingPage, URIRef(hit["landingPage"])))
        if hit.get("catalog"):
            graph.add((node, DCTERMS.isPartOf, URIRef(hit["catalog"]["iri"])))
        graph.add((node, PROV.hadPrimarySource, URIRef(hit["fdp"]["clientUrl"])))
        graph.add((node, FOAF.page, URIRef(f"{page_base}search/r/{hit['id']}")))
        if hit.get("temporal"):
            period = BNode()
            graph.add((node, DCTERMS.temporal, period))
            graph.add((period, RDF.type, DCTERMS.PeriodOfTime))
            for key, predicate in (("start", DCAT.startDate), ("end", DCAT.endDate)):
                if hit["temporal"].get(key):
                    graph.add((period, predicate, Literal(hit["temporal"][key], datatype=XSD.date)))
        for name, values in hit.get("values", {}).items():
            spec = profile.fields.get(name)
            predicate = predicates.get(name)
            if spec is None or predicate is None:
                continue
            for value in values:
                text = str(value["value"])
                if text.startswith(_IRI_PREFIXES):
                    graph.add((node, predicate, URIRef(text)))
                elif spec.kind == "agent":
                    agent = BNode()
                    graph.add((node, predicate, agent))
                    graph.add((agent, FOAF.name, Literal(text)))
                else:
                    graph.add((node, predicate, Literal(text, lang=value.get("lang"))))
    return graph


def _predicates(profile: Profile) -> dict[str, URIRef]:
    """The predicate each value field reads first, for the fields worth restating."""
    found: dict[str, URIRef] = {}
    for name, spec in profile.fields.items():
        if spec.kind not in {"iri", "agent", "literal"} or name in COLUMN_FIELDS:
            continue
        steps = profile.path_iris(spec.paths[0])
        if len(steps) == 1:
            found[name] = URIRef(steps[0])
    return found
