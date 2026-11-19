"""SHACL profile packs as data (TASKS 11.2; conformance design §2).

A pack is a folder ``<id>/<version>/`` holding ``pack.yaml`` (the manifest),
the SHACL shapes files as their publisher released them, and ``SHA256SUMS``
listing those files. The index bundles DCAT-AP 3.0.1, HealthDCAT-AP Release 8
and Health-RI core v2.0.3 under ``fdpindex/conformance/packs/``; an operator
adds packs in the same layout from the directory ``FDPINDEX_CONFORMANCE_PACKS``
names.

Each shapes file has a **role** (``shapes``, ``vocabulary``, ``recommended``,
``deprecated``, ``range``) and may be off by default (``enabled: false``). A
pack may come in **variants** chosen per record by the value of one property:
HealthDCAT-AP has one set of shapes per access level, picked by
``dct:accessRights``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rdflib import URIRef

from fdpindex.search.profile import Kind

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rdflib import Graph

BUNDLED = Path(__file__).parent / "packs"
MANIFEST = "pack.yaml"
SUMS = "SHA256SUMS"

Role = Literal["shapes", "vocabulary", "recommended", "deprecated", "range"]
_ID = r"^[a-z0-9][a-z0-9-]{0,62}$"
_VERSION = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"


class PackError(ValueError):
    """A pack that cannot be used: its manifest or files are wrong."""


class _Camel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class PackFile(_Camel):
    path: str = Field(description="Relative to the pack's folder, as in the source")
    role: Role
    variant: str | None = None
    enabled: bool = Field(default=True, description="Validated against by default")
    note: str = ""

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not value.endswith(".ttl"):
            raise ValueError(f"not a relative path to a Turtle file: {value}")
        return value


class Variants(_Camel):
    property: str = Field(description="The property whose value picks the variant")
    default: str
    values: dict[str, list[str]] = Field(description="Variant name → the IRIs that pick it")

    @model_validator(mode="after")
    def _default_listed(self) -> Variants:
        if self.default not in self.values:
            raise ValueError(f"default variant '{self.default}' is not listed")
        return self


class PackSource(_Camel):
    url: str = Field(description="Where people read the source")
    download: str = Field(description="Base URL the files are downloaded from (ends in /)")

    @field_validator("download")
    @classmethod
    def _base(cls, value: str) -> str:
        if not value.startswith("https://") or not value.endswith("/"):
            raise ValueError("download must be an https:// URL ending in /")
        return value


class PackManifest(_Camel):
    id: str = Field(pattern=_ID)
    version: str = Field(pattern=_VERSION)
    title: dict[str, str]
    description: dict[str, str] = Field(default_factory=dict)
    publisher: str = ""
    licence: str = ""
    specification: str = Field(description="IRI of the specification, for dct:conformsTo")
    source: PackSource | None = None
    applies_to: list[Kind] = Field(alias="appliesTo", min_length=1)
    vocabularies: list[str] = Field(default_factory=list)
    variants: Variants | None = None
    files: list[PackFile] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> PackManifest:
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("a file is listed twice")
        names = set(self.variants.values) if self.variants else set[str]()
        for file in self.files:
            if file.variant is not None and file.variant not in names:
                raise ValueError(f"{file.path}: unknown variant '{file.variant}'")
        if not any(f.enabled and f.role == "shapes" for f in self.files):
            raise ValueError("no shapes file is enabled")
        return self


@dataclass(frozen=True)
class Pack:
    manifest: PackManifest
    directory: Path
    bundled: bool

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def key(self) -> str:
        return f"{self.manifest.id}@{self.manifest.version}"

    @cached_property
    def digest(self) -> str:
        """A hash of the manifest and every file: results are stale when it changes."""
        sha = hashlib.sha256((self.directory / MANIFEST).read_bytes())
        for file in self.manifest.files:
            sha.update(file.path.encode())
            sha.update((self.directory / file.path).read_bytes())
        return sha.hexdigest()

    def files_for(
        self, variant: str | None, enabled: Iterable[str] | None = None
    ) -> list[PackFile]:
        """The files validated against for a variant: enabled ones, shared or of the variant.

        ``enabled`` (paths) replaces the manifest's defaults, as a deployment may.
        """
        chosen = set(enabled) if enabled is not None else None
        return [
            f
            for f in self.manifest.files
            if (f.path in chosen if chosen is not None else f.enabled)
            and (f.variant is None or f.variant == variant)
        ]

    def variant_for(self, graph: Graph, root: str | None) -> str | None:
        """The variant a record gets, from its root's value of the variants' property."""
        variants = self.manifest.variants
        if variants is None:
            return None
        prop = URIRef(variants.property)
        subjects = [URIRef(root)] if root else list(graph.subjects(prop, None))
        for subject in subjects:
            for value in graph.objects(subject, prop):
                for name, iris in variants.values.items():
                    if str(value) in iris:
                        return name
        return variants.default


def load_pack(directory: Path, *, bundled: bool = False) -> Pack:
    try:
        raw = yaml.safe_load((directory / MANIFEST).read_text(encoding="utf-8"))
        manifest = PackManifest.model_validate(raw)
    except (OSError, yaml.YAMLError, ValueError) as err:
        raise PackError(f"{directory}: {err}") from err
    if directory.name != manifest.version or directory.parent.name != manifest.id:
        raise PackError(
            f"{directory}: the folder must be <id>/<version> ({manifest.id}/{manifest.version})"
        )
    for file in manifest.files:
        if not (directory / file.path).is_file():
            raise PackError(f"{directory}: missing {file.path}")
    return Pack(manifest=manifest, directory=directory, bundled=bundled)


def _folders(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob(f"*/*/{MANIFEST}"))


def discover(extra: Path | None = None) -> dict[str, Pack]:
    """Bundled packs, then an operator's; keyed ``id@version``. A key may not repeat."""
    packs: dict[str, Pack] = {}
    found = [(d, True) for d in _folders(BUNDLED)]
    if extra is not None:
        if not extra.is_dir():
            raise PackError(f"FDPINDEX_CONFORMANCE_PACKS: not a directory: {extra}")
        found += [(d, False) for d in _folders(extra)]
    for directory, bundled in found:
        pack = load_pack(directory, bundled=bundled)
        if pack.key in packs:
            raise PackError(f"{directory}: pack {pack.key} exists already")
        packs[pack.key] = pack
    return packs


def sums(directory: Path, paths: Iterable[str]) -> str:
    """``SHA256SUMS`` content for these files, as ``sha256sum`` writes it."""
    lines = [
        f"{hashlib.sha256((directory / p).read_bytes()).hexdigest()}  {p}" for p in sorted(paths)
    ]
    return "\n".join(lines) + "\n"


def verify(pack: Pack) -> list[str]:
    """Differences between the pack's files and its ``SHA256SUMS``; empty when they match."""
    listed: dict[str, str] = {}
    try:
        text = (pack.directory / SUMS).read_text(encoding="utf-8")
    except OSError:
        return [f"{pack.key}: no {SUMS}"]
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line)
        if match:
            listed[match.group(2)] = match.group(1)
    problems: list[str] = []
    for file in pack.manifest.files:
        digest = hashlib.sha256((pack.directory / file.path).read_bytes()).hexdigest()
        if listed.get(file.path) != digest:
            problems.append(f"{pack.key}: {file.path} differs from {SUMS}")
    return problems
