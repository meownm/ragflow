"""Bounded recovery policy for generated Business Documents."""

MAX_AI_CALLS = 6
MAX_AI_SECONDS = 900

# Only representation failures may change a model response automatically.
REPAIRABLE_OUTPUT_CODES = frozenset(
    {
        "EMPTY_AI_RESPONSE",
        "INVALID_AI_JSON",
        "INVALID_DOCUMENT_DRAFT",
        "INVALID_DOCUMENT_DRAFT_V2",
        "INVALID_QUESTION_BATCH",
        "INVALID_REVIEW_PLAN",
        "INVALID_CHANGE_PLAN",
        "TEMPLATE_STRUCTURE_MISMATCH",
        "INVALID_SUBSECTION",
        "DUPLICATE_CHANGE_TARGET",
        "CHANGE_BLOCK_NOT_FOUND",
        "BLOCK_ID_CONFLICT",
    }
)

TRANSIENT_FAILURE_CODES = frozenset(
    {
        "DATASET_ACCESS_UNAVAILABLE",
        "DATASET_PREFLIGHT_UNAVAILABLE",
        "EVIDENCE_RETRIEVAL_FAILED",
        "EXPORT_STORAGE_WRITE_FAILED",
        "EVA_UNAVAILABLE",
    }
)


def is_transient_status(status: object) -> bool:
    return type(status) is int and (status in {408, 429} or 500 <= status <= 599)
