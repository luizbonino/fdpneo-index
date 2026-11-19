"""Concept labels from vocabularies (TASKS 8.6, ADR-0006).

Harvested metadata names concepts by IRI: a MeSH descriptor as a subject, a
Wikidata item as a theme, a ROR organisation as a publisher. When the FDP does
not label them itself and the index has no bundled label, they show as IRIs
and cannot be found by name. The resolver asks the concepts' own
vocabularies:

1. **Discover**: IRI values of the profile's concept fields (IRI and agent
   fields that are facets or searched) become ``concept`` rows, shared by all
   FDPs. Concepts with a bundled label, and hosts not on the allow-list, are
   marked ``skipped``.
2. **Fetch**, in the background and politely (one host at a time, a few
   requests per second), through ``OutboundClient`` with the allow-list
   checked on every redirect. Each vocabulary through what it serves best:

   * Wikidata: ``wbgetentities`` (labels and aliases of 50 items per request
     in the configured languages); "subclass of" (P279) as broader concepts;
   * ROR: its JSON API;
   * OBO ontologies and EDAM: EBI's Ontology Lookup Service;
   * any other allow-listed host: the IRI itself, as RDF by content
     negotiation. Labels come from ``skos:prefLabel``, ``rdfs:label``,
     ``dct:title``, ``dc:title``, ``schema:name``, ``foaf:name`` and their
     like (``fdpindex.rdf.LABEL_PREDICATES``), synonyms from
     ``skos:altLabel`` and its like, broader concepts from ``skos:broader``,
     MeSH's ``broaderDescriptor`` or ``rdfs:subClassOf``.

   Broader concepts are resolved too, up to ``broader_depth`` levels.
3. **Use**: labels show on facets and resource pages; labels, synonyms and
   broader concepts' labels go into the full-text vectors (search
   ``kanker``, find the dataset whose subject is Wikidata's "breast cancer").
   The entries that use a concept that changed are projected again.

A resolved concept is refreshed after ``refresh_days``; a miss or a failure
is tried again after ``retry_days``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import structlog
from sqlalchemy import delete, exists, select, update

from fdpindex.db import utcnow
from fdpindex.models import Concept, ConceptLabel, ConceptStatus, SearchValue
from fdpindex.outbound import OutboundError
from fdpindex.rdf import FORMATS, ConceptNames, RdfParseError, describe, parse_rdf
from fdpindex.search.project import ConceptText, pick_language
from fdpindex.search.vocab import bundled_label

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.outbound import OutboundClient
    from fdpindex.search.profile import Profile

log = structlog.get_logger(__name__)

BATCH = 100
MAX_SYNONYMS = 12
MAX_BROADER = 8
MAX_MATCHES = 8
# Which IRI of a group of equivalent concepts represents it: domain vocabularies first
# (their labels read as terms: "Dementia"), Wikidata last ("dementia").
_PREFERRED_HOSTS = (
    "id.nlm.nih.gov",
    "purl.obolibrary.org",
    "edamontology.org",
    "www.ebi.ac.uk",
    "ror.org",
    "orcid.org",
    "publications.europa.eu",
)
ACCEPT_RDF = (
    "text/turtle, application/rdf+xml;q=0.9, application/n-triples;q=0.8, "
    "application/ld+json;q=0.7, text/xml;q=0.5"
)
WIKIDATA = re.compile(r"^https?://www\.wikidata\.org/(?:entity|wiki)/(Q\d+)$")
OLS_HOSTS = frozenset({"purl.obolibrary.org", "edamontology.org"})
# Hosts the adapters call besides the concept's own (allowed with it).
API_HOSTS = {"ror.org": "api.ror.org", **dict.fromkeys(OLS_HOSTS, "www.ebi.ac.uk")}


@dataclass(frozen=True)
class ResolverSettings:
    enabled: bool = False
    hosts: frozenset[str] = frozenset()
    languages: tuple[str, ...] = ("en",)
    refresh_days: int = 30
    retry_days: int = 7
    broader_depth: int = 2
    interval_seconds: float = 60.0
    requests_per_second: float = 2.0
    timeout_seconds: float = 15.0


@dataclass
class Resolution:
    """What one fetch found for one concept."""

    status: ConceptStatus
    source: str
    names: ConceptNames = field(default_factory=ConceptNames)
    error: str | None = None


class _Throttled(Exception):  # a condition, not an error of ours
    """The host answered 429: leave its concepts due and pause it."""

    def __init__(self, host: str, seconds: float) -> None:
        super().__init__(host)
        self.host = host
        self.seconds = seconds


def _retry_after(value: str | None) -> float:
    try:
        return min(max(float(value or 300), 1.0), 3600.0)
    except ValueError:
        return 300.0


def concept_fields(profile: Profile) -> list[str]:
    """Fields whose IRI values are concepts: IRI and agent fields that are facets or searched."""
    return [
        name
        for name, spec in profile.fields.items()
        if spec.kind in {"iri", "agent"} and (spec.facet is not None or spec.weight is not None)
    ]


def _host(iri: str) -> str:
    return (urlsplit(iri).hostname or "").lower()


class ConceptResolver:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        outbound: OutboundClient,
        profile: Profile,
        settings: ResolverSettings,
        *,
        on_changed: Callable[[UUID], None],
    ) -> None:
        self._session_factory = session_factory
        self._outbound = outbound
        self._fields = concept_fields(profile)
        self.settings = settings
        self._on_changed = on_changed
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        # Hosts that answered 429, until when (loop time): their concepts wait.
        self._paused: dict[str, float] = {}

    # --- loop -----------------------------------------------------------------

    def set_profile(self, profile: Profile) -> None:
        """The concept fields of a changed profile (TASKS 6.12)."""
        self._fields = concept_fields(profile)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="concept-resolver")

    def notify(self) -> None:
        """New harvested values may be waiting (called after projections)."""
        self._wake.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                while await self.sync_once():
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("concept_resolver_failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.settings.interval_seconds)

    async def sync_once(self) -> int:
        """Discover new concepts and fetch one batch of due ones; return how many were fetched."""
        await self.discover()
        if not self.settings.enabled:
            return 0
        now = utcnow()
        clock = asyncio.get_running_loop().time()
        paused = [host for host, until in self._paused.items() if until > clock]
        async with self._session_factory() as session:
            due = list(
                await session.scalars(
                    select(Concept)
                    .where(
                        Concept.status != ConceptStatus.SKIPPED,
                        Concept.due_at <= now,
                        Concept.host.not_in(paused),
                    )
                    .order_by(Concept.depth, Concept.due_at)
                    .limit(BATCH)
                )
            )
        if not due:
            return 0
        results = await self._fetch_all(due)
        changed = await self._store([c for c in due if c.iri in results], results)
        await self._reproject(changed)
        return len(results)

    # --- discovery ------------------------------------------------------------

    async def discover(self) -> int:
        """Record concept IRIs used in harvested metadata that have no ``concept`` row yet."""
        hosts = self.settings.hosts
        added = 0
        async with self._session_factory() as session, session.begin():
            # A host added to the allow-list: its skipped concepts become due.
            await session.execute(
                update(Concept)
                .where(
                    Concept.status == ConceptStatus.SKIPPED,
                    Concept.source.is_(None),
                    Concept.host.in_(hosts),
                )
                .values(status=ConceptStatus.PENDING, due_at=utcnow())
            )
            while True:
                values = list(
                    await session.scalars(
                        select(SearchValue.value)
                        .where(
                            SearchValue.field.in_(self._fields),
                            SearchValue.value.like("http%"),
                            ~exists().where(Concept.iri == SearchValue.value),
                        )
                        .distinct()
                        .limit(1000)
                    )
                )
                if not values:
                    break
                for iri in values:
                    session.add(self._new(str(iri), depth=0))
                await session.flush()
                added += len(values)
        if added:
            log.info("concepts_discovered", count=added)
        return added

    def _new(self, iri: str, *, depth: int) -> Concept:
        host = _host(iri)
        now = utcnow()
        if bundled_label(iri):
            return Concept(
                iri=iri,
                host=host,
                status=ConceptStatus.SKIPPED,
                source="bundled",
                depth=depth,
                broader=[],
                due_at=now,
            )
        status = ConceptStatus.PENDING if host in self.settings.hosts else ConceptStatus.SKIPPED
        return Concept(iri=iri, host=host, status=status, depth=depth, broader=[], due_at=now)

    # --- fetching ---------------------------------------------------------------

    async def _fetch_all(self, concepts: Sequence[Concept]) -> dict[str, Resolution]:
        results: dict[str, Resolution] = {}
        wikidata = {c.iri: m.group(1) for c in concepts if (m := WIKIDATA.match(c.iri))}
        if wikidata:
            results |= await self._wikidata(wikidata, {c.iri: c.depth for c in concepts})
        for concept in concepts:
            if concept.iri in results or self._is_paused(concept.host):
                continue
            try:
                if concept.host == "ror.org":
                    results[concept.iri] = await self._ror(concept.iri)
                elif concept.host in OLS_HOSTS:
                    results[concept.iri] = await self._ols(concept.iri)
                else:
                    results[concept.iri] = await self._rdf(concept.iri)
            except OutboundError as err:
                results[concept.iri] = Resolution(ConceptStatus.FAILED, "http", error=err.message)
            except _Throttled as throttled:
                self._pause(throttled)
            await asyncio.sleep(1 / self.settings.requests_per_second)
        return results

    def _is_paused(self, host: str) -> bool:
        return self._paused.get(host, 0.0) > asyncio.get_running_loop().time()

    def _pause(self, throttled: _Throttled) -> None:
        self._paused[throttled.host] = asyncio.get_running_loop().time() + throttled.seconds
        log.warning("concept_host_paused", host=throttled.host, seconds=throttled.seconds)

    async def _get(self, url: str, *, accept: str, host: str) -> tuple[int, str, str]:
        allowed = {host, API_HOSTS.get(host, host)}
        response = await self._outbound.request(
            "GET",
            url,
            timeout_seconds=self.settings.timeout_seconds,
            headers={"Accept": accept},
            allowed_hosts=allowed,
        )
        if response.status == 429:
            raise _Throttled(host, _retry_after((response.headers.get("retry-after") or [None])[0]))
        if response.truncated:
            raise OutboundError("too_large", "response too large")
        return response.status, response.content_type, response.body

    def _keep(self, lang: str) -> bool:
        return lang == "" or lang in self.settings.languages

    async def _wikidata(
        self, items: dict[str, str], depths: Mapping[str, int]
    ) -> dict[str, Resolution]:
        results: dict[str, Resolution] = {}
        by_id = {qid: iri for iri, qid in items.items()}
        ids = list(by_id)
        languages = "|".join(self.settings.languages)
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            url = (
                "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
                f"&props=labels%7Caliases&languages={quote(languages)}&ids={'%7C'.join(chunk)}"
            )
            try:
                status, _, body = await self._get(
                    url, accept="application/json", host="www.wikidata.org"
                )
                entities: dict[str, Any] = (
                    json.loads(body).get("entities", {}) if status == 200 else {}
                )
            except _Throttled as throttled:
                self._pause(throttled)
                break
            except (OutboundError, ValueError) as err:
                for qid in chunk:
                    results[by_id[qid]] = Resolution(
                        ConceptStatus.FAILED, "wikidata", error=str(err)
                    )
                continue
            for qid in chunk:
                entity = entities.get(qid) or {}
                names = ConceptNames()
                for lang, item in (entity.get("labels") or {}).items():
                    if self._keep(lang):
                        names.labels[lang] = str(item.get("value", ""))
                for lang, aliases in (entity.get("aliases") or {}).items():
                    if self._keep(lang):
                        names.synonyms[lang] = [str(a.get("value", "")) for a in aliases][
                            :MAX_SYNONYMS
                        ]
                results[by_id[qid]] = Resolution(
                    ConceptStatus.RESOLVED if names.labels else ConceptStatus.MISSING,
                    "wikidata",
                    names,
                )
            await asyncio.sleep(1 / self.settings.requests_per_second)
        # "Subclass of" as broader concepts, only where they will be resolved too.
        for iri, result in results.items():
            if (
                result.status is not ConceptStatus.RESOLVED
                or depths.get(iri, 0) >= self.settings.broader_depth
            ):
                continue
            url = (
                "https://www.wikidata.org/w/api.php?action=wbgetclaims&format=json"
                f"&property=P279&entity={items[iri]}"
            )
            try:
                status, _, body = await self._get(
                    url, accept="application/json", host="www.wikidata.org"
                )
                claims = json.loads(body).get("claims", {}).get("P279", []) if status == 200 else []
            except _Throttled as throttled:
                self._pause(throttled)
                claims = []
            except (OutboundError, ValueError):
                claims = []
            for claim in claims[:MAX_BROADER]:
                value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
                if isinstance(value, dict) and str(value.get("id", "")).startswith("Q"):
                    result.names.broader.append(f"http://www.wikidata.org/entity/{value['id']}")
            await asyncio.sleep(1 / self.settings.requests_per_second)
        # The MeSH descriptor of the item (P486): the same concept in MeSH (TASKS 8.11).
        for iri, result in results.items():
            if result.status is not ConceptStatus.RESOLVED:
                continue
            url = (
                "https://www.wikidata.org/w/api.php?action=wbgetclaims&format=json"
                f"&property=P486&entity={items[iri]}"
            )
            try:
                status, _, body = await self._get(
                    url, accept="application/json", host="www.wikidata.org"
                )
                claims = json.loads(body).get("claims", {}).get("P486", []) if status == 200 else []
            except _Throttled as throttled:
                self._pause(throttled)
                break
            except (OutboundError, ValueError):
                claims = []
            for claim in claims[:MAX_MATCHES]:
                value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if isinstance(value, str) and re.fullmatch(r"[A-Z]\d{6,9}", value):
                    result.names.matches.append(f"http://id.nlm.nih.gov/mesh/{value}")
            await asyncio.sleep(1 / self.settings.requests_per_second)
        return results

    async def _ror(self, iri: str) -> Resolution:
        identifier = urlsplit(iri).path.strip("/")
        status, _, body = await self._get(
            f"https://api.ror.org/v2/organizations/{quote(identifier)}",
            accept="application/json",
            host="ror.org",
        )
        if status != 200:
            return Resolution(ConceptStatus.MISSING, "ror", error=f"HTTP {status}")
        names = ConceptNames()
        for name in json.loads(body).get("names", []):
            lang = str(name.get("lang") or "")
            value = str(name.get("value") or "")
            types = name.get("types") or []
            if not value or not self._keep(lang):
                continue
            if "ror_display" in types:
                names.labels[""] = value
            if "label" in types:
                names.labels.setdefault(lang, value)
            elif "alias" in types or "acronym" in types:
                names.synonyms.setdefault(lang, []).append(value)
        return Resolution(
            ConceptStatus.RESOLVED if names.labels else ConceptStatus.MISSING, "ror", names
        )

    async def _ols(self, iri: str) -> Resolution:
        status, _, body = await self._get(
            f"https://www.ebi.ac.uk/ols4/api/terms?iri={quote(iri, safe='')}",
            accept="application/json",
            host=_host(iri),
        )
        terms = (
            ((json.loads(body).get("_embedded") or {}).get("terms") or []) if status == 200 else []
        )
        if not terms:
            return Resolution(
                ConceptStatus.MISSING, "ols", error=f"HTTP {status}" if status != 200 else None
            )
        term = next((t for t in terms if t.get("is_defining_ontology")), terms[0])
        names = ConceptNames()
        if term.get("label"):
            names.labels[""] = str(term["label"])
        names.synonyms[""] = [str(s) for s in (term.get("synonyms") or [])][:MAX_SYNONYMS]
        return Resolution(
            ConceptStatus.RESOLVED if names.labels else ConceptStatus.MISSING, "ols", names
        )

    async def _rdf(self, iri: str) -> Resolution:
        status, content_type, body = await self._get(iri, accept=ACCEPT_RDF, host=_host(iri))
        if status != 200:
            return Resolution(ConceptStatus.MISSING, "rdf", error=f"HTTP {status}")
        if content_type not in FORMATS and content_type not in {"text/xml", "application/xml"}:
            return Resolution(ConceptStatus.MISSING, "rdf", error=f"not RDF: {content_type}")
        try:
            graph = await asyncio.to_thread(
                parse_rdf,
                body,
                "application/rdf+xml"
                if content_type in {"text/xml", "application/xml"}
                else content_type,
                iri,
            )
        except RdfParseError as err:
            return Resolution(ConceptStatus.MISSING, "rdf", error=f"cannot parse: {err}"[:500])
        names = await asyncio.to_thread(describe, graph, iri)
        names.labels = {k: v for k, v in names.labels.items() if self._keep(k)}
        names.synonyms = {k: v[:MAX_SYNONYMS] for k, v in names.synonyms.items() if self._keep(k)}
        return Resolution(
            ConceptStatus.RESOLVED if names.labels else ConceptStatus.MISSING, "rdf", names
        )

    # --- storing ---------------------------------------------------------------

    async def _store(
        self, concepts: Sequence[Concept], results: Mapping[str, Resolution]
    ) -> set[str]:
        """Write what was found; return the IRIs whose labels changed."""
        now = utcnow()
        changed: set[str] = set()
        async with self._session_factory() as session, session.begin():
            for concept in concepts:
                result = results[concept.iri]
                row = await session.get(Concept, concept.iri)
                if row is None:
                    continue
                before = {
                    (k, lang, label)
                    for k, lang, label in await session.execute(
                        select(ConceptLabel.kind, ConceptLabel.lang, ConceptLabel.label).where(
                            ConceptLabel.iri == concept.iri
                        )
                    )
                }
                if result.status in (ConceptStatus.RESOLVED, ConceptStatus.MISSING):
                    after = {
                        ("pref", lang, label[:512])
                        for lang, label in result.names.labels.items()
                        if label
                    }
                    after |= {
                        ("alt", lang, label[:512])
                        for lang, labels in result.names.synonyms.items()
                        for label in labels
                        if label
                    }
                    if after != before:
                        changed.add(concept.iri)
                        await session.execute(
                            delete(ConceptLabel).where(ConceptLabel.iri == concept.iri)
                        )
                        session.add_all(
                            ConceptLabel(iri=concept.iri, kind=k, lang=lang[:32], label=label)
                            for k, lang, label in after
                        )
                    broader = list(dict.fromkeys(result.names.broader))[:MAX_BROADER]
                    if broader != row.broader:
                        changed.add(concept.iri)
                    row.broader = broader
                    matches = list(dict.fromkeys(result.names.matches))[:MAX_MATCHES]
                    if matches != (row.exact_matches or []):
                        changed.add(concept.iri)
                    row.exact_matches = matches
                    days = (
                        self.settings.refresh_days
                        if result.status is ConceptStatus.RESOLVED
                        else self.settings.retry_days
                    )
                else:
                    days = self.settings.retry_days
                row.status = result.status
                row.source = result.source
                row.error = result.error[:512] if result.error else None
                row.fetched_at = now
                row.due_at = now + timedelta(days=days)
                if row.depth < self.settings.broader_depth:
                    for parent in row.broader:
                        if await session.get(Concept, parent) is None:
                            session.add(self._new(parent, depth=row.depth + 1))
                            await session.flush()
        if changed:
            log.info("concepts_resolved", changed=len(changed), fetched=len(concepts))
        return changed

    async def _reproject(self, changed: set[str]) -> None:
        """Project again the entries that use a changed concept, or one below it."""
        if not changed:
            return
        async with self._session_factory() as session:
            affected = set(changed)
            # Concepts whose broader chain reaches a changed one carry its labels too.
            rows = (await session.execute(select(Concept.iri, Concept.broader))).all()
            parents = {str(iri): [str(b) for b in broader or []] for iri, broader in rows}
            for _ in range(self.settings.broader_depth):
                more = {iri for iri, ups in parents.items() if affected.intersection(ups)}
                if more <= affected:
                    break
                affected |= more
            entries: set[UUID] = set()
            values = list(affected)
            for start in range(0, len(values), 500):
                entries.update(
                    await session.scalars(
                        select(SearchValue.entry_uuid)
                        .where(SearchValue.value.in_(values[start : start + 500]))
                        .distinct()
                    )
                )
        for entry in entries:
            self._on_changed(entry)


# --- reading -------------------------------------------------------------------------


@dataclass
class ConceptGroups:
    """Equivalent concepts (TASKS 8.11): each IRI's representative, and each group."""

    canonical: dict[str, str] = field(default_factory=lambda: {})
    members: dict[str, list[str]] = field(default_factory=lambda: {})

    def of(self, iri: str) -> str:
        return self.canonical.get(iri, iri)


