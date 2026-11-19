"""Validating a record against a pack (TASKS 11.2, 11.3; conformance design §3).

:class:`Validator` is the protocol; :class:`ShaclValidator` implements it with
pySHACL, without inference, on the record plus the vocabulary triples about
the IRIs it uses (:mod:`.vocabularies`). Two rules keep the results about the
record:

* **Only the record's own nodes are judged.** Findings whose focus node is
  not a subject of the record are dropped: they are about vocabulary concepts
  added for the checks (DCAT-AP requires a ``skos:prefLabel`` on every
  ``skos:Concept``, and the added triples carry none).
* **Top-level results only.** Results nested in ``sh:detail`` explain a
  ``sh:node`` or ``sh:or`` result and are not counted on their own.

Shapes are loaded as published, with one repair: a ``sh:property`` whose
shape has no ``sh:path`` is evaluated as ``sh:node``, as the EU validator's
engine (TopBraid SHACL) does. pySHACL refuses such shapes, and HealthDCAT-AP
Release 8 has one (the ``hasStructuredData`` rule on datasets).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from pyshacl import validate as shacl_validate  # pyright: ignore[reportUnknownVariableType]
from rdflib import RDF, BNode, Graph, Literal as RdfLiteral, Namespace, URIRef

from fdpindex.conformance.vocabularies import VocabularySet, bundled

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from rdflib.term import Node

    from fdpindex.conformance.packs import Pack

SH = Namespace("http://www.w3.org/ns/shacl#")
MAX_FINDINGS = 50
MAX_MESSAGE = 500
MAX_VALUE = 200

Severity = Literal["violation", "warning", "info"]
Status = Literal["conforms", "violations", "error"]
_SEVERITY: dict[Node, Severity] = {
    SH.Violation: "violation",
    SH.Warning: "warning",
    SH.Info: "info",
}
_ORDER: dict[Severity, int] = {"violation": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Finding:
    severity: Severity
    focus_node: str
    path: str | None
    component: str
    shape: str | None
    message: str | None
    value: str | None


@dataclass
class Outcome:
    status: Status
    violations: int = 0
    warnings: int = 0
    infos: int = 0
    findings: list[Finding] = field(default_factory=lambda: [])
    variant: str | None = None
    error: str | None = None

    @property
    def conforms(self) -> bool:
        return self.status == "conforms"


class Validator(Protocol):
    def validate(
        self,
        record: Graph,
        pack: Pack,
        *,
        root: str | None = None,
        files: Sequence[str] | None = None,
    ) -> Outcome: ...


def repair(shapes: Graph) -> int:
    """Evaluate path-less property shapes as node shapes; returns how many were repaired."""
    broken = [
        (s, o) for s, o in shapes.subject_objects(SH.property) if shapes.value(o, SH.path) is None
    ]
    for s, o in broken:
        shapes.remove((s, SH.property, o))
        shapes.add((s, SH.node, o))
    return len(broken)


def _path(shapes: Graph, node: Node | None) -> str | None:
    """A result path as text: an IRI, or SHACL's path forms in SPARQL-like syntax."""
    if node is None:
        return None
    if isinstance(node, URIRef):
        return str(node)
    inverse = shapes.value(node, SH.inversePath)
    if inverse is not None:
        return f"^{_path(shapes, inverse)}"
    for predicate, mark in (
        (SH.zeroOrMorePath, "*"),
        (SH.oneOrMorePath, "+"),
        (SH.zeroOrOnePath, "?"),
    ):
        inner = shapes.value(node, predicate)
        if inner is not None:
            return f"({_path(shapes, inner)}){mark}"
    alternatives = shapes.value(node, SH.alternativePath)
    if alternatives is not None:
        return "|".join(str(_path(shapes, x)) for x in shapes.items(alternatives))
    items = list(shapes.items(node))
    if items:
        return "/".join(str(_path(shapes, x)) for x in items)
    return None


def _text(graph: Graph, node: Node, predicate: URIRef) -> str | None:
    values = list(graph.objects(node, predicate))
    if not values:
        return None
    english = [v for v in values if isinstance(v, RdfLiteral) and v.language in {None, "en"}]
    return str((english or values)[0])


def _authored_message(shapes: Graph, shape: Node | None) -> str | None:
    """The shape's own ``sh:message``. pySHACL's generated messages ("Less than 1 values
    on <…>->dct:title") repeat the path and constraint, which are stored apart."""
    if shape is None:
        return None
    return _text(shapes, shape, SH.message)


