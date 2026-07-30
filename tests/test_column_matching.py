from backend.column_matching import build_column_index, normalize_column_name
from backend.models import Column, Dataset


def test_normalize_trims_and_lowercases():
    assert normalize_column_name("  Sales_Qty  ") == normalize_column_name("sales_qty")


def test_normalize_folds_full_width_to_half_width():
    # Full-width Latin "Ａ" (U+FF21) should normalize to match half-width "A".
    assert normalize_column_name("Ａ") == normalize_column_name("A")


def test_normalize_folds_full_width_digits():
    assert normalize_column_name("１２３") == normalize_column_name("123")


def test_build_column_index_maps_normalized_name_to_datasets():
    datasets = {
        "ds_a": Dataset(name="ds_a", type="Snowflake", columns=[Column(name="SAL_NAM", type="string")]),
        "ds_b": Dataset(name="ds_b", type="Snowflake", columns=[Column(name="sal_nam", type="string")]),
        "ds_c": Dataset(name="ds_c", type="Snowflake", columns=[Column(name="unrelated", type="string")]),
    }
    index = build_column_index(datasets)
    key = normalize_column_name("SAL_NAM")
    assert index[key] == ["ds_a", "ds_b"]


def test_build_column_index_missing_column_absent_from_index():
    datasets = {"ds_a": Dataset(name="ds_a", type="Snowflake", columns=[Column(name="x", type="string")])}
    index = build_column_index(datasets)
    assert normalize_column_name("nonexistent") not in index
