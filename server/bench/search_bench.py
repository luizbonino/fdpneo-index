"""Search performance check on a synthetic corpus (TASKS 6.15, search design §7).

Generates ``--entries`` FAIR Data Points of ``--nodes`` harvested nodes each,
shaped like the test fixtures (an FDP, catalogs, datasets, distributions and
data services; EU authority IRIs and plain strings mixed; some multilingual
titles), with words, keywords and themes drawn from Zipf distributions so
that terms have realistic selectivity. Every entry goes through the real
``project_entry`` and the indexer's write path, in worker processes. Then it
times the main query shapes through ``SearchService`` and keeps
``EXPLAIN (ANALYZE, BUFFERS)`` of every statement each shape sends.

    uv run python bench/search_bench.py --entries 1000 --nodes 2000 --out bench/out

Without ``--database-url`` a PostgreSQL 18 container is started with the
settings printed at the top of the report. ``--skip-load`` measures an
existing corpus again (with ``--database-url``).

Not a test: it takes tens of minutes at full scale and needs Docker.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import contextlib
import hashlib
import itertools
import json
import multiprocessing
import random
import re
import statistics
import time
from collections.abc import Awaitable, Callable, Generator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from fdpindex.db import make_engine, make_session_factory
from fdpindex.models import EntryPermit, EntryState, IndexEntry, SearchResource
from fdpindex.search.indexer import SearchIndexer, fingerprint
from fdpindex.search.profile import default_profile
from fdpindex.search.project import project_entry
from fdpindex.search.query import SearchParams, SearchService
from fdpindex.settings import SettingsService

NAMESPACE = UUID("8d2b3c1e-5f7a-4c3b-9a51-6f0e2d4b7a10")
EU = "http://publications.europa.eu/resource/authority/"
DCAT = "http://www.w3.org/ns/dcat#"
DCT = "http://purl.org/dc/terms/"
FOAF = "http://xmlns.com/foaf/0.1/"
VCARD = "http://www.w3.org/2006/vcard/ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"
FDPO = "https://w3id.org/fdp/fdp-o#"
R3D = "http://www.re3data.org/schema/3-0#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
HEALTH = "http://healthdataportal.eu/ns/health#"

POSTGRES_SETTINGS = {
    "shared_buffers": "1GB",
    "effective_cache_size": "3GB",
    "work_mem": "32MB",
    "maintenance_work_mem": "256MB",
    "max_wal_size": "4GB",
    "random_page_cost": "1.1",
}

# Stopwords take the top Zipf ranks, as in real text (the language configurations
# drop them, the simple one keeps them); then domain words, then a long tail.
STOPWORDS = """
the of and in to a for with on by from is are this that as at be an or which
was were it its these their has have been not all also into more than other
""".split()
DOMAIN = """
health data study patients cohort clinical registry cancer research survey
population disease hospital care treatment genome genomics sequencing samples
biobank imaging records trial outcomes lung function heart diabetes covid
infection vaccine mortality children ageing nutrition environment climate
water air quality soil agriculture energy transport mobility economy labour
education census household income housing crime justice elections budget
procurement statistics municipal regional national european international
monitoring measurements observations sensors satellite species biodiversity
ocean marine forest land use emissions pollution noise traffic accidents
library archive collection museum heritage language corpus texts annotations
interviews questionnaire panel longitudinal annual monthly daily weekly
breast prostate colorectal leukaemia rare disorders mental depression stroke
dementia asthma allergy obesity kidney liver pregnancy birth neonatal
pharmacy prescriptions medication adverse events laboratory tests blood urine
proteomics metabolomics transcriptomics microbiome variants phenotypes
""".split()
LANGS = ["nl", "de", "fr", "es", "it", "pt"]
THEMES = [
    *("HEAL", "SOCI", "ENVI", "TECH", "ECON", "EDUC", "GOVE"),
    *("AGRI", "TRAN", "ENER", "REGI", "JUST", "INTR"),
]
FORMATS = [
    f"{EU}file-type/CSV",
    f"{EU}file-type/JSON",
    "CSV",
    "csv",
    f"{EU}file-type/XLSX",
    "parquet",
    f"{EU}file-type/PDF",
    "VCF",
    f"{EU}file-type/RDF_TURTLE",
    "text/csv",
    f"{EU}file-type/ZIP",
    "FASTQ",
    "DICOM",
    f"{EU}file-type/XML",
]
MEDIA = ["text/csv", "application/json", "application/zip", "application/pdf", "text/turtle"]
LICENSES = [
    "http://creativecommons.org/licenses/by/4.0/",
    "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-4.0",
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "http://creativecommons.org/licenses/by-nc/4.0/",
    f"{EU}licence/CC_BY_4_0",
    "https://opensource.org/licenses/MIT",
    "http://creativecommons.org/licenses/by-sa/4.0/",
]
ACCESS = [
    f"{EU}access-right/PUBLIC",
    f"{EU}access-right/RESTRICTED",
    f"{EU}access-right/NON_PUBLIC",
]
COUNTRIES = ["NL", "DE", "FR", "BE", "ES", "IT", "SE", "FI", "DK", "PT", "CH", "GB", "US"]


class Zipf:
    """Draw from ``items`` with weight ``1 / rank ** s``."""

    def __init__(self, items: list[str], s: float = 1.0) -> None:
        self.items = items
        weights = [1 / (rank**s) for rank in range(1, len(items) + 1)]
        self.cumulative = list(itertools.accumulate(weights))

    def draw(self, rng: random.Random) -> str:
        point = rng.random() * self.cumulative[-1]
        return self.items[bisect.bisect_left(self.cumulative, point)]

    def sample(self, rng: random.Random, n: int) -> list[str]:
        out: list[str] = []
        while len(out) < n:
            item = self.draw(rng)
            if item not in out:
                out.append(item)
        return out


def _tail(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    consonants, vowels = "bcdfghklmnprstvz", "aeiou"
    words: set[str] = set()
    while len(words) < n:
        length = rng.randint(2, 4)
        words.add("".join(rng.choice(consonants) + rng.choice(vowels) for _ in range(length)))
    return sorted(words)


WORDS = Zipf(STOPWORDS + DOMAIN + _tail(20_000, 1), 1.05)
RARE = WORDS.items[6_000]
KEYWORDS = Zipf(
    DOMAIN + [f"{a} {b}" for a, b in zip(DOMAIN, DOMAIN[7:], strict=False)] + _tail(4_000, 2), 1.1
)
THEME_DRAW = Zipf(THEMES, 1.2)
FORMAT_DRAW = Zipf(FORMATS, 1.0)
LICENSE_DRAW = Zipf(LICENSES, 1.0)


def _lit(value: str, lang: str | None = None, dtype: str | None = None) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    if lang:
        return f'"{escaped}"@{lang}'
    if dtype:
        return f'"{escaped}"^^<{dtype}>'
    return f'"{escaped}"'


def _iri_or_lit(value: str) -> str:
    return f"<{value}>" if value.startswith("http") else _lit(value)


@dataclass
class _Node:
    iri: str
    lines: list[str] = field(default_factory=lambda: [])

    def add(self, p: str, o: str, s: str | None = None) -> None:
        self.lines.append(f"<{s or self.iri}> <{p}> {o} .")

    def nt(self) -> str:
        return "\n".join(self.lines) + "\n"


def _sentence(rng: random.Random, lo: int, hi: int) -> str:
    words = [WORDS.draw(rng) for _ in range(rng.randint(lo, hi))]
    return " ".join(words).capitalize()


def _date(rng: random.Random, start: int = 2010) -> datetime:
    begin = datetime(start, 1, 1, tzinfo=UTC)
    return begin + timedelta(days=rng.randint(0, (datetime(2026, 11, 1, tzinfo=UTC) - begin).days))


def entry_uuid(i: int) -> UUID:
    return uuid5(NAMESPACE, f"entry-{i}")


def generate(i: int, size: int) -> list[tuple[str, str]]:
    """``(node IRI, N-Triples)`` for synthetic FDP ``i`` with about ``size`` nodes."""
    rng = random.Random(i)
    base = f"https://fdp{i}.bench.example"
    org = f"https://ror.org/bench{i:05d}"
    org_name = f"{_sentence(rng, 1, 2)} Institute {i}"
    agents = [
        (f"{base}/agent/{k}", _sentence(rng, 1, 3) + " Lab") for k in range(rng.randint(1, 20))
    ]
    n_catalogs = min(rng.randint(1, 8), max(1, size // 50))
    n_services = max(0, size // 100 + rng.randint(-5, 5)) if size >= 100 else 0
    rest = max(0, size - 1 - n_catalogs - n_services)
    n_datasets = max(1, rest // 3)
    n_distributions = max(0, rest - n_datasets)
    nodes: list[_Node] = []

    fdp = _Node(base)
    fdp.add(RDF_TYPE, f"<{FDPO}FAIRDataPoint>")
    fdp.add(RDF_TYPE, f"<{R3D}Repository>")
    fdp.add(f"{DCT}title", _lit(f"{org_name} FDP", "en"))
    fdp.add(f"{DCT}publisher", f"<{org}>")
    fdp.add(f"{FOAF}name", _lit(org_name), org)
    fdp.add(f"{R3D}country", f"<http://lexvo.org/id/iso3166/{rng.choice(COUNTRIES)}>")
    nodes.append(fdp)

    catalogs: list[_Node] = []
    for c in range(n_catalogs):
        catalog = _Node(f"{base}/catalog/c{c}")
        catalog.add(RDF_TYPE, f"<{DCAT}Catalog>")
        catalog.add(f"{DCT}title", _lit(_sentence(rng, 2, 5) + " catalog", "en"))
        catalog.add(f"{DCT}description", _lit(_sentence(rng, 10, 40), "en"))
        catalog.add(f"{DCT}publisher", f"<{org}>")
        catalog.add(f"{FOAF}name", _lit(org_name), org)
        catalog.add(f"{DCAT}theme", f"<{EU}data-theme/{THEME_DRAW.draw(rng)}>")
        fdp.add(f"{FDPO}metadataCatalog", f"<{catalog.iri}>")
        catalogs.append(catalog)
        nodes.append(catalog)

    datasets: list[_Node] = []
    for d in range(n_datasets):
        catalog = catalogs[d % n_catalogs]
        ds = _Node(f"{base}/dataset/d{d}")
        ds.add(RDF_TYPE, f"<{DCAT}Dataset>")
        ds.add(f"{DCT}title", _lit(_sentence(rng, 3, 8), "en"))
        if rng.random() < 0.2:
            ds.add(f"{DCT}title", _lit(_sentence(rng, 3, 8), rng.choice(LANGS)))
        ds.add(f"{DCT}description", _lit(_sentence(rng, 20, 80), "en"))
        for keyword in KEYWORDS.sample(rng, rng.randint(1, 6)):
            ds.add(f"{DCAT}keyword", _lit(keyword, "en"))
        for theme in THEME_DRAW.sample(rng, rng.randint(1, 2)):
            ds.add(f"{DCAT}theme", f"<{EU}data-theme/{theme}>")
        ds.add(f"{DCT}license", _iri_or_lit(LICENSE_DRAW.draw(rng)))
        ds.add(f"{DCT}accessRights", f"<{ACCESS[min(2, int(rng.expovariate(1.5)))]}>")
        ds.add(f"{DCT}language", _iri_or_lit(rng.choice(["en", f"{EU}language/ENG", "nl"])))
        if rng.random() < 0.8:
            ds.add(f"{DCT}publisher", f"<{org}>")
            ds.add(f"{FOAF}name", _lit(org_name), org)
        else:
            agent, name = rng.choice(agents)
            ds.add(f"{DCT}publisher", f"<{agent}>")
            ds.add(f"{FOAF}name", _lit(name), agent)
        issued = _date(rng)
        ds.add(f"{DCT}issued", _lit(issued.date().isoformat(), dtype=f"{XSD}date"))
        if rng.random() < 0.7:
            modified = min(
                issued + timedelta(days=rng.randint(0, 2000)), datetime(2026, 11, 1, tzinfo=UTC)
            )
            ds.add(f"{DCT}modified", _lit(modified.isoformat(), dtype=f"{XSD}dateTime"))
        ds.add(f"{DCT}identifier", _lit(f"bench-{i}-{d}"))
        contact = f"{ds.iri}#contact"
        ds.add(f"{DCAT}contactPoint", f"<{contact}>")
        ds.add(f"{VCARD}fn", _lit("Data desk"), contact)
        ds.add(f"{VCARD}hasEmail", f"<mailto:desk@fdp{i}.bench.example>", contact)
        if rng.random() < 0.3:
            ds.add(
                f"{HEALTH}numberOfRecords",
                _lit(str(int(rng.lognormvariate(7, 2.5))), dtype=f"{XSD}integer"),
            )
        if rng.random() < 0.2:
            ds.add(f"{DCT}conformsTo", f"<https://schemas.bench.example/{rng.randint(1, 30)}>")
        catalog.add(f"{DCAT}dataset", f"<{ds.iri}>")
        datasets.append(ds)
        nodes.append(ds)

    for x in range(n_distributions):
        ds = datasets[rng.randrange(n_datasets)]
        dist = _Node(f"{base}/distribution/x{x}")
        dist.add(RDF_TYPE, f"<{DCAT}Distribution>")
        fmt = FORMAT_DRAW.draw(rng)
        if rng.random() < 0.85:
            dist.add(f"{DCT}title", _lit(f"{fmt.rsplit('/', 1)[-1]} export", "en"))
        dist.add(f"{DCT}format", _iri_or_lit(fmt))
        if rng.random() < 0.5:
            dist.add(
                f"{DCAT}mediaType",
                f"<https://www.iana.org/assignments/media-types/{rng.choice(MEDIA)}>",
            )
        if rng.random() < 0.5:
            dist.add(f"{DCT}license", _iri_or_lit(LICENSE_DRAW.draw(rng)))
        dist.add(f"{DCAT}downloadURL", f"<{base}/files/{x}>")
        ds.add(f"{DCAT}distribution", f"<{dist.iri}>")
        nodes.append(dist)

    for s in range(n_services):
        service = _Node(f"{base}/service/s{s}")
        service.add(RDF_TYPE, f"<{DCAT}DataService>")
        service.add(f"{DCT}title", _lit(_sentence(rng, 2, 4) + " API", "en"))
        service.add(f"{DCT}description", _lit(_sentence(rng, 10, 30), "en"))
        service.add(f"{DCAT}endpointURL", f"<{base}/api/{s}>")
        for ds in rng.sample(datasets, min(3, n_datasets)):
            service.add(f"{DCAT}servesDataset", f"<{ds.iri}>")
        catalogs[s % n_catalogs].add(f"{DCAT}service", f"<{service.iri}>")
        nodes.append(service)

    return [(node.iri, node.nt()) for node in nodes]


# --- loading -------------------------------------------------------------------------


def _load_worker(url: str, indices: list[int], size: int) -> dict[str, float]:
    return asyncio.run(_load(url, indices, size))


async def _load(url: str, indices: list[int], size: int) -> dict[str, float]:
    engine = make_engine(url)
    factory = make_session_factory(engine)
    indexer = SearchIndexer(factory, default_profile(), debounce=0)
    totals = {"generate": 0.0, "project": 0.0, "write": 0.0, "resources": 0.0, "values": 0.0}
    try:
        for i in indices:
            t0 = time.perf_counter()
            nodes = generate(i, size)
            t1 = time.perf_counter()
            projection = project_entry(str(entry_uuid(i)), nodes, indexer.profile)
            t2 = time.perf_counter()
            wanted = fingerprint(hashlib.sha256(nt.encode()).hexdigest() for _, nt in nodes)
            await indexer._write(entry_uuid(i), projection, wanted)
            t3 = time.perf_counter()
            totals["generate"] += t1 - t0
            totals["project"] += t2 - t1
            totals["write"] += t3 - t2
            totals["resources"] += len(projection.resources)
            totals["values"] += sum(len(r.values) for r in projection.resources)
    finally:
        await engine.dispose()
    return totals


async def seed_entries(engine: AsyncEngine, n: int) -> None:
    factory = make_session_factory(engine)
    now = datetime.now(UTC)
    rng = random.Random(0)
    async with factory() as session, session.begin():
        for i in range(n):
            roll = rng.random()
            state = (
                EntryState.VALID
                if roll < 0.85
                else EntryState.UNREACHABLE
                if roll < 0.95
                else EntryState.INVALID
            )
            session.add(
                IndexEntry(
                    uuid=entry_uuid(i),
                    client_url=f"https://fdp{i}.bench.example",
                    state=state,
                    permit=EntryPermit.ACCEPTED,
                    last_retrieval_at=now - timedelta(hours=rng.randint(1, 24 * 14)),
                    repository_metadata={"repositoryTitle": f"Bench FDP {i}"},
                )
            )


def load(url: str, entries: int, size: int, workers: int) -> dict[str, float]:
    chunks = [list(range(w, entries, workers)) for w in range(workers)]
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(workers, mp_context=context) as pool:
        results = list(pool.map(_load_worker, [url] * workers, chunks, [size] * workers))
    return {key: sum(r[key] for r in results) for key in results[0]}


# --- measuring -----------------------------------------------------------------------


@dataclass
class Statement:
    sql: str
    params: Any
    ms: float


class Recorder:
    """Every statement the engine sends, with its time, while ``active``."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.active = False
        self.statements: list[Statement] = []
        sync = engine.sync_engine

        @event.listens_for(sync, "before_cursor_execute")
        def before(conn: Any, cursor: Any, sql: str, params: Any, context: Any, many: bool) -> None:  # noqa: ARG001  # pyright: ignore[reportUnusedFunction]
            conn.info["bench_start"] = time.perf_counter()

        @event.listens_for(sync, "after_cursor_execute")
        def after(conn: Any, cursor: Any, sql: str, params: Any, context: Any, many: bool) -> None:  # noqa: ARG001  # pyright: ignore[reportUnusedFunction]
            if self.active and not sql.lstrip().upper().startswith("SET"):
                ms = (time.perf_counter() - conn.info.pop("bench_start")) * 1000
                self.statements.append(Statement(sql, params, ms))

    @contextlib.contextmanager
    def capture(self) -> Generator[list[Statement]]:
        self.statements = []
        self.active = True
        try:
            yield self.statements
        finally:
            self.active = False


