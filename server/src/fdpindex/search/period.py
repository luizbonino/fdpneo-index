"""Temporal coverage (``dct:temporal``) as a start and an end day (TASKS 8.3).

FDPs write a period in several ways; all of these are read:

* DCAT 2 and later: a ``dct:PeriodOfTime`` with ``dcat:startDate`` / ``dcat:endDate``;
* DCAT 1 and DCAT-AP 1.x: the same with ``schema:startDate`` / ``schema:endDate``
  (``http`` and ``https`` schema.org alike);
* OWL-Time: ``time:hasBeginning`` / ``time:hasEnd``, each an instant with
  ``time:inXSDDate``, ``time:inXSDDateTimeStamp``, ``time:inXSDDateTime``,
  ``time:inXSDgYear`` or ``time:inXSDgYearMonth``;
* a literal: ``"2010/2020"``, ``"2010-03/2012-06-30"``, ``"2010 \u2013 2020"`` (en dash),
  ``"2010-2020"``, an open ``"2010/.."``, or one year, month or day (``"2015"``).

Every value is a year, a year and month, a date or a date-time. A start is
the first day of what it names and an end the last: ``2010/2020`` covers
1 January 2010 to 31 December 2020. A period with only a start is open-ended,
one with only an end has no known beginning. Several periods on one resource
are merged into the span from the earliest start to the latest end; a start
after its end is read as the two swapped.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from rdflib import Graph, Literal, URIRef
from rdflib.term import Node

DCAT = "http://www.w3.org/ns/dcat#"
TIME = "http://www.w3.org/2006/time#"
SCHEMAS = ("http://schema.org/", "https://schema.org/")

START_PREDICATES = (URIRef(DCAT + "startDate"), *(URIRef(s + "startDate") for s in SCHEMAS))
END_PREDICATES = (URIRef(DCAT + "endDate"), *(URIRef(s + "endDate") for s in SCHEMAS))
BEGINNING = URIRef(TIME + "hasBeginning")
END = URIRef(TIME + "hasEnd")
INSTANT_PREDICATES = tuple(
    URIRef(TIME + name)
    for name in (
        "inXSDDate",
        "inXSDDateTimeStamp",
        "inXSDDateTime",
        "inXSDgYear",
        "inXSDgYearMonth",
    )
)

Period = tuple[date | None, date | None]

_DAY = re.compile(
    r"(\d{4})(?:-(\d{2})(?:-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?"
)
_YEARS = re.compile("(\\d{4})\\s*[-\u2013\u2014]\\s*(\\d{4})")
_OPEN = {"", "..", "…"}


def parse_day(text: str, *, end: bool = False) -> date | None:
    """A year, year-month, date or date-time to a day: the first one, or the last for ``end``."""
    match = _DAY.fullmatch(text.strip())
    if not match:
        return None
    year, month, day = (int(g) if g else None for g in match.groups())
    assert year is not None
    if year < 1:
        return None
    try:
        if month is None:
            return date(year, 12, 31) if end else date(year, 1, 1)
        if day is None:
            last = calendar.monthrange(year, month)[1]
            return date(year, month, last if end else 1)
        return date(year, month, day)
    except ValueError:
        return None


def parse_period_text(text: str) -> Period:
    """A literal period: ``start/end`` (either may be open), ``YYYY-YYYY``, or one value."""
    text = text.strip()
    if "/" in text:
        start, _, finish = text.partition("/")
        return _ordered(
            None if start.strip() in _OPEN else parse_day(start),
            None if finish.strip() in _OPEN else parse_day(finish, end=True),
        )
    years = _YEARS.fullmatch(text)
    if years:
        return _ordered(parse_day(years.group(1)), parse_day(years.group(2), end=True))
    for dash in (" \u2013 ", " \u2014 ", " - ", "\u2013", "\u2014"):
        if dash in text:
            start, _, finish = text.partition(dash)
            return _ordered(parse_day(start), parse_day(finish, end=True))
    return parse_day(text), parse_day(text, end=True)


def period_of(graph: Graph, node: Node) -> Period:
    """The period a ``dct:temporal`` value names; ``(None, None)`` when it names none."""
    if isinstance(node, Literal):
        return parse_period_text(str(node))
    start = _first(graph, node, START_PREDICATES, end=False)
    finish = _first(graph, node, END_PREDICATES, end=True)
    if start is None:
        start = _instant(graph, node, BEGINNING, end=False)
    if finish is None:
        finish = _instant(graph, node, END, end=True)
    return _ordered(start, finish)


def merge(periods: list[Period]) -> Period:
    """The span of several periods: the earliest start and the latest end."""
    starts = [s for s, _ in periods if s is not None]
    ends = [e for _, e in periods if e is not None]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _ordered(start: date | None, finish: date | None) -> Period:
    if start is None or finish is None or start <= finish:
        return start, finish
    # Swapped bounds ("2020/2010"): a whole year stays whole, so the period is
    # 2010-01-01 to 2020-12-31 rather than 2010-12-31 to 2020-01-01.
    low = date(finish.year, 1, 1) if (finish.month, finish.day) == (12, 31) else finish
    high = date(start.year, 12, 31) if (start.month, start.day) == (1, 1) else start
    return low, high


def _first(graph: Graph, node: Node, predicates: tuple[URIRef, ...], *, end: bool) -> date | None:
    for predicate in predicates:
        for value in sorted(graph.objects(node, predicate), key=str):  # pyright: ignore[reportArgumentType]
            parsed = parse_day(str(value), end=end)
            if parsed is not None:
                return parsed
    return None


def _instant(graph: Graph, node: Node, link: URIRef, *, end: bool) -> date | None:
    for instant in sorted(graph.objects(node, link), key=str):  # pyright: ignore[reportArgumentType]
        if isinstance(instant, Literal):
            parsed = parse_day(str(instant), end=end)
        else:
            parsed = _first(graph, instant, INSTANT_PREDICATES, end=end)
        if parsed is not None:
            return parsed
    return None
