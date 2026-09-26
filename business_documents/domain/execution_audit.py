"""Validate worker attribution and pinned retrieval evidence as plain values."""

from collections.abc import Mapping
import math
from typing import Any

from business_documents.domain.errors import RuleViolation


def validate_audit_envelope(value: object, *, evidence_required: bool) -> Mapping[str, Any]:
    if value is None:
        if evidence_required:
            raise RuleViolation("EVIDENCE_AUDIT_REQUIRED", "AI jobs with datasets require a pinned evidence audit")
        return {}
    if not isinstance(value, dict) or not value or not set(value) <= {"retrieval", "ai"}:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker execution audit is invalid")
    if evidence_required and "retrieval" not in value:
        raise RuleViolation("EVIDENCE_AUDIT_REQUIRED", "AI jobs with datasets require a pinned evidence audit")
    return value


def validate_retrieval_audit(retrieval: object) -> dict[str, Any]:
    expected = {"attempt", "retrieved_at", "dataset_ids", "query_hash", "evidence_hash", "source_refs", "chunk_count", "total_chars"}
    if not isinstance(retrieval, dict) or set(retrieval) != expected:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker retrieval audit fields are invalid")
    if (
        not isinstance(retrieval["attempt"], int)
        or retrieval["attempt"] < 1
        or not isinstance(retrieval["retrieved_at"], str)
        or not isinstance(retrieval["dataset_ids"], list)
        or len(retrieval["dataset_ids"]) > 20
        or not all(isinstance(item, str) for item in retrieval["dataset_ids"])
        or not isinstance(retrieval["source_refs"], list)
        or not all(isinstance(item, str) for item in retrieval["source_refs"])
        or not isinstance(retrieval["chunk_count"], int)
        or retrieval["chunk_count"] != len(retrieval["source_refs"])
        or not isinstance(retrieval["total_chars"], int)
        or retrieval["total_chars"] < 0
        or not all(isinstance(retrieval[field], str) and len(retrieval[field]) == 71 and retrieval[field].startswith("sha256:") for field in ("query_hash", "evidence_hash"))
    ):
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker retrieval audit values are invalid")
    return {key: retrieval[key] for key in sorted(expected)}


def verify_pinned_evidence(retrieval: Mapping[str, Any], snapshot: Mapping[str, Any] | None) -> None:
    if snapshot is None:
        raise RuleViolation("EVIDENCE_SNAPSHOT_REQUIRED", "Retrieval audit has no pinned evidence snapshot")
    chunks = snapshot["snapshot"].get("chunks", []) if isinstance(snapshot["snapshot"], dict) else []
    source_refs = [chunk.get("source_ref") for chunk in chunks if isinstance(chunk, dict)]
    if (
        retrieval["evidence_hash"] != snapshot["evidence_hash"]
        or retrieval["dataset_ids"] != snapshot["dataset_ids"]
        or retrieval["source_refs"] != source_refs
        or retrieval["chunk_count"] != len(chunks)
    ):
        raise RuleViolation("EVIDENCE_AUDIT_MISMATCH", "Retrieval audit does not match the pinned evidence snapshot")


def validate_ai_audit(ai: object) -> dict[str, Any]:
    expected = {"provider", "model", "model_type", "parameters", "duration_ms", "token_usage"}
    if not isinstance(ai, dict) or set(ai) != expected:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI audit fields are invalid")
    if not all(isinstance(ai[key], str) and ai[key] for key in ("provider", "model", "model_type")):
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI identity is invalid")
    if len(ai["provider"]) > 128 or len(ai["model"]) > 256 or len(ai["model_type"]) > 64:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI identity is invalid")
    parameters = ai["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {"temperature", "top_p", "max_completion_tokens"}:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI parameters are invalid")
    if parameters["temperature"] != 0 or parameters["top_p"] != 0.1 or parameters["max_completion_tokens"] not in {4096, 8192}:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI parameters are invalid")
    duration_ms = ai["duration_ms"]
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or not math.isfinite(duration_ms) or duration_ms < 0:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI duration is invalid")
    usage = ai["token_usage"]
    usage_keys = {"prompt_tokens", "completion_tokens", "total_tokens"}
    if not isinstance(usage, dict) or set(usage) != usage_keys or any(isinstance(usage[key], bool) or not isinstance(usage[key], int) or usage[key] < 0 for key in usage_keys):
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI token usage is invalid")
    if usage["prompt_tokens"] + usage["completion_tokens"] not in {0, usage["total_tokens"]}:
        raise RuleViolation("INVALID_EXECUTION_AUDIT", "Worker AI token usage is inconsistent")
    return {
        "duration_ms": float(duration_ms),
        "model": ai["model"],
        "model_type": ai["model_type"],
        "parameters": {key: parameters[key] for key in sorted(parameters)},
        "provider": ai["provider"],
        "token_usage": {key: usage[key] for key in sorted(usage)},
    }
