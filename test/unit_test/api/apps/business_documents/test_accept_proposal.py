"""Proposal decisions are separate from preparation and confirmed application."""

from copy import deepcopy

import pytest
from test.unit_test.api.apps.business_documents.helpers import record_confirmed_proposal

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete,
    _complete_review_assessment,
    _create,
    _question_batch,
    _request_and_complete_draft,
    database as service_database,
)
from api.apps.business_documents.runtime import document_commands, document_queries, document_writer, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.apps.business_documents.evidence import BusinessDocumentEvidence
from api.db.db_models import BusinessDocumentEvent, BusinessDocumentJob, BusinessDocumentProposalDecision, BusinessDocumentRevision
from business_documents.application.errors import BusinessDocumentError, ValidationError
from business_documents.domain.content import section_hash


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def _document(open_questions=True):
    return _request_and_complete_draft(
        _create(),
        review_questions=_question_batch("REVIEW") if open_questions else None,
        proposals=[{"text": "Добавить контроль ошибок"}, {"text": "Установить порог предупреждения"}],
    )


def _plan(document, requested, text="Контроль ошибок включён"):
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    base = document["current_revision"]
    section = next(section for section in base["document_ast"]["sections"] if section["id"] == "5.5")
    return {
        "change_plan": {
            "schema_version": "1",
            "base_revision_id": base["revision_id"],
            "source_state_version": requested["state_version"],
            "acknowledged_no_change_event_ids": [],
            "operations": [
                {
                    "operation_id": "proposal",
                    "type": "REPLACE_SECTION_CONTENT",
                    "section_id": "5.5",
                    "expected_section_hash": section_hash(section),
                    "source_event_ids": job.payload["active_change_input_event_ids"],
                    "content": {"blocks": [{"type": "paragraph", "text": text}]},
                }
            ],
        }
    }


def _request_changes(document, *, preview=True):
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR))
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "PREPARE_CHANGES" if preview else "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    return document, requested


@pytest.mark.parametrize("kind", ["DECIDE_PROPOSAL"])
@pytest.mark.parametrize("open_questions", [False, True])
def test_accept_only_records_decision_without_queue_or_revision(database, monkeypatch, kind, open_questions):
    document = _document(open_questions)
    base = deepcopy(document["current_revision"])
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    jobs_before = BusinessDocumentJob.select().count()

    def unexpected(**values):
        pytest.fail("Recording a decision must not enqueue work")

    monkeypatch.setattr(document_writer, "insert_job", unexpected)
    payload = {"proposal_id": proposal_id, **({"decision": "ACCEPTED"} if kind == "DECIDE_PROPOSAL" else {})}
    command = _command(document, kind, payload)
    result = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert result["operation_state"] == "IDLE"
    assert result["lifecycle_state"] == "REVIEW"
    assert result["state_version"] == document["state_version"] + 1
    assert "job_id" not in result
    current = result["document"]
    assert current["current_revision"] == base
    assert current["protocol"]["questions"] == document["protocol"]["questions"]
    assert current["protocol"]["proposals"][0]["decision"] == "ACCEPTED"
    assert current["protocol"]["proposals"][0]["applied"] is False
    assert current["protocol"]["proposals"][0]["application"] == {"status": "WAITING", "error": None}
    assert current["protocol"]["proposals"][1]["decision"] == "PENDING"
    assert current["change_preview"] is None
    assert "PREPARE_CHANGES" not in current["allowed_commands"]
    assert "REQUEST_REVIEW_ASSESSMENT" in current["allowed_commands"]
    replay = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert replay["idempotent_replay"] is True
    assert replay["document"] == current
    assert BusinessDocumentJob.select().count() == jobs_before
    assert BusinessDocumentRevision.select().count() == 1
    assert BusinessDocumentProposalDecision.select().count() == 1
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.event_type == "ProposalDecided").count() == 1
    with pytest.raises(BusinessDocumentError) as repeated:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(current, kind, payload))
    assert repeated.value.code == "PROPOSAL_ALREADY_DECIDED"


@pytest.mark.parametrize("open_questions", [False, True])
def test_separate_preparation_keeps_original_until_explicit_confirmation(database, open_questions):
    document = _document(open_questions)
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    record_confirmed_proposal(document, proposal_id)
    document, requested = _request_changes(document)
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.payload["preview_only"] is True
    assert "apply_proposal_id" not in job.payload
    prepared = _complete(job.id, _plan(document, requested))
    assert prepared["current_revision"] == document["current_revision"]
    assert prepared["protocol"]["proposals"][0]["applied"] is False
    assert prepared["protocol"]["proposals"][0]["application"]["status"] == "PREPARED"
    confirmed = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(prepared, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id}))
    result = confirmed["document"]
    assert result["current_revision"]["revision_number"] == 2
    assert "Контроль ошибок включён" in result["current_revision"]["body_markdown"]
    assert result["protocol"]["proposals"][0]["applied"] is True
    assert result["protocol"]["proposals"][1]["decision"] == "PENDING"
    assert result["lifecycle_state"] == "REVIEW"  # The second proposal still awaits a decision.


