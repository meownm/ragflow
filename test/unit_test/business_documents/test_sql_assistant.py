"""Research presentation must never recalculate or reinterpret saved values."""

from copy import deepcopy

import pytest

from business_documents.sql_assistant import analyze_series, compare_values, default_view, output_columns, present_rows, transfer_view, validate_view


def test_expression_identity_survives_alias_and_position_changes_but_not_new_formula():
    first = output_columns("SELECT t.region AS region, SUM(t.amount) AS revenue FROM sales AS t GROUP BY t.region", ["region", "revenue"])
    renamed = output_columns("SELECT SUM(t.amount) AS total, t.region AS area FROM sales AS t GROUP BY t.region", ["total", "area"])
    changed = output_columns("SELECT t.region AS region, AVG(t.amount) AS revenue FROM sales AS t GROUP BY t.region", ["region", "revenue"])
    assert first[0]["id"] == renamed[1]["id"]
    assert first[1]["id"] == renamed[0]["id"]
    assert first[1]["id"] != changed[1]["id"]


def test_column_identity_does_not_transfer_between_different_physical_sources():
    first = output_columns("SELECT t.amount AS value FROM public.sales AS t", ["value"])
    second = output_columns("SELECT t.amount AS value FROM private.payroll AS t", ["value"])
    assert first[0]["id"] != second[0]["id"]


def test_cast_day_is_a_drill_dimension_but_cast_aggregate_is_not():
    columns = output_columns("SELECT CAST(DATE_TRUNC('day', t.created_at) AS DATE) AS day, CAST(COUNT(t.id) AS INTEGER) AS total FROM public.events t GROUP BY 1", ["day", "total"])
    assert [column["drillable"] for column in columns] == [True, False]


def test_view_filters_and_sorts_entire_saved_set_without_changing_rows():
    columns = output_columns("SELECT id, amount FROM sales", ["id", "amount"])
    rows = [[index, 1000 - index] for index in range(250)]
    before = deepcopy(rows)
    view = default_view(columns)
    view["hidden"] = [columns[0]["id"]]
    view["filters"] = [{"column_id": columns[0]["id"], "operator": "gte", "value": 200}]
    view["sort"] = {"column_id": columns[1]["id"], "direction": "ASC"}
    result = present_rows(rows, columns, validate_view(view, columns))
    assert len(result) == 50
    assert result[0] == (249, [249, 751])
    assert rows == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("hidden", ["missing"]),
        ("column_order", []),
        ("labels", {"unknown": "Sum"}),
        ("formats", {}),
        ("sort", {"column_id": "missing", "direction": "ASC"}),
        ("filters", [{"column_id": "missing", "operator": "eq", "value": 1}]),
    ],
)
def test_invalid_view_rejected_against_exact_result(field, value):
    columns = output_columns("SELECT id FROM sales", ["id"])
    view = default_view(columns)
    if field == "formats":
        value = {columns[0]["id"]: "invented"}
    view[field] = value
    with pytest.raises(ValueError):
        validate_view(view, columns)


def test_null_filter_does_not_treat_zero_or_empty_string_as_null():
    columns = output_columns("SELECT id FROM sales", ["id"])
    view = default_view(columns)
    view["filters"] = [{"column_id": columns[0]["id"], "operator": "is_null", "value": None}]
    assert present_rows([[None], [0], [""], [False]], columns, view) == [(0, [None])]


def test_transfer_never_applies_old_settings_to_same_named_changed_expression():
    old = output_columns("SELECT SUM(amount) AS value, region FROM sales GROUP BY region", ["value", "region"])
    new = output_columns("SELECT AVG(amount) AS value, region FROM sales GROUP BY region", ["value", "region"])
    view = default_view(old)
    view["hidden"] = [old[0]["id"]]
    view["formats"] = {old[0]["id"]: "currency"}
    view["labels"] = {old[1]["id"]: "Регион"}
    transferred, removed = transfer_view(view, new)
    assert removed == [old[0]["id"]]
    assert transferred["hidden"] == []
    assert transferred["formats"] == {}
    assert transferred["labels"] == {new[1]["id"]: "Регион"}


def test_comparison_preserves_decimal_precision_and_handles_zero_missing_and_negative_base():
    result = compare_values([["0.3", "0.1"], [2, 0], [None, 3], [-3, -2]], 0, 1)
    assert result[0]["delta"] == "0.2"
    assert result[0]["percent"] == "200"
    assert result[1]["status"] == "ZERO_BASELINE" and result[1]["percent"] is None
    assert result[2]["status"] == "MISSING" and result[2]["delta"] is None
    assert result[3]["percent"] == "-50.0"


def test_anomaly_analysis_is_deterministic_and_keeps_missing_and_zero_mad_limits_explicit():
    result = analyze_series([[10], [10], [10], [10], [10], [100], [None]], 0)
    assert result["observations"] == [{"row_index": 5, "value": "100", "score": None}]
    assert result["missing_count"] == 1
    assert result["mad"] == "0"
    assert len(result["limitations"]) == 4


def test_anomaly_analysis_rejects_short_or_non_numeric_series():
    with pytest.raises(ValueError):
        analyze_series([[1], [2], [float("nan")], [True], ["text"]], 0)
