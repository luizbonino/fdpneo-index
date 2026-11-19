"""``fdpindex vocab update``: regenerate the bundled vocabulary pack (TASKS 6.8).

The pack (``search/vocab.json``) gives labels, in several languages, to IRIs
that FDPs publish without one:

* the EU authority tables DCAT-AP prescribes: data themes, access rights,
  file types, frequencies, licences, countries, continents, and the common
  languages. Each concept is dereferenced at the Publications Office, which
  answers SKOS with labels in every EU language. (The place table, 4 700
  cities, is left to the concept resolver, which fetches only those in use);
* other names of the same countries (TASKS 8.4): GeoNames' country and
  continent IRIs and lexvo's ISO 3166 IRIs as ``aliases`` of the EU IRI, and
  ISO 3166 alpha-2 and alpha-3 codes as ``codes``, from GeoNames'
  ``countryInfo.txt`` (CC BY 4.0). A country the EU table lacks is skipped;
* the SPDX licence list (English names);
* the terms of DCAT, Dublin Core, FOAF and ADMS, so properties have names in
  facets and in the SPARQL schema.

It is run by maintainers before a release and the result committed; an
operator may run it with ``--output`` and point ``FDPINDEX_VOCAB_FILE`` at the
result. Requests go through :class:`~fdpindex.outbound.OutboundClient`
restricted to the sources' hosts, a few at a time.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS, SKOS

from fdpindex.outbound import OutboundError
from fdpindex.rdf import RdfParseError, parse_rdf

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from fdpindex.outbound import OutboundClient

log = structlog.get_logger(__name__)

EU = "http://publications.europa.eu/resource/authority/"
EU_TABLES = (
    "data-theme",
    "access-right",
    "file-type",
    "frequency",
    "licence",
    "country",
    "continent",
)
# The language table has 8 000 codes; these are the ones metadata uses in practice:
# the EU's official languages and the others of Europe's research infrastructures.
EU_LANGUAGES = [
    "BUL",
    "CES",
    "DAN",
    "DEU",
    "ELL",
    "ENG",
    "EST",
    "FIN",
    "FRA",
    "GLE",
    "HRV",
    "HUN",
    "ITA",
    "LAV",
    "LIT",
    "MLT",
    "NLD",
    "POL",
    "POR",
    "RON",
    "SLK",
    "SLV",
    "SPA",
    "SWE",
    "NOR",
    "NNO",
    "NOB",
    "ISL",
    "CAT",
    "EUS",
    "GLG",
    "LTZ",
    "FRY",
    "TUR",
    "RUS",
    "UKR",
    "ARA",
    "ZHO",
    "JPN",
    "KOR",
    "HIN",
    "SRP",
    "BOS",
    "MKD",
    "SQI",
    "MUL",
    "ZXX",
]
SPDX = "https://spdx.org/licenses/licenses.json"
GEONAMES_COUNTRIES = "https://download.geonames.org/export/dump/countryInfo.txt"
GEONAMES = "https://sws.geonames.org/"
LEXVO_COUNTRY = "http://lexvo.org/id/iso3166/"
# GeoNames' continents that are one of the EU table's (it has one America, GeoNames two).
GEONAMES_CONTINENTS = {
    "6255146": "AFRICA",
    "6255147": "ASIA",
    "6255148": "EUROPE",
    "6255151": "OCEANIA",
    "6255152": "ANTARCTICA",
}
TERM_SOURCES = {
    "http://www.w3.org/ns/dcat#": "https://www.w3.org/ns/dcat.ttl",
    "http://purl.org/dc/terms/": (
        "https://www.dublincore.org/specifications/dublin-core/dcmi-terms/dublin_core_terms.ttl"
    ),
    "http://xmlns.com/foaf/0.1/": "http://xmlns.com/foaf/spec/index.rdf",
    "http://www.w3.org/ns/adms#": "https://www.w3.org/ns/adms.ttl",
}
HOSTS = frozenset(
    {
        "publications.europa.eu",
        "op.europa.eu",
        "spdx.org",
        "www.w3.org",
        "www.dublincore.org",
        "xmlns.com",
        "uri.semic.eu",
        "download.geonames.org",
    }
)
DEFAULT_LANGUAGES = ("en", "nl", "de", "fr", "es", "it", "pt", "sv", "da", "fi")
RDF_ACCEPT = "application/rdf+xml;q=1, text/turtle;q=0.9, application/ld+json;q=0.5"


@dataclass
class UpdateReport:
    labels: int = 0
    aliases: int = 0
    codes: int = 0
    sources: dict[str, int] = field(default_factory=lambda: {})
    failed: list[str] = field(default_factory=lambda: [])


class VocabFetcher:
    def __init__(
        self,
        client: OutboundClient,
        *,
        languages: Iterable[str] = DEFAULT_LANGUAGES,
        concurrency: int = 4,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._languages = frozenset(lang.lower() for lang in languages)
        self._limit = asyncio.Semaphore(concurrency)
        self._timeout = timeout_seconds
        self.labels: dict[str, dict[str, str]] = {}
        self.aliases: dict[str, str] = {}
        self.codes: dict[str, str] = {}
        self.report = UpdateReport()

    async def _graph(self, url: str) -> Graph | None:
        async with self._limit:
            try:
                response = await self._client.request(
                    "GET",
                    url,
                    timeout_seconds=self._timeout,
                    headers={"Accept": RDF_ACCEPT},
                    allowed_hosts=HOSTS,
                )
            except OutboundError as err:
                self.report.failed.append(f"{url}: {err.message}")
                return None
        if response.status != 200 or response.truncated:
            self.report.failed.append(f"{url}: HTTP {response.status}")
            return None
        try:
            return await asyncio.to_thread(parse_rdf, response.body, response.content_type, url)
        except RdfParseError as err:
            self.report.failed.append(f"{url}: {err}")
            return None

    def _keep(self, iri: str, graph: Graph, predicates: Iterable[URIRef]) -> int:
        found: dict[str, str] = {}
        for predicate in predicates:
            for value in graph.objects(URIRef(iri), predicate):
                if not isinstance(value, Literal) or not str(value).strip():
                    continue
                lang = (value.language or "").lower()
                if lang and lang not in self._languages:
                    continue
                found.setdefault(lang or "en", " ".join(str(value).split()))
        if found:
            self.labels.setdefault(iri, {}).update(found)
        return len(found)

    async def eu_table(self, table: str) -> None:
        scheme = f"{EU}{table}"
        if table == "language":
            concepts = [f"{scheme}/{code}" for code in EU_LANGUAGES]
        else:
            graph = await self._graph(scheme)
            if graph is None:
                return
            concepts = sorted(str(c) for c in graph.objects(URIRef(scheme), SKOS.hasTopConcept))

        async def one(iri: str) -> int:
            graph = await self._graph(iri)
            return self._keep(iri, graph, (SKOS.prefLabel,)) if graph is not None else 0

        counts = await asyncio.gather(*(one(iri) for iri in concepts))
        self.report.sources[f"eu:{table}"] = sum(1 for n in counts if n)

    async def spdx(self) -> None:
        async with self._limit:
            try:
                response = await self._client.request(
                    "GET", SPDX, timeout_seconds=self._timeout, allowed_hosts=HOSTS
                )
            except OutboundError as err:
                self.report.failed.append(f"{SPDX}: {err.message}")
                return
        if response.status != 200 or response.truncated:
            self.report.failed.append(f"{SPDX}: HTTP {response.status}")
            return
        data: dict[str, Any] = json.loads(response.body)
        count = 0
        for licence in data.get("licenses", []):
            if licence.get("isDeprecatedLicenseId"):
                continue
            iri = f"https://spdx.org/licenses/{licence['licenseId']}"
            self.labels.setdefault(iri, {})["en"] = str(licence["name"])
            count += 1
        self.report.sources["spdx"] = count

    async def _text(self, url: str) -> str | None:
        async with self._limit:
            try:
                response = await self._client.request(
                    "GET", url, timeout_seconds=self._timeout, allowed_hosts=HOSTS
                )
            except OutboundError as err:
                self.report.failed.append(f"{url}: {err.message}")
                return None
        if response.status != 200 or response.truncated:
            self.report.failed.append(f"{url}: HTTP {response.status}")
            return None
        return response.body

    async def geonames(self) -> None:
        """Aliases and codes of the EU countries; run after the country table is in."""
        text = await self._text(GEONAMES_COUNTRIES)
        if text is None:
            return
        for line in text.splitlines():
            columns = line.split("\t")
            if line.startswith("#") or len(columns) < 17:
                continue
            alpha2, alpha3, geonameid = columns[0].strip(), columns[1].strip(), columns[16].strip()
            eu = f"{EU}country/{alpha3}"
            if not (alpha2 and alpha3 and geonameid.isdigit()) or eu not in self.labels:
                continue
            self.aliases[f"{GEONAMES}{geonameid}/"] = eu
            self.aliases[f"{LEXVO_COUNTRY}{alpha2}"] = eu
            self.codes.setdefault(alpha2.lower(), eu)
            self.codes.setdefault(alpha3.lower(), eu)
        for geonameid, code in GEONAMES_CONTINENTS.items():
            if f"{EU}continent/{code}" in self.labels:
                self.aliases[f"{GEONAMES}{geonameid}/"] = f"{EU}continent/{code}"
        self.report.sources["geonames:countries"] = len(
            [a for a in self.aliases if a.startswith(GEONAMES)]
        )

    async def terms(self, namespace: str, url: str) -> None:
        graph = await self._graph(url)
        if graph is None:
            return
        subjects = {
            str(s)
            for s in graph.subjects()
            if isinstance(s, URIRef) and str(s).startswith(namespace)
        }
        kept = sum(1 for iri in sorted(subjects) if self._keep(iri, graph, (RDFS.label,)))
        self.report.sources[f"terms:{namespace}"] = kept

    async def run(self) -> UpdateReport:
        await asyncio.gather(
            *(self.eu_table(table) for table in (*EU_TABLES, "language")),
            self.spdx(),
            *(self.terms(ns, url) for ns, url in TERM_SOURCES.items()),
        )
        await self.geonames()
        self.report.labels = len(self.labels)
        self.report.aliases = len(self.aliases)
        self.report.codes = len(self.codes)
        return self.report


def write_pack(
    path: Path,
    labels: dict[str, dict[str, str]],
    report: UpdateReport,
    *,
    aliases: dict[str, str] | None = None,
    codes: dict[str, str] | None = None,
) -> None:
    """Write the pack: sorted, so a regenerated pack diffs cleanly."""
    pack = {
        "generated": datetime.now(UTC).date().isoformat(),
        "sources": dict(sorted(report.sources.items())),
        "labels": {iri: dict(sorted(labels[iri].items())) for iri in sorted(labels)},
        "aliases": dict(sorted((aliases or {}).items())),
        "codes": dict(sorted((codes or {}).items())),
    }
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
