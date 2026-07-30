"""Pattern-scans a webapp's backend.py for mock-data signals and real reads.

Per the spec: mock detection is pattern-scanning, not guessing - developers
already mark mock blocks in comments. This module implements the layered
signals generically, driven entirely by config/markers.yaml:
  1. Banner-delimited sections containing a mock keyword.
  2. `_mock_*`-style function names.
  3. Inline comments carrying a mock keyword.
  4. Migration-hint comments naming an intended real source.
Real dataset reads (`dataiku.Dataset(...).get_dataframe()`) and
`required_cols` checks are detected alongside, since they're the other half
of the same cross-reference. Mock blocks also get their *declared fields*
extracted (what columns the mock actually produces) - the other half of the
column-level gap analysis in backend/inventory.py.

Marker matching is scoped to COMMENT TEXT ONLY, using `tokenize` rather than
a naive `#`-split, so a UI string like `placeholder="..."` in code never
false-positives as a mock marker (confirmed necessary during Phase 0 on the
reference export).
"""
from __future__ import annotations

import ast
import io
import re
import tokenize

from backend.models import DeclaredField, MockBlock, RealRead, RequiredColsCheck
from backend.py_const_resolver import (
    resolve_local_string_constants,
    resolve_module_string_constants_with_lines,
    resolve_name_or_literal,
)


def _split_comments_and_code(source: str) -> tuple[dict[int, str], dict[int, str]]:
    """Returns (comments_by_line, code_by_line): comment text and
    comment-stripped code text, keyed by 1-indexed line number."""
    lines = source.splitlines()
    code_by_line = {i + 1: line for i, line in enumerate(lines)}
    comments_by_line: dict[int, str] = {}

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                ln = tok.start[0]
                comments_by_line[ln] = comments_by_line.get(ln, "") + " " + tok.string
                # Strip the comment portion out of the code view for that line.
                line = code_by_line.get(ln, "")
                col = tok.start[1]
                code_by_line[ln] = line[:col]
    except (tokenize.TokenizeError, SyntaxError, IndentationError):
        pass  # fall back to raw lines as "code"; no comments recognized

    return comments_by_line, code_by_line


def _describe(comments_by_line: dict[int, str], line_no: int) -> str | None:
    comment = comments_by_line.get(line_no, "")
    if not comment:
        return None
    text = comment.strip().lstrip("#").strip()
    return text or None


def _compile_keyword_pattern(keywords: list[str]) -> re.Pattern:
    escaped = [re.escape(k) for k in keywords]
    return re.compile("|".join(escaped), re.IGNORECASE)


def _find_real_reads(code_by_line: dict[int, str], constants: dict[str, str], markers: dict) -> list[RealRead]:
    pattern = re.compile(markers["real_read_pattern"])
    reads = []
    for ln, code in code_by_line.items():
        m = pattern.search(code)
        if not m:
            continue
        token = m.group(1)
        resolved_via_var = not (token.strip()[:1] in "\"'")
        name = resolve_name_or_literal(token, constants)
        if name:
            reads.append(RealRead(dataset_name=name, resolved=resolved_via_var, line_no=ln))
    return reads


def _find_required_cols(
    tree: ast.AST,
    constants_with_lines: dict[str, tuple[str, int]],
    comments_by_line: dict[int, str],
    markers: dict,
) -> list[RequiredColsCheck]:
    var_patterns = [re.compile(p) for p in markers.get("required_cols_var_patterns", [])]

    checks: list[RequiredColsCheck] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        var_name = node.targets[0].id
        if not any(p.match(var_name) for p in var_patterns):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue

        decl_fields: list[DeclaredField] = []
        for el in node.value.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                decl_fields.append(
                    DeclaredField(name=el.value, description=_describe(comments_by_line, el.lineno), source_line=el.lineno)
                )
            elif isinstance(el, ast.Name) and el.id in constants_with_lines:
                value, def_line = constants_with_lines[el.id]
                decl_fields.append(
                    DeclaredField(name=value, description=_describe(comments_by_line, def_line), source_line=def_line)
                )
        if decl_fields:
            checks.append(RequiredColsCheck(var_name=var_name, fields=decl_fields, line_no=node.lineno))
    return checks


