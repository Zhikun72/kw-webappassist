"""Loads config/markers.yaml - the one place mock-detection patterns live.

Never hardcode a marker string, dataset name, or webapp id anywhere else in
the backend; add it here instead so a different project can be supported by
editing config only.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MARKERS_PATH = REPO_ROOT / "config" / "markers.yaml"


@lru_cache(maxsize=1)
def load_markers(path: str | None = None) -> dict:
    p = Path(path) if path else Path(os.environ.get("MARKERS_CONFIG_PATH", DEFAULT_MARKERS_PATH))
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def upload_dir() -> Path:
    d = Path(os.environ.get("UPLOAD_DIR", REPO_ROOT / "uploads"))
    d.mkdir(parents=True, exist_ok=True)
    return d
