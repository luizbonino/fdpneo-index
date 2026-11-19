"""The search profile (search design §3.3 to 3.4): predicates to search fields.

A YAML document validated here. The bundled default covers DCAT 2/3,
DCAT-AP, FDP-O, r3d, FOAF and vCard; ``profile_hash`` identifies a version,
so rows built by an older profile are re-projected.

Deviation from the design: administrator overrides of the profile (``PUT
/index/search/profile``, TASKS 6.12) come later; which facets show, in what
order, is an index setting instead (:mod:`fdpindex.search.settings`).
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field as PydanticField, model_validator

Kind = Literal["fdp", "catalog", "dataset", "series", "service", "distribution", "other"]
KINDS: tuple[Kind, ...] = (
    "fdp",
    "catalog",
    "dataset",
    "series",
    "service",
    "distribution",
    "other",
)

# Fields stored as columns of search_resource rather than as search_value rows.
COLUMN_FIELDS = frozenset(
    {"title", "description", "issued", "modified", "landingPage", "temporal", "bbox"}
)
# Computed fields a card may name besides profile fields.
COMPUTED_FIELDS = frozenset({"datasetCount", "distributionCount"})


class SearchField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ``period``: the path leads to a period (a node with a start and an end, or a
    # literal such as "2010/2020"), stored as two date columns (TASKS 8.3).
    # ``geometry``: the path leads to a location or a WKT/GeoJSON literal, stored as a
    # bounding box (TASKS 9.8).
    kind: Literal[
        "text", "iri", "literal", "agent", "datetime", "period", "geometry", "number", "boolean"
    ]
    paths: list[str] = PydanticField(min_length=1)
    facet: Literal["main", "more"] | None = None
    weight: Literal["A", "B", "C", "D"] | None = None
    rollup: bool = False  # distributions' values are added to their dataset
    literals: bool = True  # for kind iri: keep literal values too
    # For kinds iri and agent: keep only IRIs in these namespaces, or drop those in
    # ``exclude`` (full IRIs or CURIEs; http and https forms are treated alike).
    namespaces: list[str] = PydanticField(default_factory=lambda: [])
    exclude: list[str] = PydanticField(default_factory=lambda: [])
    # For kinds iri and agent: equivalent concepts (exact matches) are one value
    # (TASKS 8.11), stored and filtered as their group's representative.
    equivalents: bool = False
    # For kinds iri and agent: the broader concepts the resolver found are counted too,
    # in the companion field ``<name>^`` (TASKS 8.12): a tree, and "this or narrower".
    hierarchy: bool = False
    normalise: str | None = None
    buckets: list[float] | None = None  # for kind number
    presence: bool = False  # for kind boolean: present means true
    label: dict[str, str] = PydanticField(default_factory=dict)
    help: dict[str, str] = PydanticField(default_factory=dict)


class Capture(BaseModel):
    """Raw capture of every property of a resource, for facets on any property (TASKS 9.2).

    One step from the resource: IRI objects, and literals up to
    ``literal_max_chars`` that are strings, language-tagged or booleans
    (dates and numbers make poor facet values). ``rdf:type`` is the type facet.
    """

    model_config = ConfigDict(extra="forbid")

    properties: bool = True
    literal_max_chars: int = PydanticField(default=100, ge=0, le=512)
    max_values: int = PydanticField(default=50, ge=1, le=200)
    # Two-step paths captured as well (TASKS 9.2), such as dcat:contactPoint/vcard:hasEmail:
    # path filters on POST /index/search can test them.
    paths: list[str] = PydanticField(default_factory=lambda: [], max_length=50)


class Landing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counters: list[Kind] = PydanticField(default_factory=lambda: ["fdp", "catalog", "dataset"])


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    prefixes: dict[str, str]
    types: dict[Kind, list[str]]
    fields: dict[str, SearchField]
    cards: dict[Kind, list[str]] = PydanticField(default_factory=lambda: {})
    landing: Landing = PydanticField(default_factory=Landing)
    capture: Capture = PydanticField(default_factory=Capture)
    normalise: dict[str, dict[str, str]] = PydanticField(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Profile:
        for name in ("title", "description"):
            if name not in self.fields or self.fields[name].kind != "text":
                raise ValueError(f"the profile needs a text field '{name}'")
        for name, spec in self.fields.items():
            if spec.kind == "text" and name not in {"title", "description"}:
                raise ValueError(f"field '{name}': only title and description are text fields")
            if spec.normalise is not None and spec.normalise not in self.normalise:
                raise ValueError(f"field '{name}': no normalise map '{spec.normalise}'")
            if (spec.namespaces or spec.exclude) and spec.kind not in {"iri", "agent"}:
                raise ValueError(f"field '{name}': namespaces apply to iri and agent fields")
            for namespace in (*spec.namespaces, *spec.exclude):
                self.expand(namespace)
            if spec.kind == "period" and name != "temporal":
                raise ValueError(f"field '{name}': only temporal is a period field")
            if spec.kind == "geometry" and name != "bbox":
                raise ValueError(f"field '{name}': only bbox is a geometry field")
            if spec.kind == "number" and not spec.buckets:
                raise ValueError(f"field '{name}': a number field needs buckets")
            for path in spec.paths:
                steps = path.split("/") if not path.startswith("http") else [path]
                if len(steps) > 2:
                    raise ValueError(f"field '{name}': path '{path}' is longer than two steps")
                for step in steps:
                    self.expand(step)
        for path in self.capture.paths:
            if len(self.path_iris(path)) != 2:
                raise ValueError(f"capture path '{path}' is not two steps")
        for curies in self.types.values():
            for curie in curies:
                self.expand(curie)
        for kind, names in self.cards.items():
            for name in names:
                if name not in self.fields and name not in COMPUTED_FIELDS:
                    raise ValueError(f"card '{kind}': unknown field '{name}'")
        return self

    def expand(self, curie: str) -> str:
        """A CURIE (or a full IRI in angle brackets or as is) to an IRI."""
        if curie.startswith("<") and curie.endswith(">"):
            return curie[1:-1]
        if curie.startswith(("http://", "https://", "urn:")):
            return curie
        prefix, sep, local = curie.partition(":")
        if not sep or prefix not in self.prefixes:
            raise ValueError(f"unknown prefix in '{curie}'")
        return self.prefixes[prefix] + local

    def path_iris(self, path: str) -> tuple[str, ...]:
        if path.startswith(("http://", "https://", "<")):
            return (self.expand(path),)
        return tuple(self.expand(step) for step in path.split("/"))

    def accepts(self, spec: SearchField, iri: str) -> bool:
        """Whether an IRI value passes the field's ``namespaces`` and ``exclude``.

        Values that are not IRIs (an agent's name, a literal) always pass.
        """
        if not iri.startswith(("http://", "https://", "urn:")):
            return True
        form = _scheme_free(iri)
        if spec.namespaces and not any(
            form.startswith(_scheme_free(self.expand(n))) for n in spec.namespaces
        ):
            return False
        return not any(form.startswith(_scheme_free(self.expand(n))) for n in spec.exclude)

    @property
    def facets(self) -> list[str]:
        return [name for name, spec in self.fields.items() if spec.facet is not None]

    @property
    def hierarchies(self) -> dict[str, str]:
        """Companion field (``subject^``: this concept or a narrower one) → its field."""
        return {
            f"{name}^": name
            for name, spec in self.fields.items()
            if spec.hierarchy and spec.facet is not None
        }

    def fields_by_property(self) -> dict[str, list[tuple[str, SearchField]]]:
        """The fields each predicate feeds directly (one-step paths): for naming
        properties by the index's own fields, as the SPARQL builder does (TASKS 9.3)."""
        found: dict[str, list[tuple[str, SearchField]]] = {}
        for name, spec in self.fields.items():
            for path in spec.paths:
                steps = self.path_iris(path)
                if len(steps) == 1:
                    found.setdefault(steps[0], []).append((name, spec))
        return found

    @property
    def hash(self) -> str:
        return profile_hash(self)


def _scheme_free(iri: str) -> str:
    return iri.removeprefix("http://").removeprefix("https://")


def profile_hash(profile: Profile) -> str:
    canonical = json.dumps(profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_profile(text: str) -> Profile:
    return Profile.model_validate(yaml.safe_load(text))


@cache
def default_profile() -> Profile:
    text = resources.files("fdpindex.search").joinpath("profile.default.yaml").read_text("utf-8")
    return load_profile(text)
