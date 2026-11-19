"""Concept labels from vocabularies (TASKS 8.6, ADR-0006)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from rdflib import Graph
from sqlalchemy import select

from fdpindex.models import Concept, ConceptStatus
from fdpindex.outbound import OutboundClient
from fdpindex.rdf import describe
from fdpindex.search.concepts import ConceptResolver, ResolverSettings, concept_fields
from fdpindex.search.indexer import SearchIndexer
from fdpindex.search.profile import default_profile
from fdpindex.search.query import SearchParams, SearchService
from tests.conftest import FDP, FakeNetwork, Services, ping, public_resolver

pytestmark = pytest.mark.unit

WIKIDATA = "http://www.wikidata.org/entity/Q128581"
MESH = "http://id.nlm.nih.gov/mesh/D001943"
MESH_PARENT = "http://id.nlm.nih.gov/mesh/D009371"
EDAM = "http://edamontology.org/topic_0622"
ROR = "https://ror.org/006hf6230"
ELSEWHERE = "https://vocab.unlisted.example/concept/1"
HEAL = "http://publications.europa.eu/resource/authority/data-theme/HEAL"


# --- naming predicates --------------------------------------------------------------


def _graph(turtle: str) -> Graph:
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    return graph


@pytest.mark.parametrize(
    "turtle",
    [
        '<http://x.example/c> <http://www.w3.org/2004/02/skos/core#prefLabel> "Cancer"@en .',
        '<http://x.example/c> <http://www.w3.org/2000/01/rdf-schema#label> "Cancer"@en .',
        '<http://x.example/c> <http://purl.org/dc/terms/title> "Cancer"@en .',
        '<http://x.example/c> <http://purl.org/dc/elements/1.1/title> "Cancer"@en .',
        '<http://x.example/c> <https://schema.org/name> "Cancer"@en .',
        '<http://x.example/c> <http://www.geonames.org/ontology#name> "Cancer"@en .',
        # SKOS-XL: the label is a resource with a literal form.
        "<http://x.example/c> <http://www.w3.org/2008/05/skos-xl#prefLabel> [ "
        '<http://www.w3.org/2008/05/skos-xl#literalForm> "Cancer"@en ] .',
        # Written with https, asked for with http.
        '<https://x.example/c> <http://www.w3.org/2000/01/rdf-schema#label> "Cancer"@en .',
    ],
)
def test_vocabularies_name_concepts_in_many_ways(turtle: str) -> None:
    assert describe(_graph(turtle), "http://x.example/c").labels == {"en": "Cancer"}


def test_preferred_labels_synonyms_and_broader_concepts() -> None:
    names = describe(
        _graph(
            """
            @prefix skos: <http://www.w3.org/2004/02/skos/core#> .
            @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
            <http://x.example/c> rdfs:label "breast tumour"@en ;
                skos:prefLabel "Breast cancer"@en, "Borstkanker"@nl ;
                skos:altLabel "mammary carcinoma"@en, "Breast cancer"@en ;
                <http://id.nlm.nih.gov/mesh/vocab#broaderDescriptor> <http://x.example/neoplasms> ;
                skos:broader <http://x.example/disease> .
            <http://x.example/other> rdfs:label "Not this one" .
            """
        ),
        "http://x.example/c",
    )
    # skos:prefLabel outranks rdfs:label; a synonym equal to the label is dropped.
    assert names.labels == {"en": "Breast cancer", "nl": "Borstkanker"}
    assert names.synonyms == {"en": ["mammary carcinoma"]}
    assert set(names.broader) == {"http://x.example/disease", "http://x.example/neoplasms"}


def test_concept_fields_are_the_named_iri_fields() -> None:
    fields = set(concept_fields(default_profile()))
    assert {"theme", "publisher", "creator", "conformsTo", "spatial", "license"} <= fields
    assert not {"landingPage", "downloadUrl", "accessUrl", "contactEmail", "keyword"} & fields


# --- end to end ------------------------------------------------------------------------

DATASET = f"""
@prefix dct: <http://purl.org/dc/terms/> .
@prefix dcat: <http://www.w3.org/ns/dcat#> .
<{FDP}/dataset/d1> a dcat:Dataset ;
    dct:title "Dataset one" ;
    dct:subject <{WIKIDATA}>, <{MESH}>, <{ELSEWHERE}> ;
    dcat:theme <{EDAM}>, <{HEAL}> ;
    dct:publisher <{ROR}> ;
    dct:isPartOf <{FDP}/catalog/c1> .
