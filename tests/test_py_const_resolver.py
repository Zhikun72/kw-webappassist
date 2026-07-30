from backend.py_const_resolver import resolve_module_string_constants, resolve_name_or_literal

SOURCE = '''
DATASET_NAME = "SAL_QTY_output"
DATE_COL = "日付"   # dateonly
NUMBER_CONST = 42
COMPUTED = "a" + "b"
def f():
    LOCAL_NOT_MODULE_LEVEL = "should not resolve"
'''


def test_resolves_simple_string_assignments():
    constants = resolve_module_string_constants(SOURCE)
    assert constants["DATASET_NAME"] == "SAL_QTY_output"
    assert constants["DATE_COL"] == "日付"


def test_ignores_non_string_and_non_literal_assignments():
    constants = resolve_module_string_constants(SOURCE)
    assert "NUMBER_CONST" not in constants
    assert "COMPUTED" not in constants


def test_ignores_function_local_assignments():
    constants = resolve_module_string_constants(SOURCE)
    assert "LOCAL_NOT_MODULE_LEVEL" not in constants


def test_resolve_name_or_literal_handles_quoted_literal():
    assert resolve_name_or_literal('"literal_value"', {}) == "literal_value"


def test_resolve_name_or_literal_handles_bare_identifier():
    constants = {"DATASET_NAME": "SAL_QTY_output"}
    assert resolve_name_or_literal("DATASET_NAME", constants) == "SAL_QTY_output"


def test_resolve_name_or_literal_unknown_identifier_returns_none():
    assert resolve_name_or_literal("UNKNOWN_VAR", {}) is None
