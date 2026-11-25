"""Metadata health: what the index found in an FDP's metadata (TASKS 15.8).

Computed after each harvest walk from the pages it stored, for every
deployment; nothing here depends on a network or project (design note
metadata-health, 3.2). The result says:

* **reach**: the deepest level the FDP's metadata reaches, FDP → catalogue →
  dataset → distribution → data service (FDPcrawleR's drop-off);
* **levels**: pages read per level, by the ``rdf:type`` of the page's subject;
* **findings**: problems with what the FDP specification asks, each with a
  count and a few examples; **notes**: DCAT's advice, which does not count
  against the FDP;
* **failures**: pages that could not be read, by kind only. The reason's full
  text stays in the admin-only harvest event: it can describe the index's
  own network (security audit, 2026-11-24);
* **stops**: where the index stopped by its own rules (other sites, the
  walk's page limit), which are not the FDP's fault.

Pages are read from their stored N-Triples with a line parser that keeps
only the few predicates needed, so an FDP of 100,000 pages is analysed in
seconds without building graphs.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urldefrag, urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_DCAT = "http://www.w3.org/ns/dcat#"
_DCT = "http://purl.org/dc/terms/"
_LDP = "http://www.w3.org/ns/ldp#"
_FDP_O = "https://w3id.org/fdp/fdp-o#"
_FDP_LEGACY = "https://w3id.org/fdp/o#"
_R3D = "http://www.re3data.org/schema/3-0#"

RDF_TYPE = _RDF + "type"
LDP_CONTAINS = _LDP + "contains"
LDP_MEMBER_RELATION = _LDP + "hasMemberRelation"
LDP_MEMBERSHIP_RESOURCE = _LDP + "membershipResource"
CONTAINER_TYPES = frozenset({_LDP + "DirectContainer", _LDP + "BasicContainer", _LDP + "Container"})

LEVELS = ("fdp", "catalog", "dataset", "distribution", "dataservice")
# Which level a page is, by the types of its subject, most specific first.
_TYPES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "fdp",
        frozenset(
            {
                _FDP_O + "FAIRDataPoint",
                _FDP_O + "MetadataService",
                _FDP_LEGACY + "MetadataService",
                _R3D + "Repository",
            }
        ),
    ),
    ("catalog", frozenset({_DCAT + "Catalog"})),
    ("dataservice", frozenset({_DCAT + "DataService"})),
    ("distribution", frozenset({_DCAT + "Distribution"})),
    ("dataset", frozenset({_DCAT + "Dataset", _DCAT + "DatasetSeries"})),
)

# The FDP's own navigation (harvest.NAVIGATION_PREDICATES) and the DCAT links
# between records.
NAVIGATION = frozenset(
    {
        _R3D + "dataCatalog",
        _FDP_O + "metadataCatalog",
        _FDP_LEGACY + "metadataCatalog",
        _DCAT + "dataset",
        _DCAT + "distribution",
        _DCT + "isPartOf",
    }
)
DCAT_LINKS = frozenset(
    {
        _DCAT + "catalog",
        _DCAT + "dataset",
        _DCAT + "distribution",
        _DCAT + "service",
        _DCAT + "accessService",
        _FDP_O + "metadataCatalog",
        _FDP_LEGACY + "metadataCatalog",
        _R3D + "dataCatalog",
    }
)
_ACCESS = frozenset({_DCAT + "accessURL", _DCAT + "downloadURL", _DCAT + "accessService"})
_KEPT = (
    NAVIGATION
    | DCAT_LINKS
    | _ACCESS
    | {
        RDF_TYPE,
        LDP_CONTAINS,
        LDP_MEMBER_RELATION,
        LDP_MEMBERSHIP_RESOURCE,
    }
)

# The findings, in the order they are shown. Their texts and fixes live in
# the client's messages and in the data holders' guide.
#
# Findings rest on what every FDP claims by registering as one: the FDP
# specification's navigation (LDP containers and the links they stand for)
# and records that lead somewhere. Notes are DCAT's advice, which an FDP never
# promised: DCAT makes distributions optional. They are shown, but do not make
# an FDP's status "findings" (design note metadata-health, decision 7).
#
# An empty LDP container is neither: FDP software makes a container for every
# kind of child a page may have (tried on 70 FDPs, 2026-11-25: 56 had one).
FINDINGS = (
    "linked-only-in-dcat",
    "not-linked-in-dcat",
    "catalog-without-datasets",
)
NOTES = (
    "dataset-without-distributions",
    "distribution-without-access",
)
FAILURE_KINDS = (
    "not-found",
    "http-error",
    "timeout",
    "unparsable",
    "too-large",
    "refused",
    "other",
)
EXAMPLES = 5

_LINE = re.compile(r"^(<[^>]*>|_:\S+)\s+<([^>]*)>\s+(.*?)\s*\.\s*$")


def page_key(iri: str) -> str:
    """Pages and link targets compare without fragment or trailing slash."""
    return urldefrag(iri)[0].rstrip("/")


_key = page_key


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        port = None
    return (parts.scheme, (parts.hostname or "").lower(), port)


def failure_kind(reason: str) -> str:
    """The kind of a harvest failure, from the reason the walk recorded."""
    text = reason.lower()
    if text.startswith("http "):
        status = text[5:8]
        return "not-found" if status in {"404", "410"} else "http-error"
    if "timeout" in text:
        return "timeout"
    if text.startswith("cannot parse") or text.startswith("cannot reuse"):
        return "unparsable"
    if "too large" in text:
        return "too-large"
    if "non-public address" in text or "not allowed" in text or "unsupported url" in text:
        return "refused"
    return "other"


@dataclass
class Page:
    """What one stored page says, reduced to what the findings need."""

    iri: str
    level: str | None
    # Container IRI -> (member relation or None, members)
    containers: dict[str, tuple[str | None, list[str]]] = field(default_factory=lambda: {})
    # (predicate, target) from the page's subject
    links: list[tuple[str, str]] = field(default_factory=lambda: [])
    # Subjects described in the page (IRIs), to tell an inline description from a link
    described: set[str] = field(default_factory=lambda: set())
    access: bool = False


def read_page(iri: str, ntriples: str, *, root: bool = False) -> Page:
    """Reduce a stored page's N-Triples to a :class:`Page`."""
    subject_keys = {_key(iri)}
    types: set[str] = set()
    container_types: set[str] = set()
    contains: dict[str, list[str]] = defaultdict(list)
    relation: dict[str, str] = {}
    # Every link from the page's subject to an IRI; only those with a navigation,
    # DCAT or member-relation predicate are kept once the relations are known.
    own_links: list[tuple[str, str]] = []
    described: set[str] = set()
    access = False
    for line in ntriples.split("\n"):
        match = _LINE.match(line)
        if match is None:
            continue
        s, p, o = match.groups()
        if not s.startswith("<"):
            continue
        s_iri = s[1:-1]
        described.add(_key(s_iri))
        o_iri = o[1:-1] if o.startswith("<") and o.endswith(">") else None
        own = _key(s_iri) in subject_keys
        if own and o_iri is not None and p != RDF_TYPE:
            own_links.append((p, o_iri))
        if p not in _KEPT:
            continue
        if p == RDF_TYPE and o_iri is not None:
            if own:
                types.add(o_iri)
            if o_iri in CONTAINER_TYPES:
                container_types.add(s_iri)
        elif p == LDP_CONTAINS and o_iri is not None:
            contains[s_iri].append(o_iri)
        elif p == LDP_MEMBER_RELATION and o_iri is not None:
            relation[s_iri] = o_iri
        elif own and p in _ACCESS:
            access = True
    wanted = NAVIGATION | DCAT_LINKS | {_DCAT + "accessService"} | set(relation.values())
    links = [(p, o) for p, o in own_links if p in wanted]
    level = "fdp" if root else None
    if level is None:
        for name, wanted in _TYPES:
            if types & wanted:
                level = name
                break
    containers = {
        c: (relation.get(c), contains.get(c, [])) for c in container_types | set(contains)
    }
    return Page(iri, level, containers, links, described, access)


