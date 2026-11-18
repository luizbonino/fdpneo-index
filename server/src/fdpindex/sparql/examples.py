"""The SPARQL query library: parameterised example queries (search design §2.2.4).

Each example is a query template with typed parameters. Parameters are
named ``?_name`` in the template. A run binds them by injecting a ``VALUES``
block at the template's ``# @parameters`` marker (or, without a marker, at
the start of the outermost ``WHERE`` group), never by pasting text into the
query: IRIs are validated, strings escaped, dates and numbers typed. The
bindings reach ``FILTER`` and ``EXISTS`` of that outermost group, but not
filters inside nested groups (SPARQL evaluates those first), so templates
keep parameter filters at the top level. A parameter left
empty stays an unbound variable, so it matches anything; templates test it
with ``BOUND(?_name)`` where that matters. The expanded query is a plain
SPARQL query that also runs in any other endpoint.

The bundled library is ``examples.default.yaml``; administrators can replace
it (stored in the index settings table under its own row).
"""

from __future__ import annotations

import re
from datetime import date
from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fdpindex.sparql.gate import QueryRejected, iri_term, string_term

ParamType = Literal["iri", "text", "date", "integer", "choice", "fdp"]
XSD = "http://www.w3.org/2001/XMLSchema#"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Choice(_Model):
    value: str
    label: str
    kind: Literal["iri", "text"] = "iri"


class Suggest(_Model):
    """Where the UI gets suggestions: objects of a predicate, or instances of a class."""

    predicate: str | None = None
    type: str | None = None


class Parameter(_Model):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    type: ParamType
    label: str
    help: str | None = None
    required: bool = False
    default: str | None = None
    placeholder: str | None = None
    choices: list[Choice] = Field(default_factory=lambda: [])
    suggest: Suggest | None = None


class Chart(_Model):
    type: Literal["none", "bar", "barh", "pie", "line"] = "none"
    label: str | None = None  # column with the category
    value: str | None = None  # column with the number


class Example(_Model):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    group: str
    title: str
    description: str
    parameters: list[Parameter] = Field(default_factory=lambda: [])
    query: str
    chart: Chart = Field(default_factory=Chart)


class Library(_Model):
    groups: list[str]
    examples: list[Example]


@cache
def default_library() -> Library:
    text = resources.files("fdpindex.sparql").joinpath("examples.default.yaml").read_text("utf-8")
    return Library.model_validate(yaml.safe_load(text))


def term_for(parameter: Parameter, raw: str) -> str:
    value = raw.strip()
    match parameter.type:
        case "iri" | "fdp":
            return iri_term(value)
        case "text":
            return string_term(value)
        case "date":
            try:
                parsed = date.fromisoformat(value[:10])
            except ValueError as err:
                raise QueryRejected(f"{parameter.label}: not a date (YYYY-MM-DD).") from err
            return f'"{parsed.isoformat()}"^^<{XSD}date>'
        case "integer":
            if not re.fullmatch(r"-?\d{1,12}", value):
                raise QueryRejected(f"{parameter.label}: not a whole number.")
            return value
        case "choice":
            for choice in parameter.choices:
                if choice.value == value:
                    return iri_term(value) if choice.kind == "iri" else string_term(value)
            raise QueryRejected(f"{parameter.label}: not one of the offered choices.")


_MARKER = re.compile(r"^[ \t]*#[ \t]*@parameters[ \t]*$", re.MULTILINE)
_WHERE = re.compile(r"\bWHERE\s*\{", re.IGNORECASE)


def expand(example: Example, values: dict[str, str]) -> str:
    """The template with its parameters bound; raises ``QueryRejected`` on bad input."""
    known = {p.name for p in example.parameters}
    unknown = set(values) - known
    if unknown:
        raise QueryRejected(f"Unknown parameter: {sorted(unknown)[0]}")
    lines: list[str] = []
    for parameter in example.parameters:
        raw = (values.get(parameter.name) or parameter.default or "").strip()
        if not raw:
            if parameter.required:
                raise QueryRejected(f"{parameter.label} is required.")
            continue
        lines.append(f"  VALUES ?_{parameter.name} {{ {term_for(parameter, raw)} }}")
    block = "\n".join(lines)
    query = example.query.strip()
    if _MARKER.search(query):
        return _MARKER.sub(lambda _: block, query, count=1) if block else _MARKER.sub("", query, 1)
    if not block:
        return query
    where = _WHERE.search(query)
    if where is None:
        raise QueryRejected("This example cannot take parameters (no WHERE clause).")
    return f"{query[: where.end()]}\n{block}\n{query[where.end() :]}"
