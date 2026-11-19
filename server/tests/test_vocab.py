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
    path = tmp_path / "vocab.json"
    write_pack(path, fetcher.labels, report)
    pack = json.loads(path.read_text("utf-8"))
    assert pack["labels"][HEAL]["nl"] == "Gezondheid"
    assert list(pack["labels"]) == sorted(pack["labels"])
