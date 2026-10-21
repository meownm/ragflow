from copy import deepcopy

import pytest

from test.evals.sql_projects.corpus import SQL_ORACLES, result_errors


@pytest.mark.parametrize("case", SQL_ORACLES, ids=lambda case: case.id)
def test_frozen_oracle_rejects_changed_values_or_missing_rows(case):
    result = {"columns": list(case.columns), "rows": [list(row) for row in case.rows], "row_count": len(case.rows)}
    assert result_errors(result, case) == []
    damaged = deepcopy(result)
    damaged["rows"] = [["invented"]]
    damaged["row_count"] = 1
    assert result_errors(damaged, case)


def test_counterexamples_cannot_qualify_as_correct_aggregation():
    correct = next(case for case in SQL_ORACLES if case.id == "base_sum")
    for case_id in ("item_fanout_counterexample", "distinct_sum_counterexample", "two_fanouts_counterexample"):
        wrong = next(case for case in SQL_ORACLES if case.id == case_id)
        assert result_errors({"columns": wrong.columns, "rows": wrong.rows, "row_count": 1}, correct)


def test_oracle_preserves_null_empty_and_duplicate_multiplicity():
    correct = next(case for case in SQL_ORACLES if case.id == "region_null_empty")
    damaged = {"columns": correct.columns, "rows": [["", "40.00"], *correct.rows[1:]], "row_count": len(correct.rows)}
    assert result_errors(damaged, correct)
    duplicated = {"columns": correct.columns, "rows": [*correct.rows, correct.rows[0]], "row_count": len(correct.rows) + 1}
    assert result_errors(duplicated, correct)
