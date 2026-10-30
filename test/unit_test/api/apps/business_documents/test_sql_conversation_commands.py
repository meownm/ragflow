"""Single-command conversation persistence over real SQLite artifacts."""

import json
import asyncio
from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents import test_sql_assistant_service as native
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun, SqlAssistantSession, User
from business_documents.application.errors import ConflictError

project = native.project
source = native.source
research = native.research
approved = native.approved


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


def test_one_reply_reconsiders_remaining_questions_instead_of_asking_them_again(research, actor):
    service = research[0]
    created = service.create({"question": "Show total", "client_request_id": "create-total"})
    session = SqlAssistantSession.get_by_id(created["id"])
    proposal = requirements(blocking=True)
    proposal["proposal"]["questions"] = [
        {"id": "METRIC", "question": "Какой итог?", "blocking": True, "options": ["Сумма", "Количество"]},
        {"id": "PERIOD", "question": "За какой период?", "blocking": True, "options": ["За всё время"]},
    ]
    finish_preparation(created["conversation"]["job_id"], proposal)
    reply = "Сумма amount для paid за все имеющиеся строки, одна колонка total."
    response = native.command(service, session, "message", {"text": reply}, key="total-answer")
    current = response["session"]
    job = BusinessDocumentJob.get_by_id(current["conversation"]["job_id"])
    assert job.job_type == "SQL_AGENT_REQUIREMENTS"
    assert "Show total" in job.payload["request"]["source_request"]
    assert "Вопрос помощника: Какой итог?\nОтвет пользователя: " + reply in job.payload["request"]["source_request"]
    assert "За какой период?" not in job.payload["request"]["source_request"]
    assert current["conversation"]["phase"] == "PREPARING"
    assert current["messages"][-1]["text"] == reply
    assert current["selected_result_id"] is None
    finish_preparation(job.id, requirements(blocking=False))
    next_job = BusinessDocumentJob.get_by_id(service.get(session.id)["conversation"]["job_id"])
    assert next_job.job_type == "SQL_AGENT_SCHEMA"


def test_unknown_freeform_reply_does_not_count_as_a_resolved_requirement(research, actor):
    service = research[0]
    created = service.create({"question": "Show orders", "client_request_id": "create-unknown"})
    session = SqlAssistantSession.get_by_id(created["id"])
    finish_preparation(created["conversation"]["job_id"], requirements(blocking=True))
    response = native.command(service, session, "message", {"text": "Пока не знаю"}, key="unknown-answer")
    job = BusinessDocumentJob.get_by_id(response["session"]["conversation"]["job_id"])
    assert job.job_type == "SQL_AGENT_REQUIREMENTS"
    finish_preparation(job.id, requirements(blocking=True))
    current = service.get(session.id)
    assert current["conversation"]["phase"] == "WAITING"
    assert current["messages"][-1]["text"] == "Какой год?"
    assert current["selected_result_id"] is None


def test_actor_downgrade_during_model_call_cannot_accept_or_enqueue_next_stage(research, actor):
    service = research[0]
    created = service.create({"question": "Show orders", "client_request_id": "create-chat"})
    User.update(business_document_role="READER").where(User.id == actor.id).execute()
    finish_preparation(created["conversation"]["job_id"], requirements(blocking=False))
    current = service.get(created["id"])
    assert current["conversation"]["phase"] == "ERROR"
    assert current["project"]["artifact_ids"]["requirements"] is None
    assert BusinessDocumentJob.select().count() == 1


