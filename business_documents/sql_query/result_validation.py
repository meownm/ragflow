"""Validate bounded SQL rows before they become a project result."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


class ResultValidationError(ValueError):
    """A returned table does not match the accepted query contract."""


def validate_result(
    result: Mapping[str, Any],
    *,
    expected_columns: Sequence[str],
    row_limit: int,
    max_result_bytes: int,
) -> dict[str, Any]:
    """Return checks and a bounded preview-safe table, rejecting malformed rows."""

    columns = result.get("columns")
    rows = result.get("rows")
    if not isinstance(columns, list) or not columns or any(not isinstance(value, str) or not value for value in columns):
        raise ResultValidationError("Result columns are missing or invalid")
    if [value.casefold() for value in columns] != [value.casefold() for value in expected_columns]:
        raise ResultValidationError("Result columns differ from the accepted SELECT list")
    if len({value.casefold() for value in columns}) != len(columns):
        raise ResultValidationError("Result contains duplicate column names")
    if not isinstance(rows, list) or len(rows) > row_limit + 1:
        raise ResultValidationError("Result row count exceeds the requested bound")
    for index, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(columns):
            raise ResultValidationError(f"Result row {index} does not match the result schema")
    truncated = len(rows) > row_limit
    accepted_rows = rows[:row_limit]
    try:
        encoded = json.dumps(accepted_rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResultValidationError("Result contains a value that cannot be stored safely") from exc
    if len(encoded) > max_result_bytes:
        raise ResultValidationError("Result exceeds the source byte limit")
    null_cells = sum(value is None for row in accepted_rows for value in row)
    return {
        "columns": columns,
        "rows": accepted_rows,
        "row_count": len(accepted_rows),
        "result_bytes": len(encoded),
        "checks": {
            "status": "PASS",
            "schema": "PASS",
            "bounds": "PASS",
            "truncated": truncated,
            "null_cells": null_cells,
            "completeness": "LIMITED" if truncated else "FULL",
        },
    }
