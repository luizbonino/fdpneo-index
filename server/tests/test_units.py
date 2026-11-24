"""Pure units: durations, settings, RDF extraction, outbound guard, harvester."""

from __future__ import annotations

import ssl
from datetime import timedelta

import httpcore
import httpx
import pytest
from pydantic import ValidationError

from fdpindex.durations import format_duration, parse_duration
from fdpindex.harvest import harvest, serialise
from fdpindex.outbound import OutboundClient, OutboundError, PinnedTransport, outbound_http
from fdpindex.rdf import extract_repository_metadata, parse_rdf
from fdpindex.settings import IndexSettings, default_settings
from tests.conftest import FDP, ROOT_TTL, FakeNetwork, public_resolver

pytestmark = pytest.mark.unit


# --- durations ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PT168H", timedelta(days=7)),
        ("P7D", timedelta(days=7)),
        ("PT10M", timedelta(minutes=10)),
        ("PT1M30S", timedelta(minutes=1, seconds=30)),
        ("PT0.5S", timedelta(milliseconds=500)),
        ("P1DT2H", timedelta(days=1, hours=2)),
        ("pt6h", timedelta(hours=6)),
    ],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "P", "PT", "P1DT", "7 days", "P1W", "PT-5M", "P1Y"])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_duration(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(days=7), "PT168H"),
        (timedelta(hours=6), "PT6H"),
        (timedelta(minutes=10), "PT10M"),
        (timedelta(minutes=1, seconds=30), "PT1M30S"),
        (timedelta(0), "PT0S"),
        (timedelta(milliseconds=1500), "PT1.5S"),
    ],
)
def test_format_duration_like_java(value: timedelta, expected: str) -> None:
    assert format_duration(value) == expected


# --- settings ----------------------------------------------------------------------


def test_default_settings_serialise_like_the_reference() -> None:
    dumped = default_settings(auto_permit=True).model_dump(mode="json", by_alias=True)
    assert dumped == {
        "retrieval": {"rateLimitWait": "PT10M", "timeout": "PT1M"},
        "ping": {
            "validDuration": "PT168H",
            "rateLimitDuration": "PT6H",
            "rateLimitHits": 10,
            "denyList": ["^(http|https)://localhost(:[0-9]+){0,1}.*$"],
        },
        "autoPermit": True,
        "healthCheck": {
            "enabled": True,
            "interval": "PT24H",
            "failureThreshold": 3,
            "permits": ["ACCEPTED"],
            "reharvestOnChange": True,
        },
        # additions (TASKS 4.6, 4.8)
        "verifiedPermit": False,
        "expiry": {"enabled": False, "after": "PT2160H", "hide": False},
    }


def test_settings_validation() -> None:
    base = default_settings(auto_permit=True).model_dump(mode="json", by_alias=True)
    bad_regex = {**base, "ping": {**base["ping"], "denyList": ["(unclosed"]}}
    with pytest.raises(ValidationError, match="invalid regular expression"):
        IndexSettings.model_validate(bad_regex)
    bad_duration = {**base, "retrieval": {**base["retrieval"], "timeout": "soon"}}
    with pytest.raises(ValidationError):
        IndexSettings.model_validate(bad_duration)
    zero_timeout = {**base, "retrieval": {**base["retrieval"], "timeout": "PT0S"}}
    with pytest.raises(ValidationError, match="positive"):
        IndexSettings.model_validate(zero_timeout)
    duplicates = {**base, "ping": {**base["ping"], "denyList": ["b", "a", "b", " "]}}
    assert IndexSettings.model_validate(duplicates).ping.deny_list == ["a", "b"]


def test_deny_list_matches_the_whole_url() -> None:
    settings = default_settings(auto_permit=True)
    assert settings.denies("http://localhost:8080/fdp")
    assert settings.denies("https://localhost")
    assert not settings.denies("https://fdp.example.org/localhost")


def test_is_default_ignores_auto_permit_as_the_reference_does() -> None:
    defaults = default_settings(auto_permit=True)
    assert default_settings(auto_permit=False).matches_defaults(defaults)


# --- RDF ---------------------------------------------------------------------------------


def test_extracts_repository_metadata_from_an_fdpneo_root() -> None:
    graph = parse_rdf(ROOT_TTL, "text/turtle", FDP)
    result = extract_repository_metadata(graph, FDP + "/")
    assert result is not None
    assert result.repository_uri == FDP
    assert result.metadata == {
        "title": "Demo FDP",
        "description": "A FAIR Data Point for tests",
        "version": "1.0",
        "publisher": f"{FDP}#publisher",
        "publisherName": "Example University",
        "country": "http://lexvo.org/id/iso3166/NL",
    }


@pytest.mark.parametrize(
    "rdf_type",
    ["http://www.re3data.org/schema/3-0#Repository", "https://w3id.org/fdp/o#MetadataService"],
)
def test_accepts_the_reference_and_legacy_repository_types(rdf_type: str) -> None:
    graph = parse_rdf(
        f'<{FDP}> a <{rdf_type}> ; <http://purl.org/dc/terms/title> "T" .', "text/turtle", FDP
    )
    result = extract_repository_metadata(graph, FDP)
    assert result is not None and result.metadata["title"] == "T"


