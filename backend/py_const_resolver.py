"""Resolves simple string constant assignments in a backend.py, at module
scope or within a single function's body.

The spec calls this out specifically for DATASET_NAME-style variables passed
to dataiku.Dataset(...), but the same pattern shows up for column-name
variables referenced inside required_cols checks (e.g. DATE_COL = "...") and
for column-name constants defined inside a mock function itself. We never
special-case which variable names to resolve - any simple
`NAME = "literal"` (or `'literal'`) assignment is captured generically, and
callers look up whatever name they need.

Uses `ast` rather than regex/exec: robust against arbitrary comment/string
content in the file and never executes the source.
"""
from __future__ import annotations

import ast


def _scan_simple_string_assigns(stmts: list[ast.stmt]) -> dict[str, tuple[str, int]]:
    """Scans a flat list of statements (module body, or one function's direct
    body - NOT recursing into nested defs) for `NAME = "literal"` assigns.
    Returns {name: (value, lineno)}."""
    constants: dict[str, tuple[str, int]] = {}
    for node in stmts:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants[node.targets[0].id] = (value.value, node.lineno)
    return constants


def resolve_module_string_constants(source: str) -> dict[str, str]:
    """Returns {name: value} for every module-level `NAME = "string"` (or
    tuple/list-unpacking is intentionally NOT handled - only the simple
    single-target case the spec describes)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return {name: value for name, (value, _lineno) in _scan_simple_string_assigns(tree.body).items()}


def resolve_module_string_constants_with_lines(source: str) -> dict[str, tuple[str, int]]:
    """Same as resolve_module_string_constants but also returns each
    constant's own definition line, so callers can look up an adjacent
    comment as a human description."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    return _scan_simple_string_assigns(tree.body)


def resolve_local_string_constants(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, tuple[str, int]]:
    """Same pattern, scoped to one function's direct body statements (not
    recursing into nested defs) - covers projects that define column-name
    constants inside the mock function itself rather than at module level."""
    return _scan_simple_string_assigns(func_node.body)


def resolve_name_or_literal(token: str, constants: dict[str, str]) -> str | None:
    """token is either a quoted literal (from a regex capture) or a bare
    identifier. Returns the underlying string value, or None if it's an
    identifier that doesn't resolve to a known module-level constant."""
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return constants.get(token)
