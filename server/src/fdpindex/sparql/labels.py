"""Labels for IRIs that rarely come with one in harvested metadata.

The EU data themes (``dcat:theme`` in DCAT-AP) are published as codes such as
``…/data-theme/HEAL``; FDPs almost never repeat the label. A small bundled
table covers them until the vocabulary pack of design task 6.8 exists.
Anything else gets a readable form of its local name.
"""

from __future__ import annotations

import re

EU_THEME = "http://publications.europa.eu/resource/authority/data-theme/"

KNOWN_LABELS: dict[str, str] = {
    EU_THEME + code: label
    for code, label in {
        "AGRI": "Agriculture, fisheries, forestry and food",
        "ECON": "Economy and finance",
        "EDUC": "Education, culture and sport",
        "ENER": "Energy",
        "ENVI": "Environment",
        "GOVE": "Government and public sector",
        "HEAL": "Health",
        "INTR": "International issues",
        "JUST": "Justice, legal system and public safety",
        "OP_DATPRO": "Provisional data",
        "REGI": "Regions and cities",
        "SOCI": "Population and society",
        "TECH": "Science and technology",
        "TRAN": "Transport",
    }.items()
}


def humanize(iri: str) -> str:
    """A readable label from an IRI's local name.

    ``landingPage`` → ``Landing page``; ``overijssel-medical-research`` →
    ``Overijssel medical research``; codes and versioned slugs (``ENVI``,
    ``cc-by-4.0``) stay as they are.
    """
    known = KNOWN_LABELS.get(iri)
    if known:
        return known
    local = re.split(r"[#/:]", iri.rstrip("/#"))[-1] or iri
    if any(c.isdigit() for c in local) or local.isupper():
        return local
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|[_-]+", " ", local).strip()
    return words[:1].upper() + words[1:].lower() if words else iri