def _rank(iri: str, depth: int) -> tuple[int, int, str]:
    host = urlsplit(iri).hostname or ""
    preferred = _PREFERRED_HOSTS.index(host) if host in _PREFERRED_HOSTS else len(_PREFERRED_HOSTS)
    return (depth, preferred, iri)


async def concept_parents(session: AsyncSession, groups: ConceptGroups) -> dict[str, list[str]]:
    """Each concept's broader concepts, as groups' representatives (TASKS 8.12)."""
    parents: dict[str, list[str]] = {}
    for iri, broader in (await session.execute(select(Concept.iri, Concept.broader))).all():
        node = groups.of(str(iri))
        ups = parents.setdefault(node, [])
        for parent in broader or []:
            up = groups.of(str(parent))
            if up != node and up not in ups:
                ups.append(up)
    return parents


def descendants(parents: Mapping[str, list[str]], iri: str, *, limit: int = 500) -> list[str]:
    """``iri`` and every concept below it, nearest first."""
    children: dict[str, list[str]] = {}
    for child, ups in parents.items():
        for up in ups:
            children.setdefault(up, []).append(child)
    found, frontier = [iri], [iri]
    seen = {iri}
    while frontier and len(found) < limit:
        nxt: list[str] = []
        for node in frontier:
            for child in sorted(children.get(node, [])):
                if child not in seen:
                    seen.add(child)
                    found.append(child)
                    nxt.append(child)
        frontier = nxt
    return found[:limit]


