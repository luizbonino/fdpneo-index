"""Semantic search, an evaluation behind a flag (TASKS 10.1; search design §3.7).

A multilingual sentence model turns each resource's text and each query into a
vector; nearby vectors mean similar meaning, across wording and language
("air pollution" ~ "luchtkwaliteit"). Results are fused with the full-text
ranking by reciprocal rank fusion (``hybrid``).

Off by default. It needs:

* the ``semantic`` extra (``onnxruntime``, ``tokenizers``);
* PostgreSQL with the ``vector`` extension (pgvector);
* a model on disk: ``fdpindex embeddings download`` fetches
  ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`` (Apache 2.0,
  384 dimensions, 50+ languages) at a pinned revision, int8 for the CPU;
* ``FDPINDEX_SEARCH_EMBEDDINGS=local`` and ``FDPINDEX_EMBEDDINGS_MODEL_DIR``.

The model runs inside the deployment: no text leaves it. Embeddings are
derived data, like the SPARQL store: the table is created when the feature is
switched on (not by a migration, so servers without pgvector are unaffected)
and rebuilt from ``search_resource`` when the model changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)

MODEL_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
DIMENSIONS = 384
MAX_TOKENS = 256
MAX_TEXT = 2000
KINDS = ("fdp", "catalog", "dataset", "series", "service")
BATCH = 64


class Embedder(Protocol):
    """Texts to unit vectors of ``dimensions`` numbers."""

    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def model_file() -> str:
    """The ONNX variant for this CPU: int8 for ARM or AVX2, else full precision."""
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "onnx/model_qint8_arm64.onnx"
    if machine in {"x86_64", "amd64"}:
        return "onnx/model_quint8_avx2.onnx"
    return "onnx/model.onnx"


class OnnxEmbedder:
    """The sentence model with ONNX Runtime: mean pooling over tokens, L2-normalised."""

    def __init__(self, model_dir: Path, threads: int = 0) -> None:
        # The "semantic" extra; absent from a default install (and from CI's type check).
        import numpy as np  # pyright: ignore[reportMissingImports]
        import onnxruntime as ort  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]
        from tokenizers import Tokenizer  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]

        # No usage events leave the deployment (ONNX Runtime has a telemetry component).
        ort.disable_telemetry_events()
        manifest = json.loads((model_dir / "manifest.json").read_text("utf-8"))
        self.name = f"{manifest['repo']}@{manifest['revision'][:8]}:{Path(manifest['file']).stem}"
        self.dimensions = DIMENSIONS
        self._np = np
        self._tokenizer: Any = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(MAX_TOKENS)
        self._tokenizer.enable_padding()
        options: Any = ort.SessionOptions()
        if threads:
            options.intra_op_num_threads = threads
        self._session: Any = ort.InferenceSession(
            str(model_dir / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str]) -> list[list[float]]:
        np = self._np
        encodings = self._tokenizer.encode_batch([t[:MAX_TEXT] or " " for t in texts])
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        return (pooled / np.clip(norms, 1e-12, None)).tolist()


def resource_text(segments: list[list[str]]) -> str:
    """What a resource is about: title, keywords, names of its concepts, description."""
    seen: set[str] = set()
    parts: list[str] = []
    for weight in ("A", "B", "C"):
        for _config, segment_weight, content in segments:
            if segment_weight == weight and content not in seen:
                seen.add(content)
                parts.append(content)
    return " · ".join(parts)[:MAX_TEXT]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


@dataclass
class SyncReport:
    embedded: int = 0
    seconds: float = 0.0


class SemanticIndex:
    """The embeddings of searchable resources in ``search_embedding`` (pgvector)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        max_distance: float = 0.6,
    ) -> None:
        self._session_factory = session_factory
        self.embedder = embedder
        # A hit by meaning alone must be this close (cosine distance). On the synthetic
        # corpus relevant hits were at most 0.54 away, unrelated queries at least 0.73.
        self.max_distance = max_distance
        self._lock = asyncio.Lock()

    async def ensure_schema(self) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.execute(
                text(
                    f"""CREATE TABLE IF NOT EXISTS search_embedding (
                      resource_id bigint PRIMARY KEY
                        REFERENCES search_resource(id) ON DELETE CASCADE,
                      model text NOT NULL,
                      row_hash char(64) NOT NULL,
                      embedding vector({DIMENSIONS}) NOT NULL
                    )"""
                )
            )
            await session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_search_embedding_hnsw ON search_embedding "
                    "USING hnsw (embedding vector_cosine_ops)"
                )
            )

    async def sync(self, limit: int | None = None) -> SyncReport:
        """Embed the resources that have no embedding, or an outdated one."""
        report = SyncReport()
        loop = asyncio.get_running_loop()
        started = loop.time()
        async with self._lock:
            while limit is None or report.embedded < limit:
                async with self._session_factory() as session:
                    rows = (
                        await session.execute(
                            text(
                                """SELECT r.id, r.row_hash, r.text_segments
                                   FROM search_resource r
                                   LEFT JOIN search_embedding e ON e.resource_id = r.id
                                   WHERE r.kind = ANY(:kinds)
                                     AND (e.resource_id IS NULL OR e.row_hash <> r.row_hash
                                          OR e.model <> :model)
                                   LIMIT :batch"""
                            ),
                            {"kinds": list(KINDS), "model": self.embedder.name, "batch": BATCH},
                        )
                    ).all()
                if not rows:
                    break
                texts = [resource_text(segments or []) for _, _, segments in rows]
                vectors = await asyncio.to_thread(self.embedder.embed, texts)
                async with self._session_factory() as session, session.begin():
                    await session.execute(
                        text(
                            """INSERT INTO search_embedding (resource_id, model, row_hash, embedding)
                               VALUES (:id, :model, :hash, CAST(:vector AS vector))
                               ON CONFLICT (resource_id) DO UPDATE SET model = EXCLUDED.model,
                                 row_hash = EXCLUDED.row_hash, embedding = EXCLUDED.embedding"""
                        ),
                        [
                            {
                                "id": rid,
                                "model": self.embedder.name,
                                "hash": row_hash,
                                "vector": vector_literal(vector),
                            }
                            for (rid, row_hash, _), vector in zip(rows, vectors, strict=True)
                        ],
                    )
                report.embedded += len(rows)
        report.seconds = loop.time() - started
        if report.embedded:
            log.info(
                "embeddings_synced", embedded=report.embedded, seconds=round(report.seconds, 1)
            )
        return report

    def start(self, interval: float = 30.0) -> None:
        """Keep the embeddings up to date in the background."""

        async def loop() -> None:
            while True:
                try:
                    await self.sync()
                except Exception:
                    log.exception("embeddings_sync_failed")
                await asyncio.sleep(interval)

        self._task = asyncio.create_task(loop(), name="embeddings")

    async def stop(self) -> None:
        task = getattr(self, "_task", None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def query_vector(self, query: str) -> str:
        vectors = await asyncio.to_thread(self.embedder.embed, [query])
        return vector_literal(vectors[0])


def download(target: Path, *, fetch: Any) -> dict[str, str]:
    """Fetch the pinned model into ``target``: tokenizer.json, model.onnx, manifest.json.

    ``fetch(url) -> bytes`` does the HTTP; the caller passes one, so this stays testable.
    """
    target.mkdir(parents=True, exist_ok=True)
    base = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/"
    chosen = model_file()
    digests: dict[str, str] = {}
    for remote, local in (("tokenizer.json", "tokenizer.json"), (chosen, "model.onnx")):
        body: bytes = fetch(base + remote)
        (target / local).write_bytes(body)
        digests[local] = hashlib.sha256(body).hexdigest()
    manifest = {
        "repo": MODEL_REPO,
        "revision": MODEL_REVISION,
        "file": chosen,
        "licence": "Apache-2.0",
        "sha256": digests,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", "utf-8")
    return digests
