"""A faceted search as SPARQL: the "Show as SPARQL" bridge (search design §2.2.2).

``to_sparql`` turns the parameters of ``GET /index/search`` into a query over
the SPARQL store, where each FDP is a named graph and the harvested triples are
as published. The search tables hold normalised values, so every filter value
is matched against all the raw forms that normalise to it (``"csv"``, the EU
file-type IRI, ``text/csv`` …), case-insensitively, through ``STR``. User input
only ever becomes an escaped string literal, never an IRI or a variable.

What SPARQL cannot express the same way is approximated, listed in the
response (``approximation``) and explained in comments at the top of the query:

* ``fulltext``: words match as substrings of titles, descriptions and
  keywords, without stemming, accent folding or prefix rules;
* ``ranking``: results are ordered by title instead of relevance;
* ``dates``: date ranges compare the first ten characters (the day);
* ``harvested``: the harvest time is not in the store; the range is dropped;
* ``type``: resources without a recognised type ("other") cannot be selected.

Kinds follow the class a resource declares; the index resolves a resource with
several classes by precedence, SPARQL matches each of them.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fdpindex.search.project import canonical_iri
from fdpindex.sparql.gate import is_safe_iri, string_term
from fdpindex.sparql.prefixes import PREFIXES

if TYPE_CHECKING:
    from uuid import UUID

    from fdpindex.search.profile import Kind, Profile, SearchField
    from fdpindex.search.query import SearchParams

LIMIT = 1000
TEXT_FIELDS = ("title", "description", "keyword")
NAME_PATH = "foaf:name|schema:name|vcard:fn"
DATE_FIELDS = {"issued": "issued", "modified": "modified"}
NOTES = {
    "fulltext": (
        "Words match as plain substrings of titles, descriptions and keywords: "
        "no stemming, accent folding or partial-word rules."
    ),
    "ranking": "Results are ordered by title; the search page orders them by relevance.",
    "dates": "Date ranges compare the day only, ignoring time zones.",
    "harvested": "The harvest time is not in the SPARQL store; that range is left out.",
    "type": 'Resources without a recognised type ("other") cannot be selected in SPARQL.',
}
_LOCAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_string = string_term


def _strings(values: Iterable[str]) -> str:
    return ", ".join(_string(v) for v in sorted(set(values)))


@dataclass
class _Writer:
    profile: Profile
    prefixes: dict[str, str] = field(default_factory=lambda: {})
    used: dict[str, str] = field(default_factory=lambda: {})
    approximations: list[str] = field(default_factory=lambda: [])

    def __post_init__(self) -> None:
        self.prefixes = {**self.profile.prefixes, **PREFIXES}

    def note(self, code: str) -> None:
        if code not in self.approximations:
            self.approximations.append(code)

    def iri(self, iri: str) -> str:
        """A prefixed name when a known namespace fits, else ``<iri>``."""
        best = ""
        for prefix, namespace in self.prefixes.items():
            fits = iri.startswith(namespace) and _LOCAL.match(iri[len(namespace) :])
            if fits and (not best or len(namespace) > len(self.prefixes[best])):
                best = prefix
        if best:
            self.used[best] = self.prefixes[best]
            return f"{best}:{iri[len(self.prefixes[best]) :]}"
        return f"<{iri}>"

    def path(self, spec: SearchField, *, rollup: bool = False) -> str:
        """The field's paths as one SPARQL property path (alternatives with ``|``)."""
        alternatives = [
            "/".join(self.iri(i) for i in self.profile.path_iris(p)) for p in spec.paths
        ]
        if rollup:
            # Values of the distributions count for the dataset, as in the index.
            alternatives += [
                f"{self.iri('http://www.w3.org/ns/dcat#distribution')}/{a}" for a in alternatives
            ] + [f"^{self.iri('http://purl.org/dc/terms/isPartOf')}/{a}" for a in alternatives]
        return alternatives[0] if len(alternatives) == 1 else f"({'|'.join(alternatives)})"

    def name_path(self) -> str:
        return "|".join(self.iri(self.profile.expand(c)) for c in NAME_PATH.split("|"))


