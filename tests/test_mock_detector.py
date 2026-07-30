from backend.config import load_markers
from backend.mock_detector import scan_backend

MARKERS = load_markers()

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


# ============================================================
# Signal Monitor - MOCK DATA
# ============================================================
def _mock_signal():
    return pd.DataFrame({"x": [1, 2, 3]})


# 実データ移行時は dataiku.Dataset("RealTargetDataset") から読み込む
def _mock_needs_migration():
    return pd.DataFrame({"y": [1]})
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
    assert checks[0].columns == ["日付", "SAL_QTY_avg"]


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
