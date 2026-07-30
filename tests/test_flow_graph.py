from backend.flow_graph import build_flow_graph
from backend.models import Recipe

RECIPES = [
    Recipe(name="r1", type="sync", inputs=["A"], outputs=["B"]),
    Recipe(name="r2", type="join", inputs=["B", "C"], outputs=["D"]),
    Recipe(name="r3", type="grouping", inputs=["D"], outputs=["E"]),
]


def test_terminal_datasets_are_out_degree_zero():
    graph = build_flow_graph(RECIPES)
    known = {"A", "B", "C", "D", "E"}
    terminals = graph.terminal_datasets(known)
    assert terminals == ["E"]


def test_non_terminal_datasets_excluded():
    graph = build_flow_graph(RECIPES)
    known = {"A", "B", "C", "D", "E"}
    terminals = graph.terminal_datasets(known)
    assert "A" not in terminals
    assert "D" not in terminals


def test_upstream_lineage_walks_multiple_hops():
    graph = build_flow_graph(RECIPES)
    upstream = graph.upstream_lineage("E")
    assert upstream == ["D", "B", "C", "A"]


def test_isolated_dataset_has_no_edges():
    graph = build_flow_graph(RECIPES)
    assert graph.out_degree("Z_not_in_any_recipe") == 0
    assert graph.in_degree("Z_not_in_any_recipe") == 0
