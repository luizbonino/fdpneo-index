"""Query workers: SPARQL execution in killable subprocesses (search design §2.2.3).

pyoxigraph offers no timeout or cancellation, so every query runs in a
long-lived worker process that holds a read-only handle on the store. The
parent sends one request at a time per worker and waits for the answer with
a wall-clock budget; a worker that overruns is killed and replaced. Workers
cap rows while iterating (evaluation is lazy, so a capped query stops early
unless it sorts), cap the serialised bytes, and on Linux exit when their
resident memory passes a ceiling.

Workers read published snapshots of the store, never the store being
written (see ``store.py``): each request names the snapshot to read, and a
worker reopens when it changed.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.process import BaseProcess

    from fdpindex.sparql.gate import CheckedQuery

log = structlog.get_logger(__name__)

ResultFormat = Literal["json", "xml", "csv", "tsv", "turtle", "ntriples", "jsonld", "rdfxml"]

MEDIA_TYPES: dict[str, str] = {
    "json": "application/sparql-results+json",
    "xml": "application/sparql-results+xml",
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "turtle": "text/turtle; charset=utf-8",
    "ntriples": "application/n-triples",
    "jsonld": "application/ld+json",
    "rdfxml": "application/rdf+xml",
}
SOLUTION_FORMATS = frozenset({"json", "xml", "csv", "tsv"})
GRAPH_FORMATS = frozenset({"turtle", "ntriples", "jsonld", "rdfxml"})


class QueryFailed(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Request:
    text: str
    form: str
    format: str
    snapshot: str
    max_rows: int
    max_bytes: int
    default_graphs: tuple[str, ...]
    named_graphs: tuple[str, ...]


@dataclass(frozen=True)
class QueryResult:
    body: bytes
    media_type: str
    rows: int
    truncated: bool
    seconds: float


# --- the worker process ---------------------------------------------------------


def _watch_memory(limit_mb: int) -> None:
    statm = Path("/proc/self/statm")
    if not statm.exists() or limit_mb <= 0:
        return  # not Linux: rely on the timeout alone
    page = os.sysconf("SC_PAGE_SIZE")
    while True:
        try:
            rss = int(statm.read_text().split()[1]) * page
        except (OSError, ValueError, IndexError):
            return
        if rss > limit_mb * 1024 * 1024:
            os._exit(3)  # the parent sees a dead worker and replaces it
        time.sleep(0.2)


def worker_main(conn: Connection, prefixes: dict[str, str], max_mb: int) -> None:
    import pyoxigraph as ox

    threading.Thread(target=_watch_memory, args=(max_mb,), daemon=True).start()
    store: Any = None
    snapshot = ""
    while True:
        try:
            request: Request = conn.recv()
        except EOFError:
            return
        try:
            if request.snapshot != snapshot or store is None:
                store = None  # release the previous snapshot first
                store = ox.Store.read_only(request.snapshot)
                snapshot = request.snapshot
            started = time.perf_counter()
            body, rows, truncated = _execute(ox, store, request, prefixes)
            conn.send(("ok", body, rows, truncated, time.perf_counter() - started))
        except _TooLarge as err:
            conn.send(("error", str(err), 413))
        except OSError as err:
            # Storage errors carry file paths: keep them in the server log only.
            print(f"sparql worker storage error: {err}", file=sys.stderr, flush=True)
            conn.send(("error", "The query could not be run (storage error); try again.", 503))
        except Exception as err:  # SyntaxError, evaluation errors: the message is useful
            conn.send(("error", str(err), 400))


class _TooLarge(Exception):
    pass


def _execute(
    ox: Any, store: Any, request: Request, prefixes: dict[str, str]
) -> tuple[bytes, int, bool]:
    kwargs: dict[str, Any] = {
        "prefixes": prefixes,
        "default_graph": [ox.NamedNode(g) for g in request.default_graphs],
    }
    if request.named_graphs:
        kwargs["named_graphs"] = [ox.NamedNode(g) for g in request.named_graphs]
    from fdpindex.sparql.prefixes import mentions_service

    if mentions_service(request.text):
        raise ValueError("SERVICE is not supported: the index does not query other endpoints.")
    result = store.query(request.text, **kwargs)
    if isinstance(result, ox.QueryBoolean):
        return _boolean(bool(result), request.format), 1, False
    if isinstance(result, ox.QuerySolutions):
        names = [v.value for v in result.variables]
        rows: list[list[Any]] = []
        truncated = False
        for solution in result:
            if len(rows) >= request.max_rows:
                truncated = True
                break
            rows.append([solution[name] for name in names])
        body = _solutions(ox, names, rows, request.format)
    else:
        triples: list[Any] = []
        truncated = False
        for triple in result:
            if len(triples) >= request.max_rows:
                truncated = True
                break
            triples.append(triple)
        rows = triples  # pyright: ignore[reportAssignmentType]
        body = _graph(ox, triples, request.format, prefixes)
    if len(body) > request.max_bytes:
        raise _TooLarge(
            f"The result is larger than {request.max_bytes // (1024 * 1024)} MiB; add a LIMIT."
        )
    return body, len(rows), truncated


def _graph(ox: Any, triples: list[Any], fmt: str, prefixes: dict[str, str]) -> bytes:
    formats = {
        "turtle": ox.RdfFormat.TURTLE,
        "ntriples": ox.RdfFormat.N_TRIPLES,
        "jsonld": ox.RdfFormat.JSON_LD,
        "rdfxml": ox.RdfFormat.RDF_XML,
    }
    if fmt == "turtle":
        return ox.serialize(triples, format=formats[fmt], prefixes=prefixes)
    return ox.serialize(triples, format=formats.get(fmt, ox.RdfFormat.TURTLE))


def _boolean(value: bool, fmt: str) -> bytes:
    if fmt == "xml":
        return (
            '<?xml version="1.0"?>\n<sparql xmlns="http://www.w3.org/2005/sparql-results#">'
            f"<head/><boolean>{str(value).lower()}</boolean></sparql>\n"
        ).encode()
    return f'{{"head":{{}},"boolean":{str(value).lower()}}}'.encode()


def _json_term(ox: Any, term: Any) -> dict[str, Any]:
    if isinstance(term, ox.NamedNode):
        return {"type": "uri", "value": term.value}
    if isinstance(term, ox.BlankNode):
        return {"type": "bnode", "value": term.value}
    if isinstance(term, ox.Literal):
        out: dict[str, Any] = {"type": "literal", "value": term.value}
        if term.language:
            out["xml:lang"] = term.language
        elif term.datatype.value != "http://www.w3.org/2001/XMLSchema#string":
            out["datatype"] = term.datatype.value
        return out
    return {
        "type": "triple",
        "value": {
            "subject": _json_term(ox, term.subject),
            "predicate": _json_term(ox, term.predicate),
            "object": _json_term(ox, term.object),
        },
    }


def _plain(ox: Any, term: Any) -> str:
    if term is None:
        return ""
    if isinstance(term, ox.BlankNode):
        return f"_:{term.value}"
    if isinstance(term, (ox.NamedNode, ox.Literal)):
        return term.value
    return str(term)


def _csv_cell(text: str) -> str:
    if any(c in text for c in ',"\r\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def _solutions(ox: Any, names: list[str], rows: list[list[Any]], fmt: str) -> bytes:
    import json

    if fmt == "csv":
        lines = [",".join(names)]
        lines += [",".join(_csv_cell(_plain(ox, t)) for t in row) for row in rows]
        return ("\r\n".join(lines) + "\r\n").encode()
    if fmt == "tsv":
        lines = ["\t".join(f"?{n}" for n in names)]
        lines += ["\t".join("" if t is None else str(t) for t in row) for row in rows]
        return ("\n".join(lines) + "\n").encode()
    if fmt == "xml":
        parts = [
            '<?xml version="1.0"?>\n<sparql xmlns="http://www.w3.org/2005/sparql-results#"><head>',
            *(f'<variable name="{escape(n)}"/>' for n in names),
            "</head><results>",
        ]
        for row in rows:
            parts.append("<result>")
            for name, term in zip(names, row, strict=True):
                if term is None:
                    continue
                parts.append(f'<binding name="{escape(name)}">{_xml_term(ox, term)}</binding>')
            parts.append("</result>")
        parts.append("</results></sparql>\n")
        return "".join(parts).encode()
    bindings = [
        {
            name: _json_term(ox, term)
            for name, term in zip(names, row, strict=True)
            if term is not None
        }
        for row in rows
    ]
    return json.dumps(
        {"head": {"vars": names}, "results": {"bindings": bindings}}, ensure_ascii=False
    ).encode()


def _xml_term(ox: Any, term: Any) -> str:
    if isinstance(term, ox.NamedNode):
        return f"<uri>{escape(term.value)}</uri>"
    if isinstance(term, ox.BlankNode):
        return f"<bnode>{escape(term.value)}</bnode>"
    if isinstance(term, ox.Literal):
        if term.language:
            return f'<literal xml:lang="{escape(term.language)}">{escape(term.value)}</literal>'
        if term.datatype.value == "http://www.w3.org/2001/XMLSchema#string":
            return f"<literal>{escape(term.value)}</literal>"
        return f'<literal datatype="{escape(term.datatype.value)}">{escape(term.value)}</literal>'
    return f"<literal>{escape(str(term))}</literal>"


# --- the parent side ------------------------------------------------------------


class _Worker:
    def __init__(self, ctx: Any, prefixes: dict[str, str], max_mb: int) -> None:
        parent, child = ctx.Pipe()
        self.conn: Connection = parent
        self.process: BaseProcess = ctx.Process(
            target=worker_main, args=(child, prefixes, max_mb), daemon=True
        )
        self.process.start()
        child.close()

    def call(self, request: Request, timeout: float) -> tuple[Any, ...] | None:
        """Blocking: send and wait; ``None`` when the budget ran out or the worker died."""
        try:
            self.conn.send(request)
            if self.conn.poll(timeout):
                return self.conn.recv()
        except (EOFError, OSError, BrokenPipeError):
            return None
        return None

    def kill(self) -> None:
        with contextlib.suppress(Exception):
            self.process.kill()
            self.process.join(timeout=2)
        with contextlib.suppress(Exception):
            self.conn.close()


class WorkerPool:
    def __init__(
        self,
        *,
        size: int,
        prefixes: dict[str, str],
        max_mb: int,
        max_queue: int = 10,
    ) -> None:
        self._size = size
        self._prefixes = prefixes
        self._max_mb = max_mb
        self._max_queue = max_queue
        self._ctx = multiprocessing.get_context("spawn")
        self._idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self._all: list[_Worker] = []
        self._waiting = 0
        self._closed = False
        # Anonymous queries may use all workers but one, which stays free for
        # administrators and the service's own lookups (labels, suggestions, schema).
        self._public = asyncio.Semaphore(max(1, size - 1))

    def _spawn(self) -> _Worker:
        worker = _Worker(self._ctx, self._prefixes, self._max_mb)
        self._all.append(worker)
        return worker

    async def start(self) -> None:
        for _ in range(self._size):
            self._idle.put_nowait(await asyncio.to_thread(self._spawn))

    async def run(
        self,
        query: CheckedQuery,
        *,
        fmt: str,
        snapshot: Callable[[], str],
        timeout: float,
        max_rows: int,
        max_bytes: int,
        default_graphs: Callable[[], tuple[str, ...]],
        named_graphs: Callable[[], tuple[str, ...]],
        queue_wait: float = 30.0,
        privileged: bool = False,
    ) -> QueryResult:
        """Run ``query`` on an idle worker.

        The snapshot and graph lists are taken once a worker is free, so a request
        that waited in the queue reads the newest snapshot, never one that may have
        been removed meanwhile. A worker is always returned or replaced, also when
        the awaiting task is cancelled.
        """
        if self._waiting >= self._max_queue:
            raise QueryFailed("The SPARQL service is busy; try again shortly.", status=503)
        async with contextlib.AsyncExitStack() as stack:
            self._waiting += 1
            try:
                async with asyncio.timeout(queue_wait):
                    if not privileged:
                        await stack.enter_async_context(self._public)
                    worker = await self._idle.get()
            except TimeoutError as err:
                raise QueryFailed(
                    "The SPARQL service is busy; try again shortly.", status=503
                ) from err
            finally:
                self._waiting -= 1
            return await self._execute(
                worker,
                query,
                fmt=fmt,
                snapshot=snapshot,
                timeout=timeout,
                max_rows=max_rows,
                max_bytes=max_bytes,
                default_graphs=default_graphs,
                named_graphs=named_graphs,
            )

    async def _execute(
        self,
        worker: _Worker,
        query: CheckedQuery,
        *,
        fmt: str,
        snapshot: Callable[[], str],
        timeout: float,
        max_rows: int,
        max_bytes: int,
        default_graphs: Callable[[], tuple[str, ...]],
        named_graphs: Callable[[], tuple[str, ...]],
    ) -> QueryResult:
        healthy = False
        try:
            if not worker.process.is_alive():
                # Died while idle (the memory watchdog after a large result): not this
                # query's fault, so replace it and carry on with a fresh one.
                await self._replace(worker)
                worker = await self._idle.get()
            request = Request(
                text=query.text,
                form=query.form,
                format=fmt,
                snapshot=snapshot(),
                max_rows=max_rows,
                max_bytes=max_bytes,
                default_graphs=default_graphs(),
                named_graphs=named_graphs(),
            )
            answer = await asyncio.to_thread(worker.call, request, timeout)
            if answer is None:
                dead = not worker.process.is_alive()
                if dead:
                    raise QueryFailed(
                        "The query used too much memory; narrow it or add a LIMIT.", status=413
                    )
                raise QueryFailed(
                    f"The query took longer than {timeout:g} seconds and was stopped. "
                    "Narrow it, or add a LIMIT.",
                    status=408,
                )
            healthy = True
        finally:
            if healthy:
                self._idle.put_nowait(worker)
            else:
                # Timed out, died, or the caller was cancelled mid-query: the worker may
                # still be busy, so it is killed and replaced, never reused.
                await asyncio.shield(self._replace(worker))
        if answer[0] == "error":
            raise QueryFailed(str(answer[1]), status=int(answer[2]))
        _, body, rows, truncated, seconds = answer
        return QueryResult(
            body=body, media_type=MEDIA_TYPES[fmt], rows=rows, truncated=truncated, seconds=seconds
        )

    async def _replace(self, worker: _Worker) -> None:
        await asyncio.to_thread(worker.kill)
        if worker in self._all:
            self._all.remove(worker)
        if not self._closed:
            self._idle.put_nowait(await asyncio.to_thread(self._spawn))

    async def close(self) -> None:
        self._closed = True
        for worker in list(self._all):
            await asyncio.to_thread(worker.kill)
        self._all.clear()
