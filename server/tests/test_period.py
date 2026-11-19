"""Temporal coverage: days, literal periods and the RDF forms (TASKS 8.3)."""

from __future__ import annotations

from datetime import date

import pytest
from rdflib import Graph, URIRef

from fdpindex.search.period import merge, parse_day, parse_period_text, period_of

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "first", "last"),
    [
        ("2010", date(2010, 1, 1), date(2010, 12, 31)),
        ("2012-02", date(2012, 2, 1), date(2012, 2, 29)),
        ("2010-05-17", date(2010, 5, 17), date(2010, 5, 17)),
        ("2010-05-17T08:00:00Z", date(2010, 5, 17), date(2010, 5, 17)),
        ("2010-05-17T08:00:00+02:00", date(2010, 5, 17), date(2010, 5, 17)),
        (" 2010 ", date(2010, 1, 1), date(2010, 12, 31)),
        ("2010-13", None, None),
        ("2010-02-30", None, None),
        ("0000", None, None),
        ("soon", None, None),
        ("", None, None),
    ],
)
def test_days(text: str, first: date | None, last: date | None) -> None:
    assert parse_day(text) == first
    assert parse_day(text, end=True) == last


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("2010/2020", (date(2010, 1, 1), date(2020, 12, 31))),
        ("2010-03/2012-06-30", (date(2010, 3, 1), date(2012, 6, 30))),
        ("2010/..", (date(2010, 1, 1), None)),
        ("../2020", (None, date(2020, 12, 31))),
        ("2010/", (date(2010, 1, 1), None)),
        ("2010-2020", (date(2010, 1, 1), date(2020, 12, 31))),
        ("2010\u20132020", (date(2010, 1, 1), date(2020, 12, 31))),
        ("2010-01-01 \u2013 2011-06-30", (date(2010, 1, 1), date(2011, 6, 30))),
        ("2015", (date(2015, 1, 1), date(2015, 12, 31))),
        ("2015-06", (date(2015, 6, 1), date(2015, 6, 30))),
        # Swapped: whole years stay whole.
        ("2020/2010", (date(2010, 1, 1), date(2020, 12, 31))),
        ("the nineties", (None, None)),
    ],
)
def test_literal_periods(text: str, period: tuple[date | None, date | None]) -> None:
    assert parse_period_text(text) == period


TURTLE = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct: <http://purl.org/dc/terms/> .
@prefix schema: <https://schema.org/> .
@prefix time: <http://www.w3.org/2006/time#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<urn:r> dct:temporal <urn:dcat>, <urn:schema>, <urn:time>, <urn:none>, "2001/2002" .
<urn:dcat> dcat:startDate "2010-01-01"^^xsd:date ; dcat:endDate "2011"^^xsd:gYear .
<urn:schema> schema:startDate "2012-03"^^xsd:gYearMonth .
<urn:time> time:hasBeginning [ time:inXSDDateTimeStamp "2014-05-01T00:00:00Z"^^xsd:dateTimeStamp ] ;
  time:hasEnd [ time:inXSDDate "2016-02-29"^^xsd:date ] .
<urn:none> dct:title "a period without dates" .
"""


def test_rdf_forms_and_merging() -> None:
    graph = Graph().parse(data=TURTLE, format="turtle")
    periods = {
        str(node): period_of(graph, node)
        for node in graph.objects(URIRef("urn:r"), URIRef("http://purl.org/dc/terms/temporal"))
    }
    assert periods == {
        "urn:dcat": (date(2010, 1, 1), date(2011, 12, 31)),
        "urn:schema": (date(2012, 3, 1), None),
        "urn:time": (date(2014, 5, 1), date(2016, 2, 29)),
        "urn:none": (None, None),
        "2001/2002": (date(2001, 1, 1), date(2002, 12, 31)),
    }
    assert merge(list(periods.values())) == (date(2001, 1, 1), date(2016, 2, 29))
    assert merge([]) == (None, None)