def test_no_repository_means_no_metadata() -> None:
    graph = parse_rdf(f'<{FDP}> <http://purl.org/dc/terms/title> "T" .', "text/turtle", FDP)
    assert extract_repository_metadata(graph, FDP) is None


# --- outbound guard --------------------------------------------------------------------------


def _client(
    network: FakeNetwork, *, allow_private: bool = False, max_bytes: int = 1000
) -> OutboundClient:
    return OutboundClient(
        httpx.AsyncClient(transport=httpx.MockTransport(network.handler)),
        allow_private=allow_private,
        max_bytes=max_bytes,
        max_redirects=2,
        user_agent="test",
        resolver=public_resolver,
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://internal.example/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "ftp://fdp.example.org/",
        "file:///etc/passwd",
    ],
)
async def test_outbound_refuses_non_public_targets(network: FakeNetwork, url: str) -> None:
    with pytest.raises(OutboundError) as caught:
        await _client(network).request("GET", url, timeout_seconds=5)
    assert caught.value.kind == "blocked"
    assert network.requests == []


async def test_outbound_allows_private_targets_when_configured(network: FakeNetwork) -> None:
    network.serve("http://localhost:8000/", "ok", content_type="text/plain")
    response = await _client(network, allow_private=True).request(
        "GET", "http://localhost:8000/", timeout_seconds=5
    )
    assert response.status == 200


async def test_outbound_rechecks_redirect_targets(network: FakeNetwork) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://10.1.2.3/admin"})

    client = OutboundClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        allow_private=False,
        max_bytes=1000,
        max_redirects=3,
        user_agent="test",
        resolver=public_resolver,
    )
    with pytest.raises(OutboundError, match="non-public"):
        await client.request("GET", FDP, timeout_seconds=5)


async def test_outbound_caps_the_body(network: FakeNetwork) -> None:
    network.serve(FDP + "/big", "x" * 5000, content_type="text/plain")
    response = await _client(network, max_bytes=100).request("GET", FDP + "/big", timeout_seconds=5)
    assert response.truncated
    assert len(response.body) == 100


# --- harvester -------------------------------------------------------------------------------------


async def test_harvest_walks_ldp_and_navigation_links_on_the_same_origin(
    network: FakeNetwork,
) -> None:
    result = await harvest(
        _client(network, max_bytes=100_000), FDP, timeout_seconds=5, max_nodes=50
    )
    assert sorted(node.node_iri for node in result.nodes) == [
        FDP,
        f"{FDP}/catalog/c1",
        f"{FDP}/dataset/d1",
    ]
    assert result.skipped_off_origin == 1
    assert not result.failed
    assert all(len(node.content_hash) == 64 for node in result.nodes)


async def test_harvest_stops_at_the_node_limit(network: FakeNetwork) -> None:
    result = await harvest(_client(network, max_bytes=100_000), FDP, timeout_seconds=5, max_nodes=2)
    assert len(result.nodes) == 2
    assert result.limit_reached


async def test_harvest_records_failed_nodes(network: FakeNetwork) -> None:
    del network.pages[f"{FDP}/dataset/d1"]
    network.failing.add(f"{FDP}/catalog/c1")
    result = await harvest(
        _client(network, max_bytes=100_000), FDP, timeout_seconds=5, max_nodes=50
    )
    # A 404 is the FDP saying the node does not exist; a refused connection is a failure.
    assert set(result.failed) == {f"{FDP}/catalog/c1"}
    assert not result.complete
    network.failing.clear()
    result = await harvest(
        _client(network, max_bytes=100_000), FDP, timeout_seconds=5, max_nodes=50
    )
    assert result.gone == {f"{FDP}/dataset/d1"}
    assert result.complete


