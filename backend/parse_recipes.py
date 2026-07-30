"""Parses project_config/recipes/<name>.json into Recipe models.

Only the control-plane .json files carry inputs/outputs; sidecar payload
files (.shaker, .join, .sql, ...) are ignored here.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.models import Recipe


def _refs(io_block: dict) -> list[str]:
    items = (io_block or {}).get("main", {}).get("items", []) or []
    return [item["ref"] for item in items if item.get("ref")]


def parse_recipes(recipes_dir: Path) -> list[Recipe]:
    recipes: list[Recipe] = []
    for f in sorted(recipes_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            continue

        recipes.append(
            Recipe(
                name=f.stem,
                type=data.get("type", "unknown"),
                inputs=_refs(data.get("inputs")),
                outputs=_refs(data.get("outputs")),
            )
        )
    return recipes
