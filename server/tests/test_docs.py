"""The documentation's API examples run against a test index (TASKS 14.6, 14.9).

Every ```http block of docs/api.md is one request: a method and a path, then
headers, a blank line and a body. ``{id}`` and ``{uuid}`` stand for a
resource and an FDP of the test index, ``{check}`` a check started for the
test (TASKS 15.11). Each must answer 200, or 202 where it starts work.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

from fdpindex.auth import token_digest
from fdpindex.config import Settings
from fdpindex.main import create_app
from tests.conftest import FDP, public_resolver
from tests.test_conformance_service import TOKEN, SearchApi, serve

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

DOC = Path(__file__).resolve().parents[2] / "docs" / "api.md"


def examples() -> list[tuple[str, str, dict[str, str], str]]:
    found: list[tuple[str, str, dict[str, str], str]] = []
    for block in re.findall(r"```http\n(.*?)```", DOC.read_text("utf-8"), re.S):
        head, _, body = block.partition("\n\n")
        first, *header_lines = head.strip().splitlines()
        method, path = first.split(" ", 1)
        headers = dict(line.split(": ", 1) for line in header_lines)
        found.append((method, path.strip(), headers, body.strip()))
    return found


EXAMPLES = examples()


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SearchApi]:
    from tests.conftest import FakeNetwork

    network = FakeNetwork()
    serve(network)
    config = Settings(
        database_url="sqlite+aiosqlite://",
        admin_tokens=[token_digest(TOKEN)],
        worker_concurrency=1,
        search_enabled=True,
        conformance_enabled=True,
        conformance_workers=0,
        sparql_enabled=True,
        sparql_dir=tmp_path_factory.mktemp("sparql"),
        sparql_workers=1,
        sparql_worker_max_mb=0,
        search_rate_per_minute=1000,
        sparql_rate_per_minute=1000,
    )
    app = create_app(
        config,
        transport=httpx.MockTransport(network.handler),
        resolver=public_resolver,
        create_tables=True,
        configure_logs=False,
        run_scheduler=False,
    )
    with TestClient(app) as client:
        api = SearchApi(client, app)
        client.post("/", json={"clientUrl": FDP})
        api.settle()
        portal = client.portal
        assert portal is not None
        portal.call(app.state.sparql.sync.sync_once)
        yield api


def test_the_examples_were_found() -> None:
    assert len(EXAMPLES) >= 12


@pytest.mark.parametrize(("method", "path", "headers", "body"), EXAMPLES, ids=lambda x: str(x)[:60])
def test_example(
    index: SearchApi, method: str, path: str, headers: dict[str, str], body: str
) -> None:
    client = index.client
    hit = client.get("/index/search", params={"type": "dataset", "size": 1}).json()["hits"][0]
    path = path.replace("{id}", hit["id"]).replace("{uuid}", hit["fdp"]["uuid"])
    if "{check}" in path:
        started = client.post("/index/checks", json={"clientUrl": FDP}).json()
        path = path.replace("{check}", started["id"])
    response = client.request(method, path, headers=headers, content=body or None)
    expected = 202 if (method, path) == ("POST", "/index/checks") else 200
    assert response.status_code == expected, (
        f"{method} {path}: {response.status_code} {response.text[:300]}"
    )


def test_the_python_example_names_real_fields(index: SearchApi) -> None:
    hits = index.client.get("/index/search", params={"q": "", "type": "dataset", "size": 5}).json()[
        "hits"
    ]
    assert hits
    for hit in hits:
        assert hit["title"] and hit["iri"] and hit["fdp"]["clientUrl"] == FDP
