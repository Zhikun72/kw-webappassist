"""Cross-references each webapp's real reads / mock blocks against the
project's real datasets and columns to build the column-level gap analysis:
  Ready / Partial     - dataset exists; all / some required columns present
  Mock / to-build     - section is rendered from a hardcoded mock block
  Referenced-missing  - dataset missing, or 0 of its required columns present

Column-level three-state (Tier 1, exact/normalized name matching only - see
backend/column_matching.py) answers the harder question a dataset-level view
hides: a column absent from the one dataset a section reads may still exist
in some *other* real dataset (Available elsewhere) rather than being truly
unbuildable (Missing).

Also surfaces Built-unused (real datasets no webapp reads) and duplicate
webapps (identical backend.py content, e.g. two exported versions of the
same dashboard).
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.column_matching import normalize_column_name
from backend.models import ColumnState, Dataset, DeclaredField, FieldKind, NeededColumn, SectionState, Webapp, WebappSection

REQUIRED_COLS_PAIRING_WINDOW = 100  # lines a required_cols check may trail its real_read

_STATE_RANK = {
    ColumnState.MISSING: 0,
    ColumnState.AVAILABLE_AMBIGUOUS: 1,
    ColumnState.AVAILABLE_ELSEWHERE: 2,
    ColumnState.DERIVABLE: 3,
    ColumnState.SATISFIED: 4,
}


@dataclass
class InventoryResult:
    built_unused: list[str]
    duplicate_groups: list[list[str]]


def _pair_required_cols(real_reads, required_cols_checks) -> dict[int, list[DeclaredField]]:
    """Associates each real_read with the nearest required_cols check that
    follows it within REQUIRED_COLS_PAIRING_WINDOW lines (simple textual
    proximity heuristic - both signals live in the same load function in
    every observed case)."""
    pairing: dict[int, list[DeclaredField]] = {}
    sorted_checks = sorted(required_cols_checks, key=lambda c: c.line_no)
    for idx, read in enumerate(real_reads):
        best = None
        for check in sorted_checks:
            if read.line_no <= check.line_no <= read.line_no + REQUIRED_COLS_PAIRING_WINDOW:
                if best is None or check.line_no < best.line_no:
                    best = check
        if best:
            pairing[idx] = best.fields
    return pairing


def classify_column(
    field: DeclaredField,
    matched_dataset: Dataset | None,
    column_index: dict[str, list[str]],
    migration_hint_dataset: str | None,
    section_id: str,
    markers: dict,
) -> NeededColumn:
    """Tier 1 classification: SATISFIED if it's in the dataset this section
    actually reads; else AVAILABLE_ELSEWHERE/AVAILABLE_AMBIGUOUS if it
    exists in some other real dataset by name (normalized); else MISSING.
    `column_index` already maps normalized name -> owning dataset names, so
    both checks are O(1) lookups against the same structure.

    A bare name match against every dataset in the project is not the same
    thing as a trustworthy join candidate - `日付_Month` matches ~100
    datasets by exact name, which is name-collision, not signal. Confident
    AVAILABLE_ELSEWHERE requires either a small match count, or the mock's
    own migration-hint dataset among the matches (an explicit anchor); a
    wide match count with no anchor downgrades to AVAILABLE_AMBIGUOUS -
    visible, but never presented as a clean match, and never on the
    "request from client" list. Displayed `source_datasets` is capped
    (`available_elsewhere_max_sources_shown`) with the intended source
    placed first when present; `candidate_count` always records the true,
    uncapped match count."""
    normalized = normalize_column_name(field.name)
    sources = column_index.get(normalized, [])
    ambiguous_threshold = markers.get("available_elsewhere_ambiguous_threshold", 5)
    max_shown = markers.get("available_elsewhere_max_sources_shown", 5)

    if matched_dataset and matched_dataset.name in sources:
        return NeededColumn(
            name=field.name,
            description=field.description,
            state=ColumnState.SATISFIED,
            usage=field.usage,
            source_datasets=[matched_dataset.name],
            candidate_count=1,
            in_intended_source=False,
            requested_by=[section_id],
        )
    if sources:
        in_intended = bool(migration_hint_dataset) and migration_hint_dataset in sources
        ordered = [migration_hint_dataset] + [s for s in sources if s != migration_hint_dataset] if in_intended else sources
        state = (
            ColumnState.AVAILABLE_ELSEWHERE
            if (in_intended or len(sources) <= ambiguous_threshold)
            else ColumnState.AVAILABLE_AMBIGUOUS
        )
        return NeededColumn(
            name=field.name,
            description=field.description,
            state=state,
            usage=field.usage,
            source_datasets=ordered[:max_shown],
            candidate_count=len(sources),
            in_intended_source=in_intended,
            requested_by=[section_id],
        )
    return NeededColumn(
        name=field.name,
        description=field.description,
        state=ColumnState.MISSING,
        usage=field.usage,
        source_datasets=[],
        candidate_count=0,
        in_intended_source=False,
        requested_by=[section_id],
    )


def build_sections_for_webapp(
    webapp: Webapp, scan_result: dict, datasets: dict[str, Dataset], column_index: dict[str, list[str]], markers: dict
) -> list[WebappSection]:
    sections: list[WebappSection] = []
    real_reads = scan_result["real_reads"]
    required_cols_checks = scan_result["required_cols_checks"]
    pairing = _pair_required_cols(real_reads, required_cols_checks)

    for idx, read in enumerate(real_reads):
        matched = datasets.get(read.dataset_name)
        decl_fields = pairing.get(idx, [])
        section_id = f"read-{read.dataset_name}-{read.line_no}"

        column_states = [classify_column(f, matched, column_index, None, section_id, markers) for f in decl_fields]
        satisfied_count = sum(1 for c in column_states if c.state == ColumnState.SATISFIED)
        total_count = len(column_states)

        if not matched:
            state = SectionState.REFERENCED_MISSING
        elif total_count == 0 or satisfied_count == total_count:
            state = SectionState.READY
        elif satisfied_count == 0:
            state = SectionState.REFERENCED_MISSING
        else:
            state = SectionState.PARTIAL

        sections.append(
            WebappSection(
                id=section_id,
                label=read.dataset_name,
                state=state,
                real_read=read,
                required_columns=[f.name for f in decl_fields],
                missing_columns=[c.name for c in column_states if c.state == ColumnState.MISSING],
                matched_dataset=read.dataset_name if matched else None,
                column_states=column_states,
                satisfied_count=satisfied_count,
                total_count=total_count,
            )
        )

    for block in scan_result["mock_blocks"]:
        section_id = block.id
        # Only DATA fields go through fill-ability classification - a
        # render field (a jsonify response key that never touches a
        # dataframe) always comes back Missing against real schemas, which
        # would inflate the gap count against the wrong denominator. RENDER/
        # UNCERTAIN fields are tracked separately, untouched, for review.
        data_fields = [f for f in block.required_fields if f.kind == FieldKind.DATA]
        non_data_fields = [f for f in block.required_fields if f.kind != FieldKind.DATA]
        column_states = [
            classify_column(f, None, column_index, block.migration_hint_dataset, section_id, markers) for f in data_fields
        ]
        sections.append(
            WebappSection(
                id=section_id,
                label=block.title or block.id,
                state=SectionState.MOCK,
                mock_block=block,
                required_columns=[f.name for f in data_fields],
                missing_columns=[c.name for c in column_states if c.state == ColumnState.MISSING],
                column_states=column_states,
                satisfied_count=sum(1 for c in column_states if c.state == ColumnState.SATISFIED),
                total_count=len(column_states),
                non_data_fields=non_data_fields,
            )
        )

    sections.sort(key=lambda s: (s.real_read.line_no if s.real_read else s.mock_block.start_line))
    return sections


def classify_needed_columns_for_webapp(webapp: Webapp) -> list[NeededColumn]:
    """Union of every section's classified columns, deduped by normalized
    name (a column needed by several sections keeps every requesting
    section in `requested_by`, state = best across duplicates) - the
    per-webapp 'N needed / X satisfied / Y missing' rollup."""
    merged: dict[str, NeededColumn] = {}

    for section in webapp.sections:
        for col in section.column_states:
            key = normalize_column_name(col.name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = NeededColumn(
                    name=col.name,
                    description=col.description,
                    state=col.state,
                    usage=col.usage,
                    source_datasets=list(col.source_datasets),
                    candidate_count=col.candidate_count,
                    in_intended_source=col.in_intended_source,
                    requested_by=list(col.requested_by),
                )
                continue

            for section_id in col.requested_by:
                if section_id not in existing.requested_by:
                    existing.requested_by.append(section_id)
            if not existing.description and col.description:
                existing.description = col.description
            if _STATE_RANK[col.state] > _STATE_RANK[existing.state]:
                existing.state = col.state
                existing.source_datasets = list(col.source_datasets)
                existing.candidate_count = col.candidate_count
                existing.in_intended_source = col.in_intended_source

    return sorted(merged.values(), key=lambda c: c.name)


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
