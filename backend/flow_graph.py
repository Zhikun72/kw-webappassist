"""Directed flow graph over datasets, built from recipe inputs/outputs.

Datasets are nodes; each recipe contributes one edge per (input, output)
pair. Terminal datasets (out-degree 0) are the delivery surface - most
likely what webapps consume - and are highlighted accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.models import Recipe, Zone


@dataclass
class FlowGraph:
    nodes: set[str] = field(default_factory=set)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (from, to, recipe_name)
    out_edges: dict[str, list[str]] = field(default_factory=dict)
    in_edges: dict[str, list[str]] = field(default_factory=dict)

    def out_degree(self, node: str) -> int:
        return len(self.out_edges.get(node, []))

    def in_degree(self, node: str) -> int:
        return len(self.in_edges.get(node, []))

    def terminal_datasets(self, known_datasets: set[str]) -> list[str]:
        return sorted(d for d in known_datasets if self.out_degree(d) == 0)

    def upstream_lineage(self, dataset: str, max_depth: int = 10) -> list[str]:
        """Breadth-first upstream walk from `dataset`, nearest first."""
        seen = {dataset}
        order = []
        frontier = [dataset]
        depth = 0
        while frontier and depth < max_depth:
            next_frontier = []
            for node in frontier:
                for upstream in self.in_edges.get(node, []):
                    if upstream not in seen:
                        seen.add(upstream)
                        order.append(upstream)
                        next_frontier.append(upstream)
            frontier = next_frontier
            depth += 1
        return order


def build_flow_graph(recipes: list[Recipe]) -> FlowGraph:
    graph = FlowGraph()
    for recipe in recipes:
        for i in recipe.inputs:
            graph.nodes.add(i)
        for o in recipe.outputs:
            graph.nodes.add(o)
        for i in recipe.inputs:
            for o in recipe.outputs:
                graph.edges.append((i, o, recipe.name))
                graph.out_edges.setdefault(i, []).append(o)
                graph.in_edges.setdefault(o, []).append(i)
    return graph


def zone_for_dataset(zones: list[Zone]) -> dict[str, Zone]:
    mapping: dict[str, Zone] = {}
    for zone in zones:
        for ds_id in zone.dataset_ids:
            mapping[ds_id] = zone
    return mapping
