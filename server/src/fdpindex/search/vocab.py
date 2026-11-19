"""Bundled labels for IRIs that rarely come with one (search design §3.5, source 2).

DCAT-AP mandates the EU authority tables for themes, formats, access rights,
languages and countries, but FDPs publish only the codes. The labels come
from ``vocab.json``, a pack regenerated from the sources by
``fdpindex vocab update`` (:mod:`fdpindex.search.vocab_update`), in several
languages; ``FDPINDEX_VOCAB_FILE`` points at another pack. The English table
below is the fallback for what the pack lacks (Creative Commons licences).
Harvested labels take precedence over both.

The pack also names the same place twice (TASKS 8.4): ``aliases`` map
GeoNames' and lexvo's IRIs of a country or continent to the EU authority IRI,
``codes`` map ISO 3166 codes to it. :func:`place_alias` and
:func:`country_code` apply them; :func:`aliases_of` gives them back, so
"Show as SPARQL" can match every form.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import cache
from importlib import resources
from pathlib import Path

EU = "http://publications.europa.eu/resource/authority/"


def _table(base: str, labels: dict[str, str]) -> dict[str, str]:
    return {base + code: label for code, label in labels.items()}


FALLBACK: dict[str, str] = {
    **_table(
        EU + "data-theme/",
        {
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
        },
    ),
    **_table(
        EU + "access-right/",
        {"PUBLIC": "Public", "RESTRICTED": "Restricted", "NON_PUBLIC": "Non-public"},
    ),
    **_table(
        EU + "file-type/",
        {
            "CSV": "CSV",
            "TSV": "TSV",
            "JSON": "JSON",
            "JSON_LD": "JSON-LD",
            "XML": "XML",
            "RDF": "RDF",
            "RDF_XML": "RDF/XML",
            "RDF_TURTLE": "Turtle",
            "RDF_N_TRIPLES": "N-Triples",
            "PDF": "PDF",
            "XLSX": "Excel (XLSX)",
            "XLS": "Excel (XLS)",
            "ZIP": "ZIP",
            "HTML": "HTML",
            "TXT": "Plain text",
            "PARQUET": "Parquet",
            "SPARQLQ": "SPARQL",
        },
    ),
    **_table(
        EU + "language/",
        {
            "ENG": "English",
            "NLD": "Dutch",
            "DEU": "German",
            "FRA": "French",
            "SPA": "Spanish",
            "ITA": "Italian",
            "POR": "Portuguese",
            "SWE": "Swedish",
            "DAN": "Danish",
            "FIN": "Finnish",
            "NOR": "Norwegian",
            "POL": "Polish",
            "CES": "Czech",
            "HUN": "Hungarian",
            "RON": "Romanian",
            "ELL": "Greek",
            "BUL": "Bulgarian",
            "HRV": "Croatian",
            "SLK": "Slovak",
            "SLV": "Slovenian",
            "EST": "Estonian",
            "LAV": "Latvian",
            "LIT": "Lithuanian",
            "GLE": "Irish",
            "MLT": "Maltese",
        },
    ),
    **_table(
        EU + "country/",
        {
            "NLD": "Netherlands",
            "DEU": "Germany",
            "CHE": "Switzerland",
            "BEL": "Belgium",
            "FRA": "France",
            "GBR": "United Kingdom",
            "PRT": "Portugal",
            "ESP": "Spain",
            "ITA": "Italy",
            "AUT": "Austria",
            "DNK": "Denmark",
            "SWE": "Sweden",
            "FIN": "Finland",
            "NOR": "Norway",
            "POL": "Poland",
            "IRL": "Ireland",
            "LUX": "Luxembourg",
            "CZE": "Czechia",
            "GRC": "Greece",
            "USA": "United States",
        },
    ),
    **_table(
        EU + "frequency/",
        {
            "DAILY": "Daily",
            "WEEKLY": "Weekly",
            "MONTHLY": "Monthly",
            "QUARTERLY": "Quarterly",
            "ANNUAL": "Annual",
            "IRREG": "Irregular",
            "CONT": "Continuous",
            "UNKNOWN": "Unknown",
        },
    ),
    **_table(
        EU + "licence/",
        {
            "CC_BY_4_0": "CC BY 4.0",
            "CC_BYSA_4_0": "CC BY-SA 4.0",
            "CC_BYNC_4_0": "CC BY-NC 4.0",
            "CC0": "CC0 1.0",
        },
    ),
    **_table(
        "http://creativecommons.org/licenses/",
        {
            "by/4.0/": "CC BY 4.0",
            "by-sa/4.0/": "CC BY-SA 4.0",
            "by-nc/4.0/": "CC BY-NC 4.0",
            "by-nd/4.0/": "CC BY-ND 4.0",
            "by-nc-sa/4.0/": "CC BY-NC-SA 4.0",
            "by-nc-nd/4.0/": "CC BY-NC-ND 4.0",
        },
    ),
    "http://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
    **_table(
        "https://spdx.org/licenses/",
        {
            "MIT": "MIT License",
            "Apache-2.0": "Apache License 2.0",
            "CC-BY-4.0": "CC BY 4.0",
            "CC0-1.0": "CC0 1.0",
            "GPL-3.0-only": "GNU GPL v3.0",
        },
    ),
}


@cache
def _pack_text() -> str:
    override = os.environ.get("FDPINDEX_VOCAB_FILE")
    if override:
        return Path(override).read_text("utf-8")
    try:
        return resources.files("fdpindex.search").joinpath("vocab.json").read_text("utf-8")
    except FileNotFoundError:
        return '{"labels": {}}'


@cache
def _pack() -> dict[str, dict[str, str]]:
    labels: dict[str, dict[str, str]] = {
        iri: dict(langs) for iri, langs in json.loads(_pack_text()).get("labels", {}).items()
    }
    for iri, label in FALLBACK.items():
        labels.setdefault(iri, {}).setdefault("en", label)
    return labels


@cache
def _aliases() -> dict[str, str]:
    return dict(json.loads(_pack_text()).get("aliases", {}))


@cache
def _codes() -> dict[str, str]:
    return dict(json.loads(_pack_text()).get("codes", {}))


@cache
def _reverse() -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {}
    for alias, iri in _aliases().items():
        reverse.setdefault(iri, []).append(alias)
    for code, iri in _codes().items():
        reverse.setdefault(iri, []).append(code)
    return reverse


_GEONAMES = re.compile(r"^https?://(?:sws\.|www\.)?geonames\.org/(\d+)(?:[/#].*)?$")


def geonames_iri(iri: str) -> str | None:
    """GeoNames' concept IRI (``https://sws.geonames.org/<id>/``) for any of its forms."""
    match = _GEONAMES.match(iri)
    return f"https://sws.geonames.org/{match.group(1)}/" if match else None


