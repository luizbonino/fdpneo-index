"""Query workers: SPARQL execution in killable subprocesses (search design §2.2.3).

pyoxigraph offers no timeout or cancellation, so every query runs in a
long-lived worker process that holds a read-only handle on the store. The
parent sends one request at a time per worker and waits for the answer with
a wall-clock budget; a worker that overruns is killed and replaced. Workers
cap rows while iterating (evaluation is lazy, so a capped query stops early
unless it sorts), cap the serialised bytes, and on Linux exit when their
resident memory passes a ceiling.

A read-only handle is a snapshot: each request carries the store's
generation and a worker reopens the store when it moved.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import threading
import time
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
    generation: int
    max_rows: int
    max_bytes: int
    union: bool
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


def worker_main(conn: Connection, path: str, prefixes: dict[str, str], max_mb: int) -> None:
    import pyoxigraph as ox

    threading.Thread(target=_watch_memory, args=(max_mb,), daemon=True).start()
    store: Any = None
    generation = -1
    while True:
        try:
            request: Request = conn.recv()
        except EOFError:
            return
        try:
            if request.generation != generation or store is None:
                store = ox.Store.read_only(path)
                generation = request.generation
            started = time.perf_counter()
            body, rows, truncated = _execute(ox, store, request, prefixes)
            conn.send(("ok", body, rows, truncated, time.perf_counter() - started))
        except _TooLarge as err:
            conn.send(("error", str(err), 413))
        except Exception as err:  # SyntaxError, evaluation errors: the message is useful
            conn.send(("error", str(err), 400))


class _TooLarge(Exception):
    pass


def _execute(
    ox: Any, store: Any, request: Request, prefixes: dict[str, str]
) -> tuple[bytes, int, bool]:
    kwargs: dict[str, Any] = {"prefixes": prefixes}
    if request.default_graphs:
        kwargs["default_graph"] = [ox.NamedNode(g) for g in request.default_graphs]
    else:
        kwargs["use_default_graph_as_union"] = request.union
    if request.named_graphs:
        kwargs["named_graphs"] = [ox.NamedNode(g) for g in request.named_graphs]
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
        return f'<literal datatype="{escape(term.datatype.value)}">{escape(term.value)}</literal>'
    return f"<literal>{escape(str(term))}</literal>"


# --- the parent side ------------------------------------------------------------


class _Worker:
    def __init__(self, ctx: Any, path: str, prefixes: dict[str, str], max_mb: int) -> None:
        parent, child = ctx.Pipe()
        self.conn: Connection = parent
        self.process: BaseProcess = ctx.Process(
            target=worker_main, args=(child, path, prefixes, max_mb), daemon=True
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
        path: Path,
        *,
        size: int,
        prefixes: dict[str, str],
        max_mb: int,
        max_queue: int = 10,
    ) -> None:
        self._path = str(path)
        self._size = size
        self._prefixes = prefixes
        self._max_mb = max_mb
        self._max_queue = max_queue
        self._ctx = multiprocessing.get_context("spawn")
        self._idle: asyncio.Queue[_Worker] = asyncio.Queue()
        self._all: list[_Worker] = []
        self._waiting = 0

    def _spawn(self) -> _Worker:
        worker = _Worker(self._ctx, self._path, self._prefixes, self._max_mb)
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
        generation: int,
        timeout: float,
        max_rows: int,
        max_bytes: int,
        union: bool = True,
        default_graphs: tuple[str, ...] = (),
        named_graphs: tuple[str, ...] = (),
    ) -> QueryResult:
        if self._waiting >= self._max_queue:
            raise QueryFailed("The SPARQL service is busy; try again shortly.", status=503)
        self._waiting += 1
        try:
            worker = await self._idle.get()
        finally:
            self._waiting -= 1
        request = Request(
            text=query.text,
            form=query.form,
            format=fmt,
            generation=generation,
            max_rows=max_rows,
            max_bytes=max_bytes,
            union=union,
            default_graphs=default_graphs,
            named_graphs=named_graphs,
        )
        answer = await asyncio.to_thread(worker.call, request, timeout)
        if answer is None:
            dead = not worker.process.is_alive()
            await asyncio.to_thread(worker.kill)
            self._all.remove(worker)
            self._idle.put_nowait(await asyncio.to_thread(self._spawn))
            if dead:
                raise QueryFailed(
                    "The query used too much memory; narrow it or add a LIMIT.", status=413
                )
            raise QueryFailed(
                f"The query took longer than {timeout:g} seconds and was stopped. "
                "Narrow it, or add a LIMIT.",
                status=408,
            )
        self._idle.put_nowait(worker)
        if answer[0] == "error":
            raise QueryFailed(str(answer[1]), status=int(answer[2]))
        _, body, rows, truncated, seconds = answer
        return QueryResult(
            body=body, media_type=MEDIA_TYPES[fmt], rows=rows, truncated=truncated, seconds=seconds
        )

    async def close(self) -> None:
        for worker in list(self._all):
            await asyncio.to_thread(worker.kill)
        self._all.clear()
