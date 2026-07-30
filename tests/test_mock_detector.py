from backend.config import load_markers
from backend.mock_detector import scan_backend
from backend.models import FieldKind

MARKERS = load_markers()

# Reproduces the real project's own ambiguity: the same field name
# ("conflict_field") shows up as a dataframe column in one function and as
# a jsonify response key in a sibling function within the same block -
# classification is by position (which dict a key's occurrences actually
# touch), never by name.
FIELD_KIND_SOURCE = '''
import pandas as pd
from flask import jsonify

# ============================================================
# Kind Classification - MOCK DATA
# ============================================================
def kind_view():
    lookup_table = {"east": 1, "west": 2}
    payload = {"conflict_field": "seen in jsonify here", "render_only": "just for display"}
    return jsonify(payload)


def _mock_kind_data():
    rows = []
    for i in range(2):
        rows.append({"amount": i * 2, "conflict_field": i})
    return pd.DataFrame(rows)
'''

# v3: derived intermediates, config/manual constant-value tracing, and the
# "be conservative" clause (a leading-underscore name built directly into
# the dataframe still counts as a real column).
V3_SOURCE = '''
import pandas as pd
from flask import jsonify

RATE_THRESHOLD = 12.5
PLACEHOLDER_NOTES = ["first note", "second note"]

# ============================================================
# V3 Classification - MOCK DATA
# ============================================================
def v3_view():
    df = _mock_v3_data()
    payload = {
        "my_rate_threshold": RATE_THRESHOLD,
        "notes": PLACEHOLDER_NOTES,
        "computed_threshold": [RATE_THRESHOLD] * 3,
        "total": df["amount"].sum(),
    }
    return jsonify(payload)


def _mock_v3_data():
    rows = []
    for i in range(2):
        rows.append({"amount": i, "_special_col": i * 2})
    df = pd.DataFrame(rows)
    df["_year"] = df["amount"]
    df["_special_col"] = df["_special_col"] * 2
    return df
'''

BACKEND_SOURCE = '''
import dataiku
import pandas as pd

DATASET_NAME = "SAL_QTY_output"
DATE_COL = "日付"
SALES_COL = "SAL_QTY_avg"


def load_real():
    dataset = dataiku.Dataset(DATASET_NAME)
    df = dataset.get_dataframe()
    required_cols = [DATE_COL, SALES_COL]
    missing_cols = [c for c in required_cols if c not in df.columns]
    return df


def render_dropdown():
    # not mock - a UI placeholder string, must not trigger a false positive
    widget = Dropdown(placeholder="Select all")
    return widget


# 実データ移行時は dataiku.Dataset("RealTargetDataset") から読み込む
def _mock_needs_migration():
    return pd.DataFrame({"y": [1], "error": ["should be filtered"]})


# ============================================================
# Signal Monitor - MOCK DATA
# ============================================================
PRICE_COL = "Price"  # unit price
def _mock_signal():
    rows = []
    for i in range(3):
        rows.append({PRICE_COL: i, "region": "east"})
    df = pd.DataFrame(rows)
    df["extra_flag"] = True
    return df
'''


def test_real_read_detected_via_resolved_variable():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    reads = result["real_reads"]
    assert len(reads) == 1
    assert reads[0].dataset_name == "SAL_QTY_output"
    assert reads[0].resolved is True


def test_required_cols_resolved_through_column_variables():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    checks = result["required_cols_checks"]
    assert len(checks) == 1
    assert [f.name for f in checks[0].fields] == ["日付", "SAL_QTY_avg"]


def test_banner_mock_block_detected_with_title():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    titles = [b.title for b in result["mock_blocks"]]
    assert any("Signal Monitor" in (t or "") for t in titles)


def test_ui_placeholder_string_is_not_a_false_positive():
    """The classic false-positive: placeholder="..." is a UI prop in code,
    not a comment - it must never be picked up as a mock marker."""
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    all_snippets = " ".join(b.snippet for b in result["mock_blocks"])
    assert "render_dropdown" not in [f for b in result["mock_blocks"] for f in b.mock_functions]
    assert "Select all" not in all_snippets


def test_migration_hint_captures_intended_dataset_name():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    hinted = [b for b in result["mock_blocks"] if b.migration_hint_dataset]
    assert len(hinted) == 1
    assert hinted[0].migration_hint_dataset == "RealTargetDataset"


def test_mock_function_outside_banner_gets_its_own_block():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    fn_names = [fn for b in result["mock_blocks"] for fn in b.mock_functions]
    assert "_mock_needs_migration" in fn_names
    assert "_mock_signal" in fn_names