async def concept_groups(session: AsyncSession) -> ConceptGroups:
    """Group concepts linked by exact matches, either way round (union-find).

    A group's representative is one of its known concepts: those used as
    values first (depth 0), then by vocabulary preference. A match to an IRI
    the index has not seen joins the group but never represents it.
    """
    rows = (await session.execute(select(Concept.iri, Concept.exact_matches, Concept.depth))).all()
    linked = [
        (str(iri), [str(m) for m in matches or []], int(depth)) for iri, matches, depth in rows
    ]
    if not any(matches for _, matches, _ in linked):
        return ConceptGroups()
    known = {iri: depth for iri, _, depth in linked}
    parent: dict[str, str] = {}

    def find(iri: str) -> str:
        parent.setdefault(iri, iri)
        while parent[iri] != iri:
            parent[iri] = parent[parent[iri]]
            iri = parent[iri]
        return iri

    for iri, matches, _ in linked:
        for match in matches:
            parent[find(iri)] = find(match)
    groups: dict[str, list[str]] = {}
    for iri in list(parent):
        groups.setdefault(find(iri), []).append(iri)
    result = ConceptGroups()
    for members in groups.values():
        candidates = [m for m in members if m in known]
        if len(members) < 2 or not candidates:
            continue
        representative = min(candidates, key=lambda m: _rank(m, known[m]))
        ordered = sorted(members)
        for member in members:
            result.canonical[member] = representative
        result.members[representative] = ordered
    return result


