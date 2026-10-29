"""Protocol controls for the real adapter, not live-model acceptance."""

import importlib.util
from pathlib import Path

import pytest

from business_documents.application.errors import BusinessDocumentError

# The parent api.apps package bootstraps Quart and real service settings.
# These protocol controls load the actual adapter without starting an application.
_spec = importlib.util.spec_from_file_location("sql_conversation_adapter_control", Path(__file__).resolve().parents[5] / "api/apps/sql_assistant/answer.py")
_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter)
plan_conversation = _adapter.plan_conversation


class Responses:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, tenant, system, payload, *, timeout_seconds):
        self.calls.append({"tenant": tenant, "system": system, "payload": payload, "timeout_seconds": timeout_seconds})
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def test_router_preserves_original_question_and_bounds_history():
    model = Responses({"intent": "CHANGE"})
    history = [{"role": "user", "text": str(index) + "x" * 4000} for index in range(30)]
    question = "Убери только последний фильтр."
    assert plan_conversation("tenant", question, None, history, llm=model) == {"intent": "CHANGE"}
    payload = model.calls[0]["payload"]
    assert payload["question"] == question
    assert len(payload["discussion"]) == 12
    assert all(len(item["text"]) <= 2000 for item in payload["discussion"])
    assert payload["job_input"]["task_type"] == "SQL_CONVERSATION"
    assert 0 < model.calls[0]["timeout_seconds"] <= 60


def test_router_repairs_only_once_and_never_accepts_free_sql():
    model = Responses({"intent": "CHANGE", "sql": "DROP TABLE orders"}, {"intent": "CHANGE"})
    assert plan_conversation("tenant", "Оставь North", None, [], llm=model) == {"intent": "CHANGE"}
    assert len(model.calls) == 2
    assert model.calls[1]["payload"]["format_correction"]["reason"]
    assert model.calls[1]["timeout_seconds"] <= model.calls[0]["timeout_seconds"]


def test_two_invalid_responses_fail_closed():
    model = Responses("not JSON", {"intent": "SQL", "sql": "SELECT 1"})
    with pytest.raises(BusinessDocumentError) as error:
        plan_conversation("tenant", "Вопрос", None, [], llm=model)
    assert error.value.code == "SQL_CONVERSATION_INVALID"
    assert len(model.calls) == 2


def test_provider_failure_does_not_start_a_second_generation():
    model = Responses(RuntimeError("private provider diagnostics"))
    with pytest.raises(BusinessDocumentError) as error:
        plan_conversation("tenant", "Вопрос", None, [], llm=model)
    assert error.value.code == "SQL_CONVERSATION_UNAVAILABLE"
    assert "private provider diagnostics" not in error.value.message
    assert len(model.calls) == 1