def _property_field(iri: str) -> SearchField:
    """A captured property as a field: its IRI values and literals, one step from the resource."""
    from fdpindex.search.profile import SearchField

    return SearchField(kind="iri", paths=[f"<{iri}>"])


def raw_forms(value: str, table: Mapping[str, str]) -> set[str]:
    """Every lower-cased form of a stored value that ``normalise`` folds into it."""
    forms = {value.lower()}
    if value.startswith("http://") and canonical_iri("https://" + value[7:]) == value:
        forms.add(("https://" + value[7:]).lower())
    for key, replacement in table.items():
        if replacement == "" and key.endswith("/"):
            forms.add((key + value).lower())
        elif replacement == value:
            forms.add(key.lower())
    # The index also folds a value written without its trailing slash.
    return forms | {f.rstrip("/") for f in forms}


def _bucket_condition(var: str, key: str) -> str:
    low, _, high = key.partition("..")
    number = f"xsd:double(STR({var}))"
    parts = []
    if low:
        parts.append(f"{number} >= {float(low)!r}")
    if high:
        parts.append(f"{number} < {float(high)!r}")
    return " && ".join(parts) or "true"


def _value_condition(writer: _Writer, spec: SearchField, values: list[str], var: str) -> str:
    if spec.kind == "number":
        writer.used.setdefault("xsd", PREFIXES["xsd"])
        return " || ".join(f"({_bucket_condition(var, v)})" for v in values)
    if spec.kind == "boolean":
        if spec.presence:
            return "true" if "true" in values else "false"
        accepted = {"true", "1"} if "true" in values else set()
        accepted |= {"false", "0"} if "false" in values else set()
        return f"LCASE(STR({var})) IN ({_strings(accepted)})"
    # The index holds no value outside the field's namespaces.
    values = [v for v in values if writer.profile.accepts(spec, v)]
    if not values:
        return "false"
    table = writer.profile.normalise.get(spec.normalise or "", {})
    forms = set().union(*(raw_forms(v, table) for v in values))
    condition = f"LCASE(STR({var})) IN ({_strings(forms)})"
    if spec.kind == "agent":
        # A publisher is an IRI, or a blank node whose name is the stored value.
        names = _strings(v.lower() for v in values)
        condition = (
            f"{condition} || EXISTS {{ {var} {writer.name_path()} {var}Name "
            f"FILTER(LCASE(STR({var}Name)) IN ({names})) }}"
        )
    return condition


def _exists(pattern: str, condition: str, *, negated: bool = False, inline: bool = False) -> str:
    """``FILTER [NOT] EXISTS { pattern FILTER(condition) }``, or the bare ``[!]EXISTS`` form."""
    if inline:
        return f"{'!' if negated else ''}EXISTS {{ {pattern} FILTER({condition}) }}"
    keyword = "FILTER NOT EXISTS" if negated else "FILTER EXISTS"
    return f"{keyword} {{\n  {pattern} .\n  FILTER({condition})\n}}"


