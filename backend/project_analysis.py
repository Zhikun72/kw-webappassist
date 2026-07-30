"""Orchestrates ingest -> parse -> detect -> cross-reference into a Project.

This is the one place that knows the overall pipeline order; each step
itself lives in its own module so the pipeline stays easy to iterate on.
"""
from __future__ import annotations

from pathlib import Path

from backend.column_matching import build_column_index
from backend.flow_graph import FlowGraph, build_flow_graph
from backend.inventory import InventoryResult, build_sections_for_webapp, compute_inventory
from backend.ingest import extract_zip, parse_manifest
from backend.layout import ProjectLayout, discover_layout
from backend.models import Project
from backend.parse_datasets import parse_datasets
from backend.parse_recipes import parse_recipes
from backend.parse_webapps import parse_webapps
from backend.parse_zones import parse_zones


def analyze_zip(zip_path: Path, work_dir: Path, markers: dict) -> tuple[Project, FlowGraph, InventoryResult, ProjectLayout]:
    export_root = extract_zip(zip_path, work_dir)
    manifest = parse_manifest(export_root)
    layout = discover_layout(export_root)

    project = Project(manifest=manifest)
    project.discovery_warnings.extend(manifest.warnings)
    project.discovery_warnings.extend(layout.warnings)

    if layout.datasets_dir:
        project.datasets = parse_datasets(layout.datasets_dir)
    if layout.recipes_dir:
        project.recipes = parse_recipes(layout.recipes_dir)
    if layout.zones_dir:
        project.zones = parse_zones(layout.zones_dir)

    graph = build_flow_graph(project.recipes)
    column_index = build_column_index(project.datasets)

    if layout.web_apps_dir:
        webapps, scan_results = parse_webapps(layout.web_apps_dir, markers)
        for webapp in webapps:
            webapp.sections = build_sections_for_webapp(webapp, scan_results[webapp.id], project.datasets, column_index)
        project.webapps = webapps
    else:
        project.discovery_warnings.append("No web_apps/ directory found - nothing to inventory.")

    inventory = compute_inventory(project.webapps, project.datasets)

    return project, graph, inventory, layout


def candidate_datasets_for_mock(mock_block, project, graph, max_candidates: int = 8):
    """Selects candidate real datasets for the LLM derivability step: the
    migration-hint's named dataset (exact or fuzzy match) first, then
    datasets whose upstream lineage is close to any exact match, then a
    fallback of terminal datasets so there's always something to compare
    against."""
    candidates = []
    seen = set()

    def add(name):
        ds = project.datasets.get(name)
        if ds and ds.name not in seen:
            seen.add(ds.name)
            candidates.append(ds)

    hint = mock_block.migration_hint_dataset
    if hint:
        if hint in project.datasets:
            add(hint)
            for upstream in graph.upstream_lineage(hint, max_depth=2):
                add(upstream)
        else:
            hint_lower = hint.lower()
            fuzzy = [name for name in project.datasets if hint_lower in name.lower() or name.lower() in hint_lower]
            for name in fuzzy[:max_candidates]:
                add(name)

    if len(candidates) < max_candidates:
        for name in graph.terminal_datasets(set(project.datasets.keys())):
            if len(candidates) >= max_candidates:
                break
            add(name)

    return candidates[:max_candidates]
