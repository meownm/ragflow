"""Bounded requirements-analysis capability for the SQL document constructor.

The model may propose requirements and clarification questions. Stable IDs and
acceptance remain deterministic application concerns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

REQUIREMENTS_ANALYSIS_VERSION = "1"
MAX_SOURCE_REQUEST_LENGTH = 20_000
MAX_REQUIREMENTS = 100
MAX_QUESTIONS = 12
MAX_OPTIONS = 4

_REQUIREMENT_KINDS = {"output", "join", "filter", "sort", "limit", "context"}
_REQUIREMENT_PREFIXES = {
    "output": "OUT",
    "join": "JOIN",
    "filter": "FLT",
    "sort": "SORT",
    "limit": "LIMIT",
    "context": "CTX",
}


class RequirementsAnalysisValidationError(ValueError):
    """The request or untrusted model proposal violates the closed contract."""


class RequirementsAnalystUnavailable(RuntimeError):
    """The optional tenant analyst could not produce a usable proposal."""


class RequirementsAnalyst(Protocol):
    async def propose(self, command: AnalyzeRequirementsCommand) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AnalyzeRequirementsCommand:
    tenant_id: str
    locale: str
    source_request: str


def _record(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RequirementsAnalysisValidationError(f"{field} must be an object")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise RequirementsAnalysisValidationError(f"Unknown {field} fields: {', '.join(sorted(unknown))}")


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequirementsAnalysisValidationError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise RequirementsAnalysisValidationError(f"{field} exceeds {maximum} characters")
    return result


def _optional_text(value: Any, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequirementsAnalysisValidationError(f"{field} must be a string")
    result = value.strip()
    if len(result) > maximum:
        raise RequirementsAnalysisValidationError(f"{field} exceeds {maximum} characters")
    return result


def _array(value: Any, field: str, maximum: int, *, minimum: int = 0) -> Sequence[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise RequirementsAnalysisValidationError(f"{field} must contain from {minimum} to {maximum} items")
    return value


def parse_analyze_requirements_command(payload: Any, *, tenant_id: str) -> AnalyzeRequirementsCommand:
    request = _record(payload, "request")
    _closed(request, {"schema_version", "locale", "source_request"}, "request")
    if request.get("schema_version") != REQUIREMENTS_ANALYSIS_VERSION:
        raise RequirementsAnalysisValidationError("schema_version is unsupported")
    locale = _text(request.get("locale"), "locale", 8)
    if locale not in {"ru", "en"}:
        raise RequirementsAnalysisValidationError("locale must be ru or en")
    return AnalyzeRequirementsCommand(
        tenant_id=_text(tenant_id, "tenant_id", 500),
        locale=locale,
        source_request=_text(request.get("source_request"), "source_request", MAX_SOURCE_REQUEST_LENGTH),
    )


def analyst_context(command: AnalyzeRequirementsCommand) -> dict[str, Any]:
    return {
        "schema_version": REQUIREMENTS_ANALYSIS_VERSION,
        "locale": command.locale,
        "source_request": command.source_request,
    }


def normalize_requirements_proposal(value: Any) -> dict[str, Any]:
    proposal = _record(value, "proposal")
    _closed(proposal, {"schema_version", "requirements", "questions"}, "proposal")
    if proposal.get("schema_version") != REQUIREMENTS_ANALYSIS_VERSION:
        raise RequirementsAnalysisValidationError("proposal.schema_version is unsupported")

    requirements: list[dict[str, Any]] = []
    counters = {kind: 0 for kind in _REQUIREMENT_KINDS}
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(_array(proposal.get("requirements"), "proposal.requirements", MAX_REQUIREMENTS, minimum=1)):
        field = f"proposal.requirements[{index}]"
        item = _record(raw, field)
        _closed(item, {"kind", "statement", "source_quote", "rationale"}, field)
        kind = _text(item.get("kind"), f"{field}.kind", 32)
        if kind not in _REQUIREMENT_KINDS:
            raise RequirementsAnalysisValidationError(f"{field}.kind is unsupported")
        statement = _text(item.get("statement"), f"{field}.statement", 2_000)
        identity = (kind, statement.casefold())
        if identity in seen:
            raise RequirementsAnalysisValidationError("proposal.requirements must be unique")
        seen.add(identity)
        counters[kind] += 1
        requirements.append(
            {
                "id": f"REQ-{_REQUIREMENT_PREFIXES[kind]}-{counters[kind]:03d}",
                "kind": kind,
                "statement": statement,
                "source_quote": _optional_text(item.get("source_quote"), f"{field}.source_quote", 1_000),
                "rationale": _optional_text(item.get("rationale"), f"{field}.rationale", 2_000),
                "status": "PROPOSED",
            }
        )

    questions: list[dict[str, Any]] = []
    for index, raw in enumerate(_array(proposal.get("questions"), "proposal.questions", MAX_QUESTIONS)):
        field = f"proposal.questions[{index}]"
        item = _record(raw, field)
        _closed(item, {"question", "reason", "options", "allow_custom_answer", "blocking"}, field)
        options = [_text(option, f"{field}.options[{option_index}]", 500) for option_index, option in enumerate(_array(item.get("options"), f"{field}.options", MAX_OPTIONS, minimum=2))]
        if len({option.casefold() for option in options}) != len(options):
            raise RequirementsAnalysisValidationError(f"{field}.options must be unique")
        allow_custom = item.get("allow_custom_answer")
        blocking = item.get("blocking")
        if not isinstance(allow_custom, bool) or not isinstance(blocking, bool):
            raise RequirementsAnalysisValidationError(f"{field} flags must be booleans")
        questions.append(
            {
                "id": f"Q-{index + 1:03d}",
                "question": _text(item.get("question"), f"{field}.question", 1_000),
                "reason": _text(item.get("reason"), f"{field}.reason", 1_000),
                "options": options,
                "allow_custom_answer": allow_custom,
                "blocking": blocking,
                "status": "OPEN",
            }
        )
    return {"requirements": requirements, "questions": questions}


def accept_requirements_proposal(value: Any, answers: Any = None) -> dict[str, Any]:
    """Apply explicit human answers and mark a normalized proposal accepted."""

    proposal = deepcopy(dict(_record(value, "proposal")))
    _closed(proposal, {"requirements", "questions"}, "proposal")
    requirements = _array(proposal.get("requirements"), "proposal.requirements", MAX_REQUIREMENTS, minimum=1)
    questions = _array(proposal.get("questions"), "proposal.questions", MAX_QUESTIONS)
    answer_map = {} if answers is None else dict(_record(answers, "answers"))
    known_question_ids = {_text(_record(question, f"proposal.questions[{index}]").get("id"), f"proposal.questions[{index}].id", 32) for index, question in enumerate(questions)}
    unknown_answers = set(answer_map) - known_question_ids
    if unknown_answers:
        raise RequirementsAnalysisValidationError(f"Unknown answer IDs: {', '.join(sorted(unknown_answers))}")
    for index, raw in enumerate(requirements):
        item = _record(raw, f"proposal.requirements[{index}]")
        if item.get("status") != "PROPOSED":
            raise RequirementsAnalysisValidationError("Only proposed requirements can be accepted")
        item["status"] = "ACCEPTED"
    for index, raw in enumerate(questions):
        item = _record(raw, f"proposal.questions[{index}]")
        question_id = str(item["id"])
        answer = answer_map.get(question_id)
        if item.get("blocking") is True and answer is None:
            raise RequirementsAnalysisValidationError(f"Blocking question {question_id} requires an answer")
        if answer is not None:
            item["answer"] = _text(answer, f"answers.{question_id}", 2_000)
            item["status"] = "ANSWERED"
        else:
            item["answer"] = None
            item["status"] = "SKIPPED"
    return {"requirements": requirements, "questions": questions}


class RequirementsAnalysisScenario:
    def __init__(self, analyst: RequirementsAnalyst):
        self._analyst = analyst

    async def run(self, command: AnalyzeRequirementsCommand) -> dict[str, Any]:
        try:
            raw = await self._analyst.propose(command)
            proposal = normalize_requirements_proposal(raw)
        except (RequirementsAnalystUnavailable, RequirementsAnalysisValidationError) as exc:
            return {
                "schema_version": REQUIREMENTS_ANALYSIS_VERSION,
                "status": "FALLBACK",
                "proposal": None,
                "warning": "LLM-анализ требований недоступен; заполните требования вручную.",
                "diagnostic": type(exc).__name__,
            }
        return {
            "schema_version": REQUIREMENTS_ANALYSIS_VERSION,
            "status": "NEEDS_CLARIFICATION" if any(question["blocking"] for question in proposal["questions"]) else "PROPOSED",
            "proposal": proposal,
            "warning": None,
            "diagnostic": None,
        }
