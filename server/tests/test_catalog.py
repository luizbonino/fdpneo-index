"""The catalogue feed and OAI-PMH (TASKS 11.5)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

import pytest
from rdflib import RDF, Graph, Namespace, URIRef

from fdpindex.conformance.packs import discover
from fdpindex.conformance.validator import ShaclValidator
from tests.conftest import FDP
from tests.test_conformance_service import (  # the search_api fixture too
    ADMIN,
    DCAT_AP,
    SearchApi,
    search_api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
HYDRA = Namespace("http://www.w3.org/ns/hydra/core#")
OAI = "{http://www.openarchives.org/OAI/2.0/}"
CATALOG = "http://testserver/index/catalog"


@pytest.fixture
def api(search_api: SearchApi) -> Iterator[SearchApi]:  # noqa: F811
    yield search_api


def turtle(api: SearchApi, url: str, **params: Any) -> Graph:
    response = api.client.get(url, params=params, headers={"Accept": "text/turtle"})
    assert response.status_code == 200, response.text
    graph = Graph()
    graph.parse(data=response.text, format="turtle")
    return graph


def test_the_feed_republishes_records_with_links_back(api: SearchApi) -> None:
    graph = turtle(api, "/index/catalog")
    catalog = URIRef(CATALOG)
    assert (catalog, RDF.type, DCAT.Catalog) in graph
    assert str(graph.value(catalog, HYDRA.totalItems)) == "4"  # a catalogue, three datasets
    records = set(graph.objects(catalog, DCAT.record))
    topics = {graph.value(r, FOAF.primaryTopic) for r in records}
    assert topics == {
        URIRef(f"{FDP}/catalog/c1"),
        URIRef(f"{FDP}/dataset/good"),
        URIRef(f"{FDP}/dataset/bad"),
        URIRef(f"{FDP}/dataset/nodist"),
    }
    good = next(
        r for r in records if graph.value(r, FOAF.primaryTopic) == URIRef(f"{FDP}/dataset/good")
    )
    assert graph.value(good, DCT.source) == URIRef(f"{FDP}/dataset/good")
    assert graph.value(good, DCT.modified) is not None
    assert set(graph.objects(good, DCT.conformsTo)) == {URIRef(DCAT_AP)}
    # The resource's triples as harvested, its distribution's too.
    assert (URIRef(f"{FDP}/dataset/good"), DCT.title, None) in graph
    assert (
        URIRef(f"{FDP}/distribution/g1"),
        DCAT.accessURL,
        URIRef("https://data.example.org/g1.csv"),
    ) in graph
    assert (catalog, DCAT.dataset, URIRef(f"{FDP}/dataset/good")) in graph
    assert (catalog, DCAT.catalog, URIRef(f"{FDP}/catalog/c1")) in graph


def test_pages_follow_hydra_next(api: SearchApi) -> None:
    seen: set[Any] = set()
    url, params = "/index/catalog", {"size": 3}
    pages = 0
    while True:
        response = api.client.get(url, params=params, headers={"Accept": "text/turtle"})
        graph = Graph()
        graph.parse(data=response.text, format="turtle")
        catalog = URIRef(CATALOG)
        seen |= set(graph.objects(catalog, DCAT.record))
        pages += 1
        view = graph.value(catalog, HYDRA.view)
        following = graph.value(view, HYDRA.next)
        if following is None:
            assert "link" not in response.headers
            break
        assert response.headers["link"] == f'<{following}>; rel="next"'
        url, params = str(following).removeprefix("http://testserver"), {}
    assert pages == 2
    assert len(seen) == 4


def test_formats_and_one_record(api: SearchApi) -> None:
    xml = api.client.get("/index/catalog", params={"format": "rdf"})
    assert xml.headers["content-type"].startswith("application/rdf+xml")
    jsonld = api.client.get("/index/catalog", headers={"Accept": "application/ld+json"})
    assert jsonld.headers["content-type"].startswith("application/ld+json")
    graph = turtle(api, "/index/catalog")
    record = next(iter(graph.objects(URIRef(CATALOG), DCAT.record)))
    one = turtle(api, str(record).removeprefix("http://testserver"))
    assert (record, RDF.type, DCAT.CatalogRecord) in one
    assert api.client.get("/index/catalog/records/nope").status_code == 404


def test_a_strict_feed_keeps_conforming_records(api: SearchApi) -> None:
    body = {"packs": [{"key": "dcat-ap@3.0.1"}], "surfaces": {"feed": "dcat-ap"}}
    assert (
        api.client.put("/index/conformance/settings", json=body, headers=ADMIN).status_code == 200
    )
    graph = turtle(api, "/index/catalog")
    topics = {
        graph.value(r, FOAF.primaryTopic) for r in graph.objects(URIRef(CATALOG), DCAT.record)
    }
    assert topics == {URIRef(f"{FDP}/catalog/c1"), URIRef(f"{FDP}/dataset/good")}


def test_the_feed_itself_meets_dcat_ap(api: SearchApi) -> None:
    """What 9.6 asked for: the index's own catalogue and records validate against DCAT-AP."""
    graph = turtle(api, "/index/catalog")
    outcome = ShaclValidator().validate(graph, discover()["dcat-ap@3.0.1"])
    own = {CATALOG, *(str(r) for r in graph.objects(URIRef(CATALOG), DCAT.record))}
    publisher = graph.value(URIRef(CATALOG), DCT.publisher)
    problems = [
        f
        for f in outcome.findings
        if f.severity == "violation" and (f.focus_node in own or f.focus_node == f"_:{publisher}")
    ]
    assert problems == []