async def explain(engine: AsyncEngine, statement: Statement) -> dict[str, Any]:
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        driver: Any = raw.driver_connection
        params = statement.params if isinstance(statement.params, (list, tuple)) else ()
        rows = await driver.fetch(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement.sql, *params
        )
        text_rows = await driver.fetch("EXPLAIN (ANALYZE, BUFFERS) " + statement.sql, *params)
    raw_plan: Any = rows[0][0]
    plan = (json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan)[0]
    return {
        "ms": round(plan["Execution Time"], 1),
        "planning_ms": round(plan["Planning Time"], 1),
        "text": "\n".join(r[0] for r in text_rows),
    }


async def measure(
    engine: AsyncEngine,
    recorder: Recorder,
    shapes: list[tuple[str, Callable[[], Awaitable[Any]]]],
    repeat: int,
    out: Path | None,
) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for name, run in shapes:
        times: list[float] = []
        statements: list[Statement] = []
        result: Any = None
        for _ in range(repeat):
            with recorder.capture() as captured:
                t0 = time.perf_counter()
                result = await run()
                times.append((time.perf_counter() - t0) * 1000)
            statements = statements or list(captured)
        slowest = sorted(statements, key=lambda s: s.ms, reverse=True)[:3]
        plans = [await explain(engine, s) for s in slowest[:1]]
        total = result.get("totalResults") if isinstance(result, dict) else None
        row: dict[str, Any] = {
            "shape": name,
            "first_ms": round(times[0]),
            "median_ms": round(statistics.median(times[1:] or times)),
            "statements": len(statements),
            "slowest_ms": [round(s.ms) for s in slowest],
            "statement_ms": [round(s.ms) for s in statements],
            "total": total,
            "explain_ms": plans[0]["ms"] if plans else None,
        }
        report.append(row)
        print(json.dumps(row), flush=True)
        if out and plans:
            slug = name.replace(" ", "_").replace("/", "-")
            (out / f"{slug}.sql").write_text(
                slowest[0].sql + "\n-- params: " + repr(slowest[0].params)
            )
            (out / f"{slug}.plan.txt").write_text(plans[0]["text"])
    return report


