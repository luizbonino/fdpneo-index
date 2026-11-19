"""Vocabulary sets: what the packs' shapes need to know about the IRIs a record uses.

DCAT-AP's vocabulary shapes require, for instance, that a dataset's
``dct:accessRights`` is a concept ``skos:inScheme`` the EU access-right table,
and ``sh:class`` checks need a value's types and the class hierarchy. Those
facts live in the vocabularies, not in the record. The EU's validator merges
37 MB of vocabularies into every validation; the index keeps only the
triples that matter (``keep``: types, schemes, subclasses) in a compressed
N-Triples file, and adds to each record the triples about the IRIs it uses,
followed transitively (a concept's scheme, the scheme's type, a class's
superclasses).

A vocabulary set is ``vocabularies/<id>.yaml`` (its sources) and the file it
names as ``output``; ``fdpindex conformance vocabularies`` rebuilds the file.
"""

from __future__ import annotations

import gzip
import io
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field
from rdflib import Graph, URIRef

from fdpindex.conformance.packs import PackSource

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from rdflib.term import Node

BUNDLED = Path(__file__).parent / "vocabularies"
MAX_CLOSURE = 2_000  # triples added to one record, at most

Triple = tuple[URIRef, URIRef, URIRef]


class VocabularyManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    title: dict[str, str]
    source: PackSource
    output: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*\.nt\.gz$")
    keep: list[str] = Field(min_length=1)
    files: list[str] = Field(min_length=1)


def load_manifest(path: Path) -> VocabularyManifest:
    return VocabularyManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def extract(graphs: Iterable[Graph], keep: Iterable[str]) -> bytes:
    """The kept triples between IRIs, as sorted, gzipped N-Triples (reproducible bytes)."""
    predicates = {URIRef(p) for p in keep}
    lines: set[str] = set()
    for graph in graphs:
        for s, p, o in graph:
            if p in predicates and isinstance(s, URIRef) and isinstance(o, URIRef):
                lines.add(f"{s.n3()} {p.n3()} {o.n3()} .")
    data = ("\n".join(sorted(lines)) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as out:
        out.write(data)
    return buffer.getvalue()


@dataclass
class VocabularySet:
    """Kept triples indexed by subject."""

    about: dict[URIRef, list[Triple]] = field(default_factory=lambda: {})

    @classmethod
    def read(cls, path: Path) -> VocabularySet:
        vocabulary = cls()
        with gzip.open(path, "rt", encoding="utf-8") as lines:
            for line in lines:
                # Lines are "<s> <p> <o> ." between IRIs, as extract() writes them.
                parts = line.split(" ")
                if len(parts) != 4:
                    continue
                s, p, o = (URIRef(x[1:-1]) for x in parts[:3])
                vocabulary.about.setdefault(s, []).append((s, p, o))
        return vocabulary

    def knows(self, iri: URIRef) -> bool:
        return iri in self.about

    def __len__(self) -> int:
        return sum(len(v) for v in self.about.values())

    def closure(self, graph: Graph) -> Iterator[Triple]:
        """Triples about the IRIs the graph uses as objects, followed transitively."""
        seen: set[Node] = set()
        todo: list[Node] = [o for o in graph.objects() if isinstance(o, URIRef)]
        added = 0
        while todo and added < MAX_CLOSURE:
            node = todo.pop()
            if node in seen or not isinstance(node, URIRef):
                continue
            seen.add(node)
            for triple in self.about.get(node, []):
                added += 1
                yield triple
                todo.append(triple[2])


@cache
def bundled(vocabulary: str) -> VocabularySet:
    """A bundled vocabulary set by id, read once per process."""
    manifest = load_manifest(BUNDLED / f"{vocabulary}.yaml")
    return VocabularySet.read(BUNDLED / manifest.output)
