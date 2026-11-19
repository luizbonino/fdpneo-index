"""MkDocs hook: publish the server's OpenAPI document with the site (TASKS 14.6)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

OPENAPI = Path(__file__).resolve().parents[1] / "server" / "openapi.json"


def on_post_build(config: Any, **_: Any) -> None:
    shutil.copyfile(OPENAPI, Path(config["site_dir"]) / "openapi.json")
