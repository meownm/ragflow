"""Worker completion must preserve the aggregate on rejected results."""

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_worker_exports import (
    AUTHOR,
    TENANT,
    _command,
    _create,
    _minimal_ast,
    database as worker_database,
)
from api.apps.business_documents.runtime import document_commands, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentJob, BusinessDocumentJobStreamEvent, BusinessDocumentQuestion, BusinessDocumentRevision
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def database():
    yield from worker_database.__wrapped__()


def intake_attempt():
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    job = BusinessDocumentJobQueue.claim("boundary-worker", lease_ms=60_000)
    assert job.id == requested["job_id"]
    return document, job


@pytest.mark.parametrize(
    "damage, code",
    [
        ("foreign_tenant", "JOB_NOT_FOUND"),
        ("cancelled", "JOB_LEASE_LOST"),
        ("outdated_version", "STALE_AI_RESULT"),
        ("wrong_state", "JOB_STATE_CONFLICT"),
        ("non_object", "INVALID_JOB_OUTPUT"),
        ("unknown_type", "UNKNOWN_JOB_TYPE"),
    ],
)
def test_rejected_result_never_publishes_an_event_or_finishes_job(database, damage, code):
    document, job = intake_attempt()
    tenant = TENANT
    output = {"schema_version": "1", "outcome": "COMPLETE", "questions": []}
    if damage == "foreign_tenant":
        tenant = "foreign-tenant"
    elif damage == "cancelled":
        BusinessDocumentJob.update(status="CANCELLED").where(BusinessDocumentJob.id == job.id).execute()
    elif damage == "outdated_version":
        BusinessDocument.update(state_version=job.source_state_version + 1).where(BusinessDocument.id == document["document_id"]).execute()
    elif damage == "wrong_state":
        BusinessDocument.update(operation_state="IDLE").where(BusinessDocument.id == document["document_id"]).execute()
    elif damage == "non_object":
        output = []
    else:
        BusinessDocumentJob.update(job_type="UNSUPPORTED_JOB").where(BusinessDocumentJob.id == job.id).execute()
    before_document = BusinessDocument.get_by_id(document["document_id"]).__data__.copy()
    before_job = BusinessDocumentJob.get_by_id(job.id).__data__.copy()
    before_events = BusinessDocumentEvent.select().count()
    with pytest.raises(BusinessDocumentError) as caught:
        job_completion.complete(tenant, job.lease_owner, job.id, output, job.lease_token)
    assert caught.value.code == code
    assert BusinessDocument.get_by_id(document["document_id"]).__data__ == before_document
    assert BusinessDocumentJob.get_by_id(job.id).__data__ == before_job
    assert BusinessDocumentEvent.select().count() == before_events


def test_failed_job_is_idempotent_and_retains_first_error(database):
    document, job = intake_attempt()
    error = {"code": "MODEL_UNAVAILABLE", "message": "Required model is unavailable"}
    first = job_completion.fail(TENANT, job.lease_owner, job.id, error, job.lease_token)
    count = BusinessDocumentEvent.select().count()
    second = job_completion.fail(TENANT, "later-worker", job.id, {"code": "OTHER_ERROR"}, "expired-token")
    assert first == second
    assert second["operation_state"] == "FAILED"
    assert second["last_error"] == error
    assert BusinessDocumentEvent.select().count() == count
    stored = BusinessDocumentJob.get_by_id(job.id)
    assert stored.status == "DEAD" and stored.error == error
    assert stored.lease_owner is stored.lease_token is stored.lease_expires_at is None
    assert BusinessDocument.get_by_id(document["document_id"]).state_version == job.source_state_version + 1


