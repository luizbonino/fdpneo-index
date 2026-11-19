"""The committed OpenAPI document matches the server (the client's types come from it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fdpindex.config import Settings
from fdpindex.main import create_app

pytestmark = pytest.mark.unit

SNAPSHOT = Path(__file__).resolve().parents[1] / "openapi.json"


def test_openapi_snapshot_is_current() -> None:
    current = create_app(Settings(), configure_logs=False).openapi()
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert current == committed, (
        "run `uv run fdpindex openapi openapi.json` and regenerate client types"
    )


def test_reference_paths_are_served() -> None:
    paths = set(create_app(Settings(), configure_logs=False).openapi()["paths"])
    assert {
        "/",
        "/index/entries",
        "/index/entries/all",
        "/index/entries/info",
        "/index/entries/{entry_id}",
        "/index/entries/{entry_id}/data",
        "/index/settings",
        "/index/admin/trigger",
        "/index/admin/trigger-all",
        "/index/admin/ping-webhook",
    } <= paths


def test_every_setting_is_documented() -> None:
    """docs/configuration.md lists every FDPINDEX_* variable the server reads (TASKS 14.5)."""
    from fdpindex.config import Settings

    doc = (Path(__file__).resolve().parents[2] / "docs" / "configuration.md").read_text("utf-8")
    missing = [
        f"FDPINDEX_{name.upper()}"
        for name in Settings.model_fields
        if f"`FDPINDEX_{name.upper()}`" not in doc
    ]
    assert missing == []
