"""Generic structural discovery of a Dataiku export.

The Appendix in the spec documents project_config/{datasets,recipes,zones,
web_apps}/ as the DSS 14.7.1 layout, but the spec is explicit that structure
can vary by version - so this module locates each subtree by *searching* for
it under the export root rather than hardcoding project_config/ as the only
possible parent. If a future export nests things differently, only this
module needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUBTREE_NAMES = ("datasets", "recipes", "zones", "web_apps")


@dataclass
class ProjectLayout:
    export_root: Path
    datasets_dir: Path | None
    recipes_dir: Path | None
    zones_dir: Path | None
    web_apps_dir: Path | None
    warnings: list[str]


def _find_subtree(export_root: Path, name: str) -> Path | None:
    # Prefer the conventional project_config/<name> location; fall back to a
    # search so a different DSS version's layout still resolves.
    conventional = export_root / "project_config" / name
    if conventional.is_dir():
        return conventional
    candidates = [p for p in export_root.rglob(name) if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: len(p.relative_to(export_root).parts))
    return candidates[0]


def discover_layout(export_root: Path) -> ProjectLayout:
    warnings = []
    found = {}
    for name in SUBTREE_NAMES:
        path = _find_subtree(export_root, name)
        found[name] = path
        if path is None:
            warnings.append(f"No '{name}/' directory found under the export root.")

    return ProjectLayout(
        export_root=export_root,
        datasets_dir=found["datasets"],
        recipes_dir=found["recipes"],
        zones_dir=found["zones"],
        web_apps_dir=found["web_apps"],
        warnings=warnings,
    )