"""


class Vocabularies:
    """The vocabulary services, answering as the real ones did when probed (2026-11-19)."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        host = request.headers.get("host") or parts.netloc
        if host == "www.wikidata.org" and query.get("action") == ["wbgetentities"]:
            entities = {
                qid: {
                    "labels": {
                        "en": {"value": "breast cancer"},
                        "de": {"value": "Brustkrebs"},
                    },
                    "aliases": {"en": [{"value": "mammary cancer"}]},
                }
                for qid in query["ids"][0].split("|")
            }
            return httpx.Response(200, json={"entities": entities})
        if host == "www.wikidata.org" and query.get("action") == ["wbgetclaims"]:
            return httpx.Response(200, json={"claims": {}})
        if host == "api.ror.org":
            return httpx.Response(
                200,
                json={
                    "names": [
                        {"value": "Universiteit Twente", "types": ["label"], "lang": "nl"},
                        {
                            "value": "University of Twente",
                            "types": ["ror_display", "label"],
                            "lang": "en",
                        },
                        {"value": "UT", "types": ["acronym"], "lang": None},
                    ]
                },
            )
        if host == "www.ebi.ac.uk":
            return httpx.Response(
                200,
                json={
                    "_embedded": {
                        "terms": [
                            {
                                "label": "Genomics",
                                "synonyms": ["Genomes", "Exomes"],
                                "is_defining_ontology": True,
                            }
                        ]
                    }
                },
            )
        if host == "id.nlm.nih.gov":
            iri = f"http://id.nlm.nih.gov{parts.path}"
            label = {"/mesh/D001943": "Breast Neoplasms", "/mesh/D009371": "Neoplasms by Site"}
            broader = (
                f" ; <http://id.nlm.nih.gov/mesh/vocab#broaderDescriptor> <{MESH_PARENT}>"
                if parts.path == "/mesh/D001943"
                else ""
            )
            body = (
                f'<{iri}> <http://www.w3.org/2000/01/rdf-schema#label> "{label[parts.path]}"@en'
                f"{broader} ."
            )
            return httpx.Response(200, text=body, headers={"Content-Type": "text/turtle"})
        return httpx.Response(404, text="not found")


@pytest.fixture
async def resolved(
    services: Services, network: FakeNetwork
) -> AsyncIterator[tuple[SearchService, ConceptResolver, Vocabularies, list[Any]]]:
    network.standard_fdp()
    network.serve(f"{FDP}/dataset/d1", DATASET)
    indexer = SearchIndexer(services.session_factory, default_profile(), debounce=0)
    services.index.add_listener(indexer.notify)
    await ping(services)
    await indexer.sync_once()
    vocabularies = Vocabularies()
    reprojected: list[Any] = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(vocabularies.handler), follow_redirects=False
    ) as http:
        outbound = OutboundClient(
            http,
            allow_private=False,
            max_bytes=1_000_000,
            max_redirects=3,
            user_agent="test",
            resolver=public_resolver,
        )

        def changed(entry: Any) -> None:
            reprojected.append(entry)
            indexer.reindex(entry)

        resolver = ConceptResolver(
            services.session_factory,
            outbound,
            indexer.profile,
            ResolverSettings(
                enabled=True,
                hosts=frozenset(
                    {"www.wikidata.org", "id.nlm.nih.gov", "edamontology.org", "ror.org"}
                ),
                languages=("en", "nl", "de"),
                requests_per_second=1000,
            ),
            on_changed=changed,
        )
        while await resolver.sync_once():
            pass
        await indexer.sync_once()
        yield (
            SearchService(services.session_factory, services.settings, indexer),
            resolver,
            vocabularies,
            reprojected,
        )


async def _status(services_factory: Callable[[], Any], iri: str) -> Concept:
    async with services_factory() as session:
        concept = await session.scalar(select(Concept).where(Concept.iri == iri))
    assert concept is not None, iri
    return concept


async def test_concepts_are_resolved_from_their_vocabularies(
    resolved: tuple[SearchService, ConceptResolver, Vocabularies, list[Any]],
    services: Services,
) -> None:
    _, _, vocabularies, reprojected = resolved
    factory = services.session_factory
    assert (await _status(factory, WIKIDATA)).status is ConceptStatus.RESOLVED
    assert (await _status(factory, ROR)).source == "ror"
    assert (await _status(factory, EDAM)).source == "ols"
    mesh = await _status(factory, MESH)
    assert (mesh.source, mesh.broader) == ("rdf", [MESH_PARENT])
    # The broader concept is resolved too, one level up.
    parent = await _status(factory, MESH_PARENT)
    assert (parent.status, parent.depth) == (ConceptStatus.RESOLVED, 1)
    # Not on the allow-list, or labelled by the index itself: not asked for.
    assert (await _status(factory, ELSEWHERE)).status is ConceptStatus.SKIPPED
    heal = await _status(factory, HEAL)
    assert (heal.status, heal.source) == (ConceptStatus.SKIPPED, "bundled")
    assert not any(
        "unlisted" in url or "publications.europa.eu" in url for url in vocabularies.requests
    )
    assert reprojected  # the entry that uses them was projected again


