from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_revisions import ACTOR, TENANT, SQL, project as revision_project
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.sql_query_revision_ai import propose_revision
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlAgentProposal, BusinessDocumentSqlQueryArtifact
from business_documents.sql_query.dialogue import dialogue_response


@pytest.fixture
def project():
    yield from revision_project.__wrapped__()


def test_dialogue_uses_required_fields_without_arbitrary_size_or_question_limits():
    questions = [{"id": str(index), "text": "Уточните " + "условие " * 150, "options": [str(option) for option in range(8)], "extra": "ignored"} for index in range(7)]
    response = dialogue_response({"response_kind": "CLARIFICATION", "answer": "Контекст " * 2000, "questions": questions, "extra": "ignored"})
    assert len(response["questions"]) == 7
    assert len(response["questions"][0]["options"]) == 8
    assert "extra" not in response and "extra" not in response["questions"][0]


@pytest.mark.asyncio
async def test_explanation_does_not_compile_or_prepare_changed_sql(monkeypatch):
    from api.apps.business_documents import sql_query_revision_ai as adapter

    monkeypatch.setattr(adapter, "compile_query_payload", lambda *_: pytest.fail("An explanation must not compile a query"))
    request = {"target": "QUERY", "sql": SQL, "comments": [{"text": "Объясни LIMIT"}]}
    before = deepcopy(request)

    class Model:
        async def async_generate(self, tenant, system, payload):
            assert payload["context"]["current_sql"] == SQL
            return {"response_kind": "EXPLANATION", "answer": "LIMIT ограничивает число возвращаемых строк."}

    response = await propose_revision(TENANT, request, llm=Model())
    assert request == before
    assert response["response_kind"] == "EXPLANATION"
    assert "proposal" not in response and "sql" not in response


def request_reply(project):
    thread = BusinessDocumentSqlRevisionService.create_thread(
        TENANT,
        ACTOR,
        project,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "dialogue-thread", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Почему здесь LIMIT?"},
    )
    BusinessDocumentSqlRevisionService.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "dialogue-request"})
    return thread, BusinessDocumentJobQueue.claim("dialogue-test")


@pytest.mark.parametrize(
    "response",
    [
        {"response_kind": "EXPLANATION", "answer": "LIMIT ограничивает число строк."},
        {"response_kind": "CLARIFICATION", "answer": "Уточните период.", "questions": [{"id": "year", "text": "Какой год?", "options": ["2025", "2026"]}]},
    ],
)
def test_assistant_turn_is_persisted_without_a_proposal_or_query_revision(project, response):
    _, job = request_reply(project)
    count = BusinessDocumentSqlQueryArtifact.select().count()
    BusinessDocumentSqlAgentService.complete_job(job, "dialogue-test", job.lease_token, response)
    workspace = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)
    assert workspace["sql"] == SQL
    assert workspace["threads"][0]["messages"][-1]["role"] == "assistant"
    assert workspace["threads"][0]["messages"][-1]["text"] == response["answer"]
    assert workspace["threads"][0]["version"] == 3
    assert BusinessDocumentSqlQueryArtifact.select().count() == count
    assert BusinessDocumentSqlAgentProposal.select().count() == 0
    assert BusinessDocumentSqlAgentService.get_project(TENANT, ACTOR, project)["operation_state"] == "IDLE"


def test_new_user_reply_makes_late_explanation_stale(project):
    thread, job = request_reply(project)
    BusinessDocumentSqlRevisionService.reply(
        TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_thread_version": 2, "idempotency_key": "new-message", "text": "Теперь измени лимит на 20"}
    )
    BusinessDocumentSqlAgentService.complete_job(job, "dialogue-test", job.lease_token, {"response_kind": "EXPLANATION", "answer": "Old answer"})
    messages = BusinessDocumentSqlRevisionService.workspace(TENANT, ACTOR, project)["threads"][0]["messages"]
    assert len(messages) == 2
    assert all(message.get("role") != "assistant" for message in messages)
    assert BusinessDocumentJob.get_by_id(job.id).status == "STALE"
