"""RDF parsing and the repository metadata an entry shows.

The extraction follows the reference's ``MetadataRetrievalUtils``: find the
subject typed as a repository (``r3d:Repository`` or ``fdp-o:MetadataService``),
then read ``dct:title``, ``dct:description``, ``dcat:version``,
``dct:publisher``, ``r3d:country`` and the publisher's ``foaf:name``.

Differences from the reference, both harmless for single-valued metadata:

* the legacy FDP-O namespace ``https://w3id.org/fdp/o#`` is accepted too
  (FDPneo ADR-0026), and a typed subject equal to the client URL wins over
  other typed subjects;
* among several values of one property, an English or language-less literal
  is preferred, instead of whichever statement the parser produced last.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, FOAF, RDF
from rdflib.term import Node

R3D = Namespace("http://www.re3data.org/schema/3-0#")
FDP_O = Namespace("https://w3id.org/fdp/fdp-o#")
FDP_LEGACY = Namespace("https://w3id.org/fdp/o#")
LDP = Namespace("http://www.w3.org/ns/ldp#")
# rdflib's DCAT namespace is closed over DCAT 2; dcat:version is DCAT 3.
DCAT3 = Namespace("http://www.w3.org/ns/dcat#")

REPOSITORY_TYPES = (R3D.Repository, FDP_O.MetadataService, FDP_LEGACY.MetadataService)

METADATA_FIELDS: dict[URIRef, str] = {
    DCTERMS.title: "title",
    DCTERMS.description: "description",
    DCAT3.version: "version",
    DCTERMS.publisher: "publisher",
    R3D.country: "country",
}

# Content types the index can parse, with the rdflib format for each.
FORMATS = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/n-triples": "nt",
    "application/ld+json": "json-ld",
    "application/rdf+xml": "xml",
    "text/n3": "n3",
}
ACCEPT = (
    "text/turtle, application/ld+json;q=0.9, application/rdf+xml;q=0.8, application/n-triples;q=0.7"
)


class RdfParseError(Exception):
    pass


@dataclass
class RepositoryMetadata:
    repository_uri: str
    metadata: dict[str, str] = field(default_factory=lambda: {})
    metadata_version: int = 1


def parse_rdf(body: str, content_type: str, base: str) -> Graph:
    """Parse ``body``; Turtle when the content type is unknown, as the reference assumes."""
    fmt = FORMATS.get(content_type, "turtle")
    graph = Graph()
    try:
        graph.parse(data=body, format=fmt, publicID=base)
    except Exception as err:  # rdflib raises a variety of parser-specific errors
        raise RdfParseError(str(err)) from err
    return graph


def _same_iri(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def find_repository(graph: Graph, client_url: str) -> URIRef | None:
    candidates: list[URIRef] = []
    for repository_type in REPOSITORY_TYPES:
        for subject in graph.subjects(RDF.type, repository_type):
            if isinstance(subject, URIRef) and subject not in candidates:
                candidates.append(subject)
    for subject in candidates:
        if _same_iri(str(subject), client_url):
            return subject
    return candidates[0] if candidates else None


def _preferred(values: list[Node]) -> Node:
    def rank(value: Node) -> int:
        if isinstance(value, Literal):
            lang = (value.language or "").lower()
            if lang in {"", "en"} or lang.startswith("en-"):
                return 0
            return 1
        return 2

    return sorted(values, key=rank)[0]


def extract_repository_metadata(graph: Graph, client_url: str) -> RepositoryMetadata | None:
    repository = find_repository(graph, client_url)
    if repository is None:
        return None
    result = RepositoryMetadata(repository_uri=str(repository))
    for predicate, name in METADATA_FIELDS.items():
        values = list(graph.objects(repository, predicate))
        if values:
            result.metadata[name] = str(_preferred(values))
    publisher = next(iter(graph.objects(repository, DCTERMS.publisher)), None)
    if publisher is not None:
        names = list(graph.objects(publisher, FOAF.name))
        if names:
            result.metadata["publisherName"] = str(_preferred(names))
    return result
