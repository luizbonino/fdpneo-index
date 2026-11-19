"""The liveness-check scheduler (ADR-0004).

Every ``sweep_seconds`` it asks the index to start the checks that are due,
and to mark entries without contact for too long as expired (TASKS 4.6).
Whether checks run at all, and how often per entry, are index settings; the
sweep itself is cheap when nothing is due.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from fdpindex.service import IndexService

log = structlog.get_logger(__name__)


class HealthScheduler:
    def __init__(self, index: IndexService, *, sweep_seconds: float, batch: int) -> None:
        self._index = index
        self._sweep_seconds = sweep_seconds
        self._batch = batch
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="health-scheduler")

    async def _loop(self) -> None:
        while True:
            try:
                await self._index.schedule_due_checks(batch=self._batch)
                await self._index.mark_expired(batch=self._batch)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("health_sweep_failed")
            await asyncio.sleep(self._sweep_seconds)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
