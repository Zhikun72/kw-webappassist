"""Fact export for downstream triage (Part 5).

The webapp does not decide "build vs request from client" - that decision
needs business context this tool doesn't have. This module only serializes
evidence: per field, what it is (data/render/uncertain), whether it can be
filled from existing project data (Satisfied/Available-elsewhere/Derivable),
or genuinely can't (Missing - the real "request from client" list), plus
where that evidence came from.

`derivable` reflects only an *already-cached* LLM derivability check (see
app.py's derivability_cache) - building this export never triggers a new
LLM call itself.
"""
from __future__ import annotations

from backend.column_matching import normalize_column_name
from backend.inventory import classify_needed_columns_for_webapp
from backend.models import ColumnState, Project

# Counts of section-level column_states already exclude non-data fields (see
# backend/inventory.py build_sections_for_webapp), so "data" is always the
# field_kind for anything carrying a ColumnState.
_SUMMARY_KEYS = ("satisfied", "available_elsewhere", "derivable", "missing")


def _cached_derivable_hit(derivability_cache: dict, webapp_id: str, section_id: str, field_name: str):
    cached = derivability_cache.get((webapp_id, section_id))
    if not cached:
        return None
    return next((fd for fd in cached.fields if fd.field == field_name and fd.derivable is True), None)


def _effective_state(col, webapp_id: str, section_id: str, is_mock: bool, derivability_cache: dict):
    """Returns (state, source_dataset, derivability_note). Only a mock
    section's still-Missing columns are eligible for the cached-derivable
    override - a real-read column's Missing state means the dataset it
    reads genuinely lacks that column, which derivability can't fix."""
    if is_mock and col.state == ColumnState.MISSING:
        hit = _cached_derivable_hit(derivability_cache, webapp_id, section_id, col.name)
        if hit:
            return ColumnState.DERIVABLE, hit.source_dataset, hit.note
    return col.state, (col.source_datasets[0] if col.source_datasets else None), None


def build_export(project: Project, derivability_cache: dict) -> dict:
    fields_out = []
    webapp_summaries = {}
    matrix_data: dict[str, dict[str, str | None]] = {}
    display_names: dict[str, str] = {}

    for webapp in project.webapps:
        for section in webapp.sections:
            is_mock = section.mock_block is not None
            intended_source = section.mock_block.migration_hint_dataset if is_mock else None

            for col in section.column_states:
                state, source_dataset, note = _effective_state(col, webapp.id, section.id, is_mock, derivability_cache)
                record = {
                    "field": col.name,
                    "field_kind": "data",
                    "webapp_id": webapp.id,
                    "webapp": webapp.name,
                    "section_id": section.id,
                    "section": section.label,
                    "state": state.value,
                    "source_dataset": source_dataset,
                    "all_source_datasets": list(col.source_datasets),
                    "intended_source": intended_source,
                    "description": col.description,
                    "usage": col.usage,
                }
                if note:
                    record["derivability_note"] = note
                fields_out.append(record)

            for f in section.non_data_fields:
                fields_out.append(
                    {
                        "field": f.name,
                        "field_kind": f.kind.value,
                        "webapp_id": webapp.id,
                        "webapp": webapp.name,
                        "section_id": section.id,
                        "section": section.label,
                        "state": None,
                        "source_dataset": None,
                        "all_source_datasets": [],
                        "intended_source": intended_source,
                        "description": f.description,
                        "usage": f.usage,
                    }
                )

        needed_cols = classify_needed_columns_for_webapp(webapp)
        summary = {key: 0 for key in _SUMMARY_KEYS}
        summary["needed"] = len(needed_cols)
        for col in needed_cols:
            effective_state = col.state
            if col.state == ColumnState.MISSING:
                for section_id in col.requested_by:
                    hit = _cached_derivable_hit(derivability_cache, webapp.id, section_id, col.name)
                    if hit:
                        effective_state = ColumnState.DERIVABLE
                        break
            summary[effective_state.value] += 1

            key = normalize_column_name(col.name)
            display_names.setdefault(key, col.name)
            matrix_data.setdefault(key, {})[webapp.id] = effective_state.value

        summary["non_data_excluded"] = sum(len(s.non_data_fields) for s in webapp.sections)
        summary["webapp"] = webapp.name
        webapp_summaries[webapp.id] = summary

    webapp_ids = [w.id for w in project.webapps]
    cross_webapp_matrix = [
        {"field": display_names[key], "by_webapp": {wid: matrix_data[key].get(wid) for wid in webapp_ids}}
        for key in sorted(matrix_data.keys())
    ]

    export = {
        "fields": fields_out,
        "webapp_summaries": webapp_summaries,
        "cross_webapp_matrix": cross_webapp_matrix,
    }
    export["markdown_summary"] = render_markdown(export)
    return export


def render_markdown(export: dict) -> str:
    lines = ["# Fill-ability Triage Export", "", "## Per-webapp summary (data fields only)", ""]
    for webapp_id, summary in export["webapp_summaries"].items():
        lines.append(
            f"- **{summary['webapp']}** (`{webapp_id}`): {summary['needed']} data fields needed - "
            f"{summary['satisfied']} satisfied, {summary['available_elsewhere']} available elsewhere, "
            f"{summary['derivable']} derivable, {summary['missing']} missing "
            f"({summary['non_data_excluded']} render/uncertain fields excluded from this count)"
        )

    missing_by_field: dict[str, list[str]] = {}
    for record in export["fields"]:
        if record["field_kind"] == "data" and record["state"] == "missing":
            missing_by_field.setdefault(record["field"], []).append(f"{record['webapp']} / {record['section']}")

    lines += ["", "## Request from client (genuine data gaps)", ""]
    if not missing_by_field:
        lines.append("None - every needed data field is satisfied, available elsewhere, or marked derivable.")
    else:
        for field_name in sorted(missing_by_field):
            requesters = ", ".join(sorted(set(missing_by_field[field_name])))
            lines.append(f"- **{field_name}** - needed by: {requesters}")

    return "\n".join(lines)