def test_completed_job_replay_does_not_reprocess_a_changed_payload(database):
    _, job = intake_attempt()
    first = job_completion.complete(TENANT, job.lease_owner, job.id, {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, job.lease_token)
    count = BusinessDocumentEvent.select().count()
    second = job_completion.complete(TENANT, "later-worker", job.id, "invalid replay payload", "expired-token")
    assert second == first
    assert BusinessDocumentEvent.select().count() == count
    assert BusinessDocumentJob.get_by_id(job.id).status == "COMPLETED"


def test_plan_failure_emits_failed_stream_event_once(database):
    _, job = intake_attempt()
    BusinessDocumentJob.update(job_type="PLAN_CHANGES").where(BusinessDocumentJob.id == job.id).execute()
    error = {"message": "Planning failed before output"}
    job_completion.fail(TENANT, job.lease_owner, job.id, error, job.lease_token)
    job_completion.fail(TENANT, job.lease_owner, job.id, error, job.lease_token)
    events = list(BusinessDocumentJobStreamEvent.select().where(BusinessDocumentJobStreamEvent.job_id == job.id))
    failed = [event for event in events if event.event_type == "failed"]
    assert len(failed) == 1
    assert failed[0].payload == {"code": "WORKER_FAILURE"}


@pytest.mark.parametrize(
    "damage, code",
    [
        ("open_question", "DRAFT_PRECONDITION_FAILED"),
        ("existing_revision", "DRAFT_PRECONDITION_FAILED"),
        ("template_changed", "TEMPLATE_VERSION_CONFLICT"),
        ("non_array_proposals", "INVALID_DRAFT_PROTOCOL"),
    ],
)
def test_draft_revalidates_pinned_preconditions_before_creating_revision(database, damage, code):
    _, intake = intake_attempt()
    document = job_completion.complete(TENANT, intake.lease_owner, intake.id, {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, intake.lease_token)
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    job = BusinessDocumentJobQueue.claim("draft-boundary-worker", lease_ms=60_000)
    output = {"draft": _minimal_ast()}
    if damage == "open_question":
        BusinessDocumentQuestion.create(
            id="late-question",
            document_id=document["document_id"],
            review_cycle=0,
            stage="INTAKE",
            semantic_tag="late",
            target_section_id="3.1",
            text="Unanswered input",
            options=[],
            allow_custom_answer=True,
            source_event_ids=[],
            evidence_refs=[],
        )
    elif damage == "existing_revision":
        BusinessDocument.update(current_revision_id="concurrent-revision").where(BusinessDocument.id == document["document_id"]).execute()
    elif damage == "template_changed":
        BusinessDocument.update(template_version="different-pinned-template").where(BusinessDocument.id == document["document_id"]).execute()
    else:
        output["proposals"] = {"not": "an array"}
    events_before = BusinessDocumentEvent.select().count()
    with pytest.raises(BusinessDocumentError) as caught:
        job_completion.complete(TENANT, job.lease_owner, job.id, output, job.lease_token)
    assert caught.value.code == code
    assert BusinessDocumentRevision.select().count() == 0
    assert BusinessDocumentEvent.select().count() == events_before
    assert BusinessDocumentJob.get_by_id(job.id).status == "RUNNING"


def test_draft_restores_required_sections_and_requests_explicit_absence_confirmation(database):
    _, intake = intake_attempt()
    document = job_completion.complete(TENANT, intake.lease_owner, intake.id, {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, intake.lease_token)
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    job = BusinessDocumentJobQueue.claim("draft-boundary-worker", lease_ms=60_000)
    ast = _minimal_ast()
    ast["sections"] = [section for section in ast["sections"] if section["id"] not in {"2", "5.4"}]

    result = job_completion.complete(TENANT, job.lease_owner, job.id, {"draft": ast}, job.lease_token)

    assert {section["id"] for section in result["current_revision"]["document_ast"]["sections"]} >= {"2", "5.4"}
    assert {question["target_section_id"] for question in result["protocol"]["questions"]} == {"2", "5.4"}
    assert result["lifecycle_state"] == "REVIEW"
    assert all(section["blocks"][0]["text"] == "Требования не определены" for section in result["current_revision"]["document_ast"]["sections"] if section["id"] in {"2", "5.4"})


def test_dataset_job_requires_pinned_evidence_audit_before_accepting_output(database, monkeypatch):
    _, job = intake_attempt()
    BusinessDocumentJob.update(payload={**job.payload, "dataset_ids": ["required-dataset"]}).where(BusinessDocumentJob.id == job.id).execute()
    monkeypatch.setattr(job_completion, "_retrieval_enabled", lambda: True)
    events_before = BusinessDocumentEvent.select().count()
    with pytest.raises(BusinessDocumentError) as caught:
        job_completion.complete(TENANT, job.lease_owner, job.id, {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, job.lease_token)
    assert caught.value.code == "EVIDENCE_AUDIT_REQUIRED"
    assert BusinessDocumentEvent.select().count() == events_before
    assert BusinessDocumentJob.get_by_id(job.id).status == "RUNNING"
