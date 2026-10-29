"""Persisted answers share SQL result access/retention, not SQL generation."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_sql_assistant_service import ACTOR, TENANT, command, project, research, source  # noqa: F401
from api.apps.sql_assistant.answer import plan_answer
from api.apps.sql_assistant import answer as answer_adapter
from api.apps.sql_assistant.service import SqlAssistantService
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryRun, SqlAssistantCommand, SqlAssistantSession
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError


@pytest.fixture
def dialogue(research, monkeypatch):  # noqa: F811 -- reused SQLite fixture
    service, session, run, tables, calls = research
    command(service, session, "select_result", {"run_id": run.id}, key="select-result")
    requests = []

    def model(tenant_id, context):
        assert not session._meta.database.in_transaction()
        requests.append(deepcopy(context))
        row = context["rows"][0]
        column = context["columns"][0]["id"]
        return {"kind": "ANSWER", "text": f"Значение: {{{{{row['index']}:{column}}}}}."}

    monkeypatch.setattr(answer_adapter, "plan_answer", model)
    return service, session, run, requests


def test_answer_is_persistent_and_replay_does_not_run_model_or_generate_sql(dialogue):
    service, session, run, requests = dialogue
    before = service.get(session.id)["project"]
    count = BusinessDocumentSqlQueryArtifact.select().count()
    response = command(service, session, "answer_result", {"run_id": run.id, "text": "Какое первое значение?"}, key="answer")
    message = response["session"]["messages"][-1]
    assert message["answer"]["text"] == "Значение: 0."
    assert message["parent_result_id"] == run.id
    assert service.get(session.id)["messages"][-1] == message
    command(service, session, "answer_result", {"run_id": run.id, "text": "Какое первое значение?"}, key="answer", revision=2)
    assert len(requests) == 1
    assert SqlAssistantSession.get_by_id(session.id).revision == response["session"]["revision"]
    assert "answer" not in SqlAssistantSession.get_by_id(session.id).state["messages"][-1]
    assert SqlAssistantCommand.get(SqlAssistantCommand.client_request_id == "answer").effect == response["effect"]
    assert service.get(session.id)["project"] == before
    assert BusinessDocumentSqlQueryArtifact.select().count() == count
    assert BusinessDocumentJob.select().count() == 0


def test_reader_can_ask_but_query_change_still_requires_edit(dialogue):
    _, session, run, requests = dialogue
    service = SqlAssistantService(TENANT, ACTOR, access_role="READER")
    response = command(service, session, "answer_result", {"run_id": run.id, "text": "Первое значение"})
    assert response["session"]["messages"][-1]["answer"]["status"] == "READY"
    with pytest.raises(PermissionDeniedError):
        command(service, session, "follow_up", {"run_id": run.id, "text": "По месяцам"}, key="change")
    assert len(requests) == 1


def test_expired_answer_is_not_recovered_from_log_or_replay(dialogue):
    service, session, run, requests = dialogue
    payload = {"run_id": run.id, "text": "Первое значение"}
    command(service, session, "answer_result", payload, key="answer")
    BusinessDocumentSqlQueryRun.update(status="EXPIRED", rows=[]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    message = service.get(session.id)["messages"][-1]
    assert message["answer"]["status"] == "UNAVAILABLE"
    assert "citations" not in message["answer"]
    with pytest.raises(BusinessDocumentError) as failure:
        command(service, session, "answer_result", payload, key="answer", revision=2)
    assert failure.value.code == "SQL_RESULT_EXPIRED"
    assert len(requests) == 1


@pytest.mark.parametrize("change", ["selection", "revision", "rows", "revoke"])
def test_changes_during_inference_prevent_publication(dialogue, monkeypatch, change):
    service, session, run, requests = dialogue
    model = answer_adapter.plan_answer

    def delayed(tenant, context):
        plan = model(tenant, context)
        if change == "rows":
            BusinessDocumentSqlQueryRun.update(rows=[[999 for _ in run.columns] for _ in run.rows]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
        elif change == "revoke":
            monkeypatch.setattr(service.runtime, "result", lambda *_args: (_ for _ in ()).throw(PermissionDeniedError("Revoked")))
        else:
            state = deepcopy(SqlAssistantSession.get_by_id(session.id).state)
            if change == "selection":
                state["selected_result_id"] = "other"
            SqlAssistantSession.update(state=state, revision=3 if change == "revision" else 2).where(SqlAssistantSession.id == session.id).execute()
        return plan

    monkeypatch.setattr(answer_adapter, "plan_answer", delayed)
    with pytest.raises(BusinessDocumentError):
        command(service, session, "answer_result", {"run_id": run.id, "text": "Первое значение"}, key="race")
    assert len(SqlAssistantSession.get_by_id(session.id).state["messages"]) == 1
    assert SqlAssistantCommand.select().where(SqlAssistantCommand.client_request_id == "race").count() == 0
    assert len(requests) == 1


@pytest.mark.parametrize("tenant,actor", [("foreign", ACTOR), (TENANT, "foreign")])
def test_foreign_actor_never_calls_answer_model(dialogue, tenant, actor):
    _, session, run, requests = dialogue
    foreign = SqlAssistantService(tenant, actor)
    with pytest.raises(BusinessDocumentError):
        foreign.command(session.id, {"type": "answer_result", "payload": {"run_id": run.id, "text": "Число?"}, "client_request_id": "foreign", "expected_revision": 2, "expected_project_version": 1})
    assert requests == []


def test_selected_result_must_match_before_model_call(dialogue):
    service, session, _, requests = dialogue
    with pytest.raises(ConflictError):
        command(service, session, "answer_result", {"run_id": "other", "text": "Число?"})
    assert requests == []


def test_adapter_failure_is_safe_and_does_not_turn_into_a_query(dialogue):
    _, _, _, requests = dialogue

    class BrokenModel:
        def generate(self, *_args, **_kwargs):
            raise TimeoutError("provider secret diagnostic")

    with pytest.raises(BusinessDocumentError) as failure:
        plan_answer(TENANT, requests, llm=BrokenModel())
    assert failure.value.code == "SQL_RESULT_ANSWER_UNAVAILABLE"
    assert "secret" not in failure.value.message


def test_explicit_query_change_uses_revision_job_and_keeps_selected_result(dialogue):
    service, session, run, requests = dialogue
    response = command(service, session, "follow_up", {"run_id": run.id, "text": "Добавь разрез по месяцам"}, key="change")
    assert response["session"]["selected_result_id"] == run.id
    assert BusinessDocumentJob.get().job_type == "SQL_AGENT_REVISION"
    assert requests == []


def test_revoked_answer_is_not_disclosed_from_history(dialogue, monkeypatch):
    service, session, run, _ = dialogue
    command(service, session, "answer_result", {"run_id": run.id, "text": "Значение?"}, key="answer")

    def revoked(*_args):
        raise PermissionDeniedError("Revoked")

    monkeypatch.setattr(service.runtime, "result", revoked)
    answer = service.get(session.id)["messages"][-1]["answer"]
    assert answer["status"] == "UNAVAILABLE"
    assert "citations" not in answer


def test_history_and_replay_never_select_an_old_result_implicitly(dialogue):
    service, session, run, requests = dialogue
    payload = {"run_id": run.id, "text": "Первое значение"}
    command(service, session, "answer_result", payload, key="answer", revision=2)
    values = {key: value for key, value in run.__data__.items() if key not in {"id", "create_time", "create_date", "update_time", "update_date"}}
    BusinessDocumentSqlQueryRun.create(id="other-run", **values)
    command(service, session, "select_result", {"run_id": "other-run"}, key="select-other")
    replay = command(service, session, "answer_result", payload, key="answer", revision=2)
    assert replay["session"]["selected_result_id"] == "other-run"
    assert replay["session"]["messages"][-1]["answer"]["status"] == "OTHER_RESULT"
    assert "citations" not in replay["session"]["messages"][-1]["answer"]
    assert len(requests) == 1


def test_provider_failure_keeps_session_sql_and_command_log_unchanged(dialogue, monkeypatch):
    service, session, run, _ = dialogue
    before = deepcopy(SqlAssistantSession.get_by_id(session.id).state)

    def unavailable(*_args):
        raise BusinessDocumentError("SQL_RESULT_ANSWER_UNAVAILABLE", "Повторите вопрос", 503)

    monkeypatch.setattr(answer_adapter, "plan_answer", unavailable)
    with pytest.raises(BusinessDocumentError):
        command(service, session, "answer_result", {"run_id": run.id, "text": "Значение?"}, key="failed")
    assert SqlAssistantSession.get_by_id(session.id).state == before
    assert SqlAssistantCommand.select().where(SqlAssistantCommand.client_request_id == "failed").count() == 0
    assert BusinessDocumentJob.select().count() == 0
