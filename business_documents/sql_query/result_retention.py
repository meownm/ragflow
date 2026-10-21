"""Absolute lifetime of raw SQL results, independent of reads and retries."""

RESULT_RETENTION_MS = 30 * 24 * 60 * 60 * 1000


def result_expires_at(created_at: int | None, *, source_deadline: int | None = None) -> int:
    # A missing creation timestamp cannot grant an indefinite lifetime.
    deadline = (created_at if type(created_at) is int else 0) + RESULT_RETENTION_MS
    return min(deadline, source_deadline) if source_deadline is not None else deadline
