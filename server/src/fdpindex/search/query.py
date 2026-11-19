"""Faceted search over the projection (search design §3.6, §3.9, §4, §5).

Filters are ``EXISTS`` conditions on ``search_value`` (OR within a field,
AND across fields, ``!value`` negates); facet counts are disjunctive (a
facet's own filter is left out of its count). Text matching and ranking use
the PostgreSQL vectors; on SQLite (unit tests) words are matched with
``LIKE`` and hits are sorted by date.

Every PostgreSQL query runs under ``statement_timeout``; a timeout is a 503.

Facet counts without words or date ranges read ``search_value`` alone (its
copies of the resource's kind and entry, migration 0004); field filters then
correlate with the value's resource id. With words or dates they join the
resources. Facet counts and totals of such queries are cached in the process
per indexer generation for ``FACET_CACHE_SECONDS``. Above ``SAMPLE_ABOVE``
matches with words or dates, facets are counted on a page sample and marked
``approximate`` (search design §7); below it, over the exact matching ids.

Deviations from the design: that in-process cache instead of a
``search_facet_cache`` table (the performance check, task 6.15, found the
unfiltered views need it; a table would add a refresh job for no gain on one
replica); no keyset cursor, highlights or duplicate grouping (Phases 8 and 9).
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from rdflib import Graph
from sqlalchemy import (
    REAL,
    BigInteger,
    ColumnElement,
    and_,
    any_,
    bindparam,
    case,
    cast,
    exists,
    func,
    literal,
    not_,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased

from fdpindex.db import utcnow
from fdpindex.errors import BadRequest, IndexServiceError, NotFound
from fdpindex.models import (
    ConceptLabel,
    EntryPermit,
    EntryState,
    HarvestedNode,
    IndexEntry,
    SearchEntryState,
    SearchLabel,
    SearchProperty,
    SearchResource,
    SearchValue,
)
from fdpindex.search.concepts import (
    ConceptGroups,
    concept_groups,
    concept_parents,
    concept_stats,
    descendants,
)
from fdpindex.search.profile import COLUMN_FIELDS, KINDS, Kind, Profile
from fdpindex.search.profile_store import ProfileStore
from fdpindex.search.project import (
    SIMPLE_CONFIG,
    bucket_label,
    is_property_field,
    pick_language,
    property_field,
    ts_config,
)
from fdpindex.search.settings import (
    PROPERTY_FACET,
    SearchSettings,
    SearchSettingsService,
    effective_facets,
    is_property_iri,
)
from fdpindex.search.to_sparql import to_sparql
from fdpindex.search.vocab import BUNDLED, bundled_label, bundled_labels
from fdpindex.service import api_state
from fdpindex.sparql.labels import humanize
from fdpindex.sparql.prefixes import PREFIXES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.search.indexer import SearchIndexer
    from fdpindex.settings import SettingsService

log = structlog.get_logger(__name__)

R = SearchResource
E = IndexEntry
V = SearchValue


@dataclass(frozen=True)
class _Scope:
    """The id, kind and entry columns conditions apply to."""

    id: Any
    kind: Any
    entry_uuid: Any


RESOURCE = _Scope(R.id, R.kind, R.entry_uuid)
VALUE = _Scope(V.resource_id, V.kind, V.entry_uuid)
FACET_CACHE_SECONDS = 60.0
FACET_CACHE_ENTRIES = 512
# Above this many matches, facets are counted on a page sample of about SAMPLE_SIZE.
SAMPLE_ABOVE = 50_000
SAMPLE_SIZE = 10_000
# A page sample reads its share of the whole table, whatever the matches; above
# this share every k-th id is taken instead (one pass over the matches).
MAX_PAGE_PERCENT = 2.0
# Above SAMPLE_ABOVE matches, relevance is computed for the most recently
# modified RANK_CANDIDATES of them; paging stops there (the response says so).
RANK_CANDIDATES = 5_000
RANDOM_PAGE_COST = 4.0


@dataclass
class _IdSet:
    """The resources a facet counts over: all matches, or a page sample of them.

    ``rows`` are ``(id, kind, entry)``: the type and FDP facets count them here.
    """

    rows: list[tuple[int, str, UUID]]
    total: int
    scale: float = 1.0
    approximate: bool = False
    # How it was sampled: TABLESAMPLE pages (percent), or every k-th id (step).
    percent: float | None = None
    step: int = 1

    @property
    def ids(self) -> list[int]:
        return [rid for rid, _, _ in self.rows]


@dataclass
class _Matches:
    """Matching ids of a search with words or dates, found once per request (PostgreSQL).

    Facets then count by primary key over these ids instead of evaluating the
    words for every value row. One set per set of conditions: a facet whose own
    filter is active leaves it out. Above ``SAMPLE_ABOVE`` matches the set is a
    ``TABLESAMPLE SYSTEM`` page sample (fixed seed, so counts do not change from
    page to page) and counts are scaled up (search design §7).
    """

    sets: dict[str | None, _IdSet] = field(default_factory=lambda: {})


DEFAULT_TYPES: tuple[Kind, ...] = ("fdp", "catalog", "dataset", "series", "service", "other")
DEFAULT_FACETS = (
    "type",
    "theme",
    "subject",
    "keyword",
    "publisher",
    "format",
    "license",
    "accessRights",
    "fdp",
)
SORTS = ("relevance", "modified", "issued", "title", "harvested")
MAX_Q = 512
MAX_SIZE = 100
MAX_WINDOW = 10_000
MAX_FACETS = 24
PROPERTY_LIST_SECONDS = 300
MAX_FACET_SIZE = 50
MAX_FILTER_VALUES = 50
KIND_BOOST = {
    "dataset": 1.0,
    "series": 1.0,
    "catalog": 0.9,
    "service": 0.9,
    "fdp": 0.8,
    "distribution": 0.7,
    "other": 0.6,
}
RANK_WEIGHTS = "{0.1, 0.2, 0.4, 1.0}"  # D, C, B, A
DISTRIBUTIONS_PER_HIT = 5


class SearchBusy(IndexServiceError):
    code = "index.search_busy"
    http_status = 503


@dataclass
class SearchParams:
    q: str = ""
    types: list[Kind] = field(default_factory=lambda: list(DEFAULT_TYPES))
    fdps: list[UUID] = field(default_factory=lambda: [])
    filters: dict[str, list[str]] = field(default_factory=lambda: {})
    ranges: dict[str, tuple[datetime | None, datetime | None]] = field(default_factory=lambda: {})
    fdp_state: str | None = None
    lang: str = "en"
    sort: str = ""
    page: int = 0
    size: int = 20
    # None: the index's default facets (search settings); names as in the API.
    facets: list[str] | None = None
    facet_size: int = 20
    # Mark the words of ``q`` in titles and descriptions (TASKS 8.1).
    highlight: bool = False

    def validate(self, profile: Profile, properties: frozenset[str] = frozenset()) -> None:
        """Check the parameters; ``properties`` are the ``p:`` fields resolved for this request."""
        self.q = self.q.strip()
        if len(self.q) > MAX_Q:
            raise BadRequest(f"q is longer than {MAX_Q} characters")
        unknown = [t for t in self.types if t not in KINDS]
        if unknown:
            raise BadRequest(f"Unknown type: {', '.join(unknown)}")
        if not self.types:
            self.types = list(DEFAULT_TYPES)
        facets = set(profile.facets) | properties | set(profile.hierarchies)
        if self.facets is None:
            self.facets = list(DEFAULT_FACETS)
        for name, values in self.filters.items():
            if name not in facets:
                raise BadRequest(f"Unknown filter: {name}")
            if len(values) > MAX_FILTER_VALUES:
                raise BadRequest(f"At most {MAX_FILTER_VALUES} values per filter")
        for name in self.facets:
            if name not in facets and name not in {"type", "fdp"}:
                raise BadRequest(f"Unknown facet: {name}")
        if len(self.facets) > MAX_FACETS:
            raise BadRequest(f"At most {MAX_FACETS} facets")
        if not 1 <= self.size <= MAX_SIZE:
            raise BadRequest(f"size must be between 1 and {MAX_SIZE}")
        if self.page < 0 or (self.page + 1) * self.size > MAX_WINDOW:
            raise BadRequest(f"Pages reach at most {MAX_WINDOW} results; narrow the search")
        if not 1 <= self.facet_size <= MAX_FACET_SIZE:
            raise BadRequest(f"facetSize must be between 1 and {MAX_FACET_SIZE}")
        key, _, direction = self.sort.partition(",")
        if key and key not in SORTS:
            raise BadRequest(f"Unknown sort: {key}")
        if direction and direction not in {"asc", "desc"}:
            raise BadRequest("Sort direction is asc or desc")
        if self.fdp_state is not None and self.fdp_state.upper() != "ACTIVE":
            raise BadRequest("fdpState accepts ACTIVE only")


class Labels:
    """Labels for IRIs: harvested (in the requested language first), bundled, resolved from
    the concept's vocabulary (ADR-0006), humanised."""

    def __init__(self, session: AsyncSession, lang: str) -> None:
        self._session = session
        self._lang = lang.lower()
        self._cache: dict[str, str] = {}

    async def load(self, iris: set[str]) -> dict[str, str]:
        wanted = [iri for iri in iris if iri not in self._cache and _is_iri(iri)]
        found: dict[str, dict[str, str]] = defaultdict(dict)
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            rows = await self._session.execute(
                select(SearchLabel.iri, SearchLabel.lang, SearchLabel.label)
                .join(E, E.uuid == SearchLabel.entry_uuid)
                .where(SearchLabel.iri.in_(chunk), E.permit == EntryPermit.ACCEPTED)
            )
            for iri, lang, label in rows:
                found[str(iri)].setdefault(str(lang), str(label))
        resolved: dict[str, dict[str, str]] = defaultdict(dict)
        unlabelled = [iri for iri in wanted if iri not in found and not bundled_label(iri)]
        for start in range(0, len(unlabelled), 500):
            rows = await self._session.execute(
                select(ConceptLabel.iri, ConceptLabel.lang, ConceptLabel.label).where(
                    ConceptLabel.iri.in_(unlabelled[start : start + 500]),
                    ConceptLabel.kind == "pref",
                )
            )
            for iri, lang, label in rows:
                resolved[str(iri)][str(lang)] = str(label)
        for iri in wanted:
            picked = pick_language(found.get(iri, {}), self._lang)
            concept = pick_language(resolved.get(iri, {}), self._lang)
            self._cache[iri] = (
                picked[1]
                if picked
                else bundled_label(iri, self._lang) or (concept[1] if concept else humanize(iri))
            )
        return self._cache

    def get(self, value: str) -> str:
        return self._cache.get(value, value)


