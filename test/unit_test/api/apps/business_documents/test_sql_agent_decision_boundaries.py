"""Closed SQL agent commands and reviewed proposals fail without partial writes."""

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_agents_boundaries import (
    ACTOR,
    TENANT,
    Service,
    database as agent_database,
    attempt,
    schema_project,
)
from test.unit_test.api.apps.business_documents.test_sql_query_agent_service import _create_project, RequirementsRunner
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentSqlAgentCommand, BusinessDocumentSqlAgentProposal, BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryProject, BusinessDocumentJob
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def database():
    yield from agent_database.__wrapped__()


def decision(**changes):
    return {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "decision-boundary", "decision": "ACCEPT", **changes}


def pending_requirements():
    project, job = attempt()
    Service.complete_job(job, job.lease_owner, job.lease_token, RequirementsRunner().process(job))
    return project["id"], BusinessDocumentSqlAgentProposal.get()


def metadata_snapshot(project_id):
    return (
        BusinessDocumentSqlQueryProject.get_by_id(project_id).__data__.copy(),
        [row.__data__.copy() for row in BusinessDocumentSqlAgentProposal.select()],
        BusinessDocumentSqlAgentCommand.select().count(),
        BusinessDocumentSqlQueryArtifact.select().count(),
    )


@pytest.mark.parametrize(
    "body",
    [
        None,
        {"schema_version": "2", "title": "Title", "source_request": "Query"},
        {"schema_version": "1", "title": "Title", "source_request": "Query", "locale": "de"},
        {"schema_version": "1", "title": "Title", "source_request": "Query", "owner_id": "attacker"},
    ],
)
def test_invalid_project_envelope_cannot_assign_owner_or_create_metadata(database, body):
    with pytest.raises(BusinessDocumentError) as caught:
        Service.create_project(TENANT, ACTOR, body)
    assert caught.value.status == 422
    assert BusinessDocumentSqlQueryProject.select().count() == 0
    assert BusinessDocumentSqlAgentCommand.select().count() == 0
    assert BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("operation", ["revise_question", "decide_proposal"])
def test_nonobject_mutation_body_cannot_modify_project(database, operation):
    project = _create_project()
    before = metadata_snapshot(project["id"])
    with pytest.raises(BusinessDocumentError) as caught:
        if operation == "decide_proposal":
            Service.decide_proposal(TENANT, ACTOR, project["id"], "missing-proposal", None)
        else:
            Service.revise_question(TENANT, ACTOR, project["id"], None)
    assert caught.value.status == 422
    assert metadata_snapshot(project["id"]) == before


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"schema_version": "2"}, "INVALID_SQL_AGENT_DECISION"),
        ({"expected_state_version": True}, "INVALID_SQL_AGENT_DECISION"),
        ({"decision": "APPROVE"}, "INVALID_SQL_AGENT_DECISION"),
        ({"expected_state_version": 1}, "SQL_AGENT_VERSION_CONFLICT"),
        ({"artifact_payload": []}, "INVALID_SQL_AGENT_DECISION"),
    ],
)
def test_invalid_decision_keeps_pending_proposal_and_previous_version(database, changes, code):
    project_id, proposal = pending_requirements()
    before = metadata_snapshot(project_id)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.decide_proposal(TENANT, ACTOR, project_id, proposal.id, decision(**changes))
    assert caught.value.code == code
    assert metadata_snapshot(project_id) == before


@pytest.mark.parametrize("actor,proposal_state", [("foreign-owner", "PENDING"), (ACTOR, "missing"), (ACTOR, "REJECTED")])
def test_proposal_decision_checks_owner_existing_target_and_immutability(database, actor, proposal_state):
    project_id, proposal = pending_requirements()
    if proposal_state == "REJECTED":
        Service.decide_proposal(TENANT, ACTOR, project_id, proposal.id, decision(decision="REJECT"))
    before = metadata_snapshot(project_id)
    request = decision(expected_state_version=before[0]["state_version"], idempotency_key="second-decision")
    with pytest.raises(BusinessDocumentError) as caught:
        Service.decide_proposal(TENANT, actor, project_id, "missing" if proposal_state == "missing" else proposal.id, request)
    assert caught.value.code == ({"PENDING": "DOCUMENT_PERMISSION_DENIED", "missing": "SQL_AGENT_PROPOSAL_NOT_FOUND", "REJECTED": "SQL_AGENT_PROPOSAL_ALREADY_DECIDED"}[proposal_state])
    assert metadata_snapshot(project_id) == before


@pytest.mark.parametrize("artifact_payload", [None, {}])
def test_schema_acceptance_requires_explicit_valid_schema_and_preserves_pending_work(database, artifact_payload):
    project = schema_project()
    Service.request_agent(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "schema-boundary", "kind": "SCHEMA", "payload": {"terms": ["orders"]}})
    job = BusinessDocumentJobQueue.claim("schema-decision-worker", lease_ms=60_000)
    Service.complete_job(job, job.lease_owner, job.lease_token, {"status": "UNAVAILABLE", "entities": [], "warning": "No catalog match"})
    proposal = BusinessDocumentSqlAgentProposal.get()
    before = metadata_snapshot(project["id"])
    with pytest.raises(BusinessDocumentError) as caught:
        Service.decide_proposal(TENANT, ACTOR, project["id"], proposal.id, decision(artifact_payload=artifact_payload))
    assert caught.value.code == "INVALID_SQL_AGENT_DECISION"
    assert metadata_snapshot(project["id"]) == before


def test_stale_agent_request_does_not_start_replacement_job(database):
    project = _create_project()
    before = metadata_snapshot(project["id"])
    with pytest.raises(BusinessDocumentError) as caught:
        Service.request_agent(TENANT, ACTOR, project["id"], {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "stale-request", "kind": "REQUIREMENTS", "payload": {}})
    assert caught.value.code == "SQL_AGENT_VERSION_CONFLICT"
    assert caught.value.details == {"current_state_version": 1}
    assert metadata_snapshot(project["id"]) == before
    assert BusinessDocumentJob.select().count() == 0
