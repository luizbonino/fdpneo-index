"""Field prefixes in the search box (TASKS 9.1): ``title:``, ``keyword:``, ``theme:`` …

``q`` may hold ``name:term`` or ``name:"several words"``, ``-name:term`` to
exclude. The name is ``title`` or ``description`` (the words must be in that
text), or any field of the profile whose values are IRIs, names or literals
(``keyword``, ``theme``, ``subject``, ``publisher``, ``format``, ``licence``,
``identifier`` …). Anything else, ``https://…`` included, stays a word.

A field prefix becomes a filter on the values it names, so facets, chips and
"Show as SPARQL" treat it like a ticked value:

* literal fields (keywords, identifiers): the value, ignoring case (a DOI in
  any spelling for identifiers);
* IRI fields (themes, subjects, formats): the IRI itself, its code (the last
  segment: ``theme:HEAL``), or a label in any language, harvested, resolved or
  bundled (``theme:Gezondheid``);
* agents (publishers): a name or label that contains the term
  (``publisher:alpha`` finds "Alpha University").

At most :data:`MAX_VALUES` values, the most used first. A term that names no
value matches nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select

from fdpindex.models import ConceptLabel, SearchLabel, SearchValue
from fdpindex.search.dedup import doi
from fdpindex.search.project import canonical_iri
from fdpindex.search.vocab import BUNDLED, bundled_labels

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from fdpindex.search.profile import Profile

TEXT_FIELDS = {"title": "a", "description": "c"}  # their weights in the text vectors
MAX_VALUES = 50
NOTHING = "\x00nothing"  # a filter value no resource has

_PREFIX = re.compile(r'(?:(?<=\s)|^)(-?)([A-Za-z][A-Za-z0-9]*):(?:"([^"]*)"|(\S+))')


@dataclass(frozen=True)
class Prefix:
    field: str
    term: str
    negated: bool = False


def value_fields(profile: Profile) -> dict[str, str]:
    """Lower-cased name → field, for the fields a prefix may name besides the text."""
    return {
        name.lower(): name
        for name, spec in profile.fields.items()
        if spec.kind in {"iri", "agent", "literal"}
    }


def parse(q: str, profile: Profile) -> tuple[str, list[Prefix]]:
    """Take the field prefixes out of ``q``: the rest of ``q``, and the prefixes."""
    known = {**value_fields(profile), **{n: n for n in TEXT_FIELDS}}
    found: list[Prefix] = []

    def take(match: re.Match[str]) -> str:
        name = known.get(match.group(2).lower())
        term = (match.group(3) if match.group(3) is not None else match.group(4) or "").strip()
        if name is None or not term:
            return match.group(0)
        found.append(Prefix(name, term[:256], match.group(1) == "-"))
        return " "

    rest = _PREFIX.sub(take, q)
    return " ".join(rest.split()), found


async def resolve(session: AsyncSession, profile: Profile, prefix: Prefix) -> list[str]:
    """The stored values a prefix names, the most used first."""
    spec = profile.fields[prefix.field]
    term = prefix.term.lower()
    V = SearchValue  # noqa: N806 (a table alias, as in the query module)
    lowered = func.lower(V.value)
    conditions = [lowered == term]
    if spec.kind == "literal" and (found := doi(prefix.term)):
        number = found.removeprefix("doi:")
        conditions.append(
            lowered.in_([number, found, f"https://doi.org/{number}", f"http://dx.doi.org/{number}"])
        )
    if spec.kind in {"iri", "agent"}:
        if term.startswith(("http://", "https://", "urn:")):
            conditions.append(V.value == canonical_iri(prefix.term))
        else:
            # A code: the last segment of the IRI.
            conditions += [
                lowered.like(f"%/{_like(term)}", escape="\\"),
                lowered.like(f"%#{_like(term)}", escape="\\"),
            ]
            named = await _named(session, term, contains=spec.kind == "agent")
            if named:
                conditions.append(V.value.in_(named[:2000]))
            if spec.kind == "agent":
                conditions.append(lowered.like(f"%{_like(term)}%", escape="\\"))
    count = func.count().label("n")
    rows = (
        await session.execute(
            select(V.value, count)
            .where(V.field == prefix.field, or_(*conditions))
            .group_by(V.value)
            .order_by(count.desc(), V.value)
            .limit(MAX_VALUES)
        )
    ).all()
    return [str(value) for value, _ in rows]


async def _named(session: AsyncSession, term: str, *, contains: bool) -> list[str]:
    """IRIs with a label equal to ``term`` (or containing it): harvested, resolved, bundled."""
    pattern = f"%{_like(term)}%" if contains else _like(term)
    iris: dict[str, None] = {}
    for table in (SearchLabel, ConceptLabel):
        rows = await session.scalars(
            select(table.iri).where(func.lower(table.label).like(pattern, escape="\\")).limit(1000)
        )
        iris.update(dict.fromkeys(str(iri) for iri in rows))
    for iri in BUNDLED:
        labels = (label.lower() for label in bundled_labels(iri).values())
        if any((term in label) if contains else (label == term) for label in labels):
            iris[iri] = None
    return list(iris)


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
