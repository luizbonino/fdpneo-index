"""Projection of harvested nodes into search rows (search design §3.2).

``project_entry`` is a pure function: an entry's harvested N-Triples in, one
:class:`ResourceRow` per DCAT resource and the labels found out. No I/O, so
it is unit-tested on fixture graphs and runs in a thread.

The whole entry is projected at once, because parents, roll-ups and counts
depend on several nodes; the indexer then writes only the rows whose
``row_hash`` changed.

Deviations from the design, all deliberate:

* Untagged literals are stemmed with the index's default language
  (English) instead of ``simple``: most FDPs leave titles untagged, and the
  accent-folded ``simple`` vector still matches every language.
* IRIs are not stripped of trailing slashes (licence IRIs carry one by
  definition); only the scheme of the EU authority tables and Creative
  Commons IRIs is folded to ``http``.
* Temporal coverage is two date columns (:mod:`fdpindex.search.period`); spatial
  columns and ``dedup_key`` come later (TASKS 8.4, 8.8).
* Raw capture stores every property of a resource as ``p:<key>`` values,
  where the key is a hash of the property IRI (``property_field``): the
  ``field`` column is short, and ``search_property`` maps keys back.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF
from rdflib.term import Node

from fdpindex.rdf import LABEL_PREDICATES as RDF_LABEL_PREDICATES
from fdpindex.search.period import Period, merge as merge_periods, period_of
from fdpindex.search.profile import KINDS, Kind, Profile, SearchField
from fdpindex.search.vocab import bundled_labels, country_code, geonames_iri, place_alias

DCAT = "http://www.w3.org/ns/dcat#"
DCT = "http://purl.org/dc/terms/"
LDP = "http://www.w3.org/ns/ldp#"
FDP_O = "https://w3id.org/fdp/fdp-o#"
FDP_LEGACY = "https://w3id.org/fdp/o#"
R3D = "http://www.re3data.org/schema/3-0#"

# Links from a resource to the resources below it (and LDP containment).
CHILD_PREDICATES = tuple(
    URIRef(iri)
    for iri in (
        DCAT + "dataset",
        DCAT + "distribution",
        DCAT + "catalog",
        DCAT + "service",
        DCAT + "seriesMember",
        FDP_O + "metadataCatalog",
        FDP_LEGACY + "metadataCatalog",
        R3D + "dataCatalog",
        LDP + "contains",
    )
)
IS_PART_OF = URIRef(DCT + "isPartOf")
LDP_CONTAINS = URIRef(LDP + "contains")
IGNORED_TYPE_PREFIXES = (LDP, "http://www.w3.org/ns/prov#")

# What names a thing: skos:prefLabel, rdfs:label, dct:title, dc:title, schema:name,
# foaf:name, vcard:fn … (shared with the SPARQL labels and the concept resolver).
LABEL_PREDICATES = RDF_LABEL_PREDICATES
# Bumped when the projection's output changes for the same input: entries are re-projected.
PROJECTION_VERSION = 8

# Literal language → PostgreSQL text search configuration (created by migration 0003).
TS_CONFIGS = {
    "en": "fdpindex_english",
    "nl": "fdpindex_dutch",
    "de": "fdpindex_german",
    "fr": "fdpindex_french",
    "es": "fdpindex_spanish",
    "it": "fdpindex_italian",
    "pt": "fdpindex_portuguese",
    "sv": "fdpindex_swedish",
    "da": "fdpindex_danish",
    "fi": "fdpindex_finnish",
    "no": "fdpindex_norwegian",
    "nb": "fdpindex_norwegian",
    "hu": "fdpindex_hungarian",
    "ro": "fdpindex_romanian",
    "tr": "fdpindex_turkish",
    "ru": "fdpindex_russian",
}
SIMPLE_CONFIG = "fdpindex_simple"
DEFAULT_LANGUAGE = "en"

MAX_VALUE_CHARS = 512
MAX_VALUES_PER_FIELD = 200
MAX_TITLE_CHARS = 1000
MAX_DESCRIPTION_CHARS = 20_000
MAX_LABEL_CHARS = 500

COMPLETENESS_CRITERIA = (
    "title",
    "description",
    "subject",
    "publisher",
    "license",
    "accessRights",
    "access",
    "date",
    "contact",
)


def ts_config(lang: str) -> str:
    primary = (lang or DEFAULT_LANGUAGE).split("-")[0].lower()
    return TS_CONFIGS.get(primary, SIMPLE_CONFIG)


@dataclass(frozen=True)
class Value:
    field: str
    value: str
    lang: str | None = None
    num: float | None = None  # a number field's value; ``value`` is its bucket


@dataclass
class ResourceRow:
    iri: str
    node_iri: str
    public_id: str
    kind: Kind
    type_iris: list[str]
    titles: dict[str, str]
    descriptions: dict[str, str]
    title: str
    title_fallback: bool
    description: str | None
    landing_page: str | None
    issued: datetime | None
    modified: datetime | None
    values: list[Value]
    segments: list[tuple[str, str, str]]  # (text search configuration, weight, text)
    parent_iri: str | None = None
    catalog_iri: str | None = None
    dataset_iri: str | None = None
    completeness: float = 0.0
    facts: dict[str, Any] = field(default_factory=lambda: {})
    children: list[str] = field(default_factory=lambda: [])
    part_of: str | None = None
    temporal_start: date | None = None
    temporal_end: date | None = None

    @property
    def search_text(self) -> str:
        return " ".join(text for _, _, text in self.segments).lower()

    @property
    def row_hash(self) -> str:
        data: dict[str, Any] = {
            "iri": self.iri,
            "node": self.node_iri,
            "kind": self.kind,
            "types": self.type_iris,
            "titles": self.titles,
            "descriptions": self.descriptions,
            "landing": self.landing_page,
            "issued": self.issued.isoformat() if self.issued else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "values": sorted(
                (v.field, v.value, v.lang or "", *(() if v.num is None else (v.num,)))
                for v in self.values
            ),
            "segments": self.segments,
            "parent": self.parent_iri,
            "catalog": self.catalog_iri,
            "dataset": self.dataset_iri,
            "completeness": self.completeness,
            "facts": self.facts,
        }
        if self.temporal_start or self.temporal_end:
            data["temporal"] = [
                self.temporal_start.isoformat() if self.temporal_start else None,
                self.temporal_end.isoformat() if self.temporal_end else None,
            ]
        text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class ConceptText:
    """A resolved concept's names (ADR-0006): labels and synonyms per language, and one
    label of each broader concept (``(lang, label)``)."""

    labels: dict[str, str]
    synonyms: dict[str, list[str]]
    broader: list[tuple[str, str]]
    # The broader concepts' IRIs, nearest first (TASKS 8.12).
    ancestors: list[str] = field(default_factory=lambda: [])


_ONE_LOWER = {"A": "B", "B": "C", "C": "D", "D": "D"}


HIERARCHY_SUFFIX = "^"


def add_ancestors(
    projection: EntryProjection,
    concepts: Mapping[str, ConceptText],
    canonical: Mapping[str, str],
    profile: Profile,
) -> None:
    """For fields with ``hierarchy``, store each value and its broader concepts in the
    companion field ``<name>^`` (TASKS 8.12): counting a concept there counts the
    resources tagged with it or with anything narrower. Runs after
    :func:`merge_equivalents`; ancestors are mapped to their groups too.
    """
    fields = {name for name, spec in profile.fields.items() if spec.hierarchy}
    if not fields:
        return
    by_canonical: dict[str, ConceptText] = {}
    for iri, concept in concepts.items():
        by_canonical.setdefault(canonical.get(iri, iri), concept)
    for row in projection.resources:
        extra: list[Value] = []
        for value in row.values:
            if value.field not in fields:
                continue
            companion = value.field + HIERARCHY_SUFFIX
            extra.append(Value(companion, value.value))
            concept = by_canonical.get(value.value) or concepts.get(value.value)
            for ancestor in concept.ancestors if concept is not None else []:
                extra.append(Value(companion, canonical.get(ancestor, ancestor)))
        if extra:
            row.values = _dedupe(row.values + extra)


def merge_equivalents(
    projection: EntryProjection, canonical: Mapping[str, str], profile: Profile
) -> None:
    """Store equivalent concepts as their group's representative (TASKS 8.11).

    Only in fields with ``equivalents``; a resource tagged with both the
    Wikidata item and the MeSH descriptor gets one value. Their names were
    added to the text before (``enrich``), so every name still finds it.
    """
    fields = {name for name, spec in profile.fields.items() if spec.equivalents}
    if not fields or not canonical:
        return
    for row in projection.resources:
        changed = False
        values: list[Value] = []
        for value in row.values:
            if value.field in fields and value.value in canonical:
                value = Value(value.field, canonical[value.value], value.lang)
                changed = True
            values.append(value)
        if changed:
            row.values = _dedupe(values)


def enrich(
    projection: EntryProjection, concepts: Mapping[str, ConceptText], profile: Profile
) -> None:
    """Add resolved concept names to the text of the resources that use the concepts.

    Labels at the field's weight, synonyms one lower, broader concepts' labels at
    ``D``: a search for a broader concept finds what is tagged with a narrower one,
    below what matches it directly.
    """
    if not concepts:
        return
    for row in projection.resources:
        seen = {text.lower() for _, _, text in row.segments}
        for value in row.values:
            spec = profile.fields.get(value.field)
            concept = concepts.get(value.value)
            if concept is None or spec is None or spec.weight is None:
                continue
            for lang, label in sorted(concept.labels.items()):
                _add_segment(row, seen, lang, spec.weight, label)
            for lang, synonyms in sorted(concept.synonyms.items()):
                for synonym in synonyms:
                    _add_segment(row, seen, lang, _ONE_LOWER[spec.weight], synonym)
            for lang, label in concept.broader:
                _add_segment(row, seen, lang, "D", label)


def _add_segment(row: ResourceRow, seen: set[str], lang: str, weight: str, text: str) -> None:
    cleaned = _clean(text, MAX_TITLE_CHARS)
    if cleaned and cleaned.lower() not in seen:
        seen.add(cleaned.lower())
        row.segments.append((ts_config(lang), weight, cleaned))


@dataclass(frozen=True)
class LabelRow:
    iri: str
    lang: str
    label: str


@dataclass
class EntryProjection:
    resources: list[ResourceRow]
    labels: list[LabelRow]
    # Captured properties: field key (``p:…``) to property IRI.
    properties: dict[str, str] = field(default_factory=lambda: {})


PROPERTY_PREFIX = "p:"
# Datatypes kept by the raw capture: strings and booleans; dates and numbers are not facets.
_CAPTURED_DATATYPES = {
    None,
    "http://www.w3.org/2001/XMLSchema#string",
    "http://www.w3.org/2001/XMLSchema#boolean",
    "http://www.w3.org/2001/XMLSchema#anyURI",
    "http://www.w3.org/2001/XMLSchema#token",
    "http://www.w3.org/2001/XMLSchema#normalizedString",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString",
}


def property_field(iri: str) -> str:
    """The ``search_value.field`` of a captured property: ``p:`` and 24 hex digits."""
    return PROPERTY_PREFIX + hashlib.sha256(iri.encode()).hexdigest()[:24]


def is_property_field(name: str) -> bool:
    return name.startswith(PROPERTY_PREFIX)


def public_id(entry_uuid: str, iri: str) -> str:
    """A 22-character id that is stable across reindexes and replicas."""
    digest = hashlib.sha256(f"{entry_uuid}{iri}".encode()).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def canonical_iri(iri: str) -> str:
    """One form per IRI: the EU tables and Creative Commons on ``http``, GeoNames as
    ``https://sws.geonames.org/<id>/``; a country or continent named by GeoNames or
    lexvo becomes its EU authority IRI (TASKS 8.4)."""
    geonames = geonames_iri(iri)
    if geonames is not None:
        return place_alias(geonames)
    for host in ("publications.europa.eu/", "creativecommons.org/"):
        if iri.startswith("https://" + host):
            return "http://" + iri.removeprefix("https://")
    return place_alias(iri)


def normalise(value: str, table: dict[str, str]) -> str:
    """Fold a value through a profile ``normalise`` map (case-insensitive).

    A key ending in ``/`` mapped to ``""`` strips that prefix (IANA media types).
    """
    lowered = value.lower()
    for key, replacement in table.items():
        if replacement == "" and key.endswith("/") and lowered.startswith(key.lower()):
            return value[len(key) :]
    for candidate in (lowered, lowered.rstrip("/")):
        for key, replacement in table.items():
            if key.lower() == candidate or key.lower().rstrip("/") == candidate:
                return replacement
    return value


def parse_datetime(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        match = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?(?:[+-]\d{2}:\d{2}|Z)?", text)
        if not match:
            return None
        year, month, day = match.groups()
        try:
            parsed = datetime.combine(
                date(int(year), int(month or 1), int(day or 1)), datetime.min.time()
            )
        except ValueError:
            return None
    if not 1000 <= parsed.year <= 2999:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pick_language(texts: dict[str, str], lang: str = DEFAULT_LANGUAGE) -> tuple[str, str] | None:
    """The text in ``lang``, else untagged, else English, else any (§3.6)."""
    if not texts:
        return None
    wanted = lang.lower()
    for key in (wanted, wanted.split("-")[0], ""):
        if key in texts:
            return key, texts[key]
    for key, text in texts.items():
        if key.split("-")[0] == wanted.split("-")[0]:
            return key, text
    if "en" in texts:
        return "en", texts["en"]
    key = sorted(texts)[0]
    return key, texts[key]


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _literal_rank(node: Node) -> int:
    if isinstance(node, Literal):
        lang = (node.language or "").lower()
        return 0 if lang in {"", "en"} or lang.startswith("en-") else 1
    return 2


class _NodeGraph:
    """One harvested node: its graph and the resource subject."""

    def __init__(self, node_iri: str, ntriples: str) -> None:
        self.node_iri = node_iri
        self.graph = Graph()
        self.graph.parse(data=ntriples, format="nt")
        self.subject = self._find_subject()

    def _find_subject(self) -> URIRef | None:
        base = self.node_iri.split("#")[0]
        for candidate in (self.node_iri, base, base.rstrip("/"), base.rstrip("/") + "/"):
            ref = URIRef(candidate)
            if (ref, None, None) in self.graph:
                return ref
        typed = sorted(
            {s for s in self.graph.subjects(RDF.type, None) if isinstance(s, URIRef)}, key=str
        )
        return typed[0] if len(typed) == 1 else None

    def objects(self, path: tuple[str, ...]) -> Iterator[Node]:
        assert self.subject is not None
        frontier: list[Node] = [self.subject]
        for step in path:
            predicate = URIRef(step)
            frontier = [o for s in frontier for o in self.graph.objects(s, predicate)]  # pyright: ignore[reportArgumentType]
        yield from frontier

    def name_of(self, node: Node) -> Literal | None:
        names = [
            o
            for predicate in LABEL_PREDICATES
            for o in self.graph.objects(node, predicate)  # pyright: ignore[reportArgumentType]
            if isinstance(o, Literal) and str(o).strip()
        ]
        if not names:
            return None
        return sorted(names, key=_literal_rank)[0]


class _Projector:
    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self.properties: dict[str, str] = {}
        # Titles and descriptions are searched as text; as facet values they are noise.
        self.text_predicates = frozenset(
            path[0]
            for spec in profile.fields.values()
            if spec.kind == "text"
            for path in (profile.path_iris(p) for p in spec.paths)
            if len(path) == 1
        )
        self.paths: dict[str, list[tuple[str, ...]]] = {
            name: [profile.path_iris(p) for p in spec.paths]
            for name, spec in profile.fields.items()
        }
        self.kind_types: list[tuple[Kind, frozenset[str]]] = [
            (kind, frozenset(profile.expand(c) for c in profile.types.get(kind, [])))
            for kind in KINDS
            if kind in profile.types
        ]

    def kind_of(self, types: Iterable[str]) -> Kind:
        present = set(types)
        for kind, iris in self.kind_types:
            if present & iris:
                return kind
        return "other"

    def resource(self, node: _NodeGraph, entry_uuid: str) -> tuple[ResourceRow, list[LabelRow]]:
        assert node.subject is not None
        subject = node.subject
        types = sorted(
            str(t)
            for t in node.graph.objects(subject, RDF.type)
            if isinstance(t, URIRef) and not str(t).startswith(IGNORED_TYPE_PREFIXES)
        )
        values: list[Value] = []
        segments: list[tuple[str, str, str]] = []
        texts: dict[str, dict[str, str]] = {"title": {}, "description": {}}
        dates: dict[str, datetime] = {}
        periods: list[Period] = []
        landing: str | None = None
        for name, spec in self.profile.fields.items():
            found = self._field(node, name, spec, segments)
            if spec.kind == "text":
                limit = MAX_TITLE_CHARS if name == "title" else MAX_DESCRIPTION_CHARS
                for lit in found:
                    if isinstance(lit, Literal) and str(lit).strip():
                        texts[name].setdefault(
                            (lit.language or "").lower(), _clean(str(lit), limit)
                        )
            elif spec.kind == "datetime":
                for raw in found:
                    parsed = parse_datetime(str(raw))
                    if parsed is not None:
                        dates[name] = parsed
                        break
            elif spec.kind == "period":
                periods.extend(period_of(node.graph, item) for item in found)
            elif name == "landingPage":
                landing = next((str(v) for v in found if isinstance(v, URIRef)), None)
            else:
                values.extend(found_values(name, spec, found, self.profile, node))
        if self.profile.capture.properties:
            values.extend(self._capture(node))
        title_pick = pick_language(texts["title"])
        fallback = title_pick is None
        title = title_pick[1] if title_pick else _tail(str(subject))
        description_pick = pick_language(texts["description"])
        row = ResourceRow(
            iri=str(subject),
            node_iri=node.node_iri,
            public_id=public_id(entry_uuid, str(subject)),
            kind=self.kind_of(types),
            type_iris=types,
            titles=texts["title"],
            descriptions=texts["description"],
            title=title,
            title_fallback=fallback,
            description=description_pick[1] if description_pick else None,
            landing_page=landing,
            issued=dates.get("issued"),
            modified=dates.get("modified"),
            values=_dedupe(values),
            segments=segments,
            children=_children(node),
            part_of=next(
                (str(o) for o in node.graph.objects(subject, IS_PART_OF) if isinstance(o, URIRef)),
                None,
            ),
        )
        row.temporal_start, row.temporal_end = merge_periods(periods)
        if fallback:
            row.facts["titleFallback"] = True
        return row, _labels(node)

    def _capture(self, node: _NodeGraph) -> list[Value]:
        """Every property of the resource with an IRI or a short literal value.

        Left out: ``rdf:type`` (the type facet) and the predicates of the title
        and description fields (searched as text).
        """
        assert node.subject is not None
        capture = self.profile.capture
        per_property: dict[str, int] = {}
        out: list[Value] = []
        for predicate, item in sorted(node.graph.predicate_objects(node.subject)):  # pyright: ignore[reportArgumentType]
            if (
                predicate == RDF.type
                or not isinstance(predicate, URIRef)
                or str(predicate) in self.text_predicates
            ):
                continue
            iri = str(predicate)
            if len(iri) > 2048:
                continue
            value: str | None = None
            if isinstance(item, URIRef):
                value = canonical_iri(str(item))
            elif isinstance(item, Literal):
                datatype = str(item.datatype) if item.datatype else None
                text = str(item).strip()
                if datatype in _CAPTURED_DATATYPES and 0 < len(text) <= capture.literal_max_chars:
                    value = _clean(text, capture.literal_max_chars)
            if not value or len(value) > MAX_VALUE_CHARS:
                continue
            key = property_field(iri)
            if per_property.get(key, 0) >= capture.max_values:
                continue
            per_property[key] = per_property.get(key, 0) + 1
            self.properties[key] = iri
            out.append(Value(key, value))
        return out

    def _field(
        self, node: _NodeGraph, name: str, spec: SearchField, segments: list[tuple[str, str, str]]
    ) -> list[Node]:
        found: list[Node] = []
        for path in self.paths[name]:
            found.extend(node.objects(path))
        if spec.kind in {"iri", "agent"}:
            # Literals the field does not keep add no text either (a literal
            # dct:subject is a keyword, not a theme); IRIs outside the field's
            # namespaces belong to another field.
            found = [
                item
                for item in found
                if not (isinstance(item, Literal) and spec.kind == "iri" and not spec.literals)
                and not (isinstance(item, URIRef) and not self.profile.accepts(spec, str(item)))
            ]
        if spec.weight is None:
            return found
        for item in found:
            text: str | None = None
            lang = ""
            if isinstance(item, Literal):
                text, lang = str(item), (item.language or "")
            elif spec.kind == "agent" and isinstance(item, BNode):
                named = node.name_of(item)
                if named is not None:
                    text, lang = str(named), (named.language or "")
            elif isinstance(item, URIRef):
                named = node.name_of(item)
                if named is not None:
                    text, lang = str(named), (named.language or "")
                else:
                    # Bundled labels in every language of the pack (EU themes, file
                    # types …): "Gezondheid" and "Gesundheit" find Health too.
                    limit = MAX_TITLE_CHARS
                    for code, label in bundled_labels(canonical_iri(str(item))).items():
                        segments.append((ts_config(code), spec.weight, _clean(label, limit)))
            if text and text.strip():
                limit = MAX_DESCRIPTION_CHARS if spec.weight in {"C", "D"} else MAX_TITLE_CHARS
                segments.append((ts_config(lang), spec.weight, _clean(text, limit)))
        return found


def found_values(
    name: str, spec: SearchField, found: list[Node], profile: Profile, node: _NodeGraph
) -> list[Value]:
    table = profile.normalise.get(spec.normalise or "", {})
    out: list[Value] = []
    for item in found:
        value: str | None = None
        lang: str | None = None
        num: float | None = None
        if spec.kind == "number":
            num = _number(item)
            value = _bucket(num, spec.buckets or []) if num is not None else None
        elif spec.kind == "boolean":
            if spec.presence:
                value = "true"
            elif isinstance(item, Literal) and str(item).strip().lower() in {
                "true",
                "1",
                "false",
                "0",
            }:
                value = "true" if str(item).strip().lower() in {"true", "1"} else "false"
        elif isinstance(item, URIRef):
            if spec.kind != "literal":
                value = canonical_iri(normalise(str(item), table) if table else str(item))
        elif isinstance(item, Literal):
            if spec.kind == "literal" or (spec.kind in {"iri", "agent"} and spec.literals):
                value = _clean(str(item), MAX_VALUE_CHARS)
                if table:
                    value = canonical_iri(normalise(value, table))
                if spec.normalise == "country" and not value.startswith(("http://", "https://")):
                    # A country written as its ISO code ("NL", "NLD").
                    value = country_code(value) or value
                if name == "keyword":
                    value = value.lower()
                lang = (item.language or None) if spec.kind == "literal" else None
        elif isinstance(item, BNode) and spec.kind == "agent":
            named = node.name_of(item)
            if named is not None:
                value = _clean(str(named), MAX_VALUE_CHARS)
        if value:
            out.append(Value(name, value[:MAX_VALUE_CHARS], lang, num))
        if len(out) >= MAX_VALUES_PER_FIELD:
            break
    if spec.kind == "boolean" and spec.presence and not out:
        # Absent: an explicit "false", so "No" counts and filters like any value.
        out.append(Value(name, "false"))
    return out


def _number(item: Node) -> float | None:
    if not isinstance(item, Literal):
        return None
    try:
        number = float(str(item).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _bucket(number: float, buckets: list[float]) -> str:
    lower: float | None = None
    for bound in sorted(buckets):
        if number < bound:
            return f"{_num(lower)}..{_num(bound)}" if lower is not None else f"..{_num(bound)}"
        lower = bound
    return f"{_num(lower)}.."


def _num(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value == int(value) else str(value)


def bucket_label(key: str) -> str:
    """``..100`` → ``under 100``; ``100..1000`` → ``100 to 1,000``; ``1000..`` → ``1,000 or more``."""
    low, _, high = key.partition("..")

    def fmt(text: str) -> str:
        try:
            number = float(text)
        except ValueError:
            return text
        return f"{int(number):,}" if number == int(number) else f"{number:,}"

    if not low:
        return f"under {fmt(high)}"
    if not high:
        return f"{fmt(low)} or more"
    return f"{fmt(low)} to {fmt(high)}"


def _dedupe(values: list[Value]) -> list[Value]:
    seen: set[tuple[str, str]] = set()
    out: list[Value] = []
    for value in values:
        key = (value.field, value.value)
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _children(node: _NodeGraph) -> list[str]:
    """IRIs the node links to as children; LDP containers inside the node count too."""
    assert node.subject is not None
    found: list[str] = []
    for predicate in CHILD_PREDICATES:
        sources = (
            set(node.graph.subjects(predicate, None))
            if predicate == LDP_CONTAINS
            else {node.subject}
        )
        for source in sources:
            found.extend(
                str(o)
                for o in node.graph.objects(source, predicate)
                if isinstance(o, URIRef) and o != node.subject
            )
    return list(dict.fromkeys(found))


def _tail(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/#"))[-1] or iri


def _labels(node: _NodeGraph) -> list[LabelRow]:
    found: dict[tuple[str, str], str] = {}
    for predicate in LABEL_PREDICATES:
        for subject, obj in node.graph.subject_objects(predicate):
            if isinstance(subject, URIRef) and isinstance(obj, Literal) and str(obj).strip():
                key = (canonical_iri(str(subject)), (obj.language or "").lower())
                found.setdefault(key, _clean(str(obj), MAX_LABEL_CHARS))
    return [LabelRow(iri, lang, label) for (iri, lang), label in found.items()]


KIND_RANK = {kind: index for index, kind in enumerate(KINDS)}


def project_entry(
    entry_uuid: str, nodes: Iterable[tuple[str, str]], profile: Profile
) -> EntryProjection:
    """Project an entry's harvested nodes ``(node_iri, ntriples)``."""
    projector = _Projector(profile)
    rows: dict[str, ResourceRow] = {}
    labels: dict[tuple[str, str], LabelRow] = {}
    for node_iri, ntriples in sorted(nodes):
        node = _NodeGraph(node_iri, ntriples)
        if node.subject is None:
            continue
        row, found = projector.resource(node, entry_uuid)
        rows.setdefault(row.iri, row)
        for label in found:
            labels.setdefault((label.iri, label.lang), label)
    _link(rows)
    _roll_up(rows, profile)
    for row in rows.values():
        row.completeness = _completeness(row)
    return EntryProjection(list(rows.values()), list(labels.values()), projector.properties)