def _kinds(w: _Writer, profile: Profile, wanted: set[Kind]) -> list[str]:
    """Patterns selecting resources whose kind (first match in the profile's order) is wanted.

    A resource with a class of an unwanted kind is left out unless it also has a
    class of a wanted kind that comes earlier: an FDP that is also a
    ``dcat:DataService`` is an FDP, not a service.
    """
    order: list[Kind] = list(profile.types)

    def classes(kinds: Iterable[Kind]) -> list[str]:
        return list(
            dict.fromkeys(w.iri(profile.expand(c)) for k in kinds for c in profile.types[k])
        )

    blocks = [
        f"VALUES ?class {{ {' '.join(classes(k for k in order if k in wanted))} }}",
        "?resource a ?class .",
    ]
    # Unwanted kinds before the last wanted one, grouped by the wanted kinds that precede them.
    last = max((order.index(k) for k in wanted if k in order), default=-1)
    groups: dict[tuple[Kind, ...], list[Kind]] = {}
    for position, kind in enumerate(order[:last]):
        if kind not in wanted:
            preceding: tuple[Kind, ...] = tuple(k for k in order[:position] if k in wanted)
            groups.setdefault(preceding, []).append(kind)
    for earlier, unwanted in groups.items():
        inner = f"VALUES ?other {{ {' '.join(classes(unwanted))} }}\n  ?resource a ?other ."
        if earlier:
            inner += (
                f"\n  FILTER NOT EXISTS {{ VALUES ?first {{ {' '.join(classes(earlier))} }} "
                "?resource a ?first }"
            )
        blocks.append(f"FILTER NOT EXISTS {{\n  {inner}\n}}")
    return blocks


def _terms(q: str) -> list[list[tuple[str, bool]]]:
    """websearch-style input: groups split by ``or``; each term ``(text, negated)``."""
    groups: list[list[tuple[str, bool]]] = [[]]
    for token in re.findall(r'-?"[^"]*"?|\S+', q):
        if token.lower() == "or":
            if groups[-1]:
                groups.append([])
            continue
        negated = token.startswith("-") and len(token) > 1
        text = token[1:] if negated else token
        text = text.strip('"').strip().lower()
        if text:
            groups[-1].append((text, negated))
    return [g for g in groups if g]


