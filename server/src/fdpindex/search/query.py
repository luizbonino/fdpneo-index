"""Faceted search over the projection (search design §3.6, §3.9, §4, §5).

Filters are ``EXISTS`` conditions on ``search_value`` (OR within a field,
AND across fields, ``!value`` negates); facet counts are disjunctive (a
facet's own filter is left out of its count). Text matching and ranking use
the PostgreSQL vectors; on SQLite (unit tests) words are matched with
``LIKE`` and hits are sorted by date.

Every PostgreSQL query runs under ``statement_timeout``; a timeout is a 503.

Deviations from the design: no ``search_facet_cache`` (facet counts are a
``GROUP BY`` on an index; the cache waits for the performance check, task
6.15); no keyset cursor, highlights or duplicate grouping (Phases 8 and 9).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from rdflib import Graph
from sqlalchemy import (
    REAL,
    ColumnElement,
    and_,
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
    EntryPermit,
    EntryState,
    HarvestedNode,
    IndexEntry,
    SearchEntryState,
    SearchLabel,
    SearchResource,
    SearchValue,
)
from fdpindex.search.profile import COLUMN_FIELDS, KINDS, Kind, Profile
from fdpindex.search.project import SIMPLE_CONFIG, bucket_label, pick_language, ts_config
from fdpindex.search.vocab import bundled_label
from fdpindex.service import api_state
from fdpindex.sparql.labels import humanize

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from fdpindex.search.indexer import SearchIndexer
    from fdpindex.settings import SettingsService

log = structlog.get_logger(__name__)

R = SearchResource
E = IndexEntry
V = SearchValue

DEFAULT_TYPES: tuple[Kind, ...] = ("fdp", "catalog", "dataset", "series", "service", "other")
DEFAULT_FACETS = (
    "type",
    "theme",
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
MAX_FACETS = 12
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
    facets: list[str] = field(default_factory=lambda: list(DEFAULT_FACETS))
    facet_size: int = 20

    def validate(self, profile: Profile) -> None:
        self.q = self.q.strip()
        if len(self.q) > MAX_Q:
            raise BadRequest(f"q is longer than {MAX_Q} characters")
        unknown = [t for t in self.types if t not in KINDS]
        if unknown:
            raise BadRequest(f"Unknown type: {', '.join(unknown)}")
        if not self.types:
            self.types = list(DEFAULT_TYPES)
        facets = set(profile.facets)
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
    """Labels for IRIs: harvested (in the requested language first), bundled, humanised."""

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
        for iri in wanted:
            picked = pick_language(found.get(iri, {}), self._lang)
            self._cache[iri] = picked[1] if picked else (bundled_label(iri) or humanize(iri))
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
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self.indexer = indexer
        self.profile = indexer.profile
        self._timeout = timeout_seconds
        self._admin_timeout = admin_timeout_seconds
        self._landing: tuple[int, float, dict[str, Any]] | None = None

    # --- sessions ----------------------------------------------------------------

    async def _run[T](self, admin: bool, work: Callable[[AsyncSession, bool], Awaitable[T]]) -> T:
        """Run ``work(session, postgres)`` in a read transaction with a statement timeout."""
        async with self._session_factory() as session, session.begin():
            postgres = session.get_bind().dialect.name == "postgresql"
            if postgres:
                ms = int((self._admin_timeout if admin else self._timeout) * 1000)
                await session.execute(text(f"SET LOCAL statement_timeout = {ms}"))
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
        return utcnow() - settings.ping.valid_duration

    # --- conditions --------------------------------------------------------------

    def _conditions(
        self,
        params: SearchParams,
        threshold: datetime,
        *,
        postgres: bool,
        skip: str | None = None,
    ) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [E.permit == EntryPermit.ACCEPTED]
        if skip != "type":
            conditions.append(R.kind.in_(params.types))
        if skip != "fdp" and params.fdps:
            conditions.append(R.entry_uuid.in_(params.fdps))
        for name, values in params.filters.items():
            if name == skip or not values:
                continue
            wanted = [v for v in values if not v.startswith("!")]
            unwanted = [v[1:] for v in values if v.startswith("!")]
            if wanted:
                conditions.append(_has(name, wanted))
            if unwanted:
                conditions.append(not_(_has(name, unwanted)))
        columns = {"issued": R.issued, "modified": R.modified, "harvested": R.harvested_at}
        for name, (low, high) in params.ranges.items():
            column = columns[name]
            if low is not None:
                conditions.append(column >= low)
            if high is not None:
                conditions.append(column <= high)
        if params.fdp_state:
            conditions.append(and_(E.state == EntryState.VALID, E.last_retrieval_at > threshold))
        if params.q:
            conditions.append(self._text(params, postgres=postgres))
        return conditions

    def _queries(self, params: SearchParams) -> tuple[Any, Any]:
        q_lang = func.websearch_to_tsquery(
            cast(literal(ts_config(params.lang)), REGCONFIG), params.q
        )
        q_simple = func.websearch_to_tsquery(cast(literal(SIMPLE_CONFIG), REGCONFIG), params.q)
        return q_lang, q_simple

    def _text(self, params: SearchParams, *, postgres: bool) -> ColumnElement[bool]:
        if postgres:
            q_lang, q_simple = self._queries(params)
            return or_(R.tsv_lang.op("@@")(q_lang), R.tsv_simple.op("@@")(q_simple))
        conditions: list[ColumnElement[bool]] = []
        for word in params.q.lower().replace('"', " ").split():
            if word == "or":
                continue
            needle = word.lstrip("-").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if not needle:
                continue
            match = R.search_text.like(f"%{needle}%", escape="\\")
            conditions.append(not_(match) if word.startswith("-") else match)
        return and_(*conditions) if conditions else literal(True)

    def _score(self, params: SearchParams, threshold: datetime) -> ColumnElement[float]:
        q_lang, q_simple = self._queries(params)
        weights = cast(literal(RANK_WEIGHTS), ARRAY(REAL))
        text_rank = func.greatest(
            func.ts_rank_cd(weights, R.tsv_lang, q_lang, 33),
            0.9 * func.ts_rank_cd(weights, R.tsv_simple, q_simple, 33),
        )
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
        params.validate(self.profile)
        threshold = await self._threshold()
        started = time.perf_counter()

        async def work(session: AsyncSession, postgres: bool) -> dict[str, Any]:
            conditions = self._conditions(params, threshold, postgres=postgres)
            base = select(R.id).join(E, E.uuid == R.entry_uuid).where(*conditions)
            total = int(
                await session.scalar(select(func.count()).select_from(base.subquery())) or 0
            )
            score = self._score(params, threshold) if postgres and params.q else None
            columns: list[Any] = [R, E]
            if score is not None:
                columns.append(score)
            query = (
                select(*columns)
                .join(E, E.uuid == R.entry_uuid)
                .where(*conditions)
                .order_by(*self._order(params, score))
                .offset(params.page * params.size)
                .limit(params.size)
            )
            rows = (await session.execute(query)).all()
            labels = Labels(session, params.lang)
            hits = await self._hits(
                session, rows, params, threshold, labels, scored=score is not None
            )
            facets = {
                name: await self._facet(
                    session,
                    params,
                    name,
                    threshold,
                    labels,
                    postgres=postgres,
                    size=params.facet_size,
                )
                for name in dict.fromkeys(params.facets)
            }
            return {
                "query": {
                    "q": params.q,
                    "types": params.types,
                    "filters": params.filters,
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
    ) -> dict[str, Any]:
        """One facet's buckets beyond the top ones, for the "show more" dialog."""
        params.facets = []
        params.validate(self.profile)
        if name not in self.profile.facets and name not in {"type", "fdp"}:
            raise BadRequest(f"Unknown facet: {name}")
        threshold = await self._threshold()

        async def work(session: AsyncSession, postgres: bool) -> dict[str, Any]:
            labels = Labels(session, params.lang)
            result = await self._facet(
                session, params, name, threshold, labels, postgres=postgres, size=1000
            )
            needle = contains.strip().lower()
            if needle:
                result["buckets"] = [
                    b
                    for b in result["buckets"]
                    if needle in b["value"].lower() or needle in str(b["label"]).lower()
                ]
            result["buckets"] = result["buckets"][: max(1, min(size, 1000))]
            return result

        return await self._run(admin, work)

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
    ) -> dict[str, Any]:
        conditions = self._conditions(params, threshold, postgres=postgres, skip=name)
        count = func.count().label("n")
        if name == "type":
            key: Any = R.kind
            query = select(key, count).join(E, E.uuid == R.entry_uuid)
        elif name == "fdp":
            key = R.entry_uuid
            query = select(key, count).join(E, E.uuid == R.entry_uuid)
        else:
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
        return {
            "buckets": await self._bucket_views(session, name, buckets, labels, threshold),
            "more": len(rows) > size,
        }

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
        spec = self.profile.fields[name]
        if spec.kind == "number":
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

    def _value_views(
        self, values: list[SearchValue], labels: Labels
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in values:
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
        if (
            cached is not None
            and cached[0] == self.indexer.generation
            and time.monotonic() - cached[1] < 60
        ):
            return cached[2]
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
            "facets": [
                {
                    "name": name,
                    "label": spec.label,
                    "help": spec.help,
                    "group": spec.facet,
                    "kind": spec.kind,
                }
                for name, spec in self.profile.fields.items()
                if spec.facet is not None
            ],
            "fields": {
                name: {"label": spec.label, "kind": spec.kind}
                for name, spec in self.profile.fields.items()
                if name not in COLUMN_FIELDS
            },
            "cards": self.profile.cards,
            "indexing": {"pending": pending},
        }
        self._landing = (self.indexer.generation, time.monotonic(), result)
        return result

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
        return {
            "profileHash": self.profile.hash,
            "values": values,
            "labels": labels,
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


def _has(name: str, values: list[str]) -> ColumnElement[bool]:
    # An alias: the facet queries select from search_value themselves.
    value = aliased(SearchValue)
    return exists().where(value.resource_id == R.id, value.field == name, value.value.in_(values))


async def _values(session: AsyncSession, ids: list[int]) -> dict[int, list[SearchValue]]:
    found: dict[int, list[SearchValue]] = defaultdict(list)
    for start in range(0, len(ids), 500):
        for value in await session.scalars(
            select(V).where(V.resource_id.in_(ids[start : start + 500])).order_by(V.field, V.value)
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
