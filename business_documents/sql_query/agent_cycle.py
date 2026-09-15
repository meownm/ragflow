"""Pure next-action policy and closed commands for the SQL agent cycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

AGENT_CYCLE_VERSION = "1"
SUPPORTED_AGENT_KINDS = ("REQUIREMENTS", "SCHEMA", "QUERY")
FUTURE_AGENT_KINDS = ("RESULT", "PYTHON")


class AgentCycleValidationError(ValueError):
    """A command violates the closed agent-cycle contract."""


class AgentCycleConflict(RuntimeError):
    """A valid command cannot be applied to the current project state."""


@dataclass(frozen=True, slots=True)
class ProjectAgentState:
    stage: str
    operation_state: str
    requirements_artifact_id: str | None = None
    schema_artifact_id: str | None = None
    query_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class RequestAgentCommand:
    expected_state_version: int
    idempotency_key: str
    kind: str
    payload: dict[str, Any]


def _record(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentCycleValidationError(f"{field} must be an object")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise AgentCycleValidationError(f"Unknown {field} fields: {', '.join(sorted(unknown))}")


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentCycleValidationError(f"{field} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise AgentCycleValidationError(f"{field} exceeds {maximum} characters")
    return result


def parse_request_agent_command(payload: Any) -> RequestAgentCommand:
    request = _record(payload, "request")
    _closed(request, {"schema_version", "expected_state_version", "idempotency_key", "kind", "payload"}, "request")
    if request.get("schema_version") != AGENT_CYCLE_VERSION:
        raise AgentCycleValidationError("schema_version is unsupported")
    version = request.get("expected_state_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise AgentCycleValidationError("expected_state_version must be a positive integer")
    kind = _text(request.get("kind"), "kind", 32).upper()
    if kind in FUTURE_AGENT_KINDS:
        raise AgentCycleValidationError(f"{kind} agent is not enabled by the current product capability")
    if kind not in SUPPORTED_AGENT_KINDS:
        raise AgentCycleValidationError("kind is unsupported")
    command_payload = _record(request.get("payload", {}), "payload")
    return RequestAgentCommand(
        expected_state_version=version,
        idempotency_key=_text(request.get("idempotency_key"), "idempotency_key", 128),
        kind=kind,
        payload=dict(command_payload),
    )


def next_agent_kind(state: ProjectAgentState) -> str | None:
    if state.operation_state != "IDLE":
        return None
    if state.requirements_artifact_id is None:
        return "REQUIREMENTS"
    if state.schema_artifact_id is None:
        return "SCHEMA"
    if state.query_artifact_id is None:
        return "QUERY"
    return None


def require_next_agent(state: ProjectAgentState, requested_kind: str) -> None:
    expected = next_agent_kind(state)
    if expected is None:
        raise AgentCycleConflict("The project has no agent action available in its current state")
    if requested_kind != expected:
        raise AgentCycleConflict(f"The next agent must be {expected}, not {requested_kind}")
