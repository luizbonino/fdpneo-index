"""Running validations off the event loop (conformance design §3).

SHACL validation is CPU-bound Python, so it runs in worker processes
(``FDPINDEX_CONFORMANCE_WORKERS``, default: the CPUs less one, at most four);
``0`` runs it in a thread of the server's process, as the unit tests do.
Each worker loads a pack's shapes and the vocabulary set once and keeps them.

A batch that overruns its budget (10 s per record) marks its records as
``error`` and replaces the workers, so one pathological record or operator
shape cannot hold validation up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from fdpindex.conformance.packs import Pack, load_pack
from fdpindex.conformance.records import record_graph
from fdpindex.conformance.validator import Outcome, ShaclValidator

if TYPE_CHECKING:
    from collections.abc import Sequence

log = structlog.get_logger(__name__)

SECONDS_PER_RECORD = 10.0


@dataclass(frozen=True)
class PackUse:
    directory: str
    key: str
    files: tuple[str, ...] | None


@dataclass(frozen=True)
class Job:
    """One record against the packs that apply to it."""

    root: str
    nodes: tuple[str, ...]
    packs: tuple[PackUse, ...]


_validator: ShaclValidator | None = None
_packs: dict[str, Pack] = {}


def run_jobs(jobs: Sequence[Job]) -> list[list[Outcome]]:
    """Validate each job's record against its packs. Runs in a worker (or a thread)."""
    global _validator
    if _validator is None:
        # A spawned worker has no logging set up: keep rdflib's per-literal warnings (with
        # tracebacks) about values that do not fit their datatype out of the output.
        logging.getLogger("rdflib.term").setLevel(logging.ERROR)
        _validator = ShaclValidator()
    out: list[list[Outcome]] = []
    for job in jobs:
        try:
            graph = record_graph(job.nodes)
        except Exception as err:
            out.append([Outcome(status="error", error=str(err)[:500]) for _ in job.packs])
            continue
        outcomes: list[Outcome] = []
        for use in job.packs:
            pack = _packs.get(use.directory)
            if pack is None:
                pack = _packs[use.directory] = load_pack(Path(use.directory))
            outcomes.append(_validator.validate(graph, pack, root=job.root, files=use.files))
        out.append(outcomes)
    return out


def default_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) - 1))


class ValidationPool:
    def __init__(self, workers: int) -> None:
        self._workers = workers
        self._pool: ProcessPoolExecutor | None = None

    def _executor(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=self._workers, mp_context=multiprocessing.get_context("spawn")
            )
        return self._pool

    async def run(self, jobs: Sequence[Job]) -> list[list[Outcome]]:
        if not jobs:
            return []
        budget = SECONDS_PER_RECORD * len(jobs)
        try:
            if self._workers <= 0:
                return await asyncio.wait_for(asyncio.to_thread(run_jobs, list(jobs)), budget)
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor(), run_jobs, list(jobs))
            return await asyncio.wait_for(future, budget)
        except TimeoutError:
            log.warning("conformance_batch_timeout", records=len(jobs), seconds=budget)
            self._replace()
            return [
                [Outcome(status="error", error="validation timed out") for _ in job.packs]
                for job in jobs
            ]

    def _replace(self) -> None:
        pool, self._pool = self._pool, None
        if pool is None:
            return
        terminate = getattr(pool, "terminate_workers", None)  # Python 3.14
        if terminate is not None:
            with contextlib.suppress(Exception):
                terminate()
        pool.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