def place_alias(iri: str) -> str:
    """The EU authority IRI of a country or continent named another way, else ``iri``."""
    return _aliases().get(iri, iri)


def country_code(text: str) -> str | None:
    """The EU country IRI of an ISO 3166 alpha-2 or alpha-3 code ("NL", "nld")."""
    return _codes().get(text.strip().lower())


def aliases_of(iri: str) -> list[str]:
    """The other IRIs and the codes the pack folds into ``iri``."""
    return _reverse().get(iri, [])


def pack_digest() -> str:
    """Identifies the pack: projections that used its labels are redone when it changes."""
    return hashlib.sha256(_pack_text().encode()).hexdigest()[:12]


def _canonical(iri: str) -> str:
    if iri.startswith("https://") and (
        "creativecommons.org" in iri or "publications.europa.eu" in iri
    ):
        return "http://" + iri.removeprefix("https://")
    return iri


def bundled_labels(iri: str) -> dict[str, str]:
    """Every bundled label of ``iri`` by language (empty when there is none)."""
    return _pack().get(_canonical(iri), {})


def bundled_label(iri: str, lang: str = "en") -> str | None:
    """The bundled label in ``lang``, else English, else any; None when there is none."""
    labels = bundled_labels(iri)
    if not labels:
        return None
    base = lang.lower().split("-", 1)[0]
    return labels.get(base) or labels.get("en") or next(iter(labels.values()))


# English labels by IRI, for callers that want a plain mapping.
BUNDLED: dict[str, str] = {
    iri: label for iri in _pack() if (label := bundled_label(iri)) is not None
}
