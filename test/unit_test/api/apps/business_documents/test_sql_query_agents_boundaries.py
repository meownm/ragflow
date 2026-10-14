"""SQL agent publication preserves authorization, leases and transaction ownership."""

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_agent_service import (
    ACTOR,
    TENANT,
    _create_project,
    database as agent_database,
)
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Service
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import (
    BusinessDocumentJob,
    BusinessDocumentSqlAgentCommand,
    BusinessDocumentSqlAgentProposal,
    BusinessDocumentSqlQueryArtifact,
    BusinessDocumentSqlQueryProject,
    BusinessDocumentSqlQueryRun,
)
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def database():
    yield from agent_database.__wrapped__()


def attempt():
    project = _create_project()
    Service.request_agent(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "agent-boundary", "kind": "REQUIREMENTS", "payload": {}})
    job = BusinessDocumentJobQueue.claim("agent-boundary-worker", lease_ms=60_000)
    assert job.document_id == project["id"]
    return project, job


def test_project_list_respects_owner_and_tenant_even_for_administrator(database):
    mine = _create_project()
    peer = Service.create_project(TENANT, "peer", {"schema_version": "1", "title": "Peer", "source_request": "Peer request"})
    Service.create_project("another-tenant", ACTOR, {"schema_version": "1", "title": "Foreign", "source_request": "Foreign request"})
    assert [item["id"] for item in Service.list_projects(TENANT, ACTOR)] == [mine["id"]]
    assert {item["id"] for item in Service.list_projects(TENANT, ACTOR, is_admin=True)} == {mine["id"], peer["id"]}
    with pytest.raises(BusinessDocumentError) as caught:
        Service.get_project(TENANT, ACTOR, peer["id"], access_role="ADMIN")
    assert caught.value.status == 403


@pytest.mark.parametrize("action", ["complete", "fail"])
@pytest.mark.parametrize("loss", ["token", "expired"])
def test_stale_worker_cannot_modify_job_or_project(database, action, loss):
    project, job = attempt()
    changes = {"lease_token": "new-worker-token"} if loss == "token" else {"lease_expires_at": 0}
    BusinessDocumentJob.update(**changes).where(BusinessDocumentJob.id == job.id).execute()
    before_project = BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__.copy()
    before_job = BusinessDocumentJob.get_by_id(job.id).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        if action == "complete":
            Service.complete_job(job, job.lease_owner, job.lease_token, {"proposal": {"schema_version": "1"}})
        else:
            Service.fail_job(job, job.lease_owner, job.lease_token, {"code": "MODEL_UNAVAILABLE"})
    assert caught.value.code == "SQL_AGENT_JOB_LEASE_LOST"
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__ == before_project
    assert BusinessDocumentJob.get_by_id(job.id).__data__ == before_job
    assert BusinessDocumentSqlAgentProposal.select().count() == 0


def test_no_model_proposal_releases_operation_and_exposes_retry_reason(database):
    project, job = attempt()
    Service.complete_job(job, job.lease_owner, job.lease_token, {"warning": "Required local model is unavailable"})
    result = Service.get_project(TENANT, ACTOR, project["id"])
    assert result["operation_state"] == "IDLE" and result["next_agent"] == "REQUIREMENTS"
    assert result["last_error"] == {"code": "SQL_AGENT_NO_PROPOSAL", "message": "Required local model is unavailable"}
    assert BusinessDocumentSqlAgentProposal.select().count() == 0
    stored = BusinessDocumentJob.get_by_id(job.id)
    assert stored.status == "COMPLETED"
    assert stored.lease_owner is stored.lease_token is stored.lease_expires_at is None


def test_late_failure_does_not_replace_new_project_operation(database):
    project, job = attempt()
    BusinessDocumentSqlQueryProject.update(current_job_id="new-job", state_version=3, last_error={"code": "NEW_JOB_ERROR"}).where(BusinessDocumentSqlQueryProject.id == project["id"]).execute()
    before = BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__.copy()
    Service.fail_job(job, job.lease_owner, job.lease_token, {"code": "OLD_JOB_ERROR"})
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__ == before
    stored = BusinessDocumentJob.get_by_id(job.id)
    assert stored.status == "DEAD" and stored.error == {"code": "OLD_JOB_ERROR"}


def test_agent_proposal_rolls_back_when_job_finish_cannot_be_persisted(database, monkeypatch):
    project, job = attempt()
    before = BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__.copy()

    def cannot_finish(*_args, **_kwargs):
        raise RuntimeError("Job metadata write failed")

    monkeypatch.setattr(Service, "_finish_job", cannot_finish)
    with pytest.raises(RuntimeError, match="metadata write"):
        Service.complete_job(job, job.lease_owner, job.lease_token, {"proposal": {"requirements": []}})
    assert BusinessDocumentSqlAgentProposal.select().count() == 0
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__ == before
    assert BusinessDocumentJob.get_by_id(job.id).status == "RUNNING"


