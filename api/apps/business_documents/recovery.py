"""Lease-guarded checkpoints and provider failure handling for document jobs."""

from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from typing import Any, Callable

import httpx

from business_documents.application.errors import BusinessDocumentError, ValidationError
from business_documents.domain.job_recovery import MAX_AI_CALLS, MAX_AI_SECONDS, TRANSIENT_FAILURE_CODES, is_transient_status


def is_transient_failure(error: Exception) -> bool:
    if isinstance(error, BusinessDocumentError):
        return error.code in TRANSIENT_FAILURE_CODES
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if getattr(error, "code", None) in {"insufficient_quota", "billing_hard_limit_reached"}:
        return False
    if status is not None:
        return is_transient_status(status)
    return isinstance(error, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)) or type(error).__name__ in {
        "APITimeoutError",
        "APIConnectionError",
    }


def retry_delay_ms(error: Exception, default: int) -> int:
    headers = getattr(getattr(error, "response", None), "headers", {})
    value = headers.get("retry-after")
    if value is None:
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return default
    if not 0 <= seconds <= 86400:
        return default
    return max(default, int(seconds * 1000))


class AIRecovery:
    """One budget and one format repair per stage, retained across queue retries.

    The worker supplies a checkpoint writer that checks ownership and document
    freshness. Direct adapter consumers use the same bounds in memory.
    """

    def __init__(self, state: dict[str, Any] | None = None, *, save: Callable[[dict[str, Any]], None] | None = None, clock: Callable[[], float] = time.time):
        self._clock, self._save = clock, save
        self.state = deepcopy(state) if state is not None else {"started_at": clock(), "calls": 0, "feedback": {}, "audits": [], "candidate": None}

    def checkpoint(self) -> None:
        if self._save is not None:
            self._save(deepcopy(self.state))

    def remaining_seconds(self) -> float:
        remaining = MAX_AI_SECONDS - (self._clock() - self.state["started_at"])
        if remaining <= 0:
            raise ValidationError("AI_TIME_BUDGET_EXCEEDED", "Превышено время автоматического восстановления документа")
        return remaining

    def before_call(self) -> float:
        remaining = self.remaining_seconds()
        if self.state["calls"] >= MAX_AI_CALLS:
            raise ValidationError("AI_CALL_BUDGET_EXCEEDED", "Исчерпан лимит вызовов модели для этого задания")
        self.state["calls"] += 1
        # Reserve before I/O: a terminated process still consumes this call.
        self.checkpoint()
        return remaining

    def after_call(self, stage: str, audit: dict[str, Any] | None) -> None:
        if audit:
            self.state["audits"].append({"stage": stage, "audit": audit})
        self.checkpoint()
        self.remaining_seconds()

    def repair(self, stage: str, raw: object, error: BusinessDocumentError) -> bool:
        if stage in self.state["feedback"]:
            return False
        details = error.details or {}
        self.state["feedback"][stage] = {
            "previous_response": raw,
            "error": {
                "code": error.code,
                "message": error.message[:800],
                "details": {key: details[key] for key in ("path", "validator", "block_type", "allowed_block_types", "section_id", "section_ids") if key in details},
            },
        }
        self.checkpoint()
        return True

    def input_payload(self, stage: str, payload: dict[str, Any]) -> dict[str, Any]:
        feedback = self.state["feedback"].get(stage)
        return {**payload, "format_repair": deepcopy(feedback)} if feedback else payload

    def audit(self) -> dict[str, Any] | None:
        generation = [step["audit"] for step in self.state["audits"] if step["stage"] == "generation"]
        verification = [step["audit"] for step in self.state["audits"] if step["stage"] == "verification"]
        if not generation:
            return None
        return {
            **generation[0],
            **({"recovery": generation[1:]} if len(generation) > 1 else {}),
            **({"verification": verification[0] if len(verification) == 1 else verification} if verification else {}),
        }
