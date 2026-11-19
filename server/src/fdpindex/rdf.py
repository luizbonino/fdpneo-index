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

import json
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


def _remote_contexts(value: object) -> bool:
    """Whether a JSON-LD document refers to a context by URL (``@context`` or ``@import``)."""
    if isinstance(value, dict):
        for key, item in value.items():  # pyright: ignore[reportUnknownVariableType]
            if key in ("@context", "@import") and _names_a_document(item):
                return True
            if _remote_contexts(item):
                return True
    elif isinstance(value, list):
        return any(_remote_contexts(item) for item in value)  # pyright: ignore[reportUnknownVariableType]
    return False


def _names_a_document(context: object) -> bool:
    if isinstance(context, str):
        return True
    if isinstance(context, list):
        return any(isinstance(item, str) for item in context)  # pyright: ignore[reportUnknownVariableType]
    return False


def parse_rdf(body: str, content_type: str, base: str) -> Graph:
    """Parse ``body``; Turtle when the content type is unknown, as the reference assumes.

    JSON-LD that names a context by URL is refused: rdflib would fetch it
    itself, outside ``OutboundClient`` and its address checks (ADR-0002), so a
    pinged FDP could make the index request internal addresses. Inline
    contexts are fine.
    """
    fmt = FORMATS.get(content_type, "turtle")
    if fmt == "json-ld":
        try:
            document = json.loads(body)
        except ValueError as err:
            raise RdfParseError(f"invalid JSON-LD: {err}") from err
        if _remote_contexts(document):
            raise RdfParseError("JSON-LD with a remote @context is not accepted; inline it")
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


# --- naming concepts (labels, synonyms, broader concepts) ---------------------------------

# Vocabularies name things with different predicates; the first that has a value wins.
_SKOS = "http://www.w3.org/2004/02/skos/core#"
_SKOSXL = "http://www.w3.org/2008/05/skos-xl#"
_GN = "http://www.geonames.org/ontology#"
_VCARD = "http://www.w3.org/2006/vcard/ns#"
LABEL_PREDICATES: tuple[URIRef, ...] = tuple(
    URIRef(iri)
    for iri in (
        _SKOS + "prefLabel",
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://purl.org/dc/terms/title",
        "http://purl.org/dc/elements/1.1/title",
        "http://schema.org/name",
        "https://schema.org/name",
        "http://xmlns.com/foaf/0.1/name",
        _VCARD + "fn",
        _VCARD + "organization-name",
        _GN + "name",
        _GN + "officialName",
    )
)
SYNONYM_PREDICATES: tuple[URIRef, ...] = tuple(
    URIRef(iri)
    for iri in (
        _SKOS + "altLabel",
        "http://schema.org/alternateName",
        "https://schema.org/alternateName",
        "http://purl.org/dc/terms/alternative",
        "http://www.geneontology.org/formats/oboInOwl#hasExactSynonym",
        _GN + "alternateName",
    )
)
BROADER_PREDICATES: tuple[URIRef, ...] = tuple(
    URIRef(iri)
    for iri in (
        _SKOS + "broader",
        "http://id.nlm.nih.gov/mesh/vocab#broaderDescriptor",
        "http://www.w3.org/2000/01/rdf-schema#subClassOf",
    )
)
SKOSXL_PREF = URIRef(_SKOSXL + "prefLabel")
SKOSXL_ALT = URIRef(_SKOSXL + "altLabel")
SKOSXL_LITERAL = URIRef(_SKOSXL + "literalForm")


@dataclass
class ConceptNames:
    """What a graph says about one IRI: a label per language, synonyms, broader IRIs."""

    labels: dict[str, str] = field(default_factory=lambda: {})
    synonyms: dict[str, list[str]] = field(default_factory=lambda: {})
    broader: list[str] = field(default_factory=lambda: [])

    @property
    def empty(self) -> bool:
        return not self.labels and not self.synonyms


def iri_variants(iri: str) -> set[str]:
    """The forms one IRI is written in: http/https, with or without a trailing slash."""
    forms = {iri, iri.rstrip("/"), iri.rstrip("/") + "/"}
    for form in list(forms):
        if form.startswith("https://"):
            forms.add("http://" + form[8:])
        elif form.startswith("http://"):
            forms.add("https://" + form[7:])
    return forms


def describe(graph: Graph, iri: str) -> ConceptNames:
    """Labels, synonyms and broader concepts ``graph`` gives ``iri`` (in any of its forms)."""
    subjects = [URIRef(form) for form in iri_variants(iri)]
    names = ConceptNames()

    def literals(predicates: tuple[URIRef, ...], xl: URIRef) -> list[tuple[int, Literal]]:
        found: list[tuple[int, Literal]] = []
        for rank, predicate in enumerate(predicates):
            for subject in subjects:
                found.extend(
                    (rank, o)
                    for o in graph.objects(subject, predicate)
                    if isinstance(o, Literal) and str(o).strip()
                )
        # SKOS-XL: the label is a resource with a literal form.
        for subject in subjects:
            for label in graph.objects(subject, xl):
                found.extend(
                    (0, o)
                    for o in graph.objects(label, SKOSXL_LITERAL)
                    if isinstance(o, Literal) and str(o).strip()
                )
        return found

    for _rank, literal in sorted(literals(LABEL_PREDICATES, SKOSXL_PREF), key=lambda item: item[0]):
        names.labels.setdefault((literal.language or "").lower(), " ".join(str(literal).split()))
    for _rank, literal in literals(SYNONYM_PREDICATES, SKOSXL_ALT):
        text = " ".join(str(literal).split())
        lang = (literal.language or "").lower()
        if text != names.labels.get(lang) and text not in names.synonyms.get(lang, []):
            names.synonyms.setdefault(lang, []).append(text)
    for predicate in BROADER_PREDICATES:
        for subject in subjects:
            for o in graph.objects(subject, predicate):
                if isinstance(o, URIRef) and str(o) not in names.broader and str(o) != iri:
                    names.broader.append(str(o))
    return names
