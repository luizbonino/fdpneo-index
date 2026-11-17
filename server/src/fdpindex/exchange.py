"""Recorded HTTP exchanges, in the reference's ``Exchange`` JSON shape.

Events carry the request and response they were about: ``direction``,
``state`` (``Prepared``, ``Requested``, ``Timeout``, ``Failed``,
``Retrieved``), ``remoteAddr``, ``error``, ``request`` (``method``, ``url``,
``headers``, ``body``) and ``response`` (``code``, ``url``, ``origin``,
``headers``, ``body``). Bodies are cut at a configured length.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fdpindex.outbound import OutboundError, OutboundResponse


def _cut(body: str | None, limit: int) -> str | None:
    if body is None or len(body) <= limit:
        return body
    return body[:limit]


def new_exchange(direction: str, remote_addr: str | None = None) -> dict[str, Any]:
    return {
        "direction": direction,
        "state": "Prepared",
        "remoteAddr": remote_addr,
        "error": None,
        "request": {"method": None, "url": None, "headers": None, "body": None},
        "response": {"code": None, "url": None, "origin": None, "headers": None, "body": None},
    }


def incoming(
    *,
    method: str,
    path: str,
    headers: dict[str, list[str]],
    body: str | None,
    remote_addr: str | None,
    limit: int,
) -> dict[str, Any]:
    exchange = new_exchange("INCOMING", remote_addr)
    exchange["request"] = {
        "method": method,
        "url": path,
        "headers": headers,
        "body": _cut(body, limit),
    }
    return exchange


def outgoing(
    *,
    method: str,
    url: str,
    request_body: str | None,
    response: OutboundResponse | None,
    error: OutboundError | None,
    limit: int,
) -> dict[str, Any]:
    exchange = new_exchange("OUTGOING")
    exchange["request"] = {
        "method": method,
        "url": url,
        "headers": response.request_headers if response is not None else None,
        "body": _cut(request_body, limit),
    }
    if response is not None:
        exchange["state"] = "Retrieved"
        exchange["response"] = {
            "code": response.status,
            "url": response.url,
            "origin": None,
            "headers": response.headers,
            "body": _cut(response.body, limit),
        }
    elif error is not None:
        exchange["state"] = "Timeout" if error.kind == "timeout" else "Failed"
        exchange["error"] = error.message
    return exchange