# --- OAI-PMH ---------------------------------------------------------------------


def oai(api: SearchApi, **params: str) -> ET.Element:
    response = api.client.get("/index/oai", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/xml")
    return ET.fromstring(response.content)


def error(root: ET.Element) -> str | None:
    found = root.find(f"{OAI}error")
    return None if found is None else found.get("code")


def test_oai_identify_and_formats(api: SearchApi) -> None:
    identify = oai(api, verb="Identify").find(f"{OAI}Identify")
    assert identify is not None
    assert identify.findtext(f"{OAI}protocolVersion") == "2.0"
    assert identify.findtext(f"{OAI}baseURL") == "http://testserver/index/oai"
    assert identify.findtext(f"{OAI}granularity") == "YYYY-MM-DDThh:mm:ssZ"
    formats = oai(api, verb="ListMetadataFormats")
    assert formats.findtext(f".//{OAI}metadataPrefix") == "dcat_ap"


def test_oai_lists_records_in_pages(api: SearchApi, monkeypatch: pytest.MonkeyPatch) -> None:
    import fdpindex.catalog.oai as module

    monkeypatch.setattr(module, "PAGE", 3)
    first = oai(api, verb="ListRecords", metadataPrefix="dcat_ap")
    records = first.findall(f".//{OAI}record")
    assert len(records) == 3
    token = first.find(f".//{OAI}resumptionToken")
    assert token is not None and token.text and token.get("completeListSize") == "4"
    rdf = records[0].find(f"{OAI}metadata/{{http://www.w3.org/1999/02/22-rdf-syntax-ns#}}RDF")
    assert rdf is not None
    second = oai(api, verb="ListRecords", resumptionToken=token.text)
    assert len(second.findall(f".//{OAI}record")) == 1
    last = second.find(f".//{OAI}resumptionToken")
    assert last is not None and not last.text  # the list is complete
    identifiers = oai(api, verb="ListIdentifiers", metadataPrefix="dcat_ap")
    first_id = identifiers.findtext(f".//{OAI}header/{OAI}identifier")
    assert first_id is not None and first_id.startswith("oai:testserver:")
    one = oai(api, verb="GetRecord", identifier=first_id, metadataPrefix="dcat_ap")
    assert one.find(f".//{OAI}metadata") is not None


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"verb": "Nope"}, "badVerb"),
        ({}, "badVerb"),
        ({"verb": "ListRecords"}, "badArgument"),
        ({"verb": "ListRecords", "metadataPrefix": "oai_dc"}, "cannotDisseminateFormat"),
        (
            {"verb": "ListRecords", "metadataPrefix": "dcat_ap", "from": "2999-01-01"},
            "noRecordsMatch",
        ),
        ({"verb": "ListRecords", "metadataPrefix": "dcat_ap", "from": "yesterday"}, "badArgument"),
        ({"verb": "ListRecords", "resumptionToken": "garbage!"}, "badResumptionToken"),
        ({"verb": "ListSets"}, "noSetHierarchy"),
        (
            {"verb": "GetRecord", "identifier": "oai:x:y", "metadataPrefix": "dcat_ap"},
            "idDoesNotExist",
        ),
        ({"verb": "Identify", "extra": "1"}, "badArgument"),
    ],
)
def test_oai_errors(api: SearchApi, params: dict[str, str], code: str) -> None:
    assert error(oai(api, **params)) == code


def test_oai_over_post(api: SearchApi) -> None:
    response = api.client.post("/index/oai", data={"verb": "Identify"})
    assert response.status_code == 200
    assert b"<Identify>" in response.content or b":Identify>" in response.content


def test_a_record_names_one_profile() -> None:
    """DCAT-AP: at most one dct:conformsTo on a catalogue record (found on live FDPs)."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from fdpindex.catalog.feed import CatalogFeed, CatalogMeta, FeedRecord

    async def never() -> bool:
        return False

    feed = CatalogFeed(None, CatalogMeta("t", "d", "p"), hide_expired=never)  # type: ignore[arg-type]
    record = FeedRecord(
        id=1,
        public_id="x",
        iri=f"{FDP}/dataset/a",
        kind="dataset",
        entry_uuid=uuid4(),
        node_iri=f"{FDP}/dataset/a",
        modified=datetime.now(UTC),
        conforms_to=["https://example.org/first", "https://example.org/second"],
    )
    graph = Graph()
    feed.add_record(graph, None, record, "https://index.example.org/r/x")
    assert list(graph.objects(URIRef("https://index.example.org/r/x"), DCT.conformsTo)) == [
        URIRef("https://example.org/first")
    ]
