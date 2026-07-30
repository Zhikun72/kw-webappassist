from backend.column_matching import build_column_index
from backend.config import load_markers
from backend.export import build_export
from backend.inventory import build_sections_for_webapp
from backend.llm_gap_analysis import DerivabilityResult, FieldDerivability
from backend.models import Column, Dataset, DeclaredField, FieldKind, ManifestCheck, MockBlock, Project, RealRead

MARKERS = load_markers()
DATASETS = {
    "PRIMARY": Dataset(name="PRIMARY", type="Snowflake", columns=[Column(name="date", type="date")]),
}
COLUMN_INDEX = build_column_index(DATASETS)


class _Webapp:
    def __init__(self, id, name, sections):
        self.id = id
        self.name = name
        self.sections = sections
        self.content_hash = ""


def _project_with(webapps):
    manifest = ManifestCheck(exported_with_options={}, actual_content={}, generated_with_dss_version="test", has_row_data=False)
    return Project(manifest=manifest, datasets=DATASETS, webapps=webapps)


def _mock_scan_result(mock_block):
    return {"real_reads": [], "required_cols_checks": [], "mock_blocks": [mock_block]}


def test_export_field_record_shape_and_non_data_state_is_null():
    block = MockBlock(
        id="mock-1",
        title="Section A",
        start_line=1,
        end_line=5,
        required_fields=[
            DeclaredField(name="date", description="the date column", source_line=2, kind=FieldKind.DATA, usage="dataframe column"),
            DeclaredField(name="chart_label", description=None, source_line=3, kind=FieldKind.RENDER, usage="jsonify response key"),
        ],
    )
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), _mock_scan_result(block), DATASETS, COLUMN_INDEX, MARKERS)
    webapp = _Webapp("w1", "Webapp One", sections)
    project = _project_with([webapp])

    export = build_export(project, derivability_cache={})
    by_name = {r["field"]: r for r in export["fields"]}

    # A mock section never has a "currently read" dataset, so its best
    # possible Tier-1 state is available_elsewhere, not satisfied (that
    # state is reserved for a real-read section whose read dataset already
    # has the column).
    assert by_name["date"]["field_kind"] == "data"
    assert by_name["date"]["state"] == "available_elsewhere"
    assert by_name["date"]["source_dataset"] == "PRIMARY"
    assert by_name["date"]["webapp"] == "Webapp One"
    assert by_name["date"]["section"] == "Section A"

    assert by_name["chart_label"]["field_kind"] == "render"
    assert by_name["chart_label"]["state"] is None


def test_webapp_summary_counts_exclude_non_data_fields():
    block = MockBlock(
        id="mock-1",
        title="Section A",
        start_line=1,
        end_line=5,
        required_fields=[
            DeclaredField(name="date", description=None, source_line=2, kind=FieldKind.DATA),
            DeclaredField(name="chart_label", description=None, source_line=3, kind=FieldKind.RENDER),
            DeclaredField(name="another_render", description=None, source_line=4, kind=FieldKind.RENDER),
        ],
    )
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), _mock_scan_result(block), DATASETS, COLUMN_INDEX, MARKERS)
    webapp = _Webapp("w1", "Webapp One", sections)
    project = _project_with([webapp])

    export = build_export(project, derivability_cache={})
    summary = export["webapp_summaries"]["w1"]

    assert summary["needed"] == 1
    assert summary["available_elsewhere"] == 1
    assert summary["non_data_excluded"] == 2


def test_cached_derivable_result_surfaces_as_derivable_state():
    block = MockBlock(
        id="mock-1",
        title="Section A",
        start_line=1,
        end_line=5,
        required_fields=[DeclaredField(name="totally_fabricated", description=None, source_line=2, kind=FieldKind.DATA)],
    )
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), _mock_scan_result(block), DATASETS, COLUMN_INDEX, MARKERS)
    webapp = _Webapp("w1", "Webapp One", sections)
    project = _project_with([webapp])

    cached = DerivabilityResult(
        mock_block_id="mock-1",
        fields=[
            FieldDerivability(
                field="totally_fabricated", derivable=True, source_dataset="PRIMARY", source_columns=["date"], note="plausible match"
            )
        ],
        overall_note="",
        engine="stub",
    )
    derivability_cache = {("w1", "mock-1"): cached}

    export = build_export(project, derivability_cache)
    record = next(r for r in export["fields"] if r["field"] == "totally_fabricated")

    assert record["state"] == "derivable"
    assert record["source_dataset"] == "PRIMARY"
    assert record["derivability_note"] == "plausible match"

    summary = export["webapp_summaries"]["w1"]
    assert summary["derivable"] == 1
    assert summary["missing"] == 0


