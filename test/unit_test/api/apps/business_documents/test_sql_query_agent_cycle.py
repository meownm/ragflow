from __future__ import annotations

import pytest

from business_documents.sql_query.agent_cycle import (
    AgentCycleConflict,
    AgentCycleValidationError,
    ProjectAgentState,
    next_agent_kind,
    parse_request_agent_command,
    require_next_agent,
)
from business_documents.sql_query.requirements_analysis import (
    AnalyzeRequirementsCommand,
    RequirementsAnalysisScenario,
    RequirementsAnalysisValidationError,
    RequirementsAnalystUnavailable,
    accept_requirements_proposal,
    normalize_requirements_proposal,
    parse_analyze_requirements_command,
)


def _proposal(*, blocking: bool = True):
    return {
        "schema_version": "1",
        "requirements": [
            {
                "kind": "output",
                "statement": "Вывести идентификатор заказа",
                "source_quote": "идентификатор заказа",
                "rationale": "Определяет колонку результата",
            },
            {
                "kind": "filter",
                "statement": "Ограничить данные выбранным периодом",
                "source_quote": "за период",
                "rationale": "Задаёт временной диапазон",
            },
        ],
        "questions": [
            {
                "question": "Какой часовой пояс использовать?",
                "reason": "Часовой пояс не указан",
                "options": ["UTC", "Europe/Moscow"],
                "allow_custom_answer": True,
                "blocking": blocking,
            }
        ],
    }


class FakeAnalyst:
    def __init__(self, proposal=None, error=None):
        self.proposal = proposal
        self.error = error
        self.calls = []

    async def propose(self, command):
        self.calls.append(command)
        if self.error:
            raise self.error
        return self.proposal


def test_requirements_contract_assigns_stable_ids_and_never_accepts_for_user():
    result = normalize_requirements_proposal(_proposal())

    assert [item["id"] for item in result["requirements"]] == ["REQ-OUT-001", "REQ-FLT-001"]
    assert {item["status"] for item in result["requirements"]} == {"PROPOSED"}
    assert result["questions"][0]["id"] == "Q-001"
    assert result["questions"][0]["status"] == "OPEN"


def test_requirements_contract_is_closed_and_rejects_duplicates():
    proposal = _proposal()
    proposal["unexpected"] = True
    with pytest.raises(RequirementsAnalysisValidationError, match="Unknown proposal fields"):
        normalize_requirements_proposal(proposal)


def test_blocking_requirements_questions_need_explicit_human_answers():
    proposal = normalize_requirements_proposal(_proposal())

    with pytest.raises(RequirementsAnalysisValidationError, match="Q-001 requires an answer"):
        accept_requirements_proposal(proposal)

    accepted = accept_requirements_proposal(proposal, {"Q-001": "Europe/Moscow"})

    assert {item["status"] for item in accepted["requirements"]} == {"ACCEPTED"}
    assert accepted["questions"][0]["status"] == "ANSWERED"
    assert accepted["questions"][0]["answer"] == "Europe/Moscow"

    proposal = _proposal()
    proposal["requirements"].append(dict(proposal["requirements"][0]))
    with pytest.raises(RequirementsAnalysisValidationError, match="must be unique"):
        normalize_requirements_proposal(proposal)


@pytest.mark.asyncio
async def test_requirements_scenario_returns_pending_proposal_and_manual_fallback():
    command = AnalyzeRequirementsCommand(tenant_id="tenant-1", locale="ru", source_request="Покажи заказы")
    analyst = FakeAnalyst(_proposal())

    result = await RequirementsAnalysisScenario(analyst).run(command)

    assert result["status"] == "NEEDS_CLARIFICATION"
    assert result["proposal"]["requirements"][0]["status"] == "PROPOSED"
    assert analyst.calls == [command]

    fallback = await RequirementsAnalysisScenario(FakeAnalyst(error=RequirementsAnalystUnavailable("offline"))).run(command)
    assert fallback["status"] == "FALLBACK"
    assert fallback["proposal"] is None


def test_parse_requirements_command_rejects_unknown_fields_and_bounds_locale():
    command = parse_analyze_requirements_command(
        {"schema_version": "1", "locale": "ru", "source_request": "  Покажи заказы  "},
        tenant_id="tenant-1",
    )
    assert command.source_request == "Покажи заказы"

    with pytest.raises(RequirementsAnalysisValidationError, match="Unknown request fields"):
        parse_analyze_requirements_command(
            {"schema_version": "1", "locale": "ru", "source_request": "x", "extra": True},
            tenant_id="tenant-1",
        )


def test_agent_policy_is_sequential_and_future_capabilities_fail_closed():
    state = ProjectAgentState(stage="REQUIREMENTS", operation_state="IDLE")
    assert next_agent_kind(state) == "REQUIREMENTS"
    require_next_agent(state, "REQUIREMENTS")
    with pytest.raises(AgentCycleConflict, match="must be REQUIREMENTS"):
        require_next_agent(state, "SCHEMA")

    assert next_agent_kind(ProjectAgentState("SCHEMA", "IDLE", requirements_artifact_id="requirements-1")) == "SCHEMA"
    assert (
        next_agent_kind(
            ProjectAgentState(
                "QUERY",
                "IDLE",
                requirements_artifact_id="requirements-1",
                schema_artifact_id="schema-1",
            )
        )
        == "QUERY"
    )
    assert next_agent_kind(ProjectAgentState("QUERY", "RUNNING")) is None

    with pytest.raises(AgentCycleValidationError, match="PYTHON agent is not enabled"):
        parse_request_agent_command(
            {
                "schema_version": "1",
                "expected_state_version": 1,
                "idempotency_key": "key-1",
                "kind": "PYTHON",
                "payload": {},
            }
        )


def test_parse_agent_command_is_closed():
    command = parse_request_agent_command(
        {
            "schema_version": "1",
            "expected_state_version": 3,
            "idempotency_key": "key-1",
            "kind": "requirements",
            "payload": {"locale": "ru"},
        }
    )
    assert command.kind == "REQUIREMENTS"
    assert command.expected_state_version == 3
    with pytest.raises(AgentCycleValidationError, match="Unknown request fields"):
        parse_request_agent_command(
            {
                "schema_version": "1",
                "expected_state_version": 3,
                "idempotency_key": "key-1",
                "kind": "REQUIREMENTS",
                "payload": {},
                "extra": True,
            }
        )