def test_query_clarification_keeps_the_actual_question_with_its_answer(research, actor):
    service, session, _, _, _ = research
    BusinessDocumentSqlQueryProject.update(stage="QUERY", query_artifact_id=None).where(BusinessDocumentSqlQueryProject.id == session.id).execute()
    project = service.runtime.project(session.id)
    queued = service.runtime.call(
        Projects.request_agent, session.id, {"schema_version": "1", "expected_state_version": project["state_version"], "idempotency_key": "query-question", "kind": "QUERY", "payload": {}}
    )
    state = deepcopy(session.state)
    state["conversation"] = {"turn_id": "turn", "message_id": "m1", "phase": "PREPARING", "job_id": queued["current_job"]["id"]}
    SqlAssistantSession.update(state=state).where(SqlAssistantSession.id == session.id).execute()
    finish_preparation(
        queued["current_job"]["id"],
        {
            "schema_version": "1",
            "status": "NEEDS_CLARIFICATION",
            "proposal": {"select": [{"column_id": "order-id"}], "joins": [], "filters": []},
            "clarification_questions": ["За какой год показать заказы?"],
            "warning": None,
            "diagnostic": None,
        },
    )
    waiting = service.get(session.id)
    assert waiting["conversation"]["phase"] == "WAITING"
    assert waiting["messages"][-1]["text"] == "За какой год показать заказы?"
    answered = native.command(service, session, "message", {"text": "2026"}, key="year-answer")
    next_job = BusinessDocumentJob.get_by_id(answered["session"]["conversation"]["job_id"])
    assert next_job.job_type == "SQL_AGENT_REQUIREMENTS"
    assert "Вопрос помощника: За какой год показать заказы?\nОтвет пользователя: 2026" in next_job.payload["request"]["source_request"]


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


def test_dialogue_runner_does_not_nest_the_provider_event_loop(research, actor, monkeypatch):
    from api.apps.business_documents.sql_query_agent_worker import BusinessDocumentSqlAgentRunner
    from api.apps.sql_assistant import conversation
    from business_documents.async_runtime import run_in_worker_loop

    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    native.command(service, session, "message", {"text": "Измени группировку"}, key="message")
    job = BusinessDocumentJobQueue.claim("worker", lease_ms=60000)
    actual_plan = conversation.plan_conversation

    class Model:
        def generate(self, *_args, **_kwargs):
            return run_in_worker_loop(asyncio.sleep(0, result={"intent": "CHANGE"}))

    def bounded_plan(*args):
        return actual_plan(*args, llm=Model())

    monkeypatch.setattr(conversation, "plan_conversation", bounded_plan)
    assert BusinessDocumentSqlAgentRunner().process(job) == {"intent": "CHANGE"}


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


@pytest.mark.parametrize("topic", ["conditions", "null", "scope"])
def test_explanation_persists_a_guarded_plan_without_changing_the_result_or_executing_sql(research, topic):
    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    before = service.get(session.id)
    result = service.result(session.id, run.id)
    count = BusinessDocumentSqlQueryRun.select().count()
    queued = native.command(service, session, "message", {"text": "Объясни условия расчёта"}, key="explain")
    complete(service, queued["session"]["project"]["current_job"]["id"], {"intent": "EXPLAIN", "topic": topic})
    after = service.get(session.id)
    assert after["conversation"]["phase"] == "READY" and after["selected_result_id"] == before["selected_result_id"]
    assert after["messages"][-1]["answer"]["status"] == "READY"
    assert "explanation_plan" not in after["messages"][-1]
    stored = SqlAssistantSession.get_by_id(session.id).state["messages"][-1]
    assert stored["explanation_plan"] == {"intent": "EXPLAIN", "topic": topic} and "answer" not in stored
    refreshed = service.result(session.id, run.id)
    assert {key: refreshed[key] for key in ("sql", "parameters", "rows", "view")} == {key: result[key] for key in ("sql", "parameters", "rows", "view")}
    assert BusinessDocumentSqlQueryRun.select().count() == count