def test_revising_question_with_active_result_preserves_accepted_artifacts(database):
    project = _create_project()
    BusinessDocumentSqlQueryRun.create(
        id="active-result",
        project_id=project["id"],
        tenant_id=TENANT,
        kind="SQL",
        status="READY",
        compilation_id="compilation",
        profile_id="profile",
        profile_version=1,
        rows=[[42]],
        columns=["value"],
    )
    before = BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.revise_question(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "revise-active", "source_request": "Changed question"})
    assert caught.value.code == "SQL_RESULT_ACTIVE"
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__ == before
    assert BusinessDocumentSqlQueryRun.get_by_id("active-result").rows == [[42]]
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


def test_reused_idempotency_key_with_changed_payload_is_rejected(database):
    project = _create_project()
    command = {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "same-key", "source_request": "First edit"}
    first = Service.revise_question(TENANT, ACTOR, project["id"], command)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.revise_question(TENANT, ACTOR, project["id"], {**command, "source_request": "Second edit"})
    assert caught.value.code == "SQL_AGENT_IDEMPOTENCY_CONFLICT"
    assert Service.get_project(TENANT, ACTOR, project["id"]) == first
    assert BusinessDocumentSqlAgentCommand.select().count() == 1


def test_missing_accepted_artifacts_block_compilation_without_new_artifact(database):
    project = _create_project()
    BusinessDocumentSqlQueryProject.update(stage="COMPLETE").where(BusinessDocumentSqlQueryProject.id == project["id"]).execute()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.compile_project(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "compile-missing"})
    assert caught.value.code == "SQL_PROJECT_ARTIFACT_MISSING"
    assert BusinessDocumentSqlQueryArtifact.select().count() == 0
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


def schema_project(requirements=None):
    project = _create_project()
    artifact = BusinessDocumentSqlQueryArtifact.create(
        id="accepted-requirements",
        project_id=project["id"],
        tenant_id=TENANT,
        kind="REQUIREMENTS",
        revision=1,
        payload={"requirements": [{"statement": "Show orders"}]} if requirements is None else requirements,
        content_hash="sha256:requirements",
        source_proposal_id="accepted",
        accepted_by=ACTOR,
    )
    BusinessDocumentSqlQueryProject.update(stage="SCHEMA", requirements_artifact_id=artifact.id).where(BusinessDocumentSqlQueryProject.id == project["id"]).execute()
    return project


@pytest.mark.parametrize("terms", [[], ["Orders", " orders "], [""], ["x" * 201]])
def test_schema_agent_requires_bounded_distinct_search_terms_before_job_creation(database, terms):
    project = schema_project()
    before = BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        Service.request_agent(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "schema-terms", "kind": "SCHEMA", "payload": {"terms": terms}})
    assert caught.value.code == "INVALID_SQL_AGENT_REQUEST"
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).__data__ == before
    assert BusinessDocumentJob.select().count() == 0
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


@pytest.mark.parametrize("requirements", [{"requirements": None}, {"requirements": [{"statement": " "}]}])
def test_unusable_accepted_requirements_block_schema_request(database, requirements):
    project = schema_project(requirements)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.request_agent(
            TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "schema-invalid-requirements", "kind": "SCHEMA", "payload": {"terms": ["orders"]}}
        )
    assert caught.value.code == "SQL_REQUIREMENTS_ARTIFACT_INVALID"
    assert BusinessDocumentJob.select().count() == 0
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).operation_state == "IDLE"


def test_schema_search_result_is_reviewed_without_fabricating_model_proposal(database):
    project = schema_project()
    Service.request_agent(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "schema-search", "kind": "SCHEMA", "payload": {"terms": [" orders "]}})
    job = BusinessDocumentJobQueue.claim("schema-boundary-worker", lease_ms=60_000)
    assert job.payload["request"]["terms"] == ["orders"]
    result = {"status": "UNAVAILABLE", "entities": [], "warning": "Catalog access unavailable"}
    Service.complete_job(job, job.lease_owner, job.lease_token, result)
    proposal = BusinessDocumentSqlAgentProposal.get()
    assert proposal.kind == "SCHEMA" and proposal.status == "PENDING"
    assert proposal.payload["agent_result"] == result
    assert BusinessDocumentSqlQueryProject.get_by_id(project["id"]).operation_state == "REVIEW"
    assert BusinessDocumentSqlQueryArtifact.select().count() == 1