def test_mock_block_required_fields_from_append_and_dataframe_pattern():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    signal_block = next(b for b in result["mock_blocks"] if "_mock_signal" in b.mock_functions)
    names = {f.name for f in signal_block.required_fields}
    assert names == {"Price", "region", "extra_flag"}


def test_mock_block_field_description_from_constant_comment():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    signal_block = next(b for b in result["mock_blocks"] if "_mock_signal" in b.mock_functions)
    price_field = next(f for f in signal_block.required_fields if f.name == "Price")
    assert price_field.description == "unit price"


def test_declared_field_denylist_filters_boilerplate_keys():
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    migration_block = next(b for b in result["mock_blocks"] if "_mock_needs_migration" in b.mock_functions)
    names = {f.name for f in migration_block.required_fields}
    assert "error" not in names
    assert "y" in names


def _kind_fields():
    result = scan_backend(FIELD_KIND_SOURCE, MARKERS)
    block = next(b for b in result["mock_blocks"] if "_mock_kind_data" in b.mock_functions)
    return {f.name: f for f in block.required_fields}


def test_field_used_only_as_dataframe_column_is_data():
    fields = _kind_fields()
    assert fields["amount"].kind == FieldKind.DATA
    assert fields["amount"].usage == "dataframe column"


def test_field_used_only_as_jsonify_key_is_render():
    fields = _kind_fields()
    assert fields["render_only"].kind == FieldKind.RENDER
    assert fields["render_only"].usage == "jsonify response key"


def test_field_used_as_both_dataframe_and_jsonify_key_is_uncertain_conflict():
    """The user's own real-world example: the same name plays both roles in
    different places - must be flagged, not silently resolved either way."""
    fields = _kind_fields()
    assert fields["conflict_field"].kind == FieldKind.UNCERTAIN


def test_field_with_no_data_or_render_signal_is_uncertain():
    """A key inside an unrelated lookup dict (never passed to DataFrame or
    jsonify) has no signal at all - also uncertain, not guessed."""
    fields = _kind_fields()
    assert fields["east"].kind == FieldKind.UNCERTAIN
    assert fields["east"].usage == "unclassified dict literal"


def test_required_cols_fields_are_always_data_kind():
    """required_cols-derived fields are data by construction - no
    classification ambiguity, unlike mock-block fields."""
    result = scan_backend(BACKEND_SOURCE, MARKERS)
    checks = result["required_cols_checks"]
    assert all(f.kind == FieldKind.DATA for f in checks[0].fields)


def _v3_fields():
    result = scan_backend(V3_SOURCE, MARKERS)
    block = next(b for b in result["mock_blocks"] if "_mock_v3_data" in b.mock_functions)
    return {f.name: f for f in block.required_fields}


def test_leading_underscore_subscript_only_name_is_derived():
    """work["_year"] = ... with no dataframe-constructor evidence is a
    locally-computed intermediate, not a field to source from anywhere."""
    fields = _v3_fields()
    assert fields["_year"].kind == FieldKind.DERIVED
    assert fields["_year"].usage == "post-construction column assignment"


def test_leading_underscore_name_built_into_dataframe_stays_data():
    """The 'be conservative' clause: a leading-underscore name that IS built
    directly into the mock's own dataframe-constructor dict is a real
    column regardless of naming convention - never downgraded to derived."""
    fields = _v3_fields()
    assert fields["_special_col"].kind == FieldKind.DATA


def test_dict_value_referencing_threshold_constant_is_config():
    """A dict key whose value is a bare reference to a *_THRESHOLD-named
    numeric constant is a business parameter, not a data gap - even though
    it's also inside a jsonify payload (config is a more specific reading
    of render, not a conflict with it)."""
    fields = _v3_fields()
    assert fields["my_rate_threshold"].kind == FieldKind.CONFIG
    assert "config constant reference" in fields["my_rate_threshold"].usage


def test_dict_value_referencing_placeholder_constant_is_manual():
    fields = _v3_fields()
    assert fields["notes"].kind == FieldKind.MANUAL
    assert "manual/placeholder constant reference" in fields["notes"].usage


def test_computed_expression_over_constant_is_not_traced():
    """[RATE_THRESHOLD] * 3 is a computed expression, not a bare Name -
    deliberately not traced into config/manual; stays whatever its
    containing dict's context already implies (here: plain render)."""
    fields = _v3_fields()
    assert fields["computed_threshold"].kind == FieldKind.RENDER
