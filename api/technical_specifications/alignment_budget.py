"""Limits shared by production alignment and its golden/live runners."""

import json
import math
import time
from dataclasses import dataclass, field

from .alignment import MAX_REPORT_BYTES, MAX_REQUIREMENTS, fingerprint
from .domain import fail


@dataclass
class AlignmentBudget:
    seconds: float = 300
    calls: int = 1
    spent_calls: int = 0
    started: float = field(default_factory=time.monotonic)
    token_limit: int = 1_000_000
    spent_tokens: int = 0
    reserved_tokens: int = 0
    _reservations: dict[int, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        if type(self.started) not in (int, float) or not math.isfinite(self.started):
            fail("INVALID_ALIGNMENT_BUDGET", "Некорректное начало бюджета проверки")
        if (
            type(self.seconds) not in (int, float)
            or not math.isfinite(self.seconds)
            or self.seconds <= 0
            or type(self.calls) is not int
            or not 1 <= self.calls <= 130
            or type(self.spent_calls) is not int
            or not 0 <= self.spent_calls <= self.calls
        ):
            fail("INVALID_ALIGNMENT_BUDGET", "Некорректный бюджет проверки")
        if (
            any(type(value) is not int or value < 0 for value in (self.token_limit, self.spent_tokens, self.reserved_tokens))
            or self.token_limit == 0
            or self.spent_tokens + self.reserved_tokens > self.token_limit
        ):
            fail("INVALID_ALIGNMENT_BUDGET", "Некорректный токеновый бюджет проверки")

    def remaining(self):
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            fail("ALIGNMENT_DEADLINE", "Исчерпан общий срок проверки", 504)
        return remaining

    def snapshot(self):
        return {
            "calls": self.spent_calls,
            "call_limit": self.calls,
            "spent_tokens": self.spent_tokens,
            "reserved_tokens": self.reserved_tokens,
            "token_limit": self.token_limit,
            "elapsed_ms": max(0, round((time.monotonic() - self.started) * 1000)),
            "time_limit_ms": round(self.seconds * 1000),
        }

    def claim(self, tokens=0):
        self.remaining()
        if self.spent_calls >= self.calls:
            fail("ALIGNMENT_CALL_LIMIT", "Исчерпано число вызовов; бюджет не сбрасывается", 429)
        if type(tokens) is not int or tokens < 0 or self.spent_tokens + self.reserved_tokens + tokens > self.token_limit:
            fail("ALIGNMENT_TOKEN_LIMIT", "Исчерпан общий токеновый бюджет проверки", 429)
        self.spent_calls += 1
        self.reserved_tokens += tokens
        self._reservations[self.spent_calls] = tokens
        return self.spent_calls

    def settle(self, prompt, output, *, claim_id=None):
        # Unknown usage retains the reservation; it is never settled as zero.
        if any(type(value) is not int or value <= 0 for value in (prompt, output)):
            return
        claim_id = self.spent_calls if claim_id is None else claim_id
        if claim_id not in self._reservations:
            fail("ALIGNMENT_USAGE_ALREADY_RECORDED", "Списание бюджета уже учтено или не связано с вызовом", 502)
        self.spent_tokens += prompt + output
        self.reserved_tokens -= self._reservations.pop(claim_id)
        if self.spent_tokens + self.reserved_tokens > self.token_limit:
            fail("ALIGNMENT_TOKEN_LIMIT", "Провайдер превысил общий токеновый бюджет", 502)


def report_bounds(payload, count_tokens):
    """Minimum admission sample includes a reason and citation for EVERY failure.

    The schema ceiling also includes all permitted references and additional
    rules. A context-sized allocation below that ceiling remains fallible: an
    overlong response must fail, never be compacted into a passing subset.
    Counts are explicitly estimates until provider tokenization is attested.
    """
    ids = [item["id"] for item in payload["basis"]["inventory"]]
    reference = {"section": "9", "quote": "q" * 512}
    row = {"id": "R001", "coverage": "UNDETERMINED", "consistency": "UNDETERMINED", "references": [reference], "reason": "r" * 512}
    sample = {
        "candidate_sha256": payload["candidate_sha256"],
        "basis_sha256": payload["basis_sha256"],
        "requirements": [{**row, "id": item} for item in ids],
        "additional_rules": [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": [reference], "reason": "r" * 512}],
        "joint": {"checked_ids": ids, "consistency": "UNDETERMINED", "reason": "r" * 512},
    }
    serialized = json.dumps(sample, ensure_ascii=False)
    counted = count_tokens(serialized)
    if type(counted) is not int or counted <= 0:
        fail("ALIGNMENT_TOKENIZER_UNAVAILABLE", "Не удалось оценить полный отчёт", 503)
    sample["requirements"] = [{**row, "id": item, "references": [reference] * 9} for item in ids]
    sample["additional_rules"] = [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": [reference] * 9, "reason": "r" * 512}] * MAX_REQUIREMENTS
    ceiling = min(MAX_REPORT_BYTES, len(json.dumps(sample, ensure_ascii=False).encode()))
    return max(1024, len(serialized.encode()), math.ceil(counted * 1.25) + 64), ceiling


def plan_request(system, payload, model):
    """Reserve bounded rows for both passing and failing results, not a tiny PASS."""
    from api.source_workbench.processing import ModelBudget

    budget = ModelBudget.from_context(model.context_tokens, model.count_tokens, output_limit=model.output_tokens)
    required_input = budget.estimate(system, payload)
    required_output, schema_max_bytes = report_bounds(payload, model.count_tokens)
    available = budget.output_tokens - required_input
    if model.output_tokens is not None:
        available = min(available, model.output_tokens)
    if required_input > budget.input_tokens or available < required_output:
        fail(
            "ALIGNMENT_CONTEXT_EXCEEDED",
            "Не помещаются полные основания и отчёт; усечение запрещено",
            413,
            {"required_input_tokens": required_input, "required_output_tokens": required_output, "available_output_tokens": max(0, available)},
        )
    output_limit = min(available, max(required_output, schema_max_bytes + 64))
    return {
        "input_estimate": required_input,
        "output_limit": output_limit,
        "required_output_tokens": required_output,
        "schema_max_report_bytes": schema_max_bytes,
        "schema_output_token_ceiling": schema_max_bytes + 64,
        "context_limit": model.context_tokens,
        "request_sha256": fingerprint([system, payload]),
        "token_count_kind": "CONSERVATIVE_ESTIMATE",
    }