def _link(rows: dict[str, ResourceRow]) -> None:
    """Parents from ``dct:isPartOf``, else from the resource that lists a child."""
    listed_by: dict[str, str] = {}
    for row in sorted(rows.values(), key=lambda r: (KIND_RANK[r.kind], r.iri)):
        for child in row.children:
            if child in rows and child != row.iri:
                listed_by.setdefault(child, row.iri)
    for row in rows.values():
        parent = row.part_of if row.part_of in rows else listed_by.get(row.iri, row.part_of)
        row.parent_iri = parent if parent != row.iri else None
    for row in rows.values():
        row.catalog_iri = _ancestor(row, rows, {"catalog"})
        if row.kind == "distribution":
            row.dataset_iri = _ancestor(row, rows, {"dataset", "series"})


def _ancestor(row: ResourceRow, rows: dict[str, ResourceRow], kinds: set[str]) -> str | None:
    seen = {row.iri}
    current = row.parent_iri
    while current is not None and current not in seen and len(seen) < 32:
        seen.add(current)
        parent = rows.get(current)
        if parent is None:
            return None
        if parent.kind in kinds:
            return parent.iri
        current = parent.parent_iri
    return None


def _roll_up(rows: dict[str, ResourceRow], profile: Profile) -> None:
    rolled = {name for name, spec in profile.fields.items() if spec.rollup}
    distributions: dict[str, int] = {}
    for row in rows.values():
        if row.kind != "distribution" or row.dataset_iri is None:
            continue
        dataset = rows[row.dataset_iri]
        distributions[dataset.iri] = distributions.get(dataset.iri, 0) + 1
        dataset.values = _dedupe(dataset.values + [v for v in row.values if v.field in rolled])
    datasets_per_catalog: dict[str, int] = {}
    total = 0
    for row in rows.values():
        if row.kind in {"dataset", "series"}:
            total += 1
            if row.catalog_iri is not None:
                datasets_per_catalog[row.catalog_iri] = (
                    datasets_per_catalog.get(row.catalog_iri, 0) + 1
                )
    for row in rows.values():
        if row.kind in {"dataset", "series"}:
            row.facts["distributionCount"] = distributions.get(row.iri, 0)
        elif row.kind == "catalog":
            row.facts["datasetCount"] = datasets_per_catalog.get(row.iri, 0)
        elif row.kind == "fdp":
            row.facts["datasetCount"] = total


def _completeness(row: ResourceRow) -> float:
    fields = {v.field for v in row.values}
    present = {
        "title": not row.title_fallback,
        "description": bool(row.descriptions),
        "subject": bool(fields & {"keyword", "theme", "subject"}),
        "publisher": "publisher" in fields,
        "license": "license" in fields,
        "accessRights": "accessRights" in fields,
        "access": bool(fields & {"accessUrl", "downloadUrl", "endpointUrl"})
        or row.facts.get("distributionCount", 0) > 0,
        "date": row.issued is not None or row.modified is not None,
        "contact": bool(fields & {"contactName", "contactEmail"}),
    }
    return round(sum(present[c] for c in COMPLETENESS_CRITERIA) / len(COMPLETENESS_CRITERIA), 3)
