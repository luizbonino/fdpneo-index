"""Copies of one resource across FDPs (TASKS 8.8).

The same dataset is often listed by several FDPs: a national FDP mirrors a
project's, a catalogue is re-published. Each resource gets one ``dedup_key``,
the strongest identifier it states:

1. a DOI, wherever it appears (``dct:identifier``, ``adms:identifier``,
   ``owl:sameAs`` or the resource's own IRI), as ``doi:10.…`` in lower case;
2. an identifier that is a global IRI or URN (``dct:identifier``,
   ``adms:identifier/skos:notation``);
3. the target of ``owl:sameAs``: a mirror that says which resource it copies
   gets that resource's key;
4. the resource's own IRI: the same IRI harvested from two FDPs.

IRIs are compared without their scheme, case or trailing slash. Resources
with the same key in different FDPs are copies: hits list them as
``duplicates``, and ``collapse=true`` shows one per key.

Landing pages are not used: portals give many datasets one landing page.
Local identifiers ("dataset-1") are not used either: they are unique in one
FDP only. Only datasets, series, services and catalogs get a key.
"""

from __future__ import annotations

import re

from rdflib import Graph, Literal, URIRef
from rdflib.term import Node

DCT_IDENTIFIER = URIRef("http://purl.org/dc/terms/identifier")
ADMS_IDENTIFIER = URIRef("http://www.w3.org/ns/adms#identifier")
SKOS_NOTATION = URIRef("http://www.w3.org/2004/02/skos/core#notation")
SAME_AS = URIRef("http://www.w3.org/2002/07/owl#sameAs")
KINDS = frozenset({"dataset", "series", "service", "catalog"})
MAX_KEY = 512

_DOI = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/|info:doi/)?(10\.\d{4,9}/\S+)$", re.I)
_GLOBAL = re.compile(r"^(?:https?://|urn:|ark:/|hdl:)", re.I)


def doi(text: str) -> str | None:
    """``doi:10.…`` for a DOI in any of its usual spellings, else None."""
    match = _DOI.match(text.strip())
    return f"doi:{match.group(1).lower().rstrip('/.')}" if match else None


def iri_key(text: str) -> str:
    """An IRI without scheme, case and trailing slash: ``id:example.org/a``."""
    text = text.strip().lower()
    text = re.sub(r"^https?://", "", text).rstrip("/")
    return f"id:{text}"[:MAX_KEY]


def dedup_key(graph: Graph, subject: Node) -> str | None:
    identifiers: list[str] = []
    for value in graph.objects(subject, DCT_IDENTIFIER):  # pyright: ignore[reportArgumentType]
        identifiers.append(str(value))
    for node in graph.objects(subject, ADMS_IDENTIFIER):  # pyright: ignore[reportArgumentType]
        if isinstance(node, Literal | URIRef):
            identifiers.append(str(node))
        identifiers.extend(str(n) for n in graph.objects(node, SKOS_NOTATION))
    same = sorted(
        str(o)
        for o in graph.objects(subject, SAME_AS)  # pyright: ignore[reportArgumentType]
        if isinstance(o, URIRef)
    )
    own = str(subject) if isinstance(subject, URIRef) else None
    dois = sorted({d for text in [*identifiers, *same, own or ""] if (d := doi(text))})
    if dois:
        return dois[0][:MAX_KEY]
    globals_ = sorted({iri_key(i) for i in identifiers if _GLOBAL.match(i.strip())})
    if globals_:
        return globals_[0]
    if same:
        return iri_key(same[0])
    return iri_key(own) if own else None
