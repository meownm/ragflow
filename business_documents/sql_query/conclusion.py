"""Bounded, evidence-linked conclusion checks for a SQL result."""

from __future__ import annotations

import re
from typing import Any


class ConclusionValidationError(ValueError):
    """The proposed text cannot be tied to the result cells it cites."""


_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?%?(?![\w])")


def validate_conclusion(
    text: Any,
    citations: Any,
    columns: list[str],
    rows: list[list[Any]],
    row_count: int,
) -> dict[str, Any]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ConclusionValidationError("Conclusion must contain 1 to 2000 characters")
    if not isinstance(citations, list) or len(citations) > 20 or (rows and not citations):
        raise ConclusionValidationError("Conclusion must cite result cells")
    normalized: list[dict[str, Any]] = []
    permitted_numbers = set(_NUMBER.findall(str(row_count)))
    for item in citations:
        if not isinstance(item, dict) or set(item) != {"row_index", "column"}:
            raise ConclusionValidationError("Citation must identify one result cell")
        index, column = item["row_index"], item["column"]
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(rows) or column not in columns:
            raise ConclusionValidationError("Citation points outside the bounded result")
        normalized.append({"row_index": index, "column": column})
        permitted_numbers.update(_NUMBER.findall(str(rows[index][columns.index(column)])))
    if not set(_NUMBER.findall(text)).issubset(permitted_numbers):
        raise ConclusionValidationError("Numeric claims must match cited result cells or the row count")
    return {"text": text.strip(), "citations": normalized}
