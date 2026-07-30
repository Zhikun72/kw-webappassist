"""Cross-references each webapp's real reads / mock blocks against the
project's real datasets to build the three-state inventory:
  Ready              - reads a real dataset that exists, required cols present
  Mock / to-build     - section is rendered from a hardcoded mock block
  Referenced-missing - dataset name (or a required column) not found

Also surfaces Built-unused (real datasets no webapp reads) and duplicate
webapps (identical backend.py content, e.g. two exported versions of the
same dashboard).
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.models import Dataset, SectionState, Webapp, WebappSection

REQUIRED_COLS_PAIRING_WINDOW = 100  # lines a required_cols check may trail its real_read


@dataclass
class InventoryResult:
    built_unused: list[str]
    duplicate_groups: list[list[str]]


def _pair_required_cols(real_reads, required_cols_checks):
    """Associates each real_read with the nearest required_cols check that
    follows it within REQUIRED_COLS_PAIRING_WINDOW lines (simple textual
    proximity heuristic - both signals live in the same load function in
    every observed case)."""
    pairing: dict[int, list[str]] = {}  # real_read index -> columns
    sorted_checks = sorted(required_cols_checks, key=lambda c: c.line_no)
    for idx, read in enumerate(real_reads):
        best = None
        for check in sorted_checks:
            if read.line_no <= check.line_no <= read.line_no + REQUIRED_COLS_PAIRING_WINDOW:
                if best is None or check.line_no < best.line_no:
                    best = check
        if best:
            pairing[idx] = best.columns
    return pairing


def build_sections_for_webapp(
    webapp: Webapp, scan_result: dict, datasets: dict[str, Dataset]
) -> list[WebappSection]:
    sections: list[WebappSection] = []
    real_reads = scan_result["real_reads"]
    required_cols_checks = scan_result["required_cols_checks"]
    pairing = _pair_required_cols(real_reads, required_cols_checks)

    for idx, read in enumerate(real_reads):
        matched = datasets.get(read.dataset_name)
        required_columns = pairing.get(idx, [])
        missing_columns = (
            [c for c in required_columns if c not in matched.column_names]
            if matched
            else list(required_columns)
        )
        state = SectionState.READY if matched and not missing_columns else SectionState.REFERENCED_MISSING

        sections.append(
            WebappSection(
                id=f"read-{read.dataset_name}-{read.line_no}",
                label=read.dataset_name,
                state=state,
                real_read=read,
                required_columns=required_columns,
                missing_columns=missing_columns,
                matched_dataset=read.dataset_name if matched else None,
            )
        )

    for block in scan_result["mock_blocks"]:
        sections.append(
            WebappSection(
                id=block.id,
                label=block.title or block.id,
                state=SectionState.MOCK,
                mock_block=block,
            )
        )

    sections.sort(key=lambda s: (s.real_read.line_no if s.real_read else s.mock_block.start_line))
    return sections


def compute_inventory(webapps: list[Webapp], datasets: dict[str, Dataset]) -> InventoryResult:
    read_dataset_names: set[str] = set()
    for webapp in webapps:
        for section in webapp.sections:
            if section.state == SectionState.READY and section.matched_dataset:
                read_dataset_names.add(section.matched_dataset)

    built_unused = sorted(set(datasets.keys()) - read_dataset_names)

    by_hash: dict[str, list[str]] = {}
    for webapp in webapps:
        if webapp.content_hash:
            by_hash.setdefault(webapp.content_hash, []).append(webapp.id)
    duplicate_groups = [ids for ids in by_hash.values() if len(ids) > 1]

    return InventoryResult(built_unused=built_unused, duplicate_groups=duplicate_groups)
