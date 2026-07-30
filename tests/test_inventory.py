from backend.column_matching import build_column_index
from backend.inventory import build_sections_for_webapp, classify_needed_columns_for_webapp
from backend.models import Column, ColumnState, Dataset, FieldKind, MockBlock, RealRead, RequiredColsCheck, SectionState, DeclaredField
from backend.mock_detector import scan_backend
from backend.config import load_markers

MARKERS = load_markers()

# Two real datasets: PRIMARY (what the webapp actually reads) is missing one
# required column that DOES exist in OTHER_DATASET - the "available elsewhere"
# case a flat dataset-level view can't distinguish from a truly missing one.
PRIMARY = Dataset(
    name="PRIMARY",
    type="Snowflake",
    columns=[Column(name="date", type="date"), Column(name="sales", type="double")],
)
OTHER_DATASET = Dataset(
    name="OTHER_DATASET",
    type="Snowflake",
    columns=[Column(name="region", type="string")],
)
DATASETS = {"PRIMARY": PRIMARY, "OTHER_DATASET": OTHER_DATASET}


def _scan_result_for(real_reads=None, required_cols_checks=None, mock_blocks=None):
    return {
        "real_reads": real_reads or [],
        "required_cols_checks": required_cols_checks or [],
        "mock_blocks": mock_blocks or [],
    }


def test_partial_state_when_some_required_columns_missing():
    column_index = build_column_index(DATASETS)
    real_read = RealRead(dataset_name="PRIMARY", resolved=True, line_no=10)
    check = RequiredColsCheck(
        var_name="required_cols",
        fields=[
            DeclaredField(name="date", description=None, source_line=11),
            DeclaredField(name="sales", description=None, source_line=11),
            DeclaredField(name="region", description=None, source_line=11),
        ],
        line_no=12,
    )
    scan_result = _scan_result_for(real_reads=[real_read], required_cols_checks=[check])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, DATASETS, column_index, MARKERS)
    assert len(sections) == 1
    section = sections[0]
    assert section.state == SectionState.PARTIAL
    assert section.satisfied_count == 2
    assert section.total_count == 3

    states = {c.name: c.state for c in section.column_states}
    assert states["date"] == ColumnState.SATISFIED
    assert states["sales"] == ColumnState.SATISFIED
    assert states["region"] == ColumnState.AVAILABLE_ELSEWHERE


def test_referenced_missing_when_dataset_absent():
    column_index = build_column_index(DATASETS)
    real_read = RealRead(dataset_name="DOES_NOT_EXIST", resolved=True, line_no=1)
    scan_result = _scan_result_for(real_reads=[real_read])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, DATASETS, column_index, MARKERS)
    assert sections[0].state == SectionState.REFERENCED_MISSING
    assert sections[0].matched_dataset is None


def test_ready_when_all_required_columns_present():
    column_index = build_column_index(DATASETS)
    real_read = RealRead(dataset_name="PRIMARY", resolved=True, line_no=1)
    check = RequiredColsCheck(
        var_name="required_cols",
        fields=[DeclaredField(name="date", description=None, source_line=2), DeclaredField(name="sales", description=None, source_line=2)],
        line_no=3,
    )
    scan_result = _scan_result_for(real_reads=[real_read], required_cols_checks=[check])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, DATASETS, column_index, MARKERS)
    assert sections[0].state == SectionState.READY


def test_mock_section_field_classified_available_elsewhere_with_intended_source_flag():
    column_index = build_column_index(DATASETS)
    mock_block = MockBlock(
        id="mock-1",
        title="Region breakdown",
        start_line=1,
        end_line=5,
        migration_hint_dataset="OTHER_DATASET",
        required_fields=[DeclaredField(name="region", description=None, source_line=2)],
    )
    scan_result = _scan_result_for(mock_blocks=[mock_block])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, DATASETS, column_index, MARKERS)
    assert sections[0].state == SectionState.MOCK
    col = sections[0].column_states[0]
    assert col.state == ColumnState.AVAILABLE_ELSEWHERE
    assert col.in_intended_source is True
    assert "OTHER_DATASET" in col.source_datasets


def test_mock_section_field_missing_when_nowhere_in_project():
    column_index = build_column_index(DATASETS)
    mock_block = MockBlock(
        id="mock-2",
        title="Fabricated",
        start_line=1,
        end_line=5,
        required_fields=[DeclaredField(name="totally_fabricated_field", description=None, source_line=2)],
    )
    scan_result = _scan_result_for(mock_blocks=[mock_block])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, DATASETS, column_index, MARKERS)
    assert sections[0].column_states[0].state == ColumnState.MISSING


