from __future__ import annotations

import pytest

from business_documents.sql_query.result_validation import ResultValidationError, validate_result


def test_result_gate_marks_limit_and_does_not_publish_the_probe_row():
    result = validate_result(
        {"columns": ["value"], "rows": [[1], [2], [3]]},
        expected_columns=["value"],
        row_limit=2,
        max_result_bytes=100,
    )
    assert result["rows"] == [[1], [2]]
    assert result["checks"]["completeness"] == "LIMITED"
    assert result["checks"]["truncated"] is True


@pytest.mark.parametrize(
    "source",
    [
        {"columns": ["wrong"], "rows": [[1]]},
        {"columns": ["value"], "rows": [[1, 2]]},
        {"columns": ["value"], "rows": [[1], [2], [3], [4]]},
    ],
)
def test_result_gate_rejects_schema_and_bound_mismatches(source):
    with pytest.raises(ResultValidationError):
        validate_result(source, expected_columns=["value"], row_limit=2, max_result_bytes=100)
