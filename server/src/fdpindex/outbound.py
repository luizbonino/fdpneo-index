"""The one HTTP client for every outbound request (ADR-0002).

Metadata retrieval, harvesting and webhook delivery all go through
:class:`OutboundClient`, which:

* accepts only ``http`` and ``https`` URLs;
* resolves the host and refuses loopback, private, link-local, multicast,
  reserved and unspecified addresses, unless private targets are allowed;
* follows redirects itself, re-checking every hop, up to a limit;
* stops reading a response body after a size limit.

The connection is pinned to the address that was checked, so a host that
changes its DNS answer between check and connection (DNS rebinding) cannot
redirect it to an internal address. The pinning happens where the socket is
opened (:class:`PinnedTransport`): the URL keeps the host name, so ``Host``,
the TLS server name and the certificate check all use the name, and the
connection pool keeps connections per name. An earlier version put the
address in the URL instead; the pool, keyed on the URL's host, then let a
connection opened for one name carry requests for another name at the same
address, whose certificate was never checked (security audit 2026-11-23,
follow-up to F-01).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping

_REDIRECTS = frozenset({301, 302, 303, 307, 308})


class OutboundError(Exception):
    """A request that did not produce a response.

    ``kind`` is ``blocked`` (refused by policy), ``timeout`` or ``failed``.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class OutboundResponse:
    status: int
    url: str
    headers: dict[str, list[str]]
    body: str
    truncated: bool = False
    requested_url: str = ""
    request_headers: dict[str, list[str]] = field(default_factory=lambda: {})
    # The address that answered the last hop (after redirects), when the index connected
    # to a checked public address; None with private targets allowed (local stacks).
    address: str | None = None

    @property
    def content_type(self) -> str:
        values = self.headers.get("content-type") or [""]
        return values[0].split(";", 1)[0].strip().lower()


# Where a request may connect, by host name: set by OutboundClient for the
# duration of each request, read by the network backend when it opens a socket.
_PINS: ContextVar[dict[str, str] | None] = ContextVar("fdpindex_outbound_pins", default=None)


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Opens each connection to the address its request was checked against.

    Fails closed: a connection for a host the current request has not checked
    (no pin) is refused rather than resolved again.
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        # httpcore exports a stand-in AnyIOBackend when anyio is missing, which pyright
        # sees too; anyio is installed with httpx, so this is the real backend.
        self._inner = inner or cast("httpcore.AsyncNetworkBackend", httpcore.AnyIOBackend())

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109  # httpcore's signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = (_PINS.get() or {}).get(host)
        if address is None:
            raise httpcore.ConnectError(f"no checked address for {host}")
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx's transport, with each connection opened to the checked address.

    Only :class:`OutboundClient` sets the addresses, so a client on this
    transport is of no use to anything else: its requests fail to connect.
    ``inner`` is the network backend underneath (tests pass a mock one).
    """

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        super().__init__()
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_PinnedBackend(inner),
        )


def outbound_http(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The HTTP client for :class:`OutboundClient`: pinned connections, no redirects.

    With an explicit transport, httpx also ignores ``HTTP(S)_PROXY``: a proxy
    would resolve the name itself, after the check.
    """
    return httpx.AsyncClient(transport=transport or PinnedTransport(), follow_redirects=False)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


# Not "private" by Python's definition, but never a public FAIR Data Point:
# shared address space (carrier-grade NAT, overlays, Alibaba Cloud's metadata
# service at 100.100.100.200) and the IETF protocol assignments.
_EXTRA_BLOCKED = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
)


def _blocked_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or any(ip in network for network in _EXTRA_BLOCKED)
    )


def _literal(url: str) -> str | None:
    """The host when it is an IP address literal (no DNS lookup, no pinning)."""
    host = urlsplit(url).hostname or ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


def _headers(raw: httpx.Headers) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, value in raw.multi_items():
        result.setdefault(key.lower(), []).append(value)
    return result


class OutboundClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        allow_private: bool,
        max_bytes: int,
        max_redirects: int,
        user_agent: str,
        resolver: Callable[[str, int], Awaitable[list[str]]] = system_resolver,
    ) -> None:
        self._http = http
        self._allow_private = allow_private
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent
        self._resolver = resolver

    async def check_url(self, url: str) -> str | None:
        """Raise :class:`OutboundError` (``blocked``) when ``url`` may not be requested.

        Returns the vetted address to connect to, or ``None`` when no pinning is
        needed (private targets allowed, or the host is already an IP literal).
        """
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise OutboundError("blocked", f"unsupported URL scheme: {parts.scheme or '(none)'}")
        host = parts.hostname
        if not host:
            raise OutboundError("blocked", "URL has no host")
        if self._allow_private:
            return None
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as err:
            raise OutboundError("blocked", f"invalid port: {err}") from err
        literal = False
        try:
            ipaddress.ip_address(host)
            addresses = [host]
            literal = True
        except ValueError:
            try:
                addresses = await self._resolver(host, port)
            except OSError as err:
                raise OutboundError("failed", f"cannot resolve {host}: {err}") from err
        if not addresses:
            raise OutboundError("failed", f"cannot resolve {host}")
        for address in addresses:
            if _blocked_address(address):
                raise OutboundError(
                    "blocked", f"{host} resolves to a non-public address ({address})"
                )
        return None if literal else addresses[0]

    async def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float,
        headers: Mapping[str, str] | None = None,
        content: str | None = None,
        allowed_hosts: Collection[str] | None = None,
    ) -> OutboundResponse:
        """Send a request, following redirects; each hop is checked again.

        With ``allowed_hosts``, every hop's host must be one of them (label
        resolution: only allow-listed vocabulary hosts are dereferenced).
        """
        request_headers = {"User-Agent": self._user_agent, **(headers or {})}
        current, current_method, body = url, method, content
        for _hop in range(self._max_redirects + 1):
            if allowed_hosts is not None:
                host = (urlsplit(current).hostname or "").lower()
                if host not in allowed_hosts:
                    raise OutboundError("denied", f"Host not allowed: {host}")
            pinned = await self.check_url(current)
            try:
                async with asyncio.timeout(timeout_seconds):
                    response = await self._send(
                        current_method, current, request_headers, body, pinned
                    )
            except TimeoutError as err:
                raise OutboundError("timeout", "Timeout") from err
            except httpx.TimeoutException as err:
                raise OutboundError("timeout", "Timeout") from err
            except httpx.HTTPError as err:
                raise OutboundError("failed", f"IO error: {err}") from err
            locations = response.headers.get("location", [])
            if response.status not in _REDIRECTS or not locations:
                response.address = pinned or _literal(current)
                response.requested_url = url
                response.request_headers = {k.lower(): [v] for k, v in request_headers.items()}
                return response
            current = urljoin(current, locations[0])
            if response.status == 303:
                current_method, body = "GET", None
        raise OutboundError("failed", f"more than {self._max_redirects} redirects")

    async def _send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: str | None,
        pinned: str | None,
    ) -> OutboundResponse:
        target = httpx.URL(url)
        host = target.raw_host.decode("ascii")
        # The socket goes to the checked address; the URL, Host, TLS name and the
        # pool all keep the name. Without a checked address (an IP literal, or
        # private targets allowed) the host itself is the pin.
        token = _PINS.set({host: pinned or host})
        try:
            return await self._receive(method, target, headers, content, url)
        finally:
            _PINS.reset(token)

    async def _receive(
        self,
        method: str,
        target: httpx.URL,
        headers: Mapping[str, str],
        content: str | None,
        url: str,
    ) -> OutboundResponse:
        async with self._http.stream(
            method, target, headers=dict(headers), content=content
        ) as resp:
            chunks: list[bytes] = []
            size = 0
            truncated = False
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > self._max_bytes:
                    chunks.append(chunk[: len(chunk) - (size - self._max_bytes)])
                    truncated = True
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            encoding = resp.charset_encoding or "utf-8"
            return OutboundResponse(
                status=resp.status_code,
                url=url,
                headers=_headers(resp.headers),
                body=raw.decode(encoding, errors="replace"),
                truncated=truncated,
            )
