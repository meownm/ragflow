"""Saved-set metrics preserve precision, scope and aggregation boundaries."""

from copy import deepcopy
from decimal import Inexact, localcontext

import pytest

from business_documents.sql_assistant import default_view, output_columns, result_metrics, transfer_view, validate_view

SQL = "SELECT region, amount FROM public.sales"


def metric(kind, column=None, index=None):
    return {"id": kind, "title": kind, "kind": kind, "column_id": column, "row_index": index}


def test_metrics_use_full_filtered_set_not_a_page_and_preserve_decimal_precision():
    columns = output_columns(SQL, ["region", "amount"])
    view = default_view(columns)
    view["filters"] = [{"column_id": columns[0]["id"], "operator": "eq", "value": "A"}]
    view["metrics"] = [metric(kind, columns[1]["id"]) for kind in ("sum", "avg", "count", "distinct")]
    rows = [["A", "0.1"] for _ in range(200)] + [["B", "99999"], ["A", None]]
    before = deepcopy(rows)
    values = result_metrics(rows, columns, validate_view(view, columns, SQL), SQL)
    assert [item["value"] for item in values] == ["20.0", "0.1", "200", "1"]
    assert rows == before
    view["filters"] = []
    view["metrics"] = [metric("sum", columns[1]["id"])]
    wide = result_metrics([["A", "1e1000"], ["A", "1e-1000"]], columns, view, SQL)[0]
    assert wide["value"] == "1" + "0" * 1000 + "." + "0" * 999 + "1"
    assert not wide["rounded"]


def test_cell_zero_null_empty_and_invalid_values_are_distinct():
    columns = output_columns(SQL, ["region", "amount"])
    view = default_view(columns)
    view["metrics"] = [metric("value", columns[1]["id"], 0), metric("sum", columns[1]["id"]), metric("rows")]
    assert result_metrics([["A", 0], ["A", None]], columns, view, SQL)[0]["value"] == 0
    assert result_metrics([["A", None]], columns, view, SQL)[1]["status"] == "NULL"
    assert result_metrics([], columns, view, SQL)[2]["value"] == "0"
    assert result_metrics([["A", "wrong"]], columns, view, SQL)[1]["status"] == "NON_NUMERIC"
    assert result_metrics([["A", "1e100000"]], columns, view, SQL)[1]["status"] == "OUT_OF_RANGE"
    view["filters"] = [{"column_id": columns[0]["id"], "operator": "eq", "value": "B"}]
    assert result_metrics([["A", 1]], columns, view, SQL)[0]["status"] == "FILTERED_OUT"


@pytest.mark.parametrize(
    "sql", ["SELECT SUM(amount) AS amount FROM sales", "SELECT DISTINCT amount FROM sales", "SELECT amount, COUNT(*) OVER() FROM sales", "SELECT amount FROM sales UNION ALL SELECT amount FROM sales"]
)
def test_aggregated_distinct_window_and_set_outputs_cannot_be_reaggregated(sql):
    columns = output_columns(sql, ["amount"] if "OVER" not in sql else ["amount", "count"])
    view = default_view(columns)
    view["metrics"] = [metric("sum", columns[0]["id"])]
    with pytest.raises(ValueError, match="агрегация"):
        validate_view(view, columns, sql)
    assert result_metrics([[100] * len(columns)], columns, view, sql)[0]["status"] == "REQUIRES_QUERY"
    view["metrics"] = [metric("value", columns[0]["id"], 0)]
    assert validate_view(view, columns, sql)["metrics"][0]["kind"] == "value"


def test_appearance_and_metric_positions_do_not_leak_into_another_run():
    columns = output_columns(SQL, ["region", "amount"])
    view = default_view(columns)
    view["appearance"].update(density="compact", striped=True)
    view["metrics"] = [metric("value", columns[1]["id"], 4)]
    transferred, removed = transfer_view(view, columns)
    assert transferred["appearance"] == view["appearance"]
    assert transferred["metrics"] == [] and removed == ["metric:value"]
    view["appearance"]["density"] = "invalid"
    with pytest.raises(ValueError, match="внешний вид"):
        validate_view(view, columns, SQL)


def test_repeating_average_is_explicitly_marked_rounded():
    columns = output_columns(SQL, ["region", "amount"])
    view = default_view(columns)
    view["metrics"] = [metric("avg", columns[1]["id"])]
    value = result_metrics([["A", 1], ["A", 0], ["A", 0]], columns, view, SQL)[0]
    assert value["rounded"] and value["value"].startswith("0.3333")
    with localcontext() as context:
        context.flags[Inexact] = True
        exact = result_metrics([["A", 1]], columns, view, SQL)[0]
    assert exact["value"] == "1" and exact["rounded"] is False


def test_legacy_chart_remains_visible_on_save_and_transfer():
    columns = output_columns(SQL, ["region", "amount"])
    legacy = default_view(columns)
    legacy.pop("appearance")
    legacy.pop("metrics")
    legacy["chart"] = {"kind": "bar", "x": columns[0]["id"], "y": columns[1]["id"]}
    assert validate_view(legacy, columns, SQL)["appearance"]["display"] == "both"
    transferred, _ = transfer_view(legacy, columns)
    assert transferred["appearance"]["display"] == "both"
    assert transferred["chart"] == legacy["chart"]
    changed_columns = output_columns("SELECT region FROM public.sales", ["region"])
    transferred, _ = transfer_view(legacy, changed_columns)
    assert transferred["appearance"]["display"] == "table" and transferred["chart"] is None
    explicit_table = {**legacy, "appearance": default_view(columns)["appearance"]}
    assert validate_view(explicit_table, columns, SQL)["appearance"]["display"] == "table"
