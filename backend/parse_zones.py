"""Parses project_config/zones/<id>.json into Zone models."""
from __future__ import annotations

import json
from pathlib import Path

from backend.models import Zone


def parse_zones(zones_dir: Path) -> list[Zone]:
    zones: list[Zone] = []
    for f in sorted(zones_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError:
            continue

        items = data.get("items", []) or []
        dataset_ids = [i["objectId"] for i in items if i.get("objectType") == "DATASET"]
        recipe_ids = [i["objectId"] for i in items if i.get("objectType") == "RECIPE"]

        zones.append(
            Zone(
                id=f.stem,
                name=data.get("name", f.stem),
                color=data.get("color"),
                dataset_ids=dataset_ids,
                recipe_ids=recipe_ids,
            )
        )
    return zones