def test_derivability_cache_never_invoked_for_real_read_missing_fields():
    """A real-read section's Missing column means the dataset it reads
    genuinely lacks that column - derivability doesn't apply there, only to
    mock sections' fields."""
    real_read = RealRead(dataset_name="PRIMARY", resolved=True, line_no=1)
    from backend.models import RequiredColsCheck

    check = RequiredColsCheck(
        var_name="required_cols",
        fields=[DeclaredField(name="not_in_dataset", description=None, source_line=2)],
        line_no=3,
    )
    scan_result = {"real_reads": [real_read], "required_cols_checks": [check], "mock_blocks": []}
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), scan_result, DATASETS, COLUMN_INDEX, MARKERS)
    webapp = _Webapp("w1", "Webapp One", sections)
    project = _project_with([webapp])

    cached = DerivabilityResult(
        mock_block_id="irrelevant",
        fields=[FieldDerivability(field="not_in_dataset", derivable=True, source_dataset="PRIMARY", source_columns=[], note="")],
        overall_note="",
        engine="stub",
    )
    section_id = sections[0].id
    derivability_cache = {("w1", section_id): cached}

    export = build_export(project, derivability_cache)
    record = next(r for r in export["fields"] if r["field"] == "not_in_dataset")
    assert record["state"] == "missing"


def test_cross_webapp_matrix_covers_field_present_in_two_webapps():
    from backend.models import RequiredColsCheck

    def _real_read_sections(webapp_id):
        real_read = RealRead(dataset_name="PRIMARY", resolved=True, line_no=1)
        check = RequiredColsCheck(var_name="required_cols", fields=[DeclaredField(name="date", description=None, source_line=2)], line_no=3)
        scan_result = {"real_reads": [real_read], "required_cols_checks": [check], "mock_blocks": []}
        return build_sections_for_webapp(_Webapp(webapp_id, webapp_id, []), scan_result, DATASETS, COLUMN_INDEX, MARKERS)

    project = _project_with([_Webapp("wa", "Webapp A", _real_read_sections("wa")), _Webapp("wb", "Webapp B", _real_read_sections("wb"))])

    export = build_export(project, derivability_cache={})
    row = next(r for r in export["cross_webapp_matrix"] if r["field"] == "date")
    assert row["by_webapp"] == {"wa": "satisfied", "wb": "satisfied"}


def test_markdown_summary_lists_missing_fields_as_request_from_client():
    block = MockBlock(
        id="mock-1", title="Section A", start_line=1, end_line=5,
        required_fields=[DeclaredField(name="totally_fabricated", description=None, source_line=2)],
    )
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), _mock_scan_result(block), DATASETS, COLUMN_INDEX, MARKERS)
    project = _project_with([_Webapp("w1", "Webapp One", sections)])

    export = build_export(project, derivability_cache={})
    assert "totally_fabricated" in export["markdown_summary"]
    assert "Request from client" in export["markdown_summary"]


def test_non_data_breakdown_counts_by_kind():
    block = MockBlock(
        id="mock-1", title="Section A", start_line=1, end_line=5,
        required_fields=[
            DeclaredField(name="date", description=None, source_line=2, kind=FieldKind.DATA),
            DeclaredField(name="a_render", description=None, source_line=3, kind=FieldKind.RENDER),
            DeclaredField(name="a_config", description=None, source_line=4, kind=FieldKind.CONFIG),
            DeclaredField(name="a_manual", description=None, source_line=5, kind=FieldKind.MANUAL),
            DeclaredField(name="a_derived", description=None, source_line=6, kind=FieldKind.DERIVED),
            DeclaredField(name="an_uncertain", description=None, source_line=7, kind=FieldKind.UNCERTAIN),
        ],
    )
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), _mock_scan_result(block), DATASETS, COLUMN_INDEX, MARKERS)
    project = _project_with([_Webapp("w1", "Webapp One", sections)])

    export = build_export(project, derivability_cache={})
    breakdown = export["webapp_summaries"]["w1"]["non_data_breakdown"]

    assert breakdown == {"render": 1, "config": 1, "manual": 1, "derived": 1, "uncertain": 1}
    assert export["webapp_summaries"]["w1"]["non_data_excluded"] == 5


_WIDE_DATASETS = {
    f"DS_{i}": Dataset(name=f"DS_{i}", type="Snowflake", columns=[Column(name="widecol", type="string")]) for i in range(6)
}


def test_available_ambiguous_reported_separately_from_missing():
    column_index = build_column_index(_WIDE_DATASETS)
    block = MockBlock(
        id="mock-1", title="Section A", start_line=1, end_line=5,
        required_fields=[DeclaredField(name="widecol", description=None, source_line=2)],
    )
    sections = build_sections_for_webapp(_Webapp("w1", "Webapp One", []), _mock_scan_result(block), _WIDE_DATASETS, column_index, MARKERS)
    manifest = ManifestCheck(exported_with_options={}, actual_content={}, generated_with_dss_version="test", has_row_data=False)
    project = Project(manifest=manifest, datasets=_WIDE_DATASETS, webapps=[_Webapp("w1", "Webapp One", sections)])

    export = build_export(project, derivability_cache={})
    summary = export["webapp_summaries"]["w1"]

    assert summary["available_ambiguous"] == 1
    assert summary["missing"] == 0

    record = next(r for r in export["fields"] if r["field"] == "widecol")
    assert record["state"] == "available_ambiguous"
    assert record["candidate_count"] == 6

    md = export["markdown_summary"]
    request_section = md.split("## Request from client")[1].split("## Needs confirmation")[0]
    confirmation_section = md.split("## Needs confirmation")[1]
    assert "widecol" not in request_section
    assert "widecol" in confirmation_section
