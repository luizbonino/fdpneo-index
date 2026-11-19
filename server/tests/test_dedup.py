"""Copies of one resource across FDPs: the dedup key (TASKS 8.8)."""

from __future__ import annotations

import pytest
from rdflib import Graph, URIRef

from fdpindex.search.dedup import dedup_key, doi, iri_key

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    [
        "10.5072/ABC.1",
        "doi:10.5072/abc.1",
        "DOI: 10.5072/abc.1",
        "https://doi.org/10.5072/abc.1",
        "http://dx.doi.org/10.5072/abc.1/",
        "info:doi/10.5072/abc.1",
    ],
)
def test_dois_in_every_spelling(text: str) -> None:
    assert doi(text) == "doi:10.5072/abc.1"


def test_not_dois() -> None:
    assert doi("https://example.org/10.5072/x") is None
    assert doi("dataset-1") is None
    assert iri_key("HTTPS://Example.org/A/") == "id:example.org/a"


TURTLE = """
@prefix dct: <http://purl.org/dc/terms/> .
@prefix adms: <http://www.w3.org/ns/adms#> .
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://a.example/d/1> dct:identifier "local-7", "https://doi.org/10.5072/X" ;
  owl:sameAs <https://b.example/d/9> .
<https://a.example/d/2> adms:identifier [ skos:notation "urn:uuid:1234" ] ;
  dct:identifier "local-8" ; owl:sameAs <https://b.example/d/9> .
<https://a.example/d/3> owl:sameAs <http://B.example/d/9/> ; dct:identifier "local-9" .
<https://a.example/d/4> dct:identifier "local-10" .
<https://doi.org/10.5072/Y> dct:title "a DOI as the resource's IRI" .
"""


def test_the_strongest_identifier_wins() -> None:
    graph = Graph().parse(data=TURTLE, format="turtle")

    def key(iri: str) -> str | None:
        return dedup_key(graph, URIRef(iri))

    assert key("https://a.example/d/1") == "doi:10.5072/x"
    assert key("https://a.example/d/2") == "id:urn:uuid:1234"
    # A mirror's owl:sameAs: the copied resource's own IRI, scheme and case folded.
    assert key("https://a.example/d/3") == "id:b.example/d/9"
    # Local identifiers are not keys; the resource's own IRI is.
    assert key("https://a.example/d/4") == "id:a.example/d/4"
    assert key("https://doi.org/10.5072/Y") == "doi:10.5072/y"
