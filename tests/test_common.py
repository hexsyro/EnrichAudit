"""Contract/helper tests: option coercion and the generated profile shape."""

from __future__ import annotations

import json

import pytest

from enrichaudit import RESULT_SCHEMA_VERSION, ToolResult, __version__
from enrichaudit.common import opt_bool, opt_float, opt_int, opt_str_list, to_float, validate_options
from enrichaudit.profile import build_contract


def test_public_exports():
    assert __version__ == "0.2.0"
    assert RESULT_SCHEMA_VERSION == "1.1"
    assert ToolResult().status == "success"


def test_to_float_ignores_percent_sign():
    # Rates are percentage points: "12%" means 12, not 0.12.
    assert to_float("12%") == 12.0
    assert to_float("1,200") == 1200.0
    assert to_float("5k") == 5000.0
    assert to_float("2026-10-01") is None


def test_opt_helpers_fall_back_without_strict():
    options = {
        "staleness_days": "banana",
        "rate_tolerance": None,
        "check_sentiment_emotion": "maybe",
        "conflict_columns": 7,
    }
    assert opt_int(options, "staleness_days", 7) == 7
    assert opt_float(options, "rate_tolerance", 2.0) == 2.0
    assert opt_bool(options, "check_sentiment_emotion", True) is True
    assert opt_str_list(options, "conflict_columns", ["label"]) == ["label"]


def test_opt_helpers_accept_numeric_strings_and_bool_words():
    assert opt_int({"n": "14"}, "n", 7) == 14
    assert opt_float({"n": "1.5"}, "n", 2.0) == 1.5
    assert opt_bool({"flag": "no"}, "flag", True) is False
    assert opt_str_list({"columns": "a, b ,c"}, "columns", []) == ["a", "b", "c"]


def test_validate_options_raises_with_key_name():
    with pytest.raises(ValueError, match="staleness_days"):
        validate_options({"staleness_days": []})
    with pytest.raises(ValueError, match="conflict_columns"):
        validate_options({"conflict_columns": [1, 2]})
    # Valid values, including numeric strings, pass.
    validate_options(
        {"staleness_days": "7", "rate_tolerance": 1.5, "profile": True, "conflict_columns": "label, source"}
    )


def test_build_contract_per_column_shapes():
    rows = [
        {"id": "1", "likes": 10, "enriched_at": "2026-10-01", "sentiment": "positive", "flag": True},
        {"id": "2", "likes": 20, "enriched_at": "2026-10-03", "sentiment": "negative", "flag": False},
        {"id": "3", "likes": 30, "enriched_at": "2026-10-05", "sentiment": "positive", "flag": None},
    ]
    contract = build_contract(rows)
    json.dumps(contract)  # must be JSON-serializable

    assert contract["row_count"] == 3
    assert contract["column_count"] == 5
    columns = contract["columns"]

    likes = columns["likes"]
    assert likes["type"] == "number"
    assert likes["count"] == 3
    assert likes["null_rate"] == 0.0
    assert likes["distinct_count"] == 3
    assert (likes["min"], likes["max"]) == (10.0, 30.0)
    assert likes["p50"] == 20.0
    assert likes["p95"] == pytest.approx(29.0)

    date_col = columns["enriched_at"]
    assert date_col["type"] == "date"
    assert date_col["freshness"] == {"min": "2026-10-01", "max": "2026-10-05"}

    sentiment = columns["sentiment"]
    assert sentiment["type"] == "text"
    assert sentiment["top_values"] == [
        {"value": "positive", "count": 2},
        {"value": "negative", "count": 1},
    ]

    flag = columns["flag"]
    assert flag["type"] == "bool"
    assert flag["count"] == 2
    assert flag["null_rate"] == pytest.approx(0.3333)
    assert flag["top_values"] == [
        {"value": "False", "count": 1},
        {"value": "True", "count": 1},
    ]


def test_build_contract_caps_top_values_at_ten():
    rows = [{"category": f"cat-{i}"} for i in range(15)]
    contract = build_contract(rows)
    assert len(contract["columns"]["category"]["top_values"]) == 10
    assert contract["columns"]["category"]["distinct_count"] == 15


def test_build_contract_handles_empty_rows():
    contract = build_contract([])
    assert contract == {"row_count": 0, "column_count": 0, "columns": {}}