def to_sparql(
    params: SearchParams,
    profile: Profile,
    graphs: Mapping[UUID, str],
    *,
    default_types: Iterable[str],
    properties: Mapping[str, str] | None = None,
    equivalents: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    """``{"query", "prefixes", "approximation"}`` for validated search parameters.

    ``graphs`` maps the requested FDPs (``params.fdps``) to their graph names
    (client URLs); FDPs missing from it match nothing, as in the search.
    ``properties`` maps the ``p:`` fields of property facets to their IRIs.
    """
    properties = properties or {}
    w_equivalents = equivalents or {}
    w = _Writer(profile)
    graph: list[str] = []
    outer: list[str] = []

    # Types: the classes of the requested kinds, and the index's precedence.
    if "other" in params.types and list(params.types) != list(default_types):
        w.note("type")
    graph += _kinds(w, profile, set(params.types))

    # FDPs and their state.
    if params.fdps:
        names = [graphs[u] for u in params.fdps if u in graphs]
        if all(is_safe_iri(n) for n in names):
            outer.append(f"VALUES ?fdp {{ {' '.join(f'<{n}>' for n in names)} }}")
        else:
            outer.append(f"FILTER(STR(?fdp) IN ({_strings(names)}))")
    if params.fdp_state:
        entries = w.iri("urn:fdpindex:entries")
        state = w.iri("urn:fdpindex:state")
        outer.append(f'GRAPH {entries} {{ ?fdp {state} "ACTIVE" }}')

    # Named fields: values ORed within a field, fields ANDed, "!value" excludes.
    for index, (name, values) in enumerate(sorted(params.filters.items()), start=1):
        spec = profile.fields.get(name) or _property_field(properties[name])
        path = w.path(spec, rollup=spec.rollup)
        var = f"?{re.sub(r'[^A-Za-z0-9]', '', name)}{index}"
        wanted = [v for v in values if not v.startswith("!")]
        unwanted = [v[1:] for v in values if v.startswith("!")]
        pattern = f"?resource {path} {var}"
        if spec.equivalents:
            # The index stores a group of equivalent concepts as one; the store has each.
            wanted = [m for v in wanted for m in w_equivalents.get(v, [v])]
            unwanted = [m for v in unwanted for m in w_equivalents.get(v, [v])]
        if wanted:
            graph.append(_exists(pattern, _value_condition(w, spec, wanted, var)))
        if unwanted:
            condition = _value_condition(w, spec, unwanted, var)
            graph.append(_exists(pattern, condition, negated=True))

    # Date ranges, by day.
    for name, (low, high) in sorted(params.ranges.items()):
        if name not in DATE_FIELDS:
            w.note("harvested")
            continue
        path = w.path(profile.fields[DATE_FIELDS[name]])
        day = f"SUBSTR(STR(?{name}Range), 1, 10)"
        checks = []
        if low is not None:
            checks.append(f'{day} >= "{low.date().isoformat()}"')
        if high is not None:
            checks.append(f'{day} <= "{high.date().isoformat()}"')
        graph.append(_exists(f"?resource {path} ?{name}Range", " && ".join(checks)))
        w.note("dates")

    # Full text, approximated by substrings.
    if params.q:
        w.note("fulltext")
        text_path = "|".join(
            w.path(profile.fields[n]).strip("()") for n in TEXT_FIELDS if n in profile.fields
        )
        groups = _terms(params.q)
        counter = itertools.count(1)

        def term(text: str, negated: bool, *, inline: bool) -> str:
            var = f"?text{next(counter)}"
            condition = f"CONTAINS(LCASE(STR({var})), {_string(text)})"
            return _exists(
                f"?resource {text_path} {var}", condition, negated=negated, inline=inline
            )

        if len(groups) == 1:
            graph += [term(text, negated, inline=False) for text, negated in groups[0]]
        elif groups:
            alternatives = [
                "(" + " && ".join(term(t, n, inline=True) for t, n in group) + ")"
                for group in groups
            ]
            graph.append("FILTER(\n  " + "\n  || ".join(alternatives) + "\n)")

    # Columns and order.
    title_path = (
        w.path(profile.fields["title"])
        if "title" in profile.fields
        else w.iri("http://purl.org/dc/terms/title")
    )
    graph.append(f"OPTIONAL {{ ?resource {title_path} ?anyTitle }}")
    key, _, direction = params.sort.partition(",")
    key = key or ("relevance" if params.q else "modified")
    columns = ["?resource", "(SAMPLE(?anyTitle) AS ?title)", "(SAMPLE(?class) AS ?type)", "?fdp"]
    order = ["?title"]
    if key == "relevance":
        w.note("ranking")
    elif key == "harvested":
        w.note("harvested")
    elif key in DATE_FIELDS:
        graph.append(
            f"OPTIONAL {{ ?resource {w.path(profile.fields[DATE_FIELDS[key]])} ?any{key.title()} }}"
        )
        columns.append(f"(MAX(?any{key.title()}) AS ?{key})")
        order = [f"ASC(?{key})" if direction == "asc" else f"DESC(?{key})", "?title"]
    elif key == "title" and direction == "desc":
        order = ["DESC(?title)"]

    lines = [f"PREFIX {prefix}: <{namespace}>" for prefix, namespace in sorted(w.used.items())]
    approximations = [code for code in NOTES if code in w.approximations]
    header = ["# The search page's search, translated to SPARQL."]
    if approximations:
        header.append("# Where SPARQL cannot do the same, this query approximates it:")
        header += [f"#  - {NOTES[code]}" for code in approximations]
    body = [
        *header,
        *lines,
        "",
        f"SELECT {' '.join(columns)}",
        "WHERE {",
        *_indent(outer, "  "),
        "  GRAPH ?fdp {",
        *_indent(graph, "    "),
        "  }",
        "}",
        "GROUP BY ?resource ?fdp",
        f"ORDER BY {' '.join(order)}",
        f"LIMIT {LIMIT}",
    ]
    return {
        "query": "\n".join(body) + "\n",
        "prefixes": dict(sorted(w.used.items())),
        "approximation": approximations,
    }


def _indent(blocks: list[str], prefix: str) -> list[str]:
    return [prefix + line for block in blocks for line in block.split("\n")]
