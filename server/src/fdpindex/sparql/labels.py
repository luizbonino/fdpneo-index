"""Labels for IRIs that rarely come with one in harvested metadata.

Known codes (EU data themes, file types, access rights, languages, licences)
come from the search vocabulary pack (:mod:`fdpindex.search.vocab`); anything
else gets a readable form of its local name.
"""

from __future__ import annotations

import re

from fdpindex.search.vocab import BUNDLED as KNOWN_LABELS, bundled_label

__all__ = ["KNOWN_LABELS", "humanize"]


def humanize(iri: str) -> str:
    """A readable label from an IRI's local name.

    ``landingPage`` → ``Landing page``; ``overijssel-medical-research`` →
    ``Overijssel medical research``; codes and versioned slugs (``ENVI``,
    ``cc-by-4.0``) stay as they are.
    """
    known = bundled_label(iri)
    if known:
        # Vocabularies label terms in lower case ("keyword"); names read better capitalised.
        return known[:1].upper() + known[1:]
    local = re.split(r"[#/:]", iri.rstrip("/#"))[-1] or iri
    if any(c.isdigit() for c in local) or local.isupper():
        return local
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|[_-]+", " ", local).strip()
    return words[:1].upper() + words[1:].lower() if words else iri
