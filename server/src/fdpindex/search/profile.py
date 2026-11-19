"""The search profile (search design §3.3 to 3.4): predicates to search fields.

A YAML document validated here. The bundled default covers DCAT 2/3,
DCAT-AP, FDP-O, r3d, FOAF and vCard; ``profile_hash`` identifies a version,
so rows built by an older profile are re-projected.

Deviation from the design: administrator overrides (``PUT
/index/search/profile``) and raw predicate capture come later (TASKS 6.12,
9.2); the profile is the bundled one until then.
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
COLUMN_FIELDS = frozenset({"title", "description", "issued", "modified", "landingPage"})
# Computed fields a card may name besides profile fields.
COMPUTED_FIELDS = frozenset({"datasetCount", "distributionCount"})


class SearchField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "iri", "literal", "agent", "datetime", "number", "boolean"]
    paths: list[str] = PydanticField(min_length=1)
    facet: Literal["main", "more"] | None = None
    weight: Literal["A", "B", "C", "D"] | None = None
    rollup: bool = False  # distributions' values are added to their dataset
    literals: bool = True  # for kind iri: keep literal values too
    # For kinds iri and agent: keep only IRIs in these namespaces, or drop those in
    # ``exclude`` (full IRIs or CURIEs; http and https forms are treated alike).
    namespaces: list[str] = PydanticField(default_factory=lambda: [])
    exclude: list[str] = PydanticField(default_factory=lambda: [])
    normalise: str | None = None
    buckets: list[float] | None = None  # for kind number
    presence: bool = False  # for kind boolean: present means true
    label: dict[str, str] = PydanticField(default_factory=dict)
    help: dict[str, str] = PydanticField(default_factory=dict)


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
            if spec.kind == "number" and not spec.buckets:
                raise ValueError(f"field '{name}': a number field needs buckets")
            for path in spec.paths:
                steps = path.split("/") if not path.startswith("http") else [path]
                if len(steps) > 2:
                    raise ValueError(f"field '{name}': path '{path}' is longer than two steps")
                for step in steps:
                    self.expand(step)
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
