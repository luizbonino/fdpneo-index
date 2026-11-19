"""Spatial extent as a bounding box (TASKS 9.8): four numbers, no PostGIS.

``dct:spatial`` leads to a location, read in the forms DCAT and GeoDCAT use:

* ``dcat:bbox``, ``dcat:centroid``, ``locn:geometry`` on the location;
* a WKT or GeoJSON literal as the ``dct:spatial`` value itself.

WKT ``POINT``, ``LINESTRING``, ``POLYGON``, their ``MULTI`` forms and the CQL
``ENVELOPE(minX, maxX, maxY, minY)`` are read, with or without a leading CRS
IRI. Coordinates are longitude then latitude (CRS84, GeoSPARQL's default);
with an explicit EPSG:4326 CRS, whose axis order is latitude first, they are
swapped. GeoJSON geometries and features are read from their ``coordinates``
(or their ``bbox``). Several geometries are merged into the box around all of
them. A box outside -180..180 / -90..90 is dropped.

The box crossing the antimeridian is not handled: such an extent reads as
the box the other way round the globe.
"""

from __future__ import annotations

import json
import re
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.term import Node

DCAT = "http://www.w3.org/ns/dcat#"
LOCN = "http://www.w3.org/ns/locn#"
GEOMETRY_PREDICATES = tuple(
    URIRef(iri) for iri in (DCAT + "bbox", DCAT + "centroid", LOCN + "geometry")
)

Box = tuple[float, float, float, float]  # min lon, min lat, max lon, max lat

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_CRS = re.compile(r"^\s*<([^>]*)>\s*")
_ENVELOPE = re.compile(
    rf"^ENVELOPE\s*\(\s*({_NUMBER})\s*,\s*({_NUMBER})\s*,\s*({_NUMBER})\s*,\s*({_NUMBER})\s*\)$",
    re.I,
)
_WKT = re.compile(r"^(MULTI)?(POINT|LINESTRING|POLYGON)\s*(Z|M|ZM)?\s*\(", re.I)


def parse(text: str) -> Box | None:
    """The box around a WKT or GeoJSON geometry; None when it is neither or out of range."""
    text = text.strip()
    if text.startswith("{"):
        return _geojson(text)
    swap = False
    crs = _CRS.match(text)
    if crs:
        swap = bool(re.search(r"EPSG/0/4326$|EPSG:4326$", crs.group(1)))
        text = text[crs.end() :]
    envelope = _ENVELOPE.match(text)
    if envelope:
        min_x, max_x, max_y, min_y = (float(g) for g in envelope.groups())
        return _valid((min_x, min_y, max_x, max_y))
    if not _WKT.match(text):
        return None
    pairs: list[tuple[float, float]] = []
    for group in re.findall(r"\(([^()]*)\)", text):
        for point in group.split(","):
            numbers = re.findall(_NUMBER, point)
            if len(numbers) >= 2:
                x, y = float(numbers[0]), float(numbers[1])
                pairs.append((y, x) if swap else (x, y))
    return _around(pairs)


def box_of(graph: Graph, node: Node) -> Box | None:
    """The box a ``dct:spatial`` value describes, if it describes one."""
    if isinstance(node, Literal):
        return parse(str(node))
    boxes = [
        found
        for predicate in GEOMETRY_PREDICATES
        for value in graph.objects(node, predicate)  # pyright: ignore[reportArgumentType]
        if isinstance(value, Literal) and (found := parse(str(value))) is not None
    ]
    return merge(boxes)


def merge(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def parse_query(text: str) -> Box | None:
    """``minLon,minLat,maxLon,maxLat`` as given to ``bbox=``."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        return None
    try:
        min_x, min_y, max_x, max_y = (float(p) for p in parts)
    except ValueError:
        return None
    if min_x > max_x or min_y > max_y:
        return None
    return _valid((min_x, min_y, max_x, max_y))


def _geojson(text: str) -> Box | None:
    try:
        data: Any = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("bbox"), list) and len(data["bbox"]) >= 4:  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        bbox: list[Any] = data["bbox"]  # pyright: ignore[reportUnknownVariableType]
        try:
            if len(bbox) == 6:  # with heights
                return _valid((float(bbox[0]), float(bbox[1]), float(bbox[3]), float(bbox[4])))
            return _valid((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])))
        except (TypeError, ValueError):
            return None
    pairs: list[tuple[float, float]] = []
    _collect(data, pairs)
    return _around(pairs)


def _collect(data: Any, pairs: list[tuple[float, float]]) -> None:
    if isinstance(data, dict):
        for key in ("coordinates", "geometry", "geometries", "features"):
            if key in data:
                _collect(data[key], pairs)
    elif isinstance(data, list):
        items: list[Any] = data  # pyright: ignore[reportUnknownVariableType]
        if len(items) >= 2 and all(isinstance(v, int | float) for v in items[:2]):
            pairs.append((float(items[0]), float(items[1])))
        else:
            for item in items:
                _collect(item, pairs)


def _around(pairs: list[tuple[float, float]]) -> Box | None:
    if not pairs:
        return None
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    return _valid((min(xs), min(ys), max(xs), max(ys)))


def _valid(box: Box) -> Box | None:
    min_x, min_y, max_x, max_y = box
    if not (-180 <= min_x <= max_x <= 180 and -90 <= min_y <= max_y <= 90):
        return None
    return box
