"""Network profiles: what a network expects of its FDPs (TASKS 15.9)."""

from __future__ import annotations

from typing import Any

import pytest

from fdpindex.findings import WalkSummary, analyse, read_page
from fdpindex.network import EXAMPLES, ProfileError, example, parse
from tests.conftest import FDP
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.test_declared import (
    SCHEMA,
    api as declared_api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    harvest,
)

pytestmark = pytest.mark.unit

DCAT = "http://www.w3.org/ns/dcat#"
DCT = "http://purl.org/dc/terms/"
EJP = "https://w3id.org/ejp-rd/vocabulary#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def test_the_bundled_profiles_are_valid() -> None:
    for name in EXAMPLES:
        parse(example(name))
    assert parse(example("generic")).empty
    erdera = parse(example("erdera"))
    assert erdera.reach == "dataservice"
    assert erdera.benchmarks == [
        "https://ostrails.github.io/assessment-component-metadata-records/benchmark/ERDERA_Onboard.ttl"
    ]
    expected = erdera.expectations()
    assert f"{EJP}Biobank" in expected.types["dataset"]
    assert f"{DCT}title" in expected.requires["dataset"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("levels: {dataset: {requires: [foo:title]}}", "unknown prefix 'foo'"),
        ("shapes: [ftp://x.example/s.ttl]", r"not an http\(s\) address"),
        ("reach: everything", "reach"),
        ("[a, b]", "must be a mapping"),
        ("network: {name: x\n", "not valid YAML"),
        ("colour: blue", "colour"),
    ],
)
def test_a_broken_profile_says_why(source: str, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        parse(source)


def _page(iri: str, *triples: tuple[str, str, str], root: bool = False, watched: Any = None):
    text = "".join(
        f"<{s}> <{p}> {o if o.startswith(chr(34)) else f'<{o}>'} .\n" for s, p, o in triples
    )
    return read_page(iri, text, root=root, watched=watched or frozenset())


def test_requirements_recommendations_types_and_vocabularies() -> None:
    profile = parse(
        """
prefixes: {ejp: "https://w3id.org/ejp-rd/vocabulary#"}
reach: dataservice
levels:
  dataset:
    types: [ejp:Biobank]
    requires: [dct:title, dct:description]
    recommends: [dcat:keyword]
vocabularies:
  ejp:vpConnection: ["https://w3id.org/ejp-rd/vocabulary#"]
"""
    )
    expected = profile.expectations()
    watched = expected.watched
    biobank = f"{FDP}/biobank/b1"
    dataset = f"{FDP}/dataset/d1"
    pages = [
        _page(FDP, (FDP, RDF_TYPE, "https://w3id.org/fdp/fdp-o#MetadataService"), root=True),
        # A biobank counts as a dataset: it is checked as one.
        _page(
            biobank,
            (biobank, RDF_TYPE, f"{EJP}Biobank"),
            (biobank, f"{DCT}title", '"Bank"'),
            (biobank, f"{EJP}vpConnection", "https://example.org/other#flag"),
            watched=watched,
        ),
        _page(
            dataset,
            (dataset, RDF_TYPE, f"{DCAT}Dataset"),
            (dataset, f"{DCT}title", '"D"'),
            (dataset, f"{DCT}description", '"About D"'),
            (dataset, f"{DCAT}keyword", '"k"'),
            (dataset, f"{EJP}vpConnection", f"{EJP}VPDiscoverable"),
            watched=watched,
        ),
    ]
    report = analyse(FDP, pages, WalkSummary(complete=True), expectations=expected)
    assert report["levels"]["dataset"] == 2
    got = {(r["code"], r["property"]): (r["count"], r["examples"]) for r in report["requirements"]}
    assert got == {
        ("reach", None): (1, []),
        ("requires", f"{DCT}description"): (1, [biobank]),
        ("vocabulary", f"{EJP}vpConnection"): (1, [biobank]),
    }
    assert [(r["code"], r["property"], r["count"]) for r in report["recommendations"]] == [
        ("recommends", f"{DCAT}keyword", 1)
    ]
    assert report["status"] == "findings"
    # Without a profile the same pages are fine: nothing is imposed.
    plain = analyse(FDP, pages, WalkSummary(complete=True))
    assert (plain["requirements"], plain["status"]) == ([], "fine")


def test_the_profile_through_the_api(api: Api) -> None:  # noqa: F811
    view = api.client.get("/index/network-profile").json()
    assert (view["isDefault"], view["empty"]) == (True, True)
    erdera = example("erdera")
    assert api.client.put("/index/network-profile", json={"source": erdera}).status_code == 401
    response = api.client.put("/index/network-profile", json={"source": erdera}, headers=ADMIN)
    assert response.status_code == 200, response.text
    view = response.json()
    assert (view["isDefault"], view["empty"]) == (False, False)
    assert view["source"] == erdera  # comments kept
    assert view["profile"]["network"]["name"] == "ERDERA Virtual Platform"
    bad = api.client.put(
        "/index/network-profile", json={"source": "reach: everywhere"}, headers=ADMIN
    )
    assert bad.status_code == 400
    assert "reach" in bad.json()["message"]
    assert api.client.delete("/index/network-profile", headers=ADMIN).json()["isDefault"]
    schema = api.client.get("/index/network-profile/schema").json()
    assert {"reach", "levels", "shapes", "benchmarks"} <= set(schema["properties"])
    names = [e["name"] for e in api.client.get("/index/network-profile/examples").json()]
    assert names == ["generic", "erdera"]


def test_a_profile_change_computes_health_again(api: Api) -> None:  # noqa: F811
    assert api.ping(FDP).status_code == 204
    index = api.app.state.index
    portal = api.client.portal
    assert portal is not None
    portal.call(index.schedule_health)
    api.drain()
    [entry] = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()[
        "content"
    ]
    url = f"/index/entries/{entry['uuid']}/health"
    assert api.client.get(url).json()["requirements"] == []
    source = "reach: distribution\nlevels: {dataset: {requires: [dcat:keyword]}}\n"
    api.client.put("/index/network-profile", json={"source": source}, headers=ADMIN)
    assert portal.call(index.schedule_health) == 1
    api.drain()
    codes = [r["code"] for r in api.client.get(url).json()["requirements"]]
    assert codes == ["reach", "requires"]
    stats = api.client.get("/index/stats").json()["health"]
    assert stats["missingRequirements"] == 1


def test_the_networks_shapes_are_a_required_check(declared_api: Any) -> None:  # noqa: F811
    conformance_api = declared_api
    source = f"shapes: [{SCHEMA}]\n"
    response = conformance_api.client.put(
        "/index/network-profile", json={"source": source}, headers=ADMIN
    )
    assert response.status_code == 200, response.text
    harvest(conformance_api)
    report = conformance_api.client.get(
        f"/index/conformance/entries/{conformance_api.entry}"
    ).json()
    network = next(p for p in report["packs"] if p["pack"] == "network")
    assert network["role"] == "required"
    # The FDP's own schema asks datasets for a description: "bad" has none.
    assert (network["records"], network["violations"]) == (5, 1)
