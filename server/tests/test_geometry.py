"""Spatial extent as a bounding box: WKT and GeoJSON (TASKS 9.8)."""

from __future__ import annotations

import pytest

from fdpindex.search.geometry import merge, parse, parse_query

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "box"),
    [
        ("POINT(4.89 52.37)", (4.89, 52.37, 4.89, 52.37)),
        ("point (4.89 52.37)", (4.89, 52.37, 4.89, 52.37)),
        ("POINT Z (4.89 52.37 3)", (4.89, 52.37, 4.89, 52.37)),
        (
            "POLYGON((3.31 50.75, 7.23 50.75, 7.23 53.68, 3.31 53.68, 3.31 50.75))",
            (3.31, 50.75, 7.23, 53.68),
        ),
        (
            "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 0)), ((10 10, 11 10, 11 12, 10 10)))",
            (0.0, 0.0, 11.0, 12.0),
        ),
        ("LINESTRING(-1 -2, 3 4)", (-1.0, -2.0, 3.0, 4.0)),
        ("ENVELOPE(3.31, 7.23, 53.68, 50.75)", (3.31, 50.75, 7.23, 53.68)),
        (
            "<http://www.opengis.net/def/crs/OGC/1.3/CRS84> POINT(4.89 52.37)",
            (4.89, 52.37, 4.89, 52.37),
        ),
        # EPSG:4326 puts latitude first.
        (
            "<http://www.opengis.net/def/crs/EPSG/0/4326> POINT(52.37 4.89)",
            (4.89, 52.37, 4.89, 52.37),
        ),
        ('{"type": "Point", "coordinates": [4.89, 52.37]}', (4.89, 52.37, 4.89, 52.37)),
        (
            '{"type": "Feature", "geometry": {"type": "Polygon", '
            '"coordinates": [[[0, 0], [2, 0], [2, 3], [0, 0]]]}}',
            (0.0, 0.0, 2.0, 3.0),
        ),
        ('{"type": "Polygon", "bbox": [1, 2, 3, 4], "coordinates": []}', (1.0, 2.0, 3.0, 4.0)),
        ("POINT(200 10)", None),
        ("Amsterdam", None),
        ("{not json", None),
    ],
)
def test_geometries(text: str, box: tuple[float, float, float, float] | None) -> None:
    assert parse(text) == box


def test_query_boxes_and_merging() -> None:
    assert parse_query("3.3, 50.7, 7.3, 53.6") == (3.3, 50.7, 7.3, 53.6)
    for bad in ("1,2,3", "a,b,c,d", "5,0,1,1", "0,0,200,1"):
        assert parse_query(bad) is None
    assert merge([(0, 0, 1, 1), (5, -2, 6, 0.5)]) == (0, -2, 6, 1)
    assert merge([]) is None
