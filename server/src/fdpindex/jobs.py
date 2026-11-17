"""In-process background jobs with bounded concurrency.

The reference runs retrievals, harvests and webhook deliveries with Spring's
``@Async``. Here they are asyncio tasks limited by a semaphore. Durable state
lives in the event table: a retrieval or delivery whose event never finished
(the process stopped mid-way) is resumed at start-up, as the reference does.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = structlog.get_logger(__name__)


class JobRunner:
    def __init__(self, concurrency: int) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def submit(self, name: str, job: Callable[[], Awaitable[None]]) -> None:
        if self._closed:
            log.warning("job_dropped_after_shutdown", job=name)
            return
        task = asyncio.create_task(self._run(name, job), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, name: str, job: Callable[[], Awaitable[None]]) -> None:
        async with self._semaphore:
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job_failed", job=name)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def drain(self) -> None:
        """Wait until no job is left, including jobs that running jobs submitted."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self, *, grace_seconds: float = 10.0) -> None:
        self._closed = True
        if not self._tasks:
            return
        _done, pending = await asyncio.wait(list(self._tasks), timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