@pytest.mark.parametrize("revoked", [False, True], ids=["expired", "revoked"])
def test_stored_explanation_rechecks_result_lifetime_and_catalog_access(research, monkeypatch, revoked):
    from business_documents.application.errors import PermissionDeniedError

    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    queued = native.command(service, session, "message", {"text": "Поясни NULL"}, key="explain")
    complete(service, queued["session"]["project"]["current_job"]["id"], {"intent": "EXPLAIN", "topic": "null"})
    assert service.get(session.id)["messages"][-1]["answer"]["status"] == "READY"
    if revoked:

        async def deny(*_args):
            raise PermissionDeniedError("Catalog access revoked")

        monkeypatch.setattr(native.BusinessDocumentSqlQuerySchemaService, "load_entities", deny)
        with pytest.raises(PermissionDeniedError):
            service.get(session.id)
    else:
        BusinessDocumentSqlQueryRun.update(status="EXPIRED", rows=[]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
        answer = service.get(session.id)["messages"][-1]["answer"]
        assert answer["status"] == "UNAVAILABLE" and "NULL означает" not in answer["text"]
    assert "answer" not in SqlAssistantSession.get_by_id(session.id).state["messages"][-1]


def test_stop_preserves_selected_ready_result(research):
    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    queued = native.command(service, session, "message", {"text": "Измени группировку"}, key="message")
    stopped = native.command(service, session, "stop", {}, key="stop")
    assert stopped["session"]["selected_result_id"] == run.id
    assert stopped["session"]["conversation"]["phase"] == "CANCELLED"
    assert BusinessDocumentJob.get_by_id(queued["session"]["project"]["current_job"]["id"]).status == "CANCELLED"


def test_cancelled_explanation_job_cannot_publish_a_late_answer(research):
    service, session, run, _, _ = research
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    queued = native.command(service, session, "message", {"text": "Поясни период"}, key="explain")
    job = BusinessDocumentJobQueue.claim("worker", lease_ms=60000)
    assert job.id == queued["session"]["conversation"]["job_id"]
    native.command(service, session, "stop", {}, key="stop")
    with pytest.raises(ConflictError):
        Projects.complete_job(job, "worker", job.lease_token, {"intent": "EXPLAIN", "topic": "conditions"})
    after = service.get(session.id)
    assert after["selected_result_id"] == run.id and after["conversation"]["phase"] == "CANCELLED"
    assert all("explanation_plan" not in message for message in SqlAssistantSession.get_by_id(session.id).state["messages"])


@pytest.mark.parametrize("bound,max_rows,expected", [(True, 50, 50), (True, 100000, 10000), (False, 50, None)])
def test_preparation_passes_only_the_bound_source_row_cap_to_the_durable_query(research, monkeypatch, bound, max_rows, expected):
    from api.apps.business_documents.sql_execution_registry import BusinessDocumentSqlExecutionRegistryService

    service, session, _, _, _ = research
    observed = []

    def resolve(actor_id, scope, is_admin, access_role):
        observed.append(scope)
        assert actor_id == native.ACTOR and access_role == "AUTHOR_CREATOR"
        return {"status": "BOUND" if bound else "AMBIGUOUS", "selection": {"profile": {"max_rows": max_rows}} if bound else None}

    monkeypatch.setattr(BusinessDocumentSqlExecutionRegistryService, "resolve", resolve)
    BusinessDocumentSqlQueryProject.update(query_artifact_id=None, stage="QUERY").where(BusinessDocumentSqlQueryProject.id == session.id).execute()
    state = deepcopy(session.state)
    state["conversation"] = {"turn_id": "limit-turn", "message_id": "m1", "phase": "PREPARING"}
    with SqlAssistantSession._meta.database.atomic():
        service._progress_preparation(session, state)
    job = BusinessDocumentJob.get_by_id(state["conversation"]["job_id"])
    assert job.job_type == "SQL_AGENT_QUERY"
    assert job.payload["request"].get("row_limit_cap") == expected
    assert observed[0]["schema_snapshot"]["status"] == "READY"


@pytest.fixture
def metric_binding(approved, actor):
    service, session, _, definition, _, _ = approved
    BusinessDocumentSqlQueryProject.update(query_artifact_id=None, stage="QUERY").where(BusinessDocumentSqlQueryProject.id == session.id).execute()
    state = deepcopy(SqlAssistantSession.get_by_id(session.id).state)
    state["messages"] = [{"id": "m1", "role": "user", "text": "Сумма по клиентам, порог пять", "parent_result_id": None}]
    state["conversation"] = {
        "turn_id": "m1",
        "message_id": "m1",
        "phase": "WAITING",
        "pending": {"kind": "METRIC", "candidates": [{"id": definition["definition_id"], "version": definition["version"], "title": definition["title"]}]},
    }
    SqlAssistantSession.update(state=state).where(SqlAssistantSession.id == session.id).execute()
    response = native.command(service, session, "message", {"text": definition["title"]}, key="metric-choice")
    return service, session, response["session"]


def test_metric_choice_does_not_execute_template_defaults(metric_binding):
    _, _, current = metric_binding
    assert current["conversation"]["phase"] == "ROUTING"
    job = BusinessDocumentJob.get_by_id(current["conversation"]["job_id"])
    assert job.job_type == "SQL_AGENT_DIALOGUE"
    assert job.payload["request"]["payload"] == {"kind": "DEFINITION_BINDING"}
    assert BusinessDocumentSqlQueryRun.select().count() == 1


def test_interpreted_metric_parameter_requires_confirmation_before_run(metric_binding, monkeypatch):
    service, session, current = metric_binding
    plan = {"intent": "BIND_PARAMETERS", "parameters": {"positive": {"value": "5", "source_quote": "порог пять"}}}
    finish_preparation(current["conversation"]["job_id"], plan)
    waiting = service.get(session.id)
    assert waiting["conversation"]["phase"] == "WAITING"
    assert "5" in waiting["conversation"]["question"]["text"]
    assert BusinessDocumentSqlQueryRun.select().count() == 1
    resumed = []

    def resume(current_session, _state):
        from business_documents.sql_conversation import preparation_action

        resumed.append(preparation_action(service.runtime.project(current_session.id)))

    # This contract stops at native compilation/admission. Actual catalog/profile
    # resolution and physical execution belong to the live journey.
    monkeypatch.setattr(service, "_progress_preparation", resume)
    native.command(service, session, "message", {"text": "Да"}, key="confirm-parameters")
    updated = service.get(session.id)
    assert resumed == ["EXECUTE"]
    assert updated["project"]["compilation"]["result"]["parameters"]["positive"] == "5"
    assert updated["definition_status"] == "DEFINITION_VALIDATED"
    assert BusinessDocumentSqlQueryRun.select().count() == 1


def test_metric_confirmation_cannot_apply_after_draft_changed(metric_binding):
    service, session, current = metric_binding
    finish_preparation(current["conversation"]["job_id"], {"intent": "BIND_PARAMETERS", "parameters": {"positive": {"value": "5", "source_quote": "порог пять"}}})
    draft = deepcopy(service.get(session.id)["draft"])
    draft["query"]["filters"][2]["parameter_value"] = "50"
    native.command(service, session, "save_draft", {"query": draft["query"], "base_query_artifact_id": draft["base_query_artifact_id"]}, key="edit-draft")
    with pytest.raises(ConflictError) as error:
        native.command(service, session, "message", {"text": "Да, выполнить"}, key="stale-confirmation")
    assert error.value.code == "SQL_DIALOGUE_CONTEXT_CHANGED"
    assert BusinessDocumentSqlQueryRun.select().count() == 1


def test_followup_metric_choice_keeps_result_and_exploration_revises_original_question(approved, actor):
    service, session, run, definition, _, _ = approved
    native.command(service, session, "select_result", {"run_id": run.id}, key="select")
    question = definition["title"] + " с порогом десять"
    queued = native.command(service, session, "message", {"text": question}, key="metric-followup")
    finish_preparation(queued["session"]["conversation"]["job_id"], {"intent": "CHANGE"})
    waiting = service.get(session.id)
    assert waiting["conversation"]["pending"]["kind"] == "METRIC"
    assert waiting["selected_result_id"] == run.id
    response = native.command(service, session, "message", {"text": "Исследовательский расчёт"}, key="explore")
    current = response["session"]
    job = BusinessDocumentJob.get_by_id(current["conversation"]["job_id"])
    assert job.job_type == "SQL_AGENT_REVISION"
    assert question in json.dumps(job.payload, ensure_ascii=False)
    assert current["selected_result_id"] == run.id
    assert BusinessDocumentSqlQueryRun.select().count() == 1


def test_metric_binding_rechecks_edit_permission_before_publication(metric_binding, actor):
    service, session, current = metric_binding
    User.update(business_document_role="READER").where(User.id == actor.id).execute()
    finish_preparation(current["conversation"]["job_id"], {"intent": "BIND_PARAMETERS", "parameters": {"positive": {"value": "5", "source_quote": "порог пять"}}})
    assert service.get(session.id)["conversation"]["phase"] == "ERROR"
    assert BusinessDocumentSqlQueryRun.select().count() == 1