def _find_mock_function_defs(
    tree: ast.AST, name_patterns: list[re.Pattern]
) -> list[tuple[str, int, int, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Returns (name, start_line, end_line, node) for every function whose
    name matches a mock_function_name_pattern."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(p.match(node.name) for p in name_patterns):
                end = getattr(node, "end_lineno", node.lineno)
                hits.append((node.name, node.lineno, end, node))
    return hits


def _extend_start_for_leading_comments(start: int, comments_by_line: dict[int, str], code_by_line: dict[int, str]) -> int:
    """A comment-only line (e.g. a migration hint) directly above a `def`,
    with no blank line in between, reads as attached to that function -
    extend the block's start to include it, mirroring how banner titles are
    already treated as part of the section they precede."""
    ln = start - 1
    while ln >= 1 and comments_by_line.get(ln) and not code_by_line.get(ln, "").strip():
        start = ln
        ln -= 1
    return start


def _cap_last_banner_end(
    tree: ast.AST, start: int, fallback_end: int, mock_fn_hits: list[tuple[str, int, int, ast.AST]]
) -> int:
    """The last banner in a file has no following banner to cap its range,
    so a naive end-of-file fallback would sweep unrelated trailing functions
    (and their dict-literal keys) into this section's field extraction.
    Prefer the end of the last mock-named function starting at/after
    `start` (a section commonly has a builder + a helper); otherwise fall
    back to just the single top-level function immediately following the
    banner, which is the observed one-route-handler-per-section pattern."""
    mock_ends = [e for _name, s, e, _node in mock_fn_hits if s >= start]
    if mock_ends:
        return max(mock_ends)

    following = [
        node for node in getattr(tree, "body", []) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno >= start
    ]
    if following:
        first = min(following, key=lambda n: n.lineno)
        return getattr(first, "end_lineno", first.lineno)

    return fallback_end


def _extract_hint_dataset(comment_text: str, real_read_pattern: str) -> str | None:
    m = re.search(real_read_pattern, comment_text)
    if not m:
        return None
    token = m.group(1).strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return None


def _find_banner_blocks(
    lines: list[str],
    comments_by_line: dict[int, str],
    markers: dict,
    keyword_pattern: re.Pattern,
) -> list[dict]:
    banner_re = re.compile(markers["banner_line_pattern"])
    lookahead = markers.get("banner_lookahead_lines", 2)
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        line_no = i + 1
        if banner_re.match(lines[i]):
            window_text = ""
            title = None
            for j in range(line_no, min(line_no + lookahead + 1, n + 1)):
                text = comments_by_line.get(j, "")
                window_text += " " + text
                stripped = text.strip().lstrip("#").strip(" =")
                if stripped and title is None and not banner_re.match(lines[j - 1]):
                    title = stripped
            if keyword_pattern.search(window_text):
                blocks.append({"start_line": line_no, "title": title, "window_text": window_text})
        i += 1
    return blocks


def _resolve_dict_key(key_node: ast.AST, scope_constants: dict[str, tuple[str, int]]) -> tuple[str, int] | None:
    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
        return key_node.value, key_node.lineno
    if isinstance(key_node, ast.Name) and key_node.id in scope_constants:
        return scope_constants[key_node.id]
    return None


def _unwrap_subscript_key(node: ast.AST) -> ast.AST:
    # Python <3.9 wrapped a plain subscript key in ast.Index; harmless no-op
    # on 3.9+, where Subscript.slice is already the bare expression.
    return node.value if isinstance(node, getattr(ast, "Index", ())) else node


def _extract_declared_fields(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    scope_constants: dict[str, tuple[str, int]],
    comments_by_line: dict[int, str],
    denylist: set[str] = frozenset(),
) -> list[DeclaredField]:
    """Finds the fields one function's own code produces: every dict
    literal's string keys (covers both the `pd.DataFrame([{...}, ...])` /
    `rows.append({...})` pattern and the equally common plain
    dict-then-jsonify pattern with no DataFrame involved at all - real mock
    sections in the wild use both), plus post-construction `df[COL] = ...`
    column adds. Nested dicts are walked too (a dict value that is itself a
    dict contributes its own keys), which is deliberately permissive: a few
    structural wrapper keys ("trend", "rows") mixed in with real data field
    names ("base", "category") is an acceptable tradeoff for a best-effort
    discovery tool - column-matching downstream still classifies each by
    whether it exists anywhere in the project's real schemas.

    Deliberately scoped to a single function's AST subtree (ast.walk here
    only visits that subtree) rather than a banner block's line range - a
    block commonly contains sibling helper/rendering functions that reuse
    generic variable names like `rows`/`children` for unrelated JSON-response
    construction, and a range-based scan cross-contaminates those with the
    mock's actual fields."""
    fields: dict[str, DeclaredField] = {}

    def add_field(key_node: ast.AST) -> None:
        resolved = _resolve_dict_key(key_node, scope_constants)
        if not resolved:
            return
        name, desc_line = resolved
        if name in fields or name in denylist:
            return
        fields[name] = DeclaredField(name=name, description=_describe(comments_by_line, desc_line), source_line=key_node.lineno)

    for node in ast.walk(func_node):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if k is not None:
                    add_field(k)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                add_field(_unwrap_subscript_key(target.slice))

    return list(fields.values())


def _find_functions_in_range(tree: ast.Module, start_line: int, end_line: int) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Top-level (module-scope) function defs whose own def line falls
    within a banner block's range - typically the Flask route handler that
    follows the banner and builds its mock response, named however the
    developer likes (not necessarily matching a mock_function_name_pattern)."""
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and start_line <= node.lineno <= end_line
    ]


def scan_backend(source: str, markers: dict) -> dict:
    """Returns {real_reads, required_cols_checks, mock_blocks} for one
    backend.py source string."""
    lines = source.splitlines()
    comments_by_line, code_by_line = _split_comments_and_code(source)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = ast.Module(body=[], type_ignores=[])

    module_constants_with_lines = resolve_module_string_constants_with_lines(source)
    constants = {name: value for name, (value, _lineno) in module_constants_with_lines.items()}

    # Merge in any function-local string constants (column-name constants
    # sometimes live inside the mock function itself, not at module level).
    scope_constants = dict(module_constants_with_lines)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope_constants.update(resolve_local_string_constants(node))

    real_reads = _find_real_reads(code_by_line, constants, markers)
    required_cols_checks = _find_required_cols(tree, module_constants_with_lines, comments_by_line, markers)

    keyword_pattern = _compile_keyword_pattern(markers["mock_keywords"])
    mock_fn_patterns = [re.compile(p) for p in markers.get("mock_function_name_patterns", [])]
    migration_patterns = [re.compile(p, re.IGNORECASE) for p in markers.get("migration_hint_patterns", [])]

    banner_hits = _find_banner_blocks(lines, comments_by_line, markers, keyword_pattern)
    mock_fn_hits = _find_mock_function_defs(tree, mock_fn_patterns)

    # Merge banner-defined section boundaries; anything not covered by a
    # banner gets its own block scoped to the function body.
    starts = sorted(b["start_line"] for b in banner_hits)
    mock_blocks: list[MockBlock] = []

    for idx, b in enumerate(banner_hits):
        start = b["start_line"]
        if idx + 1 < len(starts):
            end = starts[idx + 1] - 1
        else:
            # The last banner has no following banner to cap it - falling
            # back to end-of-file would sweep in any unrelated trailing
            # functions (and their unrelated dict-literal keys) into this
            # block's field extraction. Cap to the end of whatever actually
            # belongs to this section instead.
            end = _cap_last_banner_end(tree, start, len(lines), mock_fn_hits)
        block_fns = [name for name, s, e, _node in mock_fn_hits if start <= s <= end]
        block_keywords = sorted(set(m.group(0) for m in keyword_pattern.finditer(b["window_text"])))
        hint, hint_dataset = _scan_migration_hint(comments_by_line, start, end, migration_patterns, markers)
        mock_blocks.append(
            MockBlock(
                id=f"mock-banner-{start}",
                title=b["title"],
                start_line=start,
                end_line=end,
                trigger_keywords=block_keywords,
                mock_functions=block_fns,
                migration_hint=hint,
                migration_hint_dataset=hint_dataset,
                snippet="\n".join(lines[start - 1: min(end, start + 39)]),
            )
        )

    banner_ranges = [(blk.start_line, blk.end_line) for blk in mock_blocks]
    for name, s, e, _node in mock_fn_hits:
        if any(bs <= s <= be for bs, be in banner_ranges):
            continue
        block_start = _extend_start_for_leading_comments(s, comments_by_line, code_by_line)
        hint, hint_dataset = _scan_migration_hint(comments_by_line, block_start, e, migration_patterns, markers)
        window_text = " ".join(comments_by_line.get(ln, "") for ln in range(block_start, e + 1))
        mock_blocks.append(
            MockBlock(
                id=f"mock-fn-{name}-{s}",
                title=name,
                start_line=block_start,
                end_line=e,
                trigger_keywords=sorted(set(m.group(0) for m in keyword_pattern.finditer(name + " " + window_text))),
                mock_functions=[name],
                migration_hint=hint,
                migration_hint_dataset=hint_dataset,
                snippet="\n".join(lines[block_start - 1: min(e, block_start + 39)]),
            )
        )

    mock_blocks.sort(key=lambda b: b.start_line)

    denylist = set(markers.get("declared_field_denylist", []))
    mock_fn_nodes = {name: node for name, _s, _e, node in mock_fn_hits}
    for block in mock_blocks:
        target_nodes = {id(mock_fn_nodes[fn_name]): mock_fn_nodes[fn_name] for fn_name in block.mock_functions if fn_name in mock_fn_nodes}
        for fn_node in _find_functions_in_range(tree, block.start_line, block.end_line):
            target_nodes[id(fn_node)] = fn_node

        merged: dict[str, DeclaredField] = {}
        for fn_node in target_nodes.values():
            for f in _extract_declared_fields(fn_node, scope_constants, comments_by_line, denylist):
                merged.setdefault(f.name, f)
        block.required_fields = list(merged.values())

    return {
        "real_reads": real_reads,
        "required_cols_checks": required_cols_checks,
        "mock_blocks": mock_blocks,
    }


def _scan_migration_hint(
    comments_by_line: dict[int, str], start: int, end: int, migration_patterns: list[re.Pattern], markers: dict
) -> tuple[str | None, str | None]:
    for ln in range(start, end + 1):
        text = comments_by_line.get(ln, "")
        if not text:
            continue
        if any(p.search(text) for p in migration_patterns):
            dataset = _extract_hint_dataset(text, markers["real_read_pattern"])
            return text.strip().lstrip("#").strip(), dataset
    return None, None