@dataclass
class WalkSummary:
    """What the walk that stored the pages reported (``HarvestWalk`` at its end)."""

    failed: Mapping[str, str] = field(default_factory=lambda: {})
    unread: int = 0
    off_site: int = 0
    at_ceiling: bool = False
    complete: bool | None = None


def analyse(
    client_url: str,
    pages: Iterable[Page],
    walk: WalkSummary,
    *,
    origins: Iterable[str] = (),
) -> dict[str, Any]:
    """The health report of one FDP from its stored pages and the last walk."""
    allowed = {_origin(client_url), *(_origin(o) for o in origins)}
    read: dict[str, Page] = {}
    for page in pages:
        read[_key(page.iri)] = page
    members: set[str] = set()
    for page in read.values():
        for _relation, contained in page.containers.values():
            members.update(_key(m) for m in contained)
    failed_keys = {_key(iri) for iri in walk.failed}

    levels: dict[str, int] = dict.fromkeys((*LEVELS, "other"), 0)
    examples: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)

    def found(code: str, iri: str) -> None:
        counts[code] += 1
        if len(examples[code]) < EXAMPLES:
            examples[code].append(iri)

    for key, page in read.items():
        levels[page.level or "other"] += 1
        linked = {_key(target) for _p, target in page.links}
        for relation, contained in page.containers.values():
            for member in contained:
                member_key = _key(member)
                has_link = any(
                    _key(target) == member_key and (relation is None or p == relation)
                    for p, target in page.links
                )
                if not has_link:
                    found("not-linked-in-dcat", member)
        for predicate, target in page.links:
            target_key = _key(target)
            if (
                predicate in DCAT_LINKS
                and target_key not in read
                and target_key not in members
                and target_key not in failed_keys
                and target_key not in page.described
                and target_key != key
                and _origin(target) in allowed
            ):
                found("linked-only-in-dcat", target)
        children = linked | {
            _key(m) for _r, contained in page.containers.values() for m in contained
        }
        children.discard(key)
        if page.level == "catalog" and not children:
            found("catalog-without-datasets", page.iri)
        elif (
            page.level == "dataset"
            and not any(p == _DCAT + "distribution" for p, _t in page.links)
            and not any(read.get(c) and read[c].level == "distribution" for c in children)
        ):
            found("dataset-without-distributions", page.iri)
        elif page.level == "distribution" and not page.access:
            found("distribution-without-access", page.iri)

    failures: dict[str, int] = defaultdict(int)
    failure_examples: dict[str, list[str]] = defaultdict(list)
    for iri, reason in walk.failed.items():
        kind = failure_kind(reason)
        failures[kind] += 1
        if len(failure_examples[kind]) < EXAMPLES:
            failure_examples[kind].append(iri)

    reached = [name for name in LEVELS if levels[name]]
    reach = reached[-1] if reached else None
    findings = [
        {"code": code, "count": counts[code], "examples": examples[code]}
        for code in FINDINGS
        if counts[code]
    ]
    notes = [
        {"code": code, "count": counts[code], "examples": examples[code]}
        for code in NOTES
        if counts[code]
    ]
    failure_list = [
        {"kind": kind, "count": failures[kind], "examples": failure_examples[kind]}
        for kind in FAILURE_KINDS
        if failures[kind]
    ]
    if not read:
        status = "unreadable"
    elif findings or failure_list:
        status = "findings"
    else:
        status = "fine"
    return {
        "reach": reach,
        "status": status,
        "levels": levels,
        "pages": len(read),
        "findings": findings,
        "notes": notes,
        "failures": failure_list,
        "stops": {
            "offSite": walk.off_site,
            "unread": walk.unread,
            "atCeiling": walk.at_ceiling,
        },
        "complete": walk.complete,
    }
