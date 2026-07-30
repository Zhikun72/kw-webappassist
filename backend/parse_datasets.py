"""Parses project_config/datasets/<NAME>.json into Dataset models."""
from __future__ import annotations

import json
from pathlib import Path

from backend.models import Column, Dataset


def parse_datasets(datasets_dir: Path) -> dict[str, Dataset]:
    datasets: dict[str, Dataset] = {}
    for f in sorted(datasets_dir.glob("*.json")):
        name = f.stem
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            continue

        schema = data.get("schema", {}) or {}
        columns = [
            Column(name=c.get("name", ""), type=c.get("type", "unknown"))
            for c in schema.get("columns", []) or []
            if c.get("name")
        ]
        datasets[name] = Dataset(name=name, type=data.get("type", "unknown"), columns=columns)
    return datasets
