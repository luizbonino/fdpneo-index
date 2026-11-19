"""Semantic search pieces that need no model or pgvector (TASKS 10.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fdpindex.search import semantic
from fdpindex.search.query import SearchParams

pytestmark = pytest.mark.unit


def test_the_model_file_suits_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    for machine, expected in (
        ("arm64", "onnx/model_qint8_arm64.onnx"),
        ("aarch64", "onnx/model_qint8_arm64.onnx"),
        ("x86_64", "onnx/model_quint8_avx2.onnx"),
        ("riscv64", "onnx/model.onnx"),
    ):
        monkeypatch.setattr(semantic.platform, "machine", lambda m=machine: m)
        assert semantic.model_file() == expected


def test_a_resources_text_is_title_first_without_repeats() -> None:
    segments = [
        ["fdpindex_english", "C", "A long description."],
        ["fdpindex_english", "A", "Breast cancer registry"],
        ["fdpindex_dutch", "B", "Gezondheid"],
        ["fdpindex_english", "B", "Health"],
        ["fdpindex_english", "A", "Breast cancer registry"],
        ["fdpindex_english", "D", "an identifier"],
    ]
    assert semantic.resource_text(segments) == (
        "Breast cancer registry · Gezondheid · Health · A long description."
    )
    assert semantic.vector_literal([0.5, -0.25]) == "[0.500000,-0.250000]"


def test_the_download_pins_the_revision_and_records_digests(tmp_path: Path) -> None:
    asked: list[str] = []

    def fetch(url: str) -> bytes:
        asked.append(url)
        return url.encode()

    digests = semantic.download(tmp_path / "model", fetch=fetch)
    assert all(f"/resolve/{semantic.MODEL_REVISION}/" in url for url in asked)
    assert set(digests) == {"tokenizer.json", "model.onnx"}
    manifest = json.loads((tmp_path / "model" / "manifest.json").read_text())
    assert manifest["repo"] == semantic.MODEL_REPO and manifest["licence"] == "Apache-2.0"


def test_the_mode_is_checked() -> None:
    from fdpindex.errors import BadRequest
    from fdpindex.search.profile import default_profile

    with pytest.raises(BadRequest):
        SearchParams(semantic="always").validate(default_profile())
    SearchParams(semantic="only").validate(default_profile())


def test_a_missing_model_leaves_search_as_it_was(tmp_path: Path) -> None:
    """The flag on but no model: the index starts, without semantic search."""
    from fastapi.testclient import TestClient

    from fdpindex.config import Settings
    from fdpindex.main import create_app

    config = Settings(
        database_url="sqlite+aiosqlite://",
        search_enabled=True,
        search_embeddings="local",
        embeddings_model_dir=tmp_path / "missing",
    )
    app = create_app(config, create_tables=True, configure_logs=False, run_scheduler=False)
    with TestClient(app) as client:
        assert app.state.search.semantic is None
        assert client.get("/index/search", params={"q": "x", "semantic": "only"}).status_code == 200
