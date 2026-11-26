"""Check an FDP before registering it (TASKS 15.11; design note metadata-health 3.5).

A maintainer, or an administrator reviewing a pending FDP, sees what the
index would find before the FDP is in it: whether the root metadata is an
FDP's, and the metadata health report (reach, findings, the network
profile's requirements) of a **sample** of its pages. The sample reads at
most :data:`PAGES` pages, and at most :data:`PER_PAGE` children of each, so
it reaches every level of a large FDP instead of stopping among the first
catalogue's datasets.

Who may check follows the approval policy (``checkBeforeRegistering`` in the
index settings, default ``policy``): when the index accepts FDPs without an
administrator, anyone may, under the ping's rate limit (per address, counted
here); otherwise administrators only, who check pending FDPs from their page.
The deny list applies to everyone.

A check runs in the background; its result is kept in memory for an hour
under an unguessable id, and nothing is stored. A deviation from the event
log as the durable work log (CLAUDE.md): a check that a restart interrupts is
lost, and the caller asks again.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import structlog

from fdpindex.db import utcnow
from fdpindex.errors import Forbidden, PingDenied, RateLimited
from fdpindex.models import EntryPermit

if TYPE_CHECKING:
    from fdpindex.jobs import JobRunner
    from fdpindex.service import IndexService
    from fdpindex.settings import SettingsService

log = structlog.get_logger(__name__)

PAGES = 200
PER_PAGE = 5
SECONDS = 120.0
KEEP_SECONDS = 3600.0
MAX_RUNNING = 3
MAX_KEPT = 500


@dataclass
class Check:
    id: str
    client_url: str
    started_at: datetime
    admin: bool
    finished_at: datetime | None = None
    state: str | None = None
    error: str | None = None
    title: str | None = None
    registered: tuple[str, EntryPermit] | None = None
    report: dict[str, Any] | None = None
    sampled_out: int = 0
    created: float = field(default_factory=time.monotonic)

    @property
    def running(self) -> bool:
        return self.finished_at is None


class CheckService:
    """Starts checks, runs them in the background, and keeps their results an hour."""

    def __init__(self, index: IndexService, settings: SettingsService, jobs: JobRunner) -> None:
        self._index = index
        self._settings = settings
        self._jobs = jobs
        self._checks: dict[str, Check] = {}
        self._hits: dict[str, deque[float]] = {}

    async def policy(self) -> Literal["anyone", "admins"]:
        return (await self._settings.get()).check_policy()

    async def start(self, client_url: str, *, admin: bool, remote_addr: str | None) -> Check:
        """Start a check, or return the one already running for this URL.

        Raises ``Forbidden`` when the caller may not check, ``PingDenied`` for a
        URL on the deny list and ``RateLimited`` past the ping's rate limit or
        when the index runs as many checks as it allows at once.
        """
        settings = await self._settings.get()
        if not admin and settings.check_policy() != "anyone":
            raise Forbidden("Only administrators may check an FDP before it is registered")
        if settings.denies(client_url):
            raise PingDenied(f"Client URL is denied: {client_url}")
        self._forget_old()
        for check in self._checks.values():
            if check.running and check.client_url == client_url and check.admin == admin:
                return check
        if sum(1 for c in self._checks.values() if c.running) >= MAX_RUNNING:
            raise RateLimited("The index is checking other FDPs; try again in a minute")
        if not admin:
            self._count(
                remote_addr or "?",
                settings.ping.rate_limit_hits,
                settings.ping.rate_limit_duration.total_seconds(),
            )
        check = Check(id=uuid4().hex, client_url=client_url, started_at=utcnow(), admin=admin)
        self._checks[check.id] = check
        self._jobs.submit(f"check:{check.id}", lambda: self._run(check))
        log.info("check_started", client_url=client_url, admin=admin)
        return check

    def get(self, check_id: str) -> Check | None:
        self._forget_old()
        return self._checks.get(check_id)

    async def _run(self, check: Check) -> None:
        try:
            entry = await self._index.find_entry(check.client_url)
            origins: list[str] = []
            if entry is not None and (check.admin or entry.permit is EntryPermit.ACCEPTED):
                check.registered = (str(entry.uuid), entry.permit)
                if check.admin:
                    origins = list(entry.harvest_origins or [])
            root, report, sampled_out = await self._index.sample_health(
                check.client_url,
                origins=origins,
                pages=PAGES,
                per_page=PER_PAGE,
                seconds=SECONDS,
            )
            check.state = root.state.name
            check.error = root.error
            metadata: dict[str, Any] = dict((root.metadata or {}).get("metadata") or {})
            title = metadata.get("title")
            check.title = str(title) if title else None
            check.report = report
            check.sampled_out = sampled_out
            log.info(
                "check_finished",
                client_url=check.client_url,
                state=check.state,
                status=report["status"] if report else None,
            )
        except Exception as err:  # a check must end, whatever went wrong
            log.exception("check_failed", client_url=check.client_url)
            check.state = check.state or "UNREACHABLE"
            check.error = check.error or f"The check failed: {type(err).__name__}"
        finally:
            check.finished_at = utcnow()

    def _count(self, key: str, hits: int, seconds: float) -> None:
        now = time.monotonic()
        window = self._hits.setdefault(key, deque())
        while window and window[0] < now - seconds:
            window.popleft()
        if len(window) >= hits:
            raise RateLimited(f"Rate limit reached for {key} (max. {hits} checks)")
        window.append(now)
        if len(self._hits) > 10_000:
            for old in [k for k, w in self._hits.items() if not w or w[-1] < now - seconds]:
                del self._hits[old]

    def _forget_old(self) -> None:
        cutoff = time.monotonic() - KEEP_SECONDS
        old = [k for k, c in self._checks.items() if not c.running and c.created < cutoff]
        for key in old:
            del self._checks[key]
        while len(self._checks) > MAX_KEPT:
            finished = [k for k, c in self._checks.items() if not c.running]
            if not finished:
                break
            del self._checks[finished[0]]
