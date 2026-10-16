"""One click accepts and applies one proposal through the durable worker."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete,
    _create,
    _question_batch,
    _request_and_complete_draft,
    database as service_database,
)
from api.apps.business_documents.runtime import document_commands, document_queries, document_writer, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue
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


@pytest.mark.parametrize("open_questions", [False, True])
def test_accept_starts_application_without_assessment_or_preview_and_preserves_other_inputs(database, open_questions):
    document = _document(open_questions)
    base = deepcopy(document["current_revision"])
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": base["revision_id"],
                "section_id": None,
                "text": "Другое несогласованное замечание",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(document["document_id"], AUTHOR)
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    command = _command(document, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id})
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert requested["operation_state"] == "APPLYING_CHANGES"
    assert requested["document"]["current_revision"] == base
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], command)["idempotent_replay"] is True
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.payload["apply_proposal_id"] == proposal_id
    assert len(job.payload["active_change_input_event_ids"]) == 1
    assert not job.payload.get("preview_only")
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "ASSESS_REVIEW").count() == 0
    result = _complete(job.id, _plan(document, requested))
    assert result["current_revision"]["revision_number"] == 2
    assert "Контроль ошибок включён" in result["current_revision"]["body_markdown"]
    assert "Другое несогласованное замечание" not in result["current_revision"]["body_markdown"]
    assert result["lifecycle_state"] == "REVIEW"
    assert result["active_review_cycle"] == document["active_review_cycle"]
    assert result["protocol"]["questions"] == document["protocol"]["questions"]
    assert result["protocol"]["proposals"][0]["applied"] is True
    assert result["protocol"]["proposals"][1]["decision"] == "PENDING"
    assert result["change_preview"] is None
    replay = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert replay["idempotent_replay"] is True
    assert replay["document"]["current_revision"]["revision_number"] == 2
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 1
    with pytest.raises(BusinessDocumentError) as applied:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(result, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    assert applied.value.code == "PROPOSAL_ALREADY_APPLIED"
    analyzed = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(result, "REQUEST_REVIEW_ASSESSMENT", {}))
    analysis_job = BusinessDocumentJob.get_by_id(analyzed["job_id"])
    for key in ("revision_id", "document_ast", "body_markdown"):
        assert analysis_job.payload["current_revision"][key] == result["current_revision"][key]


def test_failed_application_can_be_retried_without_another_decision(database):
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    job = BusinessDocumentJobQueue.claim("worker-1")
    job_completion.fail(TENANT, "worker-1", job.id, {"code": "MODEL_UNAVAILABLE", "message": "Model unavailable"}, job.lease_token)
    failed = document_queries.get_document(document["document_id"], AUTHOR)
    assert failed["current_revision"] == document["current_revision"]
    assert failed["protocol"]["proposals"][0]["decision"] == "ACCEPTED"
    assert failed["protocol"]["proposals"][0]["applied"] is False
    assert "ACCEPT_PROPOSAL" in failed["allowed_commands"]
    retried = document_commands.execute(TENANT, AUTHOR, failed["document_id"], _command(failed, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    assert retried["job_id"] != requested["job_id"]
    assert BusinessDocumentProposalDecision.select().count() == 1
    result = _complete(retried["job_id"], _plan(failed, retried))
    assert result["protocol"]["proposals"][0]["applied"] is True
    assert BusinessDocumentRevision.select().count() == 2


@pytest.mark.parametrize("damage,code", [("foreign", "DOCUMENT_PERMISSION_DENIED"), ("stale", "STATE_VERSION_CONFLICT"), ("missing", "PROPOSAL_NOT_FOUND"), ("rejected", "PROPOSAL_NOT_ACCEPTED")])
def test_accept_rejects_unauthorized_stale_unknown_and_rejected_inputs(database, damage, code):
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    if damage == "rejected":
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "REJECTED"}))
        document = document_queries.get_document(document["document_id"], AUTHOR)
    command = _command(document, "ACCEPT_PROPOSAL", {"proposal_id": "unknown" if damage == "missing" else proposal_id}, expected=document["state_version"] + 1 if damage == "stale" else None)
    with pytest.raises(BusinessDocumentError) as error:
        document_commands.execute(TENANT, "other-author" if damage == "foreign" else AUTHOR, document["document_id"], command)
    assert error.value.code == code
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 0
    assert document_queries.get_document(document["document_id"], AUTHOR)["current_revision"] == document["current_revision"]


def test_accept_rolls_back_the_decision_when_queueing_fails(database, monkeypatch):
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]

    def unavailable(**values):
        raise ValidationError("QUEUE_UNAVAILABLE", "Queue unavailable")

    monkeypatch.setattr(document_writer, "insert_job", unavailable)
    with pytest.raises(BusinessDocumentError) as error:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    assert error.value.code == "QUEUE_UNAVAILABLE"
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["state_version"] == document["state_version"]
    assert after["protocol"]["proposals"][0]["decision"] == "PENDING"
    assert BusinessDocumentProposalDecision.select().count() == 0


def test_worker_cannot_apply_another_accepted_proposal_in_the_same_pass(database):
    document = _document()
    other_id = document["protocol"]["proposals"][1]["proposal_id"]
    other = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": other_id, "decision": "ACCEPTED"}))
    document = document_queries.get_document(document["document_id"], AUTHOR)
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "ACCEPT_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"]}))
    output = _plan(document, requested)
    output["change_plan"]["operations"][0]["source_event_ids"] = [other["event_id"]]
    job = BusinessDocumentJobQueue.claim("worker-1")
    with pytest.raises(BusinessDocumentError) as error:
        job_completion.complete(TENANT, "worker-1", job.id, output, job.lease_token)
    assert error.value.code == "CHANGE_SOURCE_NOT_ACTIVE"
    assert BusinessDocumentRevision.select().count() == 1
    assert not BusinessDocumentEvent.select().where(BusinessDocumentEvent.event_type == "ChangesApplied").exists()