def shapes_for(
    service: SearchService, fdp: UUID, resource_id: str
) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
    heal = f"{EU}data-theme/HEAL"
    csv = f"{EU}file-type/CSV"
    cc_by = "http://creativecommons.org/licenses/by/4.0/"

    def search(**kw: Any) -> Callable[[], Awaitable[Any]]:
        return lambda: service.search(SearchParams(**kw))

    return [
        ("landing", service.landing),
        ("browse all", search()),
        ("browse datasets", search(types=["dataset"])),
        ("browse no facets", search(facets=[])),
        ("text common: health", search(q="health")),
        ("text medium: dementia", search(q="dementia")),
        ("text rare: one tail word", search(q=RARE)),
        ("text two words: cancer registry", search(q="cancer registry")),
        ("text phrase: health data", search(q='"health data"')),
        ("text prefix: gen", search(q="gen")),
        (
            "text + filter: cancer, HEAL, datasets",
            search(q="cancer", types=["dataset"], filters={"theme": [heal]}),
        ),
        ("filter theme HEAL", search(filters={"theme": [heal]})),
        (
            "filter theme+format+license",
            search(filters={"theme": [heal], "format": [csv], "license": [cc_by]}),
        ),
        ("filter not HEAL", search(filters={"theme": [f"!{heal}"]})),
        ("filter one FDP", search(fdps=[fdp])),
        (
            "filter modified since 2025",
            search(ranges={"modified": (datetime(2025, 1, 1, tzinfo=UTC), None)}),
        ),
        ("filter active FDPs", search(fdp_state="ACTIVE")),
        ("deep page by title", search(sort="title,asc", page=499, size=20, facets=[])),
        (
            "facet dialog keyword ~ca",
            lambda: service.facet(SearchParams(), "keyword", contains="ca"),
        ),
        ("facet dialog fdp", lambda: service.facet(SearchParams(), "fdp")),
        ("resource page", lambda: service.resource(resource_id)),
    ]


