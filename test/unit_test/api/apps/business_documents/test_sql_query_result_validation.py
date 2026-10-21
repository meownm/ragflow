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


@pytest.mark.parametrize("types", [[], [{"postgres_oid": True}], [{"postgres_oid": 0}], [{"postgres_oid": "1700"}], [{"postgres_oid": 1700, "extra": 1}]])
def test_result_gate_rejects_invalid_type_metadata(types):
    with pytest.raises(ResultValidationError):
        validate_result({"columns": ["value"], "rows": [["0.00100"]], "column_types": types}, expected_columns=["value"], row_limit=2, max_result_bytes=100)


def test_result_gate_persists_driver_types_without_changing_stored_values():
    checked = validate_result({"columns": ["value"], "rows": [["0.00100"]], "column_types": [{"postgres_oid": 1700}]}, expected_columns=["value"], row_limit=2, max_result_bytes=100)
    assert checked["checks"]["column_types"] == [{"postgres_oid": 1700}]
    assert checked["rows"] == [["0.00100"]]