async def concept_texts(
    session: AsyncSession, iris: Iterable[str], *, depth: int = 2
) -> dict[str, ConceptText]:
    """Labels, synonyms and broader concepts' labels for ``iris``, for the projection."""
    wanted = list(dict.fromkeys(iris))
    labels: dict[str, dict[str, str]] = {}
    synonyms: dict[str, dict[str, list[str]]] = {}
    broader: dict[str, list[str]] = {}

    async def load(batch: list[str]) -> list[str]:
        ups: list[str] = []
        for start in range(0, len(batch), 500):
            chunk = batch[start : start + 500]
            for iri, kind, lang, label in await session.execute(
                select(
                    ConceptLabel.iri, ConceptLabel.kind, ConceptLabel.lang, ConceptLabel.label
                ).where(ConceptLabel.iri.in_(chunk))
            ):
                if kind == "pref":
                    labels.setdefault(str(iri), {})[str(lang)] = str(label)
                else:
                    synonyms.setdefault(str(iri), {}).setdefault(str(lang), []).append(str(label))
            for iri, parents in await session.execute(
                select(Concept.iri, Concept.broader).where(Concept.iri.in_(chunk))
            ):
                broader[str(iri)] = [str(p) for p in parents or []]
                ups.extend(broader[str(iri)])
        return [u for u in dict.fromkeys(ups) if u not in labels and u not in broader]

    level = wanted
    for _ in range(depth + 1):
        if not level:
            break
        level = await load(level)

    def ancestors(iri: str) -> tuple[list[tuple[str, str]], list[str]]:
        found: list[tuple[str, str]] = []
        iris: list[str] = []
        frontier, seen = [iri], {iri}
        for _ in range(depth):
            nxt: list[str] = []
            for node in frontier:
                for parent in broader.get(node, []):
                    if parent in seen:
                        continue
                    seen.add(parent)
                    nxt.append(parent)
                    # One label per broader concept: English or untagged first.
                    chosen = pick_language(labels.get(parent, {}), "en")
                    if chosen is not None:
                        found.append(chosen)
                        iris.append(parent)
            frontier = nxt
        return found, iris

    return {
        iri: ConceptText(
            labels=labels.get(iri, {}),
            synonyms=synonyms.get(iri, {}),
            broader=(up := ancestors(iri))[0],
            ancestors=up[1],
        )
        for iri in wanted
        if iri in labels or iri in synonyms
    }


async def resolved_labels(
    session_factory: async_sessionmaker[AsyncSession], iris: Iterable[str], lang: str = "en"
) -> dict[str, str]:
    """Preferred labels of resolved concepts, in ``lang`` when there is one."""
    wanted = list(dict.fromkeys(iris))
    found: dict[str, dict[str, str]] = {}
    async with session_factory() as session:
        for start in range(0, len(wanted), 500):
            for iri, label_lang, label in await session.execute(
                select(ConceptLabel.iri, ConceptLabel.lang, ConceptLabel.label).where(
                    ConceptLabel.iri.in_(wanted[start : start + 500]), ConceptLabel.kind == "pref"
                )
            ):
                found.setdefault(str(iri), {})[str(label_lang)] = str(label)
    picked = {iri: pick_language(texts, lang) for iri, texts in found.items()}
    return {iri: choice[1] for iri, choice in picked.items() if choice}


async def concept_stats(session: AsyncSession) -> dict[str, int]:
    from sqlalchemy import func

    rows = await session.execute(select(Concept.status, func.count()).group_by(Concept.status))
    return {str(getattr(status, "value", status)): int(n) for status, n in rows}