@pytest.mark.parametrize(
    ("words", "why"),
    [
        ("Brustkrebs", "a Wikidata label in German"),
        ("mammary cancer", "a Wikidata alias"),
        ("Breast Neoplasms", "a MeSH label"),
        ("Neoplasms by Site", "the label of the MeSH descriptor above it"),
        ("Exomes", "an EDAM synonym from OLS"),
        ("University of Twente", "the ROR name of the publisher"),
    ],
)
async def test_resolved_names_are_searchable(
    resolved: tuple[SearchService, ConceptResolver, Vocabularies, list[Any]],
    words: str,
    why: str,
) -> None:
    search = resolved[0]
    result = await search.search(SearchParams(q=words, facets=[]))
    assert [h["title"] for h in result["hits"]] == ["Dataset one"], why


async def test_resolved_names_label_facets_and_values(
    resolved: tuple[SearchService, ConceptResolver, Vocabularies, list[Any]],
) -> None:
    search = resolved[0]
    result = await search.search(SearchParams(types=["dataset"], lang="de"))
    themes = {b["value"]: b["label"] for b in result["facets"]["theme"]["buckets"]}
    assert themes[WIKIDATA] == "Brustkrebs"  # in the requested language
    assert themes[EDAM] == "Genomics"
    assert themes[HEAL] == "Gesundheit" or themes[HEAL] == "Health"  # bundled, untouched
    publishers = {b["value"]: b["label"] for b in result["facets"]["publisher"]["buckets"]}
    assert publishers[ROR] == "University of Twente"


async def test_a_host_that_answers_429_is_paused_and_its_concepts_wait(
    services: Services, network: FakeNetwork
) -> None:
    network.standard_fdp()
    network.serve(f"{FDP}/dataset/d1", DATASET)
    indexer = SearchIndexer(services.session_factory, default_profile(), debounce=0)
    services.index.add_listener(indexer.notify)
    await ping(services)
    await indexer.sync_once()
    vocabularies = Vocabularies()

    def handler(request: httpx.Request) -> httpx.Response:
        if (request.headers.get("host") or request.url.host) == "www.wikidata.org":
            return httpx.Response(429, headers={"Retry-After": "120"}, text="slow down")
        return vocabularies.handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        outbound = OutboundClient(
            http,
            allow_private=False,
            max_bytes=1_000_000,
            max_redirects=3,
            user_agent="test",
            resolver=public_resolver,
        )
        resolver = ConceptResolver(
            services.session_factory,
            outbound,
            indexer.profile,
            ResolverSettings(
                enabled=True,
                hosts=frozenset({"www.wikidata.org", "id.nlm.nih.gov"}),
                requests_per_second=1000,
            ),
            on_changed=indexer.reindex,
        )
        while await resolver.sync_once():
            pass
    wikidata = await _status(services.session_factory, WIKIDATA)
    assert wikidata.status is ConceptStatus.PENDING and wikidata.fetched_at is None
    assert (await _status(services.session_factory, MESH)).status is ConceptStatus.RESOLVED


async def test_a_redirect_off_the_allow_list_is_not_followed(services: Services) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.nlm.nih.gov" or request.headers.get("host") == "id.nlm.nih.gov":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example.org/x"})
        return httpx.Response(200, text="should not be reached")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        outbound = OutboundClient(
            http,
            allow_private=False,
            max_bytes=10_000,
            max_redirects=3,
            user_agent="test",
            resolver=public_resolver,
        )
        resolver = ConceptResolver(
            services.session_factory,
            outbound,
            default_profile(),
            ResolverSettings(enabled=True, hosts=frozenset({"id.nlm.nih.gov"})),
            on_changed=lambda _entry: None,
        )
        results = await resolver._fetch_all(  # pyright: ignore[reportPrivateUsage]
            [Concept(iri=MESH, host="id.nlm.nih.gov", depth=0, broader=[])]
        )
    assert results[MESH].status is ConceptStatus.FAILED
    assert "not allowed" in (results[MESH].error or "")


def test_settings_parse_comma_separated_hosts() -> None:
    from fdpindex.config import Settings

    config = Settings(labels_hosts="a.example, b.example", labels_languages="en,nl")  # pyright: ignore[reportArgumentType]
    assert (config.labels_hosts, config.labels_languages) == (
        ["a.example", "b.example"],
        ["en", "nl"],
    )
    assert json.loads(json.dumps(config.labels_resolve)) is False