def test_json_ld_with_a_remote_context_is_refused_without_fetching_it() -> None:
    """rdflib would fetch a remote @context itself, past the outbound checks (ADR-0002)."""
    import http.server
    import socketserver
    import threading
    import warnings

    from fdpindex.rdf import RdfParseError, parse_rdf

    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/ld+json")
            self.end_headers()
            self.wfile.write(b'{"@context": {"name": "http://schema.org/name"}}')

        def log_message(self, format: str, *args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        internal = f"http://127.0.0.1:{server.server_address[1]}/internal"
        for context in (f'"{internal}"', f'["{internal}", {{"a": "http://x/a"}}]'):
            document = f'{{"@context": {context}, "@id": "https://x.example/a", "name": "A"}}'
            with pytest.raises(RdfParseError, match="remote @context"):
                parse_rdf(document, "application/ld+json", "https://x.example/")
        nested = '{"@context": {"@import": "' + internal + '"}, "@id": "https://x.example/a"}'
        with pytest.raises(RdfParseError, match="remote @context"):
            parse_rdf(nested, "application/ld+json", "https://x.example/")
        server.shutdown()
    assert hits == []
    inline = '{"@context": {"name": "http://schema.org/name"}, "@id": "https://x.example/a", "name": "A"}'
    with warnings.catch_warnings():
        # rdflib's JSON-LD parser uses its own deprecated ConjunctiveGraph.
        warnings.simplefilter("ignore", DeprecationWarning)
        assert len(parse_rdf(inline, "application/ld+json", "https://x.example/")) == 1


_OK = [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"] * 3


class _RecordingNetwork(httpcore.AsyncNetworkBackend):
    """A network that records where each socket goes and the TLS name it uses."""

    def __init__(self) -> None:
        self.connects: list[tuple[str, int]] = []
        self.tls_names: list[str | None] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connects.append((host, port))
        network = self

        class Stream(httpcore.AsyncMockStream):
            async def start_tls(
                self,
                ssl_context: ssl.SSLContext,
                server_hostname: str | None = None,
                timeout: float | None = None,  # noqa: ASYNC109
            ) -> httpcore.AsyncNetworkStream:
                network.tls_names.append(server_hostname)
                return self

        return Stream(list(_OK))


def _pinned_client(
    network: _RecordingNetwork, answers: dict[str, list[list[str]]]
) -> tuple[httpx.AsyncClient, OutboundClient]:
    """DNS answers each host's lists in turn, the last one from then on."""

    async def resolver(host: str, port: int) -> list[str]:
        queue = answers[host]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    http = outbound_http(PinnedTransport(network))
    return http, OutboundClient(
        http,
        allow_private=False,
        max_bytes=1000,
        max_redirects=0,
        user_agent="test",
        resolver=resolver,
    )


async def test_connections_are_pinned_to_the_vetted_address() -> None:
    """DNS rebinding: the socket goes to the address the index checked, never a fresh lookup."""
    network = _RecordingNetwork()
    # A second lookup would rebind to an internal address.
    http, client = _pinned_client(network, {"fdp.example.org": [["93.184.216.34"], ["10.0.0.5"]]})
    async with http:
        response = await client.request(
            "GET", "https://fdp.example.org:8443/x?y=1", timeout_seconds=5
        )
    assert response.status == 200 and response.url == "https://fdp.example.org:8443/x?y=1"
    assert response.address == "93.184.216.34"
    assert network.connects == [("93.184.216.34", 8443)]
    # The certificate is checked against the name, not the address.
    assert network.tls_names == ["fdp.example.org"]


async def test_names_sharing_an_address_do_not_share_connections() -> None:
    """A kept-open connection serves only the name its certificate was checked for."""
    network = _RecordingNetwork()
    shared = [["93.184.216.34"]]
    http, client = _pinned_client(network, {"a.example.org": shared, "b.example.org": shared})
    async with http:
        for url in (
            "https://a.example.org/1",
            "https://a.example.org/2",  # same name: the connection is reused
            "https://b.example.org/1",  # same address, other name: a new connection
        ):
            assert (await client.request("GET", url, timeout_seconds=5)).status == 200
    assert network.connects == [("93.184.216.34", 443)] * 2
    assert network.tls_names == ["a.example.org", "b.example.org"]


async def test_the_pinned_transport_refuses_requests_it_has_not_checked() -> None:
    """Fails closed: on its own, without OutboundClient's check, nothing connects."""
    network = _RecordingNetwork()
    async with outbound_http(PinnedTransport(network)) as http:
        with pytest.raises(httpx.ConnectError, match="no checked address"):
            await http.get("https://fdp.example.org/")
    assert network.connects == []


@pytest.mark.parametrize("address", ["100.64.0.1", "100.100.100.200", "192.0.0.8"])
async def test_shared_address_space_is_blocked(address: str) -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return [address]

    async with httpx.AsyncClient() as http:
        client = OutboundClient(
            http,
            allow_private=False,
            max_bytes=10,
            max_redirects=0,
            user_agent="t",
            resolver=resolver,
        )
        with pytest.raises(OutboundError) as caught:
            await client.check_url("http://cloud.example/latest/meta-data")
    assert caught.value.kind == "blocked"


async def test_a_harvest_stops_at_its_byte_budget(network: FakeNetwork) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(network.handler)) as http:
        client = OutboundClient(
            http,
            allow_private=False,
            max_bytes=1_000_000,
            max_redirects=0,
            user_agent="t",
            resolver=public_resolver,
        )
        result = await harvest(client, FDP, timeout_seconds=5, max_nodes=100, max_bytes=10)
    assert result.limit_reached and len(result.nodes) == 1


def test_serialise_keeps_literals_with_line_separator_characters_whole() -> None:
    from rdflib import Graph, Literal, URIRef

    graph = Graph()
    text = "Osnovana baza sadr\u00c3\u0085i, line\u2028separator, form\x0cfeed"
    graph.add(
        (
            URIRef("https://fdp.example.org/d"),
            URIRef("http://purl.org/dc/terms/title"),
            Literal(text),
        )
    )
    stored = serialise(graph, "https://fdp.example.org/d")
    assert stored.ntriples.count("\n") == 1
    again = Graph()
    again.parse(data=stored.ntriples, format="nt")
    assert str(next(iter(again))[2]) == text
