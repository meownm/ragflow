"""Value-only operational facts for recorded alignment attempts, never quality scores."""

import re
from datetime import datetime

LIMIT_CODES = frozenset(
    {
        "ALIGNMENT_CONTEXT_EXCEEDED",
        "ALIGNMENT_CALL_LIMIT",
        "ALIGNMENT_TOKEN_LIMIT",
        "ALIGNMENT_DEADLINE",
        "MODEL_OUTPUT_LIMIT_REACHED",
        "MODEL_INPUT_TRUNCATED",
        "MODEL_LIMITS_VIOLATED",
        "MODEL_PROVIDER_OVERLOADED",
        "MODEL_CONTEXT_REJECTED",
    }
)
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def error_code(value):
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return round(parsed.timestamp() * 1000) if parsed.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def observation(item):
    """Keep only bounded operational metadata; no titles, quotes or provider text."""
    accepted = _timestamp(item.get("created_at"))
    attempt = item.get("attempt")
    if not isinstance(attempt, str) or not 1 <= len(attempt) <= 32 or accepted is None:
        return None
    state = item.get("status") if item.get("status") in {"PROCESSING", "FINISHED", "INCOMPLETE"} else "INCOMPLETE"
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    budget = item.get("budget") if isinstance(item.get("budget"), dict) else {}
    code = item.get("reason_code") or result.get("reason_code")
    code = error_code(code)
    calls = _integer(budget.get("calls"))
    spent = _integer(budget.get("spent_tokens"))
    reserved = _integer(budget.get("reserved_tokens"))
    terminal = state != "PROCESSING"
    # Zero calls is known zero usage. A terminal row alone never attests usage.
    known = (
        terminal
        and reserved == 0
        and spent is not None
        and (
            calls == 0
            or audit.get("backend_completion_known") is True
            and audit.get("reasoning_token_accounting") in {"TOTAL_INCLUDES_REASONING", "SEPARATE_ACCOUNTED", "NO_REASONING"}
            and all(type(audit.get(key)) is int and audit[key] > 0 for key in ("prompt_tokens", "completion_tokens"))
        )
    )
    return {
        "id": attempt,
        "accepted_at_ms": accepted,
        "finished_at_ms": _timestamp(item.get("finished_at")),
        "state": state,
        "execution_status": result.get("execution_status") if result.get("execution_status") in {"COMPLETE", "INCOMPLETE"} else "UNKNOWN",
        "report_complete": result.get("report_status") == "COMPLETE" if terminal else None,
        "error_code": code,
        "duration_ms": _integer(budget.get("elapsed_ms")) if terminal and code != "EXECUTION_LEASE_EXPIRED" else None,
        "model_duration_ms": _integer(audit.get("provider_duration_ms")) if terminal else None,
        "calls": calls,
        "spent_tokens": spent,
        "reserved_tokens": reserved,
        "usage_known": bool(known),
    }