def _is_iri(value: str) -> bool:
    return value.startswith(("http://", "https://", "urn:", "mailto:"))


class SearchService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: SettingsService,
        indexer: SearchIndexer,
        *,
        timeout_seconds: float = 5.0,
        admin_timeout_seconds: float = 30.0,
        facet_cache_seconds: float = FACET_CACHE_SECONDS,
        sample_above: int = SAMPLE_ABOVE,
        sample_size: int = SAMPLE_SIZE,
        max_page_percent: float = MAX_PAGE_PERCENT,
        search_settings: SearchSettingsService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self.indexer = indexer
        self.profiles = ProfileStore(session_factory)
        self.search_settings = search_settings or SearchSettingsService(
            session_factory, lambda: indexer.profile
        )
        self._properties: dict[tuple[Any, ...], tuple[float, int, list[dict[str, Any]]]] = {}
        self._timeout = timeout_seconds
        self._admin_timeout = admin_timeout_seconds
        self._landing: tuple[tuple[int, int, str], float, dict[str, Any]] | None = None
        self._facet_cache_seconds = facet_cache_seconds
        self._sample_above = sample_above
        self._sample_size = sample_size
        self._max_page_percent = max_page_percent
        self._facet_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        self._hide_expired = False
        self._concept_groups: tuple[float, ConceptGroups] | None = None

    # --- sessions ----------------------------------------------------------------

    async def _run[T](self, admin: bool, work: Callable[[AsyncSession, bool], Awaitable[T]]) -> T:
        """Run ``work(session, postgres)`` in a read transaction with a statement timeout."""
        async with self._session_factory() as session, session.begin():
            postgres = session.get_bind().dialect.name == "postgresql"
            if postgres:
                ms = int((self._admin_timeout if admin else self._timeout) * 1000)
                await session.execute(text(f"SET LOCAL statement_timeout = {ms}"))
                # Search rows are wide (text vectors inline, ~3 KB): fetching them one by one
                # costs more than an SSD-tuned random_page_cost (1.1) suggests. With it the
                # planner walked 285 000 rows of a common theme to test a word (8.6 s)
                # instead of using the full-text index (0.4 s). Task 6.15.
                await session.execute(text(f"SET LOCAL random_page_cost = {RANDOM_PAGE_COST}"))
            try:
                return await work(session, postgres)
            except DBAPIError as err:
                if "statement timeout" in str(err).lower():
                    raise SearchBusy(
                        "The search took too long; try a narrower search in a moment."
                    ) from err
                raise

    async def _threshold(self) -> datetime:
        settings = await self._settings.get()
        # Read with the threshold, once per request: the expiry policy may hide entries.
        self._hide_expired = settings.expiry.enabled and settings.expiry.hide
        return utcnow() - settings.ping.valid_duration

    async def change_profile(self, overlay: dict[str, Any] | None) -> Profile:
        """Store an administrator's overlay (``None`` removes it) and re-project everything."""
        profile = await (
            self.profiles.save(overlay) if overlay is not None else self.profiles.delete()
        )
        if profile.hash != self.profile.hash:
            self.indexer.set_profile(profile)
            self._facet_cache.clear()
            self._properties.clear()
        return profile

    @property
    def profile(self) -> Profile:
        """The indexer's: an administrator may change it at run time (TASKS 6.12)."""
        return self.indexer.profile

    # --- facet names ---------------------------------------------------------------

    async def _prepare(self, params: SearchParams, *, admin: bool) -> dict[str, str]:
        """Resolve facet names and validate; returns ``p:`` field → API name (``prop:<IRI>``).

        Without ``facets``, the index's main facets apply (search settings).
        Property facets are open to visitors unless switched off or hidden; a
        property an administrator made a facet is open to everyone.
        """
        settings = await self.search_settings.get()
        if params.facets is None:
            params.facets = [
                "type",
                *(
                    f.name
                    for f in effective_facets(settings, self.profile)
                    if f.placement == "main"
                ),
            ]
        names: dict[str, str] = {}

        def resolve(name: str) -> str:
            if not name.startswith(PROPERTY_FACET):
                return name
            iri = name.removeprefix(PROPERTY_FACET)
            if not is_property_iri(iri):
                raise BadRequest(f"Not a property IRI: {iri[:100]}")
            if not admin and not self._open(settings, name, iri):
                raise BadRequest(f"Facets on {iri} are not offered on this index")
            key = property_field(iri)
            names[key] = name
            return key

        params.filters = {resolve(k): v for k, v in params.filters.items()}
        params.facets = [resolve(n) for n in params.facets]
        merged = [
            n
            for n in params.filters
            if (spec := self.profile.fields.get(self.profile.hierarchies.get(n, n))) is not None
            and spec.equivalents
        ]
        if merged:
            # A filter on any member of a group of equivalent concepts is the group's.
            groups = await self._groups()
            for name in merged:
                params.filters[name] = list(
                    dict.fromkeys(
                        ("!" + groups.of(v[1:])) if v.startswith("!") else groups.of(v)
                        for v in params.filters[name]
                    )
                )
        params.validate(self.profile, frozenset(names))
        return names

    async def _groups(self) -> ConceptGroups:
        """Groups of equivalent concepts (TASKS 8.11), read at most once a minute."""
        cached = self._concept_groups
        if cached is not None and time.monotonic() - cached[0] < 60:
            return cached[1]
        async with self._session_factory() as session:
            groups = await concept_groups(session)
        self._concept_groups = (time.monotonic(), groups)
        return groups

    @staticmethod
    def _open(settings: SearchSettings, name: str, iri: str) -> bool:
        if any(f.name == name for f in settings.facets or []):
            return True
        return settings.properties.visitors and not settings.hides(iri)

    # --- conditions --------------------------------------------------------------

    def _conditions(
        self,
        params: SearchParams,
        threshold: datetime,
        *,
        postgres: bool,
        skip: str | None = None,
        scope: _Scope = RESOURCE,
        res: Any = R,
    ) -> list[ColumnElement[bool]]:
        """The search's conditions; ``scope=VALUE`` only when there are no words or dates.

        ``res`` is the resource entity: ``search_resource``, or a sampled alias of it.
        """
        if res is not R:
            scope = _Scope(res.id, res.kind, res.entry_uuid)
        conditions: list[ColumnElement[bool]] = [E.permit == EntryPermit.ACCEPTED]
        if self._hide_expired:
            conditions.append(E.expired_at.is_(None))
        if skip != "type":
            conditions.append(scope.kind.in_(params.types))
        if skip != "fdp" and params.fdps:
            conditions.append(scope.entry_uuid.in_(params.fdps))
        for name, values in params.filters.items():
            if name == skip or not values:
                continue
            wanted = [v for v in values if not v.startswith("!")]
            unwanted = [v[1:] for v in values if v.startswith("!")]
            if wanted:
                conditions.append(_has(name, wanted, scope.id))
            if unwanted:
                conditions.append(not_(_has(name, unwanted, scope.id)))
        columns = {"issued": res.issued, "modified": res.modified, "harvested": res.harvested_at}
        for name, (low, high) in params.ranges.items():
            column = columns[name]
            if low is not None:
                conditions.append(column >= low)
            if high is not None:
                conditions.append(column <= high)
        if params.fdp_state:
            conditions.append(and_(E.state == EntryState.VALID, E.last_retrieval_at > threshold))
        if params.q:
            conditions.append(self._text(params, postgres=postgres, res=res))
        return conditions

    def _queries(self, params: SearchParams) -> tuple[Any, Any]:
        q_lang = func.websearch_to_tsquery(
            cast(literal(ts_config(params.lang)), REGCONFIG), params.q
        )
        q_simple = func.websearch_to_tsquery(cast(literal(SIMPLE_CONFIG), REGCONFIG), params.q)
        return q_lang, q_simple

    def _prefix(self, params: SearchParams) -> Any | None:
        """``word:* & word:*`` for plain input, so "demo" finds "demonstration".

        Only for queries without phrase, ``or`` or exclusion syntax; the words are
        reduced to letters and digits, so the ``to_tsquery`` input is always valid.
        """
        if '"' in params.q or re.search(r"(^|\s)-|(^|\s)or(\s|$)", params.q, re.IGNORECASE):
            return None
        words = [w for w in re.findall(r"\w+", params.q.lower()) if len(w) >= 2][:8]
        if not words:
            return None
        return func.to_tsquery(
            cast(literal(SIMPLE_CONFIG), REGCONFIG), " & ".join(f"{w}:*" for w in words)
        )

    def _text(self, params: SearchParams, *, postgres: bool, res: Any = R) -> ColumnElement[bool]:
        if postgres:
            q_lang, q_simple = self._queries(params)
            matches = [res.tsv_lang.op("@@")(q_lang), res.tsv_simple.op("@@")(q_simple)]
            prefix = self._prefix(params)
            if prefix is not None:
                matches.append(res.tsv_simple.op("@@")(prefix))
            return or_(*matches)
        conditions: list[ColumnElement[bool]] = []
        for word in params.q.lower().replace('"', " ").split():
            if word == "or":
                continue
            needle = word.lstrip("-").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if not needle:
                continue
            match = res.search_text.like(f"%{needle}%", escape="\\")
            conditions.append(not_(match) if word.startswith("-") else match)
        return and_(*conditions) if conditions else literal(True)

    def _score(self, params: SearchParams, threshold: datetime) -> ColumnElement[float]:
        q_lang, q_simple = self._queries(params)
        # ts_rank, not ts_rank_cd: the field weights carry the signal, and cover density
        # cost ten times as much (1.6 s against 0.16 s over 29 000 matches; task 6.15).
        weights = cast(literal(RANK_WEIGHTS), ARRAY(REAL))
        ranks = [
            func.ts_rank(weights, R.tsv_lang, q_lang, 33),
            0.9 * func.ts_rank(weights, R.tsv_simple, q_simple, 33),
        ]
        prefix = self._prefix(params)
        if prefix is not None:
            # Partial words count, but less than whole ones.
            ranks.append(0.5 * func.ts_rank(weights, R.tsv_simple, prefix, 33))
        text_rank = func.greatest(*ranks)
        bonus = case(
            (func.lower(func.unaccent(R.title)) == func.lower(func.unaccent(params.q)), 0.3),
            (func.similarity(R.title, params.q) > 0.6, 0.15),
            else_=0.0,
        )
        boost = (
            literal(1.0) if len(params.types) == 1 else case(KIND_BOOST, value=R.kind, else_=0.6)
        )
        liveness = case(
            (and_(E.state == EntryState.VALID, E.last_retrieval_at > threshold), 1.0),
            (E.state.in_([EntryState.VALID, EntryState.UNKNOWN]), 0.9),
            else_=0.6,
        )
        age_days = (
            func.extract("epoch", func.now() - func.coalesce(R.modified, R.harvested_at)) / 86400.0
        )
        recency = 1 + 0.1 * func.exp(-func.greatest(age_days, 0) / 365.0)
        language = case((R.titles.has_key(params.lang.lower()), 1.05), else_=1.0)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
        quality = 0.9 + 0.1 * R.completeness
        return ((text_rank + bonus) * boost * liveness * recency * language * quality).label(
            "score"
        )

    def _order(self, params: SearchParams, score: Any) -> list[Any]:
        key, _, direction = params.sort.partition(",")
        key = key or ("relevance" if params.q else "modified")
        descending = direction != "asc"
        order: list[Any]
        if key == "relevance" and score is not None:
            order = [score.desc()]
        elif key == "title":
            order = [
                func.lower(R.title).desc() if direction == "desc" else func.lower(R.title).asc()
            ]
        else:
            column = {
                "relevance": R.modified,
                "modified": func.coalesce(R.modified, R.issued),
                "issued": R.issued,
                "harvested": R.harvested_at,
            }[key]
            order = [column.desc().nulls_last() if descending else column.asc().nulls_last()]
            if key in {"relevance", "modified"}:
                order.append(func.lower(R.title))
        return [*order, R.completeness.desc(), R.id]

    # --- search -------------------------------------------------------------------

    async def search(self, params: SearchParams, *, admin: bool = False) -> dict[str, Any]:
        names = await self._prepare(params, admin=admin)
        assert params.facets is not None
        threshold = await self._threshold()
        started = time.perf_counter()

        async def work(session: AsyncSession, postgres: bool) -> dict[str, Any]:
            conditions = self._conditions(params, threshold, postgres=postgres)
            matches: _Matches | None = None
            exact_ids: list[int] | None = None
            if postgres and not self._light(params):
                matches = _Matches()
                if "type" in (params.facets or []):
                    # The type facet's matches (every kind), when few, give the search's own
                    # too; when many, they are sampled like the search's own, after it.
                    await self._ids(session, params, threshold, matches, "type", probe=True)
                base = await self._ids(session, params, threshold, matches, None)
                assert base is not None
                total = base.total
                exact_ids = None if base.approximate else base.ids
            else:
                total = await self._count(session, params, conditions)
            score = self._score(params, threshold) if postgres and params.q else None
            columns: list[Any] = [R, E]
            if score is not None:
                columns.append(score)
            # Known matches are not evaluated again (a phrase is rechecked row by row).
            known = (
                [R.id == any_(bindparam("hit_ids", exact_ids, type_=ARRAY(BigInteger)))]
                if exact_ids is not None
                else conditions
            )
            query = (
                select(*columns)
                .join(E, E.uuid == R.entry_uuid)
                .where(*known)
                .order_by(*self._order(params, score))
                .offset(params.page * params.size)
                .limit(params.size)
            )
            bounded = score is not None and total > self._sample_above
            if bounded:
                # Ranking every one of hundreds of thousands of matches takes seconds;
                # rank the newest ones, walking ix_search_resource_recent.
                candidates = (
                    select(R.id)
                    .join(E, E.uuid == R.entry_uuid)
                    .where(*conditions)
                    .order_by(func.coalesce(R.modified, R.issued).desc().nulls_last())
                    .limit(RANK_CANDIDATES)
                )
                query = query.where(R.id.in_(candidates))
            rows = (await session.execute(query)).all()
            labels = Labels(session, params.lang)
            hits = await self._hits(
                session, rows, params, threshold, labels, scored=score is not None
            )
            if params.highlight and params.q:
                await self._highlight(session, hits, params, postgres=postgres)
            for hit in hits:
                hit.pop("fullDescription", None)
            assert params.facets is not None
            facets = {
                names.get(name, name): await self._facet(
                    session,
                    params,
                    name,
                    threshold,
                    labels,
                    postgres=postgres,
                    size=params.facet_size,
                    matches=matches,
                )
                for name in dict.fromkeys(params.facets)
            }
            return {
                "query": {
                    "q": params.q,
                    "types": params.types,
                    "filters": {names.get(k, k): v for k, v in params.filters.items()},
                    "fdp": [str(u) for u in params.fdps],
                    "lang": params.lang,
                    "sort": params.sort or ("relevance" if params.q else "modified,desc"),
                },
                "totalResults": total,
                "startIndex": params.page * params.size,
                "itemsPerPage": params.size,
                "page": params.page,
                "hits": hits,
                "facets": facets,
                **({"ranking": {"candidates": RANK_CANDIDATES}} if bounded else {}),
            }

        result: dict[str, Any] = await self._run(admin, work)
        result["took"] = round((time.perf_counter() - started) * 1000)
        return result

    async def facet(
        self,
        params: SearchParams,
        name: str,
        *,
        contains: str = "",
        size: int = 200,
        admin: bool = False,
        tree: bool = False,
    ) -> dict[str, Any]:
        """One facet's buckets beyond the top ones, for the "show more" dialog.

        With ``tree`` (fields with ``hierarchy``, TASKS 8.12): the concepts and their
        broader ones as a tree, each counting the resources tagged with it or with
        anything narrower; their filter is ``<name>^``.
        """
        if tree:
            spec = self.profile.fields.get(name)
            if spec is None or not spec.hierarchy:
                raise BadRequest(f"{name} has no hierarchy")
            name = f"{name}^"
        params.facets = [name]
        await self._prepare(params, admin=admin)
        name = params.facets[0]
        params.facets = []
        threshold = await self._threshold()

        async def work(session: AsyncSession, postgres: bool) -> dict[str, Any]:
            labels = Labels(session, params.lang)
            matches = _Matches() if postgres and not self._light(params) else None
            result = await self._facet(
                session,
                params,
                name,
                threshold,
                labels,
                postgres=postgres,
                size=1000,
                matches=matches,
            )
            needle = contains.strip().lower()
            if needle:
                result["buckets"] = [
                    b
                    for b in result["buckets"]
                    if needle in b["value"].lower() or needle in str(b["label"]).lower()
                ]
            if not tree:  # a tree needs every concept to place the others
                result["buckets"] = result["buckets"][: max(1, min(size, 1000))]
            if tree:
                groups = await self._groups()
                parents = await concept_parents(session, groups)
                result["filter"] = name
                result["tree"] = _tree(result.pop("buckets"), parents)
            return result

        return await self._run(admin, work)

    async def sparql(self, params: SearchParams, *, admin: bool = False) -> dict[str, Any]:
        """The search as a SPARQL query over the store (``search.to_sparql``)."""
        params.facets = []
        names = await self._prepare(params, admin=admin)
        properties = {key: name.removeprefix(PROPERTY_FACET) for key, name in names.items()}
        hierarchies = {n: b for n, b in self.profile.hierarchies.items() if n in params.filters}
        if hierarchies:
            # "This concept or a narrower one": the store has no broader links, so the
            # concept and those the index knows below it (TASKS 8.12).
            async with self._session_factory() as session:
                parents = await concept_parents(session, await self._groups())
            for name, base in hierarchies.items():
                expanded = [
                    ("!" + d) if v.startswith("!") else d
                    for v in params.filters.pop(name)
                    for d in descendants(parents, v.lstrip("!"))
                ]
                params.filters[base] = list(
                    dict.fromkeys([*params.filters.get(base, []), *expanded])
                )
        graphs: dict[UUID, str] = {}
        if params.fdps:
            async with self._session_factory() as session:
                query = select(E.uuid, E.client_url).where(E.uuid.in_(params.fdps))
                if not admin:
                    # Anonymous callers learn the address of accepted FDPs only.
                    query = query.where(E.permit == EntryPermit.ACCEPTED)
                graphs = {uuid: str(url) for uuid, url in (await session.execute(query)).all()}
        return to_sparql(
            params,
            self.profile,
            graphs,
            default_types=DEFAULT_TYPES,
            properties=properties,
            equivalents=(await self._groups()).members,
        )

    async def _facet(
        self,
        session: AsyncSession,
        params: SearchParams,
        name: str,
        threshold: datetime,
        labels: Labels,
        *,
        postgres: bool,
        size: int,
        matches: _Matches | None = None,
    ) -> dict[str, Any]:
        if matches is not None:
            return await self._facet_over_ids(
                session, params, name, threshold, labels, size=size, matches=matches
            )
        cache_key = self._cache_key(params, name, size)
        if cache_key is not None and (cached := self._cached(cache_key)) is not None:
            return cached
        count = func.count().label("n")
        if name == "type":
            conditions = self._conditions(params, threshold, postgres=postgres, skip=name)
            key: Any = R.kind
            query = select(key, count).join(E, E.uuid == R.entry_uuid)
        elif name == "fdp":
            conditions = self._conditions(params, threshold, postgres=postgres, skip=name)
            key = R.entry_uuid
            query = select(key, count).join(E, E.uuid == R.entry_uuid)
        elif self._light(params):
            # Kind and entry are on the value rows: no resource to look up per value.
            conditions = self._conditions(
                params, threshold, postgres=postgres, skip=name, scope=VALUE
            )
            key = V.value
            query = (
                select(key, count)
                .select_from(V)
                .join(E, E.uuid == V.entry_uuid)
                .where(V.field == name)
            )
        else:
            conditions = self._conditions(params, threshold, postgres=postgres, skip=name)
            conditions.append(V.kind.in_(params.types))
            key = V.value
            query = (
                select(key, count)
                .select_from(V)
                .join(R, R.id == V.resource_id)
                .join(E, E.uuid == R.entry_uuid)
                .where(V.field == name)
            )
        rows = (
            await session.execute(
                query.where(*conditions).group_by(key).order_by(count.desc(), key).limit(size + 1)
            )
        ).all()
        buckets = [(str(value), int(n)) for value, n in rows[:size]]
        selected = [v.lstrip("!") for v in self._selected(params, name)]
        missing = [v for v in selected if v not in {b for b, _ in buckets}]
        if missing:
            extra = (
                await session.execute(query.where(*conditions, key.in_(missing)).group_by(key))
            ).all()
            found = {str(value): int(n) for value, n in extra}
            buckets.extend((v, found.get(v, 0)) for v in missing)
        result = {
            "buckets": await self._bucket_views(session, name, buckets, labels, threshold),
            "more": len(rows) > size,
        }
        if cache_key is not None:
            self._remember(cache_key, result)
        return result

    async def _count(
        self, session: AsyncSession, params: SearchParams, conditions: list[ColumnElement[bool]]
    ) -> int:
        key = self._cache_key(params, "", 0)
        cached = self._cached(key) if key is not None else None
        if cached is not None:
            return int(cached)
        base = select(R.id).join(E, E.uuid == R.entry_uuid).where(*conditions)
        total = int(await session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        if key is not None:
            self._remember(key, total)
        return total

    async def _ids(
        self,
        session: AsyncSession,
        params: SearchParams,
        threshold: datetime,
        matches: _Matches,
        skip: str | None,
        *,
        probe: bool = False,
    ) -> _IdSet | None:
        """The matching ids without the ``skip`` facet's filter: all of them, or a sample.

        With ``probe``, only when they are few (``None`` otherwise, nothing counted).
        """
        if skip in matches.sets:
            return matches.sets[skip]
        conditions = self._conditions(params, threshold, postgres=True, skip=skip)
        query = (
            select(R.id, R.kind, R.entry_uuid).join(E, E.uuid == R.entry_uuid).where(*conditions)
        )
        base = matches.sets.get(None)
        if skip is not None and base is not None and base.approximate:
            # Leaving a condition out only adds matches: this set is large too. The same
            # pages (same percentage and seed, so already cached) scaled by their share.
            rows = await self._sampled_rows(
                session, params, threshold, skip, percent=base.percent, step=base.step
            )
            scale = 100.0 / base.percent if base.percent is not None else float(base.step)
            found = _IdSet(rows, round(len(rows) * scale), scale, True, base.percent, base.step)
        else:
            rows = _rows(await session.execute(query.limit(self._sample_above + 1)))
            if len(rows) <= self._sample_above:
                found = _IdSet(rows, len(rows))
                if skip == "type" and None not in matches.sets:
                    # Every kind matched: the search's own matches are those of its kinds.
                    own = [row for row in rows if row[1] in params.types]
                    matches.sets[None] = _IdSet(own, len(own))
            elif probe:
                return None
            else:
                total = int(
                    await session.scalar(select(func.count()).select_from(query.subquery())) or 0
                )
                share = min(100.0, 100.0 * self._sample_size / max(total, 1))
                percent: float | None = share if share <= self._max_page_percent else None
                step = 1 if percent is not None else max(1, total // self._sample_size)
                rows = await self._sampled_rows(
                    session, params, threshold, skip, percent=percent, step=step
                )
                scale = total / len(rows) if rows else 0.0
                found = _IdSet(rows, total, scale, True, percent, step)
        matches.sets[skip] = found
        return found

    async def _sampled_rows(
        self,
        session: AsyncSession,
        params: SearchParams,
        threshold: datetime,
        skip: str | None,
        *,
        percent: float | None,
        step: int,
    ) -> list[tuple[int, str, UUID]]:
        """A page sample of ``percent`` of the table, or (``None``) every ``step``-th id."""
        if percent is None:
            conditions = self._conditions(params, threshold, postgres=True, skip=skip)
            conditions.append(R.id % step == 0)
            query = (
                select(R.id, R.kind, R.entry_uuid)
                .join(E, E.uuid == R.entry_uuid)
                .where(*conditions)
            )
            return _rows(await session.execute(query))
        sampled = aliased(
            R,
            R.__table__.tablesample(func.system(percent), name="sampled", seed=literal(1)),  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
        )
        conditions = self._conditions(params, threshold, postgres=True, skip=skip, res=sampled)
        query = (
            select(sampled.id, sampled.kind, sampled.entry_uuid)
            .join(E, E.uuid == sampled.entry_uuid)
            .where(*conditions)
        )
        return _rows(await session.execute(query))

    async def _facet_over_ids(
        self,
        session: AsyncSession,
        params: SearchParams,
        name: str,
        threshold: datetime,
        labels: Labels,
        *,
        size: int,
        matches: _Matches,
    ) -> dict[str, Any]:
        selected = [v.lstrip("!") for v in self._selected(params, name)]
        own = name == "type" or bool(selected)
        found = await self._ids(session, params, threshold, matches, name if own else None)
        assert found is not None

        def scaled(n: Any) -> int:
            return max(1, round(int(n) * found.scale)) if found.approximate else int(n)

        if name in {"type", "fdp"}:
            # Kind and entry came with the ids.
            position = 1 if name == "type" else 2
            counter = Counter(str(row[position]) for row in found.rows)
            rows = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[: size + 1]
            counted = dict(counter)
        else:
            wanted = any_(
                bindparam(f"ids_{re.sub(r'\W', '_', name)}", found.ids, type_=ARRAY(BigInteger))
            )
            count = func.count().label("n")
            query = select(V.value, count).where(V.field == name, V.resource_id == wanted)
            rows = [
                (str(value), int(n))
                for value, n in await session.execute(
                    query.group_by(V.value).order_by(count.desc(), V.value).limit(size + 1)
                )
            ]
            counted = {}
            missing = [v for v in selected if v not in {value for value, _ in rows[:size]}]
            if missing:
                extra = await session.execute(query.where(V.value.in_(missing)).group_by(V.value))
                counted = {str(value): int(n) for value, n in extra}
        buckets = [(value, scaled(n)) for value, n in rows[:size]]
        shown = {value for value, _ in buckets}
        buckets.extend(
            (v, scaled(counted[v]) if counted.get(v) else 0) for v in selected if v not in shown
        )
        result: dict[str, Any] = {
            "buckets": await self._bucket_views(session, name, buckets, labels, threshold),
            "more": len(rows) > size,
        }
        if found.approximate:
            result["approximate"] = True
        return result

    # --- the facet cache -------------------------------------------------------------

    @staticmethod
    def _light(params: SearchParams) -> bool:
        """No words and no date ranges: conditions need no resource columns but kind and entry."""
        return not params.q and not params.ranges

    def _cache_key(self, params: SearchParams, name: str, size: int) -> tuple[Any, ...] | None:
        if not self._light(params) or self._facet_cache_seconds <= 0:
            return None
        filters = tuple(sorted((k, tuple(sorted(v))) for k, v in params.filters.items() if v))
        return (
            self.indexer.generation,
            self._hide_expired,
            name,
            size,
            params.lang,
            tuple(sorted(params.types)),
            tuple(sorted(str(u) for u in params.fdps)),
            params.fdp_state,
            filters,
        )

    def _cached(self, key: tuple[Any, ...]) -> Any | None:
        found = self._facet_cache.get(key)
        if found is None or time.monotonic() - found[0] > self._facet_cache_seconds:
            return None
        return copy.deepcopy(found[1])

    def _remember(self, key: tuple[Any, ...], value: Any) -> None:
        if len(self._facet_cache) >= FACET_CACHE_ENTRIES:
            # Drop the oldest half; entries of older generations go first by age anyway.
            for old in sorted(self._facet_cache, key=lambda k: self._facet_cache[k][0])[
                : FACET_CACHE_ENTRIES // 2
            ]:
                del self._facet_cache[old]
        self._facet_cache[key] = (time.monotonic(), copy.deepcopy(value))

    def _selected(self, params: SearchParams, name: str) -> list[str]:
        if name == "type":
            return [] if params.types == list(DEFAULT_TYPES) else list(params.types)
        if name == "fdp":
            return [str(u) for u in params.fdps]
        return params.filters.get(name, [])

    async def _bucket_views(
        self,
        session: AsyncSession,
        name: str,
        buckets: list[tuple[str, int]],
        labels: Labels,
        threshold: datetime,
    ) -> list[dict[str, Any]]:
        if name == "type":
            return [{"value": v, "label": v, "count": n} for v, n in buckets]
        if name == "fdp":
            ids = [UUID(v) for v, _ in buckets]
            entries = {
                str(e.uuid): e for e in await session.scalars(select(E).where(E.uuid.in_(ids)))
            }
            views: list[dict[str, Any]] = []
            for value, n in buckets:
                entry = entries.get(value)
                views.append(
                    {
                        "value": value,
                        "label": _entry_title(entry) if entry else value,
                        "count": n,
                        "state": api_state(entry, threshold) if entry else None,
                    }
                )
            return views
        spec = self.profile.fields.get(name)
        if spec is not None and spec.kind == "number":
            return [{"value": v, "label": bucket_label(v), "count": n} for v, n in buckets]
        await labels.load({v for v, _ in buckets})
        return [{"value": v, "label": labels.get(v), "count": n} for v, n in buckets]

    async def _hits(
        self,
        session: AsyncSession,
        rows: Sequence[Any],
        params: SearchParams,
        threshold: datetime,
        labels: Labels,
        *,
        scored: bool,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        resources: list[SearchResource] = [row[0] for row in rows]
        ids = [r.id for r in resources]
        datasets = [(r.entry_uuid, r.iri) for r in resources if r.kind in {"dataset", "series"}]
        distributions: dict[tuple[UUID, str], list[SearchResource]] = defaultdict(list)
        if datasets:
            for dist in await session.scalars(
                select(R)
                .where(R.kind == "distribution", tuple_(R.entry_uuid, R.dataset_iri).in_(datasets))
                .order_by(R.title, R.id)
            ):
                key = (dist.entry_uuid, dist.dataset_iri or "")
                if len(distributions[key]) < DISTRIBUTIONS_PER_HIT:
                    distributions[key].append(dist)
        all_ids = ids + [d.id for group in distributions.values() for d in group]
        values = await _values(session, all_ids)
        catalogs = (
            {
                (r.entry_uuid, r.iri): r
                for r in await session.scalars(
                    select(R).where(
                        tuple_(R.entry_uuid, R.iri).in_(
                            [(r.entry_uuid, r.catalog_iri) for r in resources if r.catalog_iri]
                        )
                    )
                )
            }
            if any(r.catalog_iri for r in resources)
            else {}
        )
        await labels.load({v.value for group in values.values() for v in group})
        top = max((float(row[2] or 0) for row in rows), default=0.0) if scored else 0.0
        hits: list[dict[str, Any]] = []
        for row in rows:
            resource: SearchResource = row[0]
            entry: IndexEntry = row[1]
            title_lang, title = pick_language(resource.titles, params.lang) or ("", resource.title)
            description = pick_language(resource.descriptions, params.lang)
            catalog = catalogs.get((resource.entry_uuid, resource.catalog_iri or ""))
            hit: dict[str, Any] = {
                "id": resource.public_id,
                "iri": resource.iri,
                "type": resource.kind,
                "typeIris": resource.type_iris,
                "title": title,
                "titleLang": title_lang or None,
                "titleFallback": bool(resource.facts.get("titleFallback")),
                "description": description[1][:600] if description else None,
                # Removed again after highlighting: the snippet may come from further in.
                "fullDescription": description[1][:5000] if description else None,
                "score": round(float(row[2] or 0) / top, 4) if scored and top > 0 else None,
                "completeness": resource.completeness,
                "fdp": _fdp_view(entry, threshold),
                "catalog": (
                    {"id": catalog.public_id, "iri": catalog.iri, "title": catalog.title}
                    if catalog
                    else None
                ),
                "issued": resource.issued.isoformat() if resource.issued else None,
                "modified": resource.modified.isoformat() if resource.modified else None,
                "landingPage": resource.landing_page,
                "sourceUrl": resource.node_iri,
                "facts": {k: v for k, v in resource.facts.items() if k != "titleFallback"},
                "values": self._value_views(values.get(resource.id, []), labels),
            }
            if resource.kind in {"dataset", "series"}:
                hit["distributions"] = [
                    {
                        "id": d.public_id,
                        "iri": d.iri,
                        "title": d.title,
                        "values": self._value_views(values.get(d.id, []), labels),
                    }
                    for d in distributions.get((resource.entry_uuid, resource.iri), [])
                ]
            hits.append(hit)
        return hits

    async def _highlight(
        self,
        session: AsyncSession,
        hits: list[dict[str, Any]],
        params: SearchParams,
        *,
        postgres: bool,
    ) -> None:
        """Add ``highlight`` to each hit: its title and description as segments, the
        matching ones marked. Segments, not HTML: harvested text stays text.

        PostgreSQL's ``ts_headline`` marks stemmed matches (searching "registries"
        marks "registry") and picks the description's best fragments; elsewhere
        words that start with a searched word are marked.
        """
        texts: list[str] = []
        for hit in hits:
            texts += [_plain(hit["title"]), _plain(hit.get("fullDescription") or "")]
        if postgres:
            q_lang, _ = self._queries(params)
            options = (
                f"StartSel={START}, StopSel={STOP}, MaxFragments=2, MinWords=12, "
                'MaxWords=35, FragmentDelimiter=" … ", HighlightAll=false'
            )
            title_options = f"StartSel={START}, StopSel={STOP}, HighlightAll=true"
            config = cast(literal(ts_config(params.lang)), REGCONFIG)
            marked: list[str] = []
            for index, text in enumerate(texts):
                if not text:
                    marked.append("")
                    continue
                opts = title_options if index % 2 == 0 else options
                value = await session.scalar(
                    select(func.ts_headline(config, literal(text), q_lang, literal(opts)))
                )
                marked.append(str(value or ""))
        else:
            words = [
                w.lower()
                for w in re.findall(r"-?\w+", params.q)
                if not w.startswith("-") and w.lower() != "or"
            ]
            marked = [_mark_prefixes(text, words) for text in texts]
        for index, hit in enumerate(hits):
            title, description = marked[2 * index], marked[2 * index + 1]
            highlight: dict[str, Any] = {"title": _segments(title)}
            if description and START in description:
                highlight["description"] = _segments(description)
            hit["highlight"] = highlight

    def _value_views(
        self, values: list[SearchValue], labels: Labels
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in values:
            if is_property_field(value.field):
                continue  # captured properties: the harvested triples show them
            spec = self.profile.fields.get(value.field)
            label = (
                bucket_label(value.value)
                if spec is not None and spec.kind == "number"
                else labels.get(value.value)
            )
            view: dict[str, Any] = {"value": value.value, "label": label}
            if value.lang:
                view["lang"] = value.lang
            grouped[value.field].append(view)
        return dict(grouped)

    # --- resources ----------------------------------------------------------------

    async def resource(
        self, public_id: str, *, lang: str = "en", admin: bool = False
    ) -> dict[str, Any]:
        threshold = await self._threshold()

        async def work(session: AsyncSession, _postgres: bool) -> dict[str, Any]:
            row = (
                await session.execute(
                    select(R, E)
                    .join(E, E.uuid == R.entry_uuid)
                    .where(R.public_id == public_id, E.permit == EntryPermit.ACCEPTED)
                )
            ).first()
            if row is None:
                raise NotFound("Resource not found")
            resource: SearchResource = row[0]
            entry: IndexEntry = row[1]
            family = {
                r.iri: r
                for r in await session.scalars(select(R).where(R.entry_uuid == resource.entry_uuid))
            }
            parents: list[dict[str, Any]] = []
            seen = {resource.iri}
            current = resource.parent_iri
            while current and current not in seen and current in family and len(parents) < 16:
                seen.add(current)
                parent = family[current]
                parents.insert(0, _brief(parent, lang))
                current = parent.parent_iri
            children = sorted(
                (r for r in family.values() if r.parent_iri == resource.iri),
                key=lambda r: (KINDS.index(r.kind) if r.kind in KINDS else 99, r.title.lower()),
            )
            values = await _values(session, [resource.id])
            labels = Labels(session, lang)
            await labels.load({v.value for v in values.get(resource.id, [])} | {resource.iri})
            title_lang, title = pick_language(resource.titles, lang) or ("", resource.title)
            description = pick_language(resource.descriptions, lang)
            return {
                "id": resource.public_id,
                "iri": resource.iri,
                "type": resource.kind,
                "typeIris": resource.type_iris,
                "title": title,
                "titleLang": title_lang or None,
                "titles": resource.titles,
                "description": description[1] if description else None,
                "descriptions": resource.descriptions,
                "completeness": resource.completeness,
                "issued": resource.issued.isoformat() if resource.issued else None,
                "modified": resource.modified.isoformat() if resource.modified else None,
                "landingPage": resource.landing_page,
                "sourceUrl": resource.node_iri,
                "harvestedAt": resource.harvested_at.isoformat(),
                "facts": resource.facts,
                "fdp": _fdp_view(entry, threshold),
                "parents": parents,
                "children": [_brief(c, lang) for c in children[:500]],
                "childCount": len(children),
                "values": self._value_views(values.get(resource.id, []), labels),
            }

        return await self._run(admin, work)

    async def resource_graph(self, public_id: str) -> Graph:
        """The harvested node a resource was projected from."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(HarvestedNode.ntriples)
                    .join(
                        R,
                        and_(
                            R.entry_uuid == HarvestedNode.entry_uuid,
                            R.node_iri == HarvestedNode.node_iri,
                        ),
                    )
                    .join(E, E.uuid == R.entry_uuid)
                    .where(R.public_id == public_id, E.permit == EntryPermit.ACCEPTED)
                )
            ).first()
        if row is None:
            raise NotFound("Resource not found")
        ntriples = str(row[0])

        def parse() -> Graph:
            graph = Graph()
            graph.parse(data=ntriples, format="nt")
            return graph

        return await asyncio.to_thread(parse)

    # --- landing, labels, stats ----------------------------------------------------

    async def landing(self) -> dict[str, Any]:
        """Counters per kind and FDP state, the facet descriptions and card fields (cached)."""
        cached = self._landing
        version = (self.indexer.generation, self.search_settings.version, self.profile.hash)
        if cached is not None and cached[0] == version and time.monotonic() - cached[1] < 60:
            return cached[2]
        settings = await self.search_settings.get()
        threshold = await self._threshold()
        async with self._session_factory() as session:
            kinds = dict(
                (
                    await session.execute(
                        select(R.kind, func.count())
                        .join(E, E.uuid == R.entry_uuid)
                        .where(E.permit == EntryPermit.ACCEPTED)
                        .group_by(R.kind)
                    )
                ).all()
            )
            entries = list(await session.scalars(select(E).where(E.permit == EntryPermit.ACCEPTED)))
            pending = int(
                await session.scalar(
                    select(func.count(func.distinct(HarvestedNode.entry_uuid)))
                    .join(E, E.uuid == HarvestedNode.entry_uuid)
                    .where(
                        E.permit == EntryPermit.ACCEPTED,
                        ~exists().where(SearchEntryState.entry_uuid == HarvestedNode.entry_uuid),
                    )
                )
                or 0
            )
        states: dict[str, int] = defaultdict(int)
        for entry in entries:
            states[api_state(entry, threshold)] += 1
        result = {
            "counters": {kind: int(kinds.get(kind, 0)) for kind in KINDS},
            "landing": list(self.profile.landing.counters),
            "fdps": {"total": len(entries), "byState": dict(states)},
            "facets": self._facet_views(settings),
            "properties": {"visitors": settings.properties.visitors},
            # Languages offered for titles and labels: those of the label pack.
            "languages": LANGUAGES,
            "defaultLanguage": settings.default_language,
            "fields": {
                name: {"label": spec.label, "kind": spec.kind}
                for name, spec in self.profile.fields.items()
                if name not in COLUMN_FIELDS
            },
            "cards": self.profile.cards,
            "indexing": {"pending": pending},
        }
        self._landing = (version, time.monotonic(), result)
        return result

    def _facet_views(self, settings: SearchSettings) -> list[dict[str, Any]]:
        """The facets in display order with their group (``main`` or ``more``); hidden left out."""
        views: list[dict[str, Any]] = []
        for facet in effective_facets(settings, self.profile):
            if facet.placement == "hidden":
                continue
            view: dict[str, Any] = {"name": facet.name, "group": facet.placement}
            spec = self.profile.fields.get(facet.name)
            if spec is not None:
                view |= {"label": spec.label, "help": spec.help, "kind": spec.kind}
                if spec.hierarchy:
                    view["hierarchy"] = f"{facet.name}^"
            elif facet.name == "fdp":
                view |= {"label": {}, "help": {}, "kind": "fdp"}
            else:
                iri = facet.name.removeprefix(PROPERTY_FACET)
                view |= {
                    "label": {"en": humanize(iri)},
                    "help": {},
                    "kind": "property",
                    "property": iri,
                    "curie": curie(iri),
                }
            view["label"] = {**view["label"], **facet.label}
            view["help"] = {**view["help"], **facet.help}
            views.append(view)
        return views

    async def properties(
        self, types: list[Kind], *, admin: bool = False, contains: str = ""
    ) -> dict[str, Any]:
        """Captured properties with the number of resources of ``types`` that have them.

        For the "add a facet" picker. Visitors see the properties open to them;
        counts are kept for a few minutes.
        """
        settings = await self.search_settings.get()
        if not admin and not settings.properties.visitors:
            listed = []
        else:
            key = (tuple(sorted(types)), admin)
            cached = self._properties.get(key)
            if cached is not None and (
                cached[1] == self.indexer.generation
                or time.monotonic() - cached[0] < PROPERTY_LIST_SECONDS
            ):
                listed = cached[2]
            else:
                listed = await self._run(
                    True, lambda session, _: self._count_properties(session, types)
                )
                self._properties[key] = (time.monotonic(), self.indexer.generation, listed)
            if not admin:
                listed = [p for p in listed if self._open(settings, p["name"], p["property"])]
        needle = contains.strip().lower()
        if needle:
            listed = [
                p for p in listed if needle in p["property"].lower() or needle in p["label"].lower()
            ]
        return {"visitors": settings.properties.visitors, "properties": listed}

    async def _count_properties(
        self, session: AsyncSession, types: list[Kind]
    ) -> list[dict[str, Any]]:
        n = func.count(func.distinct(V.resource_id)).label("n")
        rows = (
            await session.execute(
                select(SearchProperty.iri, n)
                .select_from(V)
                .join(SearchProperty, SearchProperty.field == V.field)
                .join(E, E.uuid == V.entry_uuid)
                .where(
                    V.field.startswith("p:"),
                    V.kind.in_(types),
                    E.permit == EntryPermit.ACCEPTED,
                )
                .group_by(SearchProperty.iri)
                .order_by(n.desc(), SearchProperty.iri)
            )
        ).all()
        return [
            {
                "name": PROPERTY_FACET + str(iri),
                "property": str(iri),
                "curie": curie(str(iri)),
                "label": humanize(str(iri)),
                "resources": int(count),
            }
            for iri, count in rows
        ]

    async def suggest(
        self, text: str, *, types: list[Kind], lang: str = "en", admin: bool = False
    ) -> dict[str, Any]:
        """Suggestions while typing (TASKS 8.2): resources by title, and filter values.

        Titles match anywhere (trigram index on PostgreSQL), most similar first.
        Values are keywords that start with the text, and subjects, themes and
        publishers whose name (harvested, resolved or bundled) starts with it,
        each with the number of resources that have it.
        """
        needle = " ".join(text.split())[:100]
        if len(needle) < 2:
            return {"query": needle, "suggestions": []}
        lowered = needle.lower()
        pattern = _like(lowered)
        await self._threshold()  # reads the expiry policy: hidden entries stay hidden
        settings_fields = [f for f in ("subject", "theme", "publisher") if f in self.profile.fields]

        async def work(session: AsyncSession, postgres: bool) -> dict[str, Any]:
            visible = [E.permit == EntryPermit.ACCEPTED]
            if self._hide_expired:
                visible.append(E.expired_at.is_(None))
            title_match = func.lower(R.title).like(f"%{pattern}%", escape="\\")
            order: list[Any] = [R.completeness.desc(), R.id]
            if postgres:
                order = [func.similarity(R.title, needle).desc(), *order]
            resources = (
                await session.scalars(
                    select(R)
                    .join(E, E.uuid == R.entry_uuid)
                    .where(*visible, R.kind.in_(types), title_match)
                    .order_by(*order)
                    .limit(SUGGEST_TITLES * 8)
                )
            ).all()
            # The same title in several FDPs (each its own "Dementia data" catalog) is
            # one suggestion: a phrase search for it, with how many there are.
            groups: dict[str, list[SearchResource]] = {}
            for resource in resources:
                groups.setdefault(resource.title.lower(), []).append(resource)
            suggestions: list[dict[str, Any]] = [
                {
                    "kind": "resource",
                    "id": group[0].public_id,
                    "title": group[0].title,
                    "type": group[0].kind,
                }
                if len(group) == 1
                else {"kind": "query", "text": group[0].title, "count": len(group)}
                for group in list(groups.values())[:SUGGEST_TITLES]
            ]
            count = func.count(func.distinct(V.resource_id)).label("n")
            keyword_rows = (
                await session.execute(
                    select(V.value, count)
                    .join(E, E.uuid == V.entry_uuid)
                    .where(
                        *visible,
                        V.field == "keyword",
                        V.kind.in_(types),
                        V.value.like(f"{pattern}%", escape="\\"),
                    )
                    .group_by(V.value)
                    .order_by(count.desc(), V.value)
                    .limit(SUGGEST_VALUES)
                )
            ).all()
            values: list[dict[str, Any]] = [
                {
                    "kind": "value",
                    "field": "keyword",
                    "value": str(v),
                    "label": str(v),
                    "count": int(n),
                }
                for v, n in keyword_rows
            ]
            # Concepts by name: harvested labels, resolved labels, the bundled pack.
            named: dict[str, str] = {}
            label_like = func.lower(SearchLabel.label).like(f"{pattern}%", escape="\\")
            for iri, label in (
                await session.execute(
                    select(SearchLabel.iri, SearchLabel.label)
                    .join(E, E.uuid == SearchLabel.entry_uuid)
                    .where(*visible, label_like)
                    .limit(200)
                )
            ).all():
                named.setdefault(str(iri), str(label))
            concept_like = func.lower(ConceptLabel.label).like(f"{pattern}%", escape="\\")
            for iri, label in (
                await session.execute(
                    select(ConceptLabel.iri, ConceptLabel.label).where(concept_like).limit(200)
                )
            ).all():
                named.setdefault(str(iri), str(label))
            for iri, labels in bundled_matches(lowered):
                named.setdefault(iri, labels)
            if named and settings_fields:
                concept_rows = (
                    await session.execute(
                        select(V.field, V.value, count)
                        .join(E, E.uuid == V.entry_uuid)
                        .where(
                            *visible,
                            V.field.in_(settings_fields),
                            V.kind.in_(types),
                            V.value.in_(list(named)[:400]),
                        )
                        .group_by(V.field, V.value)
                        .order_by(count.desc(), V.value)
                        .limit(SUGGEST_VALUES)
                    )
                ).all()
                labels = Labels(session, lang)
                await labels.load({str(v) for _, v, _ in concept_rows})
                values += [
                    {
                        "kind": "value",
                        "field": str(field),
                        "value": str(value),
                        "label": labels.get(str(value)),
                        "count": int(n),
                    }
                    for field, value, n in concept_rows
                ]
            values.sort(key=lambda v: (-int(v["count"]), str(v["label"]).lower()))
            return {"query": needle, "suggestions": suggestions + values[:SUGGEST_VALUES]}

        return await self._run(admin, work)

    async def labels(self, iris: list[str], lang: str) -> dict[str, str]:
        async with self._session_factory() as session:
            labels = Labels(session, lang)
            found = await labels.load(set(iris))
        return {iri: found[iri] for iri in iris if iri in found}

    async def stats(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            per_entry = (
                await session.execute(
                    select(
                        R.entry_uuid,
                        E.client_url,
                        func.count(),
                        func.avg(R.completeness),
                    )
                    .join(E, E.uuid == R.entry_uuid)
                    .group_by(R.entry_uuid, E.client_url)
                    .order_by(E.client_url)
                )
            ).all()
            states = {s.entry_uuid: s for s in await session.scalars(select(SearchEntryState))}
            values = int(await session.scalar(select(func.count()).select_from(V)) or 0)
            labels = int(await session.scalar(select(func.count()).select_from(SearchLabel)) or 0)
            concepts = await concept_stats(session)
        return {
            "profileHash": self.profile.hash,
            "values": values,
            "labels": labels,
            "concepts": concepts,
            "entries": [
                {
                    "uuid": str(uuid),
                    "clientUrl": url,
                    "resources": int(n),
                    "completeness": round(float(avg or 0), 3),
                    "projectedAt": states[uuid].projected_at.isoformat()
                    if uuid in states
                    else None,
                }
                for uuid, url, n, avg in per_entry
            ],
        }


def _tree(buckets: list[dict[str, Any]], parents: Mapping[str, list[str]]) -> list[dict[str, Any]]:
    """Buckets as a tree: each under its nearest broader concept that is a bucket too."""
    nodes = {b["value"]: {**b, "children": []} for b in buckets}

    def placed_under(value: str) -> str | None:
        frontier, seen = list(parents.get(value, [])), {value}
        while frontier:
            nxt: list[str] = []
            for up in frontier:
                if up in nodes:
                    return up
                if up not in seen:
                    seen.add(up)
                    nxt.extend(parents.get(up, []))
            frontier = nxt
        return None

    roots: list[dict[str, Any]] = []
    for value, node in nodes.items():
        parent = placed_under(value)
        (nodes[parent]["children"] if parent is not None else roots).append(node)

    def order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items.sort(key=lambda n: (-int(n["count"]), str(n["label"]).lower()))
        for item in items:
            order(item["children"])
        return items

    return order(roots)


LANGUAGES = ["en", "nl", "de", "fr", "es", "it", "pt", "sv", "da", "fi"]
SUGGEST_TITLES = 5
SUGGEST_VALUES = 6


def _like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def bundled_matches(prefix: str) -> list[tuple[str, str]]:
    """Bundled labels (any language) that start with ``prefix``: (IRI, label)."""
    out: list[tuple[str, str]] = []
    for iri in BUNDLED:
        for label in bundled_labels(iri).values():
            if label.lower().startswith(prefix):
                out.append((iri, label))
                break
    return out[:200]


START, STOP = "\x02", "\x03"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _plain(text: str) -> str:
    """Harvested text without control characters, so the markers cannot be forged."""
    return _CONTROL.sub(" ", text)


def _mark_prefixes(text: str, words: list[str]) -> str:
    if not text or not words:
        return text

    def mark(match: re.Match[str]) -> str:
        token = match.group(0)
        return f"{START}{token}{STOP}" if token.lower().startswith(tuple(words)) else token

    return re.sub(r"\w+", mark, text)


def _segments(marked: str) -> list[dict[str, Any]]:
    """``a\x02b\x03c`` → ``[{"text": "a", "match": false}, {"text": "b", "match": true}, …]``."""
    out: list[dict[str, Any]] = []
    for part in re.split(f"({START}[^{STOP}]*{STOP})", marked):
        if not part:
            continue
        if part.startswith(START):
            out.append({"text": part[1:-1], "match": True})
        else:
            out.append({"text": part, "match": False})
    return out


def curie(iri: str) -> str | None:
    """``dct:title`` for a property in a well-known namespace, else None."""
    best = max(
        (prefix for prefix, namespace in PREFIXES.items() if iri.startswith(namespace)),
        key=lambda prefix: len(PREFIXES[prefix]),
        default=None,
    )
    if best is None:
        return None
    local = iri[len(PREFIXES[best]) :]
    return f"{best}:{local}" if re.fullmatch(r"[A-Za-z_][\w.-]*", local) else None


def _rows(result: Any) -> list[tuple[int, str, UUID]]:
    return [(int(rid), str(kind), entry) for rid, kind, entry in result]


def _has(name: str, values: list[str], resource_id: Any) -> ColumnElement[bool]:
    # An alias: the facet queries select from search_value themselves.
    value = aliased(SearchValue)
    return exists().where(
        value.resource_id == resource_id, value.field == name, value.value.in_(values)
    )


async def _values(session: AsyncSession, ids: list[int]) -> dict[int, list[SearchValue]]:
    found: dict[int, list[SearchValue]] = defaultdict(list)
    for start in range(0, len(ids), 500):
        for value in await session.scalars(
            select(V)
            .where(V.resource_id.in_(ids[start : start + 500]), ~V.field.startswith("p:"))
            .order_by(V.field, V.value)
        ):
            found[value.resource_id].append(value)
    return found


def _entry_title(entry: IndexEntry) -> str:
    return (entry.repository_metadata or {}).get("title") or entry.client_url


def _fdp_view(entry: IndexEntry, threshold: datetime) -> dict[str, Any]:
    return {
        "uuid": str(entry.uuid),
        "clientUrl": entry.client_url,
        "title": _entry_title(entry),
        "state": api_state(entry, threshold),
    }


def _brief(resource: SearchResource, lang: str) -> dict[str, Any]:
    picked = pick_language(resource.titles, lang)
    return {
        "id": resource.public_id,
        "iri": resource.iri,
        "type": resource.kind,
        "title": picked[1] if picked else resource.title,
    }