def test_failed_preparation_preserves_acceptance_and_can_be_prepared_again(database):
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    record_confirmed_proposal(document, proposal_id)
    document, requested = _request_changes(document)
    job = BusinessDocumentJobQueue.claim("worker")
    job_completion.fail(TENANT, "worker", job.id, {"code": "MODEL_UNAVAILABLE", "message": "Model unavailable"}, job.lease_token)
    failed = document_queries.get_document(document["document_id"], AUTHOR)
    assert failed["current_revision"] == document["current_revision"]
    assert failed["protocol"]["proposals"][0]["decision"] == "ACCEPTED"
    retried = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(failed, "PREPARE_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}))
    assert retried["job_id"] != requested["job_id"]
    prepared = _complete(retried["job_id"], _plan(document, retried))
    assert prepared["current_revision"] == document["current_revision"]
    assert BusinessDocumentProposalDecision.select().count() == 1


@pytest.mark.parametrize(
    "damage,code",
    [
        ("foreign", "DOCUMENT_PERMISSION_DENIED"),
        ("reader", "DOCUMENT_PERMISSION_DENIED"),
        ("stale", "STATE_VERSION_CONFLICT"),
        ("missing", "PROPOSAL_NOT_FOUND"),
        ("rejected", "PROPOSAL_NOT_ACCEPTED"),
        ("busy", "OPERATION_IN_PROGRESS"),
        ("cycle", "PROPOSAL_NOT_FOUND"),
    ],
)
def test_accept_guards_preserve_decisions_and_text(database, damage, code):
    from api.db.db_models import BusinessDocument, BusinessDocumentProposal

    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    if damage == "rejected":
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "REJECTED"}))
        document = document_queries.get_document(document["document_id"], AUTHOR)
    if damage == "busy":
        BusinessDocument.update(operation_state="ANALYZING_REVIEW").where(BusinessDocument.id == document["document_id"]).execute()
    if damage == "cycle":
        BusinessDocumentProposal.update(review_cycle=0).where(BusinessDocumentProposal.id == proposal_id).execute()
    before = BusinessDocumentProposalDecision.select().count()
    command = _command(document, "ACCEPT_PROPOSAL", {"proposal_id": "unknown" if damage == "missing" else proposal_id}, expected=document["state_version"] + 1 if damage == "stale" else None)
    with pytest.raises(BusinessDocumentError) as error:
        document_commands.execute(TENANT, "other-author" if damage == "foreign" else AUTHOR, document["document_id"], command, access_role="READER" if damage == "reader" else "AUTHOR_CREATOR")
    assert error.value.code == code
    assert BusinessDocumentProposalDecision.select().count() == before
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 0
    assert document_queries.get_document(document["document_id"], AUTHOR)["current_revision"] == document["current_revision"]


def test_accept_rolls_back_decision_if_audit_fails(database, monkeypatch):
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]

    def unavailable(*args, **kwargs):
        raise ValidationError("AUDIT_UNAVAILABLE", "Audit unavailable")

    monkeypatch.setattr(document_writer, "append_event", unavailable)
    with pytest.raises(BusinessDocumentError) as error:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    assert error.value.code == "AUDIT_UNAVAILABLE"
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["state_version"] == document["state_version"]
    assert after["protocol"]["proposals"][0]["decision"] == "PENDING"
    assert BusinessDocumentProposalDecision.select().count() == 0


def test_evidence_only_change_creates_a_revision_even_when_markdown_is_identical(database):
    document = _document()
    record_confirmed_proposal(document, document["protocol"]["proposals"][0]["proposal_id"])
    document, requested = _request_changes(document, preview=False)
    output = _plan(document, requested)
    section = next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    operation = output["change_plan"]["operations"][0]
    operation["content"]["blocks"] = deepcopy(section["blocks"])
    job = BusinessDocumentJobQueue.claim("evidence-only-worker")
    snapshot = BusinessDocumentEvidence._normalize(job, "query", ["dataset"], [{"dataset_id": "dataset", "document_id": "source", "chunk_id": "chunk", "content": "Control requirements"}])
    BusinessDocumentEvidence._pin(job, snapshot)
    operation["evidence_refs"] = [snapshot["chunks"][0]["source_ref"]]
    result = job_completion.complete(TENANT, "evidence-only-worker", job.id, output, job.lease_token, BusinessDocumentEvidence.audit(snapshot, job.attempt))
    assert result["current_revision"]["body_markdown"] == document["current_revision"]["body_markdown"]
    assert result["current_revision"]["revision_number"] == 2
    updated = next(section for section in result["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    assert updated["evidence_refs"] == operation["evidence_refs"]
    assert BusinessDocumentRevision.select().count() == 2


def test_existing_scoped_job_keeps_its_saved_application_contract(database):
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    record_confirmed_proposal(document, proposal_id)
    document, requested = _request_changes(document, preview=False)
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    payload = {**job.payload, "apply_proposal_id": proposal_id}
    BusinessDocumentJob.update(payload=payload).where(BusinessDocumentJob.id == job.id).execute()
    result = _complete(job.id, _plan(document, requested))
    assert result["protocol"]["proposals"][0]["applied"] is True
    assert result["lifecycle_state"] == "REVIEW"
    assert result["current_revision"]["revision_number"] == 2


@pytest.mark.parametrize("open_questions", [False, True])
def test_explicit_application_saves_accepted_proposal_without_preview_confirmation(database, open_questions):
    document = _document(open_questions)
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    accepted = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    assert accepted["document"]["current_revision"] == document["current_revision"]
    assert "job_id" in accepted
    requested = accepted
    assert "preview_only" not in BusinessDocumentJob.get_by_id(requested["job_id"]).payload
    output = _plan(document, requested)
    result = _complete(requested["job_id"], output)
    assert result["current_revision"]["revision_number"] == 2
    assert "Контроль ошибок включён" in result["current_revision"]["body_markdown"]
    assert result["protocol"]["proposals"][0]["applied"] is True
    assert result["change_preview"] is None
    assert "CONFIRM_PREPARED_CHANGES" not in result["allowed_commands"]
    assert result["lifecycle_state"] == "REVIEW"
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    replay = job_completion.complete(TENANT, "worker", job.id, output)
    assert replay["current_revision"]["revision_id"] == result["current_revision"]["revision_id"]
    assert BusinessDocumentRevision.select().count() == 2
