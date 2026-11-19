"""The bundled vocabulary pack and ``fdpindex vocab update`` (TASKS 6.8)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from fdpindex.outbound import OutboundClient
from fdpindex.search.profile import default_profile
from fdpindex.search.project import project_entry
from fdpindex.search.vocab import BUNDLED, bundled_label, bundled_labels, pack_digest
from fdpindex.search.vocab_update import EU, VocabFetcher, write_pack
from fdpindex.sparql.labels import humanize
from tests.conftest import public_resolver
from tests.fixtures.search_corpus import ALPHA, nodes

pytestmark = pytest.mark.unit

HEAL = f"{EU}data-theme/HEAL"


def test_the_pack_has_labels_in_several_languages() -> None:
    assert bundled_label(HEAL) == "Health"
    assert bundled_label(HEAL, "nl") == "Gezondheid"
    assert bundled_label(HEAL, "de-AT") == "Gesundheit"
    assert bundled_label(HEAL, "xx") == "Health"  # English when the language is missing
    # https forms of the EU tables and Creative Commons are the same IRIs.
    assert bundled_label("https://publications.europa.eu/resource/authority/data-theme/HEAL")
    assert bundled_label("https://creativecommons.org/licenses/by/4.0/") == "CC BY 4.0"
    assert bundled_label("https://spdx.org/licenses/MIT") == "MIT License"
    assert {"en", "nl", "de", "fr"} <= set(bundled_labels(f"{EU}language/NLD"))
    assert bundled_label("https://example.org/unknown") is None
    assert BUNDLED[HEAL] == "Health"
    assert len(pack_digest()) == 12


def test_one_country_however_it_is_named() -> None:
    from fdpindex.search.project import canonical_iri
    from fdpindex.search.to_sparql import raw_forms
    from fdpindex.search.vocab import aliases_of, country_code

    nld = f"{EU}country/NLD"
    for form in (
        "https://sws.geonames.org/2750405/",
        "http://sws.geonames.org/2750405",
        "https://www.geonames.org/2750405/netherlands.html",
        "http://lexvo.org/id/iso3166/NL",
        "https://publications.europa.eu/resource/authority/country/NLD",
    ):
        assert canonical_iri(form) == nld, form
    assert country_code(" NL ") == country_code("nld") == nld
    assert bundled_label("https://sws.geonames.org/6255148/") is None  # aliases, not labels
    assert canonical_iri("https://sws.geonames.org/6255148/") == f"{EU}continent/EUROPE"
    assert bundled_label(f"{EU}continent/EUROPE", "nl") == "Europa"
    # A GeoNames place that is not a country keeps its one GeoNames form.
    assert canonical_iri("http://www.geonames.org/2759794/amsterdam.html") == (
        "https://sws.geonames.org/2759794/"
    )
    assert "https://sws.geonames.org/2750405/" in aliases_of(nld)
    forms = raw_forms(nld, {})
    assert {"http://www.geonames.org/2750405", "nl", "nld", nld.lower()} <= forms
    assert "http://sws.geonames.org/2759794/" in raw_forms("https://sws.geonames.org/2759794/", {})


def test_terms_name_properties() -> None:
    assert humanize("http://www.w3.org/ns/dcat#keyword") == "Keyword"
    assert humanize("http://purl.org/dc/terms/accessRights") == "Access Rights"


def test_bundled_labels_are_searched_in_every_language() -> None:
    projection = project_entry("alpha", nodes(ALPHA), default_profile())
    d1 = next(r for r in projection.resources if r.iri == f"{ALPHA}/dataset/d1")
    texts = {(config, text) for config, _, text in d1.segments}
    assert ("fdpindex_dutch", "Gezondheid") in texts
    assert ("fdpindex_german", "Gesundheit") in texts
    assert ("fdpindex_english", "Health") in texts


SCHEME = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:skos="http://www.w3.org/2004/02/skos/core#">
  <skos:ConceptScheme rdf:about="{EU}data-theme">
    <skos:hasTopConcept rdf:resource="{EU}data-theme/HEAL"/>
  </skos:ConceptScheme>
</rdf:RDF>"""
CONCEPT = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:skos="http://www.w3.org/2004/02/skos/core#">
  <skos:Concept rdf:about="{EU}data-theme/HEAL">
    <skos:prefLabel xml:lang="en">Health</skos:prefLabel>
    <skos:prefLabel xml:lang="nl">Gezondheid</skos:prefLabel>
    <skos:prefLabel xml:lang="hu">Egészségügy</skos:prefLabel>
  </skos:Concept>
