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
of the same cross-reference.

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

from backend.models import MockBlock, RealRead, RequiredColsCheck
from backend.py_const_resolver import resolve_module_string_constants, resolve_name_or_literal


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


def _find_required_cols(source: str, code_by_line: dict[int, str], constants: dict[str, str], markers: dict) -> list[RequiredColsCheck]:
    var_patterns = [re.compile(p) for p in markers.get("required_cols_var_patterns", [])]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

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

        cols = []
        for el in node.value.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                cols.append(el.value)
            elif isinstance(el, ast.Name) and el.id in constants:
                cols.append(constants[el.id])
        if cols:
            checks.append(RequiredColsCheck(var_name=var_name, columns=cols, line_no=node.lineno))
    return checks


def _find_mock_function_defs(source: str, name_patterns: list[re.Pattern]) -> list[tuple[str, int, int]]:
    """Returns (name, start_line, end_line) for every function whose name
    matches a mock_function_name_pattern."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(p.match(node.name) for p in name_patterns):
                end = getattr(node, "end_lineno", node.lineno)
                hits.append((node.name, node.lineno, end))
    return hits


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


def scan_backend(source: str, markers: dict) -> dict:
    """Returns {real_reads, required_cols_checks, mock_blocks} for one
    backend.py source string."""
    lines = source.splitlines()
    comments_by_line, code_by_line = _split_comments_and_code(source)
    constants = resolve_module_string_constants(source)

    real_reads = _find_real_reads(code_by_line, constants, markers)
    required_cols_checks = _find_required_cols(source, code_by_line, constants, markers)

    keyword_pattern = _compile_keyword_pattern(markers["mock_keywords"])
    mock_fn_patterns = [re.compile(p) for p in markers.get("mock_function_name_patterns", [])]
    migration_patterns = [re.compile(p, re.IGNORECASE) for p in markers.get("migration_hint_patterns", [])]

    banner_hits = _find_banner_blocks(lines, comments_by_line, markers, keyword_pattern)
    mock_fn_hits = _find_mock_function_defs(source, mock_fn_patterns)

    # Merge banner-defined section boundaries; anything not covered by a
    # banner gets its own block scoped to the function body.
    starts = sorted(b["start_line"] for b in banner_hits)
    mock_blocks: list[MockBlock] = []

    for idx, b in enumerate(banner_hits):
        start = b["start_line"]
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(lines)
        block_fns = [name for name, s, e in mock_fn_hits if start <= s <= end]
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

    covered = {(b["start_line"]) for b in banner_hits}
    for name, s, e in mock_fn_hits:
        if any(bs <= s <= be for bs, be in ((b["start_line"], (starts[i + 1] - 1 if i + 1 < len(starts) else len(lines))) for i, b in enumerate(banner_hits))):
            continue
        hint, hint_dataset = _scan_migration_hint(comments_by_line, s, e, migration_patterns, markers)
        window_text = " ".join(comments_by_line.get(ln, "") for ln in range(s, e + 1))
        mock_blocks.append(
            MockBlock(
                id=f"mock-fn-{name}-{s}",
                title=name,
                start_line=s,
                end_line=e,
                trigger_keywords=sorted(set(m.group(0) for m in keyword_pattern.finditer(name + " " + window_text))),
                mock_functions=[name],
                migration_hint=hint,
                migration_hint_dataset=hint_dataset,
                snippet="\n".join(lines[s - 1: min(e, s + 39)]),
            )
        )

    mock_blocks.sort(key=lambda b: b.start_line)
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
