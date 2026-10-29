"""Single-command conversation persistence over real SQLite artifacts."""

import json
from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents import test_sql_assistant_service as native
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentJob, SqlAssistantSession, User

project = native.project
source = native.source
research = native.research


@pytest.fixture
def actor(research):
    database = SqlAssistantSession._meta.database
    with database.bind_ctx((User,), bind_refs=False, bind_backrefs=False):
        database.create_tables((User,))
        user = User.create(id=native.ACTOR, nickname="SQL tester", email="sql-chat@fixture.invalid", business_document_role="AUTHOR_CREATOR")
        yield user
        database.drop_tables((User,))


def requirements(*, blocking):
    return {
        "schema_version": "1",
        "status": "PROPOSED",
        "warning": None,
        "diagnostic": None,
        "proposal": {
            "requirements": [{"id": "REQ-OUT-001", "kind": "output", "statement": "Show orders", "source_quote": "Show orders", "rationale": "Requested result", "status": "PROPOSED"}],
            "questions": [{"id": "ASK-YEAR", "question": "Какой год?", "blocking": True, "options": ["2025", "2026"]}] if blocking else [],
        },
    }


def finish_preparation(job_id, output):
    from api.apps.sql_assistant.conversation import advance

    job = BusinessDocumentJobQueue.claim("worker", lease_ms=60000)
    assert job.id == job_id
    with SqlAssistantSession._meta.database.atomic():
        Projects.complete_job(job, "worker", job.lease_token, deepcopy(output))
        advance(job)
    return job


def test_initial_question_progresses_without_browser_and_asks_only_blocking_question(research, actor):
    service = research[0]
    created = service.create({"question": "Show orders", "client_request_id": "create-chat"})
    session = SqlAssistantSession.get_by_id(created["id"])
    assert created["conversation"]["phase"] == "PREPARING"
    finish_preparation(created["conversation"]["job_id"], requirements(blocking=True))
    waiting = service.get(session.id)
    assert waiting["conversation"]["phase"] == "WAITING"
    assert waiting["messages"][-1]["text"] == "Какой год?"
    response = native.command(service, session, "message", {"text": "2026"}, key="year")
    next_job = BusinessDocumentJob.get_by_id(response["session"]["conversation"]["job_id"])
    assert next_job.job_type == "SQL_AGENT_SCHEMA"
    assert "2026" in json.dumps(next_job.payload["request"]["clarifications"])
    assert response["session"]["messages"][-1]["text"] == "2026"


def test_sufficient_initial_question_queues_schema_without_manual_accept(research, actor):
    service = research[0]
    created = service.create({"question": "Show orders", "client_request_id": "create-chat"})
    finish_preparation(created["conversation"]["job_id"], requirements(blocking=False))
    current = service.get(created["id"])
    assert current["conversation"]["phase"] == "PREPARING"
    assert BusinessDocumentJob.get_by_id(current["conversation"]["job_id"]).job_type == "SQL_AGENT_SCHEMA"
    assert len(current["messages"]) == 1


def test_actor_downgrade_during_model_call_cannot_accept_or_enqueue_next_stage(research, actor):
    service = research[0]
    created = service.create({"question": "Show orders", "client_request_id": "create-chat"})
    User.update(business_document_role="READER").where(User.id == actor.id).execute()
    finish_preparation(created["conversation"]["job_id"], requirements(blocking=False))
    current = service.get(created["id"])
    assert current["conversation"]["phase"] == "ERROR"
    assert current["project"]["artifact_ids"]["requirements"] is None
    assert BusinessDocumentJob.select().count() == 1


def complete(service, job_id, plan):
    job = BusinessDocumentJobQueue.claim("worker", lease_ms=60000)
    assert job.id == job_id
    with SqlAssistantSession._meta.database.atomic():
        Projects.complete_job(job, "worker", job.lease_token, plan)
        service.complete_dialogue_job(BusinessDocumentJob.get_by_id(job.id))


def test_message_is_saved_before_model_and_job_never_copies_result_cells(research):
    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    before = service.get(session.id)
    raw = {
        "client_request_id": "message",
        "expected_revision": before["revision"],
        "expected_project_version": before["project"]["state_version"],
        "type": "message",
        "payload": {"text": "Назови сумму сохранённых строк"},
    }
    queued = service.command(session.id, raw)
    current = queued["session"]
    assert current["messages"][-1]["text"] == "Назови сумму сохранённых строк"
    assert current["conversation"]["phase"] == "ROUTING"
    request = BusinessDocumentJob.get_by_id(current["project"]["current_job"]["id"]).payload["request"]
    assert "context_hash" in request
    assert "rows" not in request and "context" not in request
    assert "rows" not in request["payload"]
    replay = service.command(session.id, raw)
    assert replay["session"]["selected_result_id"] == run.id
    assert BusinessDocumentJob.select().count() == 1


def test_server_aggregate_is_recomputed_from_result_not_stored_as_text(research):
    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    current = service.get(session.id)
    column = service.result(session.id, run.id)["columns"][0]["id"]
    queued = native.command(service, session, "message", {"text": "Сумма по сохранённым строкам"}, key="message")
    complete(service, queued["session"]["project"]["current_job"]["id"], {"intent": "AGGREGATE", "operation": "sum", "column_id": column, "scope": "SAVED_RESULT"})
    after = service.get(session.id)
    answer = after["messages"][-1]["answer"]
    assert "31125" in answer["text"] and answer["scope"] == "LIMITED"
    assert after["selected_result_id"] == current["selected_result_id"]
    assert after["conversation"]["phase"] == "READY"
    assert "31125" not in json.dumps(SqlAssistantSession.get_by_id(session.id).state)


def test_stop_preserves_selected_ready_result(research):
    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    queued = native.command(service, session, "message", {"text": "Измени группировку"}, key="message")
    stopped = native.command(service, session, "stop", {}, key="stop")
    assert stopped["session"]["selected_result_id"] == run.id
    assert stopped["session"]["conversation"]["phase"] == "CANCELLED"
    assert BusinessDocumentJob.get_by_id(queued["session"]["project"]["current_job"]["id"]).status == "CANCELLED"