</rdf:RDF>"""
COUNTRY_SCHEME = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:skos="http://www.w3.org/2004/02/skos/core#">
  <skos:ConceptScheme rdf:about="{EU}country">
    <skos:hasTopConcept rdf:resource="{EU}country/NLD"/>
  </skos:ConceptScheme>
</rdf:RDF>"""
NLD = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:skos="http://www.w3.org/2004/02/skos/core#">
  <skos:Concept rdf:about="{EU}country/NLD">
    <skos:prefLabel xml:lang="en">Netherlands</skos:prefLabel>
  </skos:Concept>
</rdf:RDF>"""
COUNTRY_INFO = (
    "#ISO\tISO3\tISO-Numeric\tfips\tCountry\tCapital\tArea\tPopulation\tContinent\ttld"
    "\tCurrencyCode\tCurrencyName\tPhone\tPostal\tRegex\tLanguages\tgeonameid\n"
    "NL\tNLD\t528\tNL\tNetherlands\tAmsterdam\t41526\t17231017\tEU\t.nl\tEUR\tEuro"
    "\t31\t####\t^x$\tnl-NL\t2750405\tDE,BE\t\n"
    # Not in the EU table here: skipped.
    "XK\tXKX\t0\tKV\tKosovo\tPristina\t10908\t1845300\tEU\t\tEUR\tEuro"
    "\t383\t\t\tsq,sr\t831053\tRS,AL\t\n"
)
DCAT_TTL = """@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
dcat:keyword rdfs:label "keyword"@en, "mot-clés"@fr ."""


async def test_the_update_fetches_and_writes_a_pack(tmp_path: Path) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        host = request.headers.get("host", "")
        url = url.replace(request.url.host, host, 1) if host else url
        asked.append(url)
        if url == f"{EU}data-theme":
            return httpx.Response(200, text=SCHEME, headers={"Content-Type": "text/xml"})
        if url == f"{EU}data-theme/HEAL":
            return httpx.Response(200, text=CONCEPT, headers={"Content-Type": "text/xml"})
        if url == "https://spdx.org/licenses/licenses.json":
            body = {
                "licenses": [
                    {"licenseId": "MIT", "name": "MIT License", "isDeprecatedLicenseId": False},
                    {"licenseId": "GPL-2.0", "name": "old", "isDeprecatedLicenseId": True},
                ]
            }
            return httpx.Response(200, json=body)
        if url == f"{EU}country":
            return httpx.Response(200, text=COUNTRY_SCHEME, headers={"Content-Type": "text/xml"})
        if url == f"{EU}country/NLD":
            return httpx.Response(200, text=NLD, headers={"Content-Type": "text/xml"})
        if url == "https://download.geonames.org/export/dump/countryInfo.txt":
            return httpx.Response(200, text=COUNTRY_INFO, headers={"Content-Type": "text/plain"})
        if url == "https://www.w3.org/ns/dcat.ttl":
            return httpx.Response(200, text=DCAT_TTL, headers={"Content-Type": "text/turtle"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OutboundClient(
            http,
            allow_private=False,
            max_bytes=1_000_000,
            max_redirects=3,
            user_agent="test",
            resolver=public_resolver,
        )
        fetcher = VocabFetcher(client, languages=["en", "nl"])
        report = await fetcher.run()
    assert fetcher.labels[HEAL] == {"en": "Health", "nl": "Gezondheid"}  # hu not kept
    assert fetcher.labels["https://spdx.org/licenses/MIT"] == {"en": "MIT License"}
    assert "https://spdx.org/licenses/GPL-2.0" not in fetcher.labels
    assert fetcher.labels["http://www.w3.org/ns/dcat#keyword"] == {"en": "keyword"}
    assert report.sources["eu:data-theme"] == 1
    assert report.failed  # the other tables answered 404, and say so
    # GeoNames' and lexvo's names of a country in the EU table, and its codes (8.4).
    assert fetcher.aliases == {
        "https://sws.geonames.org/2750405/": f"{EU}country/NLD",
        "http://lexvo.org/id/iso3166/NL": f"{EU}country/NLD",
    }
    assert fetcher.codes == {"nl": f"{EU}country/NLD", "nld": f"{EU}country/NLD"}
    path = tmp_path / "vocab.json"
    write_pack(path, fetcher.labels, report, aliases=fetcher.aliases, codes=fetcher.codes)
    pack = json.loads(path.read_text("utf-8"))
    assert pack["labels"][HEAL]["nl"] == "Gezondheid"
    assert pack["codes"]["nld"] == f"{EU}country/NLD"
    assert list(pack["labels"]) == sorted(pack["labels"])