def _named_shape(shapes: Graph, shape: Node | None) -> str | None:
    """The shape, or for an anonymous property shape the named shape that holds it."""
    if shape is None:
        return None
    if isinstance(shape, URIRef):
        return str(shape)
    for parent in shapes.subjects(SH.property, shape):
        if isinstance(parent, URIRef):
            return str(parent)
    return None


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _local(iri: Node | None) -> str:
    text = str(iri or "")
    return text.removeprefix(str(SH))


class ShaclValidator:
    """pySHACL, without inference; the shapes graph per pack and variant is built once."""

    def __init__(
        self,
        vocabularies: Callable[[str], VocabularySet] = bundled,
    ) -> None:
        self._vocabularies = vocabularies
        self._shapes: dict[tuple[str, str, str | None, tuple[str, ...] | None], Graph] = {}
        self._lock = threading.Lock()

    def shapes(self, pack: Pack, variant: str | None, files: Sequence[str] | None = None) -> Graph:
        """The shapes of a pack's variant; ``files`` replaces the manifest's defaults."""
        chosen = tuple(sorted(files)) if files is not None else None
        key = (pack.key, pack.digest, variant, chosen)
        with self._lock:
            cached = self._shapes.get(key)
            if cached is not None:
                return cached
            graph = Graph()
            for file in pack.files_for(variant, chosen):
                graph.parse(pack.directory / file.path, format="turtle")
            repair(graph)
            self._shapes[key] = graph
            return graph

    def data_graph(self, record: Graph, pack: Pack) -> Graph:
        data = Graph()
        for triple in record:
            data.add(triple)
        for vocabulary in pack.manifest.vocabularies:
            for triple in self._vocabularies(vocabulary).closure(record):
                data.add(triple)
        return data

    def validate(
        self,
        record: Graph,
        pack: Pack,
        *,
        root: str | None = None,
        files: Sequence[str] | None = None,
    ) -> Outcome:
        variant = pack.variant_for(record, root)
        try:
            shapes = self.shapes(pack, variant, files)
            data = self.data_graph(record, pack)
            _conforms, report, _text_report = shacl_validate(  # pyright: ignore[reportUnknownVariableType]
                data,
                shacl_graph=shapes,
                inference="none",
                abort_on_first=False,
                allow_infos=True,
                allow_warnings=True,
                meta_shacl=False,
                advanced=False,
                inplace=True,
            )
        except Exception as err:
            return Outcome(status="error", variant=variant, error=_clip(str(err), MAX_MESSAGE))
        assert isinstance(report, Graph)
        return self._outcome(
            report, shapes, own=self.judged(record, pack), variant=variant, root=root
        )

    def judged(self, record: Graph, pack: Pack) -> set[Node]:
        """The record's subjects, less the vocabulary concepts it describes in passing.

        An FDP may label an EU concept inline (``<…/language/ENG> rdfs:label "English"``);
        the concept is the vocabulary's, not a resource the FDP publishes.
        """
        known = [self._vocabularies(v) for v in pack.manifest.vocabularies]
        return {
            s
            for s in record.subjects()
            if not (isinstance(s, URIRef) and any(v.knows(s) for v in known))
        }

    def _outcome(
        self, report: Graph, shapes: Graph, *, own: set[Node], variant: str | None, root: str | None
    ) -> Outcome:
        outcome = Outcome(status="conforms", variant=variant)
        found: list[Finding] = []
        for result in _results(report):
            focus = report.value(result, SH.focusNode)
            if focus is None or focus not in own:
                continue
            raw = report.value(result, SH.resultSeverity)
            severity: Severity = "violation" if raw is None else _SEVERITY.get(raw, "violation")
            if severity == "violation":
                outcome.violations += 1
            elif severity == "warning":
                outcome.warnings += 1
            else:
                outcome.infos += 1
            value = report.value(result, SH.value)
            found.append(
                Finding(
                    severity=severity,
                    focus_node=str(focus) if isinstance(focus, URIRef) else f"_:{focus}",
                    path=_path(shapes, report.value(result, SH.resultPath)),
                    component=_local(report.value(result, SH.sourceConstraintComponent)),
                    shape=_named_shape(shapes, report.value(result, SH.sourceShape)),
                    message=_clip(
                        _authored_message(shapes, report.value(result, SH.sourceShape)),
                        MAX_MESSAGE,
                    ),
                    value=None
                    if value is None or isinstance(value, BNode)
                    else _clip(str(value), MAX_VALUE),
                )
            )
        found.sort(key=lambda f: (_ORDER[f.severity], f.focus_node != root, f.path or ""))
        outcome.findings = found[:MAX_FINDINGS]
        if outcome.violations:
            outcome.status = "violations"
        return outcome


def _results(report: Graph) -> Iterable[Node]:
    for validation in report.subjects(RDF.type, SH.ValidationReport):
        yield from report.objects(validation, SH.result)
