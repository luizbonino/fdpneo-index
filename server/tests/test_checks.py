"""Checking an FDP before registering it (TASKS 15.11)."""

from __future__ import annotations

from typing import Any

import pytest

from fdpindex.findings import WalkSummary, analyse, read_page
from fdpindex.harvest import harvest
from fdpindex.network import parse
from tests.conftest import FDP, ROOT_TTL, FakeNetwork
from tests.test_api import (
    ADMIN,
    Api,
    api,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)

pytestmark = pytest.mark.unit

BIG = "https://big.example.org"
DATASETS = 12


def big_fdp(network: FakeNetwork) -> None:
    """An FDP with one catalogue of twelve datasets, each with one distribution."""
    root = ROOT_TTL.replace(FDP, BIG)
    root += f"<{BIG}> fdp:metadataCatalog <{BIG}/catalog/c1> .\n"
    network.serve(BIG, root)
    members = ", ".join(f"<{BIG}/dataset/d{i}>" for i in range(DATASETS))
    network.serve(
        f"{BIG}/catalog/c1",
        f"""
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix ldp: <http://www.w3.org/ns/ldp#> .
<{BIG}/catalog/c1> a dcat:Catalog ; dct:title "Big" ; dcat:dataset {members} .
<{BIG}/catalog/c1#datasets> a ldp:DirectContainer ;
    ldp:membershipResource <{BIG}/catalog/c1> ;
    ldp:hasMemberRelation dcat:dataset ;
    ldp:contains {members} .
""",
    )
    for i in range(DATASETS):
        d = f"{BIG}/dataset/d{i}"
        network.serve(
            d,
            f"""
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix ldp: <http://www.w3.org/ns/ldp#> .
<{d}> a dcat:Dataset ; dct:title "D{i}" ; dcat:distribution <{d}/dist> .
<{d}#distributions> a ldp:DirectContainer ;
    ldp:membershipResource <{d}> ;
    ldp:hasMemberRelation dcat:distribution ;
    ldp:contains <{d}/dist> .
""",
        )
        network.serve(
            f"{d}/dist",
            f"""
@prefix dcat: <http://www.w3.org/ns/dcat#> .
<{d}/dist> a dcat:Distribution ; dcat:accessURL <{d}/data> .
""",
        )


async def test_a_sample_reads_a_few_children_of_each_page(
    services: Any, network: FakeNetwork
) -> None:
    big_fdp(network)
    result = await harvest(services.outbound, BIG, timeout_seconds=5, max_nodes=100, per_page=3)
    read = {n.node_iri for n in result.nodes}
    # The root, the catalogue, three of its datasets and their distributions.
    assert len(read) == 1 + 1 + 3 + 3
    assert f"{BIG}/dataset/d0/dist" in read
    assert len(result.sampled_out) == DATASETS - 3
    assert not result.queue


def test_pages_left_unread_are_not_findings() -> None:
    catalog = f"{FDP}/catalog/c1"
    dataset = f"{FDP}/dataset/d1"
    pages = [
        read_page(
            catalog,
            f"<{catalog}> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> "
            f"<http://www.w3.org/ns/dcat#Catalog> .\n"
            f"<{catalog}> <http://www.w3.org/ns/dcat#dataset> <{dataset}> .\n",
            root=False,
        )
    ]
    found = analyse(FDP, pages, WalkSummary(complete=True))
    assert [f["code"] for f in found["findings"]] == ["linked-only-in-dcat"]
    sampled = analyse(FDP, pages, WalkSummary(not_read=frozenset({dataset})))
    assert sampled["findings"] == []


def test_a_walk_cut_short_does_not_say_how_far_the_metadata_reaches() -> None:
    expected = parse("reach: dataservice").expectations()
    pages = [read_page(FDP, f'<{FDP}> <http://purl.org/dc/terms/title> "x" .\n', root=True)]
    whole = analyse(FDP, pages, WalkSummary(complete=True), expectations=expected)
    assert [r["code"] for r in whole["requirements"]] == ["reach"]
    cut = analyse(FDP, pages, WalkSummary(at_ceiling=True, unread=3), expectations=expected)
    assert cut["requirements"] == []


def _settings(api: Api, **changes: Any) -> None:  # noqa: F811
    settings = api.client.get("/index/settings", headers=ADMIN).json()
    del settings["isDefault"]
    settings.update(changes)
    response = api.client.put("/index/settings", json=settings, headers=ADMIN)
    assert response.status_code == 200, response.text


def _check(api: Api, url: str, **kwargs: Any) -> dict[str, Any]:  # noqa: F811
    response = api.client.post("/index/checks", json={"clientUrl": url}, **kwargs)
    assert response.status_code == 202, response.text
    started = response.json()
    api.drain()
    done = api.client.get(f"/index/checks/{started['id']}").json()
    assert done["status"] == "done"
    return done


def test_anyone_may_check_when_the_index_accepts_fdps(api: Api, network: FakeNetwork) -> None:  # noqa: F811
    big_fdp(network)
    policy = api.client.get("/index/checks").json()
    assert policy == {"who": "anyone", "allowed": True, "pages": 200, "perPage": 5}
    check = _check(api, BIG)
    assert check["root"] == {"state": "VALID", "error": None, "title": "Demo FDP"}
    assert check["registered"] is None
    report = check["report"]
    assert report["reach"] == "distribution"
    assert report["levels"]["dataset"] == 5
    assert check["sampledOut"] == DATASETS - 5
    assert report["findings"] == []
    # Nothing was registered or stored.
    entries = api.client.get("/index/entries", params={"state": "ALL"}, headers=ADMIN).json()
    assert entries["content"] == []


def test_a_root_that_is_not_an_fdp(api: Api) -> None:  # noqa: F811
    check = _check(api, "https://nothing.example.org")
    assert check["root"]["state"] == "INVALID"
    assert check["root"]["error"] == "HTTP 404"
    assert check["report"] is None


def test_with_approval_only_administrators_check(api: Api) -> None:  # noqa: F811
    _settings(api, autoPermit=False)
    assert api.client.get("/index/checks").json()["allowed"] is False
    assert api.client.get("/index/checks", headers=ADMIN).json()["allowed"] is True
    anonymous = api.client.post("/index/checks", json={"clientUrl": FDP})
    assert anonymous.status_code == 401
    # A pending FDP: the administrator sees its entry while reviewing it.
    assert api.ping(FDP).status_code == 204
    check = _check(api, FDP, headers=ADMIN)
    assert check["registered"]["permit"] == "PENDING"
    assert check["report"]["reach"] == "dataset"
    # An administrator may open checks to anyone.
    _settings(api, autoPermit=False, checkBeforeRegistering="anyone")
    check = _check(api, FDP)
    assert check["registered"] is None  # a pending entry is not public


def test_checks_are_rate_limited_and_follow_the_deny_list(api: Api) -> None:  # noqa: F811
    denied = api.client.post("/index/checks", json={"clientUrl": "http://localhost:8080"})
    assert denied.status_code == 403
    _settings(
        api,
        ping={
            **api.client.get("/index/settings", headers=ADMIN).json()["ping"],
            "rateLimitHits": 1,
        },
    )
    _check(api, FDP)
    again = api.client.post("/index/checks", json={"clientUrl": FDP})
    assert again.status_code == 429
    # Administrators are not counted.
    _check(api, FDP, headers=ADMIN)
    assert api.client.get("/index/checks/nope").status_code == 404
