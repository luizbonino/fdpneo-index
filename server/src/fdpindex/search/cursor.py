"""Keyset paging with ``cursor`` (TASKS 9.4): every hit of a search, page after page.

Offset paging stops at 10 000 results. A harvester that wants all of them asks
with ``cursor=*`` and then passes each response's ``nextCursor`` until it is
``null``. A cursor holds the sort, the last hit's sort key and its id; it is
opaque to callers (URL-safe base64 of JSON) and only good for the same sort.

The order is strict: the sort key, then the id. Missing dates are a sentinel
that sorts after every real date, so resources without one come last, as with
offset paging. Relevance has no stable key and cannot be paged this way.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, literal, tuple_
from sqlalchemy.sql.elements import ColumnElement

from fdpindex.db import AwareDateTime
from fdpindex.errors import BadRequest
from fdpindex.models import SearchResource as R

START = "*"
SORTS = ("modified", "issued", "title", "harvested")
_EARLIEST = datetime(1, 1, 2, tzinfo=UTC)
_LATEST = datetime(9999, 12, 30, tzinfo=UTC)


def direction(sort: str) -> tuple[str, bool]:
    """The sort key and whether it descends; the default is ``modified`` descending."""
    key, _, way = sort.partition(",")
    key = key or "modified"
    if key not in SORTS:
        raise BadRequest(f"cursor paging needs sort={'|'.join(SORTS)}, not {key}")
    return key, (way or ("asc" if key == "title" else "desc")) == "desc"


def key_of(sort: str) -> ColumnElement[Any]:
    """The sort key as one never-null expression."""
    key, descending = direction(sort)
    if key == "title":
        return func.lower(R.title)
    if key == "harvested":
        return R.harvested_at  # pyright: ignore[reportReturnType]
    missing = literal(_EARLIEST if descending else _LATEST, AwareDateTime())
    if key == "modified":
        return func.coalesce(R.modified, R.issued, missing)
    return func.coalesce(R.issued, missing)


def order(sort: str) -> list[Any]:
    key = key_of(sort)
    _, descending = direction(sort)
    return [key.desc(), R.id.desc()] if descending else [key.asc(), R.id.asc()]


def after(sort: str, token: str) -> ColumnElement[bool] | None:
    """The keyset condition for a cursor; None for the first page."""
    if token == START:
        return None
    state = decode(token)
    if state.get("s") != (sort or "modified"):
        raise BadRequest("This cursor belongs to another sort; start again with cursor=*")
    key, descending = direction(sort)
    value: Any = state.get("k")
    try:
        bound = (
            literal(str(value))
            if key == "title"
            else literal(datetime.fromisoformat(str(value)), AwareDateTime())
        )
        last = int(state["i"])
    except (KeyError, TypeError, ValueError) as err:
        raise BadRequest("Not a valid cursor") from err
    pair = tuple_(key_of(sort), R.id)
    return (
        pair < tuple_(bound, literal(last)) if descending else pair > tuple_(bound, literal(last))
    )


def encode(sort: str, value: Any, resource_id: int) -> str:
    key = value.isoformat() if isinstance(value, datetime) else str(value)
    raw = json.dumps({"s": sort or "modified", "k": key, "i": resource_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(token: str) -> dict[str, Any]:
    try:
        padded = token + "=" * (-len(token) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError) as err:
        raise BadRequest("Not a valid cursor") from err
    if not isinstance(state, dict):
        raise BadRequest("Not a valid cursor")
    return state  # pyright: ignore[reportUnknownVariableType]
