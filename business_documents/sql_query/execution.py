"""Classify proven SQL outcomes separately from delivery and result storage."""


def failed_outcome(phase: str | None, code: str) -> tuple[str, str]:
    if code == "SQL_CANCELED":
        return "CANCELLED", "CANCELLED"
    if code == "SQL_RESULT_EXPIRED":
        return "EXPIRED", phase or "FAILED"
    if phase == "SUCCEEDED":
        return "SUCCEEDED", "SUCCEEDED"
    if phase == "DISPATCHING" and code not in {"SQL_TIMEOUT", "SQL_EXECUTION_FAILED"}:
        return "UNKNOWN", "UNKNOWN"
    return "FAILED", "FAILED"