def test_classify_needed_columns_for_webapp_dedupes_and_tracks_requesters():
    column_index = build_column_index(DATASETS)
    real_read = RealRead(dataset_name="PRIMARY", resolved=True, line_no=1)
    check = RequiredColsCheck(
        var_name="required_cols", fields=[DeclaredField(name="region", description=None, source_line=2)], line_no=3
    )
    mock_block = MockBlock(
        id="mock-1", title="dup", start_line=10, end_line=15,
        required_fields=[DeclaredField(name="region", description=None, source_line=11)],
    )
    scan_result = _scan_result_for(real_reads=[real_read], required_cols_checks=[check], mock_blocks=[mock_block])

    class FakeWebapp:
        id = "w1"
        sections = []

    webapp = FakeWebapp()
    webapp.sections = build_sections_for_webapp(webapp, scan_result, DATASETS, column_index, MARKERS)
    needed = classify_needed_columns_for_webapp(webapp)

    region_cols = [c for c in needed if c.name == "region"]
    assert len(region_cols) == 1
    assert len(region_cols[0].requested_by) == 2


def test_render_and_uncertain_fields_excluded_from_fill_ability_analysis():
    """A mock block's render/uncertain fields (Part 5's classification gate)
    must never inflate the column-state counts - only DATA fields get a
    fill-ability state."""
    column_index = build_column_index(DATASETS)
    mock_block = MockBlock(
        id="mock-3",
        title="Mixed kinds",
        start_line=1,
        end_line=5,
        required_fields=[
            DeclaredField(name="region", description=None, source_line=2, kind=FieldKind.DATA),
            DeclaredField(name="chart_label", description=None, source_line=3, kind=FieldKind.RENDER),
            DeclaredField(name="ambiguous_key", description=None, source_line=4, kind=FieldKind.UNCERTAIN),
        ],
    )
    scan_result = _scan_result_for(mock_blocks=[mock_block])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, DATASETS, column_index, MARKERS)
    section = sections[0]

    assert section.total_count == 1
    assert [c.name for c in section.column_states] == ["region"]
    assert {f.name for f in section.non_data_fields} == {"chart_label", "ambiguous_key"}


# Six datasets all declaring "widecol" - a name-collision scenario: matching
# this many datasets by exact name with no anchor is noise, not a
# trustworthy join candidate (default available_elsewhere_ambiguous_threshold is 5).
_WIDE_DATASETS = {
    f"DS_{i}": Dataset(name=f"DS_{i}", type="Snowflake", columns=[Column(name="widecol", type="string")]) for i in range(6)
}


def test_wide_name_match_with_no_anchor_downgrades_to_available_ambiguous():
    column_index = build_column_index(_WIDE_DATASETS)
    mock_block = MockBlock(
        id="mock-wide", title="Wide match", start_line=1, end_line=5,
        required_fields=[DeclaredField(name="widecol", description=None, source_line=2)],
    )
    scan_result = _scan_result_for(mock_blocks=[mock_block])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, _WIDE_DATASETS, column_index, MARKERS)
    col = sections[0].column_states[0]

    assert col.state == ColumnState.AVAILABLE_AMBIGUOUS
    assert col.candidate_count == 6
    assert len(col.source_datasets) <= MARKERS["available_elsewhere_max_sources_shown"]


def test_wide_name_match_with_intended_source_anchor_stays_available_elsewhere():
    column_index = build_column_index(_WIDE_DATASETS)
    mock_block = MockBlock(
        id="mock-wide-anchored", title="Wide match, anchored", start_line=1, end_line=5,
        migration_hint_dataset="DS_3",
        required_fields=[DeclaredField(name="widecol", description=None, source_line=2)],
    )
    scan_result = _scan_result_for(mock_blocks=[mock_block])

    class FakeWebapp:
        id = "w1"

    sections = build_sections_for_webapp(FakeWebapp(), scan_result, _WIDE_DATASETS, column_index, MARKERS)
    col = sections[0].column_states[0]

    assert col.state == ColumnState.AVAILABLE_ELSEWHERE
    assert col.in_intended_source is True
    assert col.source_datasets[0] == "DS_3"
    assert col.candidate_count == 6
