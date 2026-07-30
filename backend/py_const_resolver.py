"""Resolves simple module-level string constant assignments in a backend.py.

The spec calls this out specifically for DATASET_NAME-style variables passed
to dataiku.Dataset(...), but the same pattern shows up for column-name
variables referenced inside required_cols checks (e.g. DATE_COL = "..."). We
never special-case which variable names to resolve - any top-level
`NAME = "literal"` (or `'literal'`) assignment is captured generically, and
callers look up whatever name they need.

Uses `ast` rather than regex/exec: robust against arbitrary comment/string
content in the file and never executes the source.
"""
from __future__ import annotations

import ast


def resolve_module_string_constants(source: str) -> dict[str, str]:
    """Returns {name: value} for every module-level `NAME = "string"` (or
    tuple/list-unpacking is intentionally NOT handled - only the simple
    single-target case the spec describes)."""
    constants: dict[str, str] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return constants

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            constants[node.targets[0].id] = value.value

    return constants


def resolve_name_or_literal(token: str, constants: dict[str, str]) -> str | None:
    """token is either a quoted literal (from a regex capture) or a bare
    identifier. Returns the underlying string value, or None if it's an
    identifier that doesn't resolve to a known module-level constant."""
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return constants.get(token)