async def sizes(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT relname, n_live_tup, pg_total_relation_size(relid), pg_relation_size(relid) "
                    "FROM pg_stat_user_tables WHERE relname LIKE 'search_%' ORDER BY 3 DESC"
                )
            )
        ).all()
        indexes = (
            await conn.execute(
                text(
                    "SELECT indexrelname, pg_relation_size(indexrelid) FROM pg_stat_user_indexes "
                    "WHERE relname LIKE 'search_%' ORDER BY 2 DESC"
                )
            )
        ).all()
    mb = 1024 * 1024
    return {
        "tables": {
            r[0]: {"rows": r[1], "total_mb": round(r[2] / mb), "heap_mb": round(r[3] / mb)}
            for r in tables
        },
        "indexes": {r[0]: round(r[1] / mb) for r in indexes},
    }


async def gin_builds(engine: AsyncEngine, memories: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        for memory in memories:
            await conn.execute(text(f"SET maintenance_work_mem = '{memory}'"))
            await conn.execute(text("SET max_parallel_maintenance_workers = 0"))
            t0 = time.perf_counter()
            await conn.execute(text("REINDEX INDEX ix_search_resource_tsv_lang"))
            out[memory] = round(time.perf_counter() - t0, 1)
            print(json.dumps({"gin_rebuild": memory, "seconds": out[memory]}), flush=True)
    return out


async def run(args: argparse.Namespace, url: str) -> dict[str, Any]:
    engine = make_engine(url)
    report: dict[str, Any] = {
        "entries": args.entries,
        "nodes": args.nodes,
        "postgres": POSTGRES_SETTINGS,
    }
    try:
        if not args.skip_load:
            await seed_entries(engine, args.entries)
            t0 = time.perf_counter()
            totals = load(url, args.entries, args.nodes, args.workers)
            report["load"] = {
                "seconds": round(time.perf_counter() - t0),
                "workers": args.workers,
                **{k: round(v, 1) for k, v in totals.items()},
            }
            print(json.dumps({"load": report["load"]}), flush=True)
            async with engine.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                t0 = time.perf_counter()
                await conn.execute(text("VACUUM ANALYZE"))
                report["vacuum_analyze_seconds"] = round(time.perf_counter() - t0)
        report["sizes"] = await sizes(engine)
        print(json.dumps(report["sizes"]), flush=True)

        factory = make_session_factory(engine)
        settings = SettingsService(factory, auto_permit_default=True)
        indexer = SearchIndexer(factory, default_profile(), debounce=0)
        service = SearchService(
            factory, settings, indexer, timeout_seconds=300, admin_timeout_seconds=300
        )
        async with factory() as session:
            resource_id = await session.scalar(
                select(SearchResource.public_id).where(SearchResource.kind == "dataset").limit(1)
            )
        recorder = Recorder(engine)
        out = Path(args.out) if args.out else None
        if out:
            out.mkdir(parents=True, exist_ok=True)
        shapes = shapes_for(service, entry_uuid(0), str(resource_id))
        if args.only:
            shapes = [s for s in shapes if re.search(args.only, s[0])]
        report["queries"] = await measure(engine, recorder, shapes, args.repeat, out)
        if args.gin:
            report["gin_rebuild_seconds"] = await gin_builds(engine, ["64MB", "256MB", "1GB"])
    finally:
        await engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--entries", type=int, default=1000)
    parser.add_argument("--nodes", type=int, default=2000)
    parser.add_argument(
        "--workers", type=int, default=max(1, (multiprocessing.cpu_count() or 2) - 2)
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--gin", action="store_true", help="also time GIN rebuilds")
    parser.add_argument("--out", default="")
    parser.add_argument("--only", default="", help="measure only shapes matching this regex")
    args = parser.parse_args()

    if args.database_url:
        report = asyncio.run(run(args, args.database_url))
    else:
        from alembic import command
        from testcontainers.community.postgres import PostgresContainer

        from fdpindex.cli import alembic_config

        settings = " ".join(f"-c {k}={v}" for k, v in POSTGRES_SETTINGS.items())
        container = PostgresContainer("postgres:18-alpine", driver="asyncpg")
        container.with_command(f"postgres {settings}").with_kwargs(shm_size="1g")
        with container:
            url = container.get_connection_url()
            print(json.dumps({"database": url}), flush=True)
            config = alembic_config(url)
            config.attributes["configure_logger"] = False
            command.upgrade(config, "head")
            report = asyncio.run(run(args, url))
    if args.out:
        (Path(args.out) / "report.json").write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
