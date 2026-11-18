"""Prefixes the SPARQL endpoint knows without ``PREFIX`` lines.

Queries may use these directly; the editor still inserts the ``PREFIX`` line,
so a copied query works in any other endpoint. The same map is offered to
rdflib's parser (the gate) and to pyoxigraph, so both agree on what parses.
"""

from __future__ import annotations

PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "dcterms": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "prov": "http://www.w3.org/ns/prov#",
    "odrl": "http://www.w3.org/ns/odrl/2/",
    "ldp": "http://www.w3.org/ns/ldp#",
    "void": "http://rdfs.org/ns/void#",
    "sh": "http://www.w3.org/ns/shacl#",
    "schema": "http://schema.org/",
    "adms": "http://www.w3.org/ns/adms#",
    "locn": "http://www.w3.org/ns/locn#",
    "time": "http://www.w3.org/2006/time#",
    "fdp-o": "https://w3id.org/fdp/fdp-o#",
    "r3d": "http://www.re3data.org/schema/3-0#",
    "healthdcatap": "http://healthdataportal.eu/ns/health#",
    "fdpindex": "urn:fdpindex:",
}


def compact(iri: str) -> str:
    """``dcat:Dataset`` for a known namespace, else the IRI itself."""
    best = ""
    for prefix, namespace in PREFIXES.items():
        if iri.startswith(namespace) and len(namespace) > len(PREFIXES.get(best, "")):
            if prefix == "dcterms":
                continue
            best = prefix
    if not best:
        return iri
    local = iri[len(PREFIXES[best]) :]
    return f"{best}:{local}" if local and all(c.isalnum() or c in "_-." for c in local) else iri


def with_prefixes(query: str) -> str:
    """Prepend ``PREFIX`` lines for the known prefixes the query uses but does not declare."""
    import re

    declared = {m.group(1) for m in re.finditer(r"PREFIX\s+([\w-]*):", query, re.IGNORECASE)}
    used = [
        prefix
        for prefix in PREFIXES
        if prefix not in declared
        and re.search(rf"(?<![\w:<#/-]){re.escape(prefix)}:", _without_iris_and_strings(query))
    ]
    header = "".join(f"PREFIX {p}: <{PREFIXES[p]}>\n" for p in used)
    return header + query


def _without_iris_and_strings(query: str) -> str:
    import re

    return re.sub(r'<[^<>\s]*>|"(?:[^"\\]|\\.)*"|#[^\n]*', " ", query)
