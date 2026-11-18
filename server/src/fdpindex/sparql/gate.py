"""Structural gate for the public SPARQL endpoint (search design §2.2.3).

Every query is parsed with rdflib before it reaches a worker. Only the query
forms are accepted (SELECT, ASK, CONSTRUCT, DESCRIBE); update forms, ``LOAD``
included, do not parse as queries and are rejected with them. ``SERVICE`` is
rejected anywhere in the query: it would make the index fetch an
attacker-chosen URL (ADR-0002). The same rules as FDPneo's
``shared/sparql_safety.py``; the workers also open the store read-only, so a
gap here cannot write.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from pyparsing import ParseException
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.processor import prepareQuery

from fdpindex.sparql.prefixes import with_prefixes

QueryForm = Literal["SELECT", "ASK", "CONSTRUCT", "DESCRIBE"]

_FORMS: dict[str, QueryForm] = {
    "SelectQuery": "SELECT",
    "AskQuery": "ASK",
    "ConstructQuery": "CONSTRUCT",
    "DescribeQuery": "DESCRIBE",
}
FORBIDDEN_IRI_CHARS = frozenset(' \t\n\r<>"{}|^`\\')
MAX_IRI_LENGTH = 2048


class QueryRejected(ValueError):
    """The query is not acceptable; the message is safe to show, with a position if known."""

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


@dataclass(frozen=True)
class CheckedQuery:
    text: str
    form: QueryForm
    has_limit: bool


def check_query(text: str, *, max_chars: int) -> CheckedQuery:
    body = text.strip()
    if not body:
        raise QueryRejected("The query is empty.")
    if len(body) > max_chars:
        raise QueryRejected(f"The query is longer than {max_chars} characters.")
    # Declare the known prefixes in the text: rdflib's initNs keeps one prefix per
    # namespace, so dct: and dcterms: could not both work that way.
    declared = with_prefixes(body)
    added = declared.count("\n") - body.count("\n")
    try:
        prepared = prepareQuery(declared)
    except ParseException as err:
        line = max(1, err.lineno - added)
        raise QueryRejected(_parse_message(err, line), line=line, column=err.col) from err
    except Exception as err:  # rdflib raises assorted errors for unknown prefixes etc.
        raise QueryRejected(f"Could not read the query: {err}") from err
    form = _FORMS.get(prepared.algebra.name)
    if form is None:
        raise QueryRejected("Only SELECT, ASK, CONSTRUCT and DESCRIBE queries are accepted.")
    for node in walk(prepared.algebra):
        if node.name == "ServiceGraphPattern":
            raise QueryRejected(
                "SERVICE is not supported: the index does not query other endpoints."
            )
    return CheckedQuery(text=body, form=form, has_limit=_has_limit(prepared.algebra))


def _parse_message(err: ParseException, line: int) -> str:
    found = err.line[err.col - 1 : err.col + 19].strip() if err.line else ""
    near = f" near “{found}”" if found else ""
    return f"Syntax error at line {line}, column {err.col}{near}."


def _has_limit(algebra: CompValue) -> bool:
    node: Any = algebra.get("p")
    while isinstance(node, CompValue):
        if node.name == "Slice" and node.get("length") is not None:
            return True
        if node.name in {"Project", "Distinct", "Reduced", "OrderBy", "ToMultiSet"}:
            node = node.get("p")
            continue
        return False
    return False


def walk(node: Any) -> Iterator[CompValue]:
    if isinstance(node, CompValue):
        yield node
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            yield from walk(value)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            yield from walk(item)
    elif isinstance(node, dict):
        for value in node.values():  # pyright: ignore[reportUnknownVariableType]
            yield from walk(value)


def is_safe_iri(iri: str) -> bool:
    if not iri or len(iri) > MAX_IRI_LENGTH:
        return False
    if any(c in FORBIDDEN_IRI_CHARS or ord(c) < 0x20 for c in iri):
        return False
    return re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", iri) is not None


def iri_term(iri: str) -> str:
    if not is_safe_iri(iri):
        raise QueryRejected(f"Not a valid IRI: {iri[:80]}")
    return f"<{iri}>"


def string_term(value: str) -> str:
    """A SPARQL string literal: JSON's escapes are a safe subset of SPARQL's."""
    return json.dumps(value, ensure_ascii=False)
