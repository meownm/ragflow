"""Real SQLite queue/CAS/lease boundaries; no model or API substitutes."""

import pytest

from test.unit_test.api.apps.business_documents import test_sql_query_revisions as native
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryProject, SqlAssistantSession
from business_documents.application.errors import BusinessDocumentError, ConflictError


@pytest.fixture
def project():
    for identity in native.project.__wrapped__():
        yield BusinessDocumentSqlQueryProject.get_by_id(identity)


@pytest.fixture
def research(project):
    session = SqlAssistantSession.create(id=project.id, tenant_id=native.TENANT, owner_id=native.ACTOR, create_request_id="initial", create_request_hash="initial", state={})
    return project, session


def request(project):
    return {"schema_version": "1", "expected_state_version": project.state_version, "idempotency_key": "dialogue-1", "request": {"question": "Объясни результат", "context": None, "discussion": []}}


def test_dialogue_is_a_durable_idempotent_job_with_immutable_snapshot(research):
    project, _ = research
    raw = request(project)
    result = Projects.request_dialogue(native.TENANT, native.ACTOR, project.id, raw)
    assert result["operation_state"] == "RUNNING"
    job = BusinessDocumentJob.get_by_id(result["current_job"]["id"])
    assert job.job_type == "SQL_AGENT_DIALOGUE"
    assert job.source_state_version == result["state_version"]
    assert Projects.request_dialogue(native.TENANT, native.ACTOR, project.id, raw) == result
    assert BusinessDocumentJob.select().count() == 1
    raw["request"]["question"] = "Другое сообщение"
    assert BusinessDocumentJob.get_by_id(job.id).payload["request"]["question"] == "Объясни результат"
    with pytest.raises(ConflictError):
        Projects.request_dialogue(native.TENANT, native.ACTOR, project.id, raw)
    assert BusinessDocumentJob.select().count() == 1


def test_reader_can_queue_a_question_and_cancel_their_own_inference(research):
    project, _ = research
    result = Projects.request_dialogue(native.TENANT, native.ACTOR, project.id, request(project), access_role="READER")
    job = BusinessDocumentJobQueue.claim("worker", lease_ms=60000)
    assert job is not None
    cancel = {"schema_version": "1", "expected_state_version": result["state_version"], "idempotency_key": "stop-1"}
    stopped = Projects.cancel_agent(native.TENANT, native.ACTOR, project.id, cancel, access_role="READER")
    assert stopped["operation_state"] == "IDLE" and stopped["current_job"] is None
    assert BusinessDocumentJob.get_by_id(job.id).status == "CANCELLED"
    assert Projects.cancel_agent(native.TENANT, native.ACTOR, project.id, cancel, access_role="READER") == stopped
    with pytest.raises(BusinessDocumentError):
        Projects.complete_job(job, "worker", job.lease_token, {"intent": "CHANGE"})
    assert BusinessDocumentJob.get_by_id(job.id).status == "CANCELLED"


def test_dialogue_requires_an_actual_session_and_cannot_cross_tenants(project):
    with pytest.raises(BusinessDocumentError):
        Projects.request_dialogue("foreign", native.ACTOR, project.id, request(project))
    with pytest.raises(ConflictError):
        Projects.request_dialogue(native.TENANT, native.ACTOR, project.id, request(project))
    assert BusinessDocumentJob.select().count() == 0
