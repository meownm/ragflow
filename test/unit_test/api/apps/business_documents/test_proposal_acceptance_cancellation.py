"""Cancellation preserves audit history and revokes unapplied change authority."""

import pytest

from test.unit_test.api.apps.business_documents.test_accept_proposal import _document, _plan, _request_changes
from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, TENANT, _command, _complete, _complete_review_assessment, database as service_database
from api.apps.business_documents.runtime import document_commands, document_queries, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentEvent, BusinessDocumentJob, BusinessDocumentProposalDecision
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def _accepted():
    document = _document()
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    result = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "ACCEPTED"}))
    return document_queries.get_document(document["document_id"], AUTHOR), proposal_id, result["event_id"]


def test_cancel_and_reaccept_preserves_history_and_excludes_cancelled_input(database):
    document, proposal_id, old_event = _accepted()
    base = document["current_revision"]
    assert document["protocol"]["proposals"][0]["application"]["status"] == "WAITING"
    command = _command(document, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id})
    cancelled = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], command)["idempotent_replay"]
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["current_revision"] == base
    assert after["protocol"]["proposals"][0]["decision"] == "PENDING"
    assert after["protocol"]["proposals"][0]["application"] is None
    assert not after["protocol"]["proposals"][0]["applied"]
    assert not BusinessDocumentProposalDecision.select().exists()
    event = BusinessDocumentEvent.get_by_id(cancelled["event_id"])
    assert event.event_type == "ProposalAcceptanceCancelled"
    assert event.payload["decision_event_id"] == old_event
    assert BusinessDocumentEvent.get_by_id(old_event).payload["decision"] == "ACCEPTED"
    review = document_commands._writer.review(document["document_id"], document["active_review_cycle"])
    assert old_event not in review.active_inputs | review.accepted_inputs
    assert review.has_unassessed_feedback
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(after, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    after, requested = _request_changes(after, preview=False)
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert old_event not in job.payload["active_change_input_event_ids"]
    result = _complete(job.id, _plan(after, requested))
    assert result["protocol"]["proposals"][0]["applied"]


def test_cancelled_proposal_can_be_rejected(database):
    document, proposal_id, _ = _accepted()
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id}))
    document = document_queries.get_document(document["document_id"], AUTHOR)
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "REJECTED"}))
    assert document_queries.get_document(document["document_id"], AUTHOR)["protocol"]["proposals"][0]["decision"] == "REJECTED"


def test_cancel_invalidates_prepared_changes_and_old_authorizing_source(database):
    from business_documents.domain.change_plan import validate_change_inputs
    from business_documents.domain.errors import RuleViolation

    document, proposal_id, old_event = _accepted()
    document = _complete_review_assessment(document)
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}))
    plan = _plan(document, requested)
    prepared = _complete(requested["job_id"], plan)
    assert prepared["protocol"]["proposals"][0]["application"]["status"] == "PREPARED"
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(prepared, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id}))
    cancelled = document_queries.get_document(document["document_id"], AUTHOR)
    assert cancelled["change_preview"] is None
    with pytest.raises(BusinessDocumentError) as stale:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(cancelled, "CONFIRM_PREPARED_CHANGES", {"job_id": requested["job_id"]}))
    assert stale.value.code == "CHANGE_PREVIEW_STALE"
    review = document_commands._writer.review(document["document_id"], document["active_review_cycle"])
    with pytest.raises(RuleViolation) as revoked:
        validate_change_inputs(review, plan["change_plan"], snapshot_source_ids=frozenset({old_event}), evidence_source_refs=frozenset())
    assert revoked.value.code == "CHANGE_SOURCE_NOT_ACTIVE"


def test_cancel_rolls_back_if_audit_write_fails(database, monkeypatch):
    document, proposal_id, _ = _accepted()

    def fail(*args, **kwargs):
        raise BusinessDocumentError("AUDIT_UNAVAILABLE", "Audit unavailable", 503)

    monkeypatch.setattr(document_commands._writer, "append_event", fail)
    with pytest.raises(BusinessDocumentError):
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id}))
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["state_version"] == document["state_version"]
    assert after["protocol"]["proposals"][0]["decision"] == "ACCEPTED"


def test_cancel_rejects_active_job_even_if_operation_state_drifted(database):
    from api.db.db_models import BusinessDocument

    document, proposal_id, _ = _accepted()
    _request_changes(document)
    BusinessDocument.update(operation_state="FAILED").where(BusinessDocument.id == document["document_id"]).execute()
    document = document_queries.get_document(document["document_id"], AUTHOR)
    with pytest.raises(BusinessDocumentError) as busy:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id}))
    assert busy.value.code == "OPERATION_IN_PROGRESS"


@pytest.mark.parametrize(
    "damage,code",
    [
        ("foreign", "DOCUMENT_PERMISSION_DENIED"),
        ("reader", "DOCUMENT_PERMISSION_DENIED"),
        ("stale", "STATE_VERSION_CONFLICT"),
        ("unknown", "PROPOSAL_NOT_FOUND"),
        ("pending", "PROPOSAL_NOT_ACCEPTED"),
        ("busy", "OPERATION_IN_PROGRESS"),
        ("applied", "PROPOSAL_ALREADY_APPLIED"),
    ],
)
def test_cancel_guards_preserve_current_decision_and_text(database, damage, code):
    document, proposal_id, _ = _accepted()
    if damage in {"busy", "applied"}:
        document, requested = _request_changes(document, preview=False)
        if damage == "applied":
            _complete(requested["job_id"], _plan(document, requested))
        document = document_queries.get_document(document["document_id"], AUTHOR)
    target = "unknown" if damage == "unknown" else document["protocol"]["proposals"][1]["proposal_id"] if damage == "pending" else proposal_id
    command = _command(document, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": target}, expected=document["state_version"] + 1 if damage == "stale" else None)
    with pytest.raises(BusinessDocumentError) as rejected:
        document_commands.execute(TENANT, "other-author" if damage == "foreign" else AUTHOR, document["document_id"], command, access_role="READER" if damage == "reader" else "AUTHOR_CREATOR")
    assert rejected.value.code == code
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["current_revision"] == document["current_revision"]
    assert after["protocol"]["proposals"][0]["decision"] == "ACCEPTED"
    assert not BusinessDocumentEvent.select().where(BusinessDocumentEvent.event_type == "ProposalAcceptanceCancelled").exists()


def test_failed_job_reason_survives_unrelated_job_and_clears_on_reacceptance(database):
    document, proposal_id, _ = _accepted()
    document, requested = _request_changes(document, preview=False)
    queued = document_queries.get_document(document["document_id"], AUTHOR)["protocol"]["proposals"][0]
    assert queued["application"]["status"] == "PENDING"
    job = BusinessDocumentJobQueue.claim("worker")
    assert document_queries.get_document(document["document_id"], AUTHOR)["protocol"]["proposals"][0]["application"]["status"] == "RUNNING"
    job_completion.fail(TENANT, "worker", job.id, {"code": "MODEL_UNAVAILABLE", "message": "Причина ошибки"}, job.lease_token)
    failed = document_queries.get_document(document["document_id"], AUTHOR)
    proposal = failed["protocol"]["proposals"][0]
    assert proposal["application"]["error"]["message"] == "Причина ошибки"
    assert proposal["application"]["status"] == "DEAD"
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(failed, "ADD_COMMENT", {"revision_id": failed["current_revision"]["revision_id"], "section_id": None, "anchor": None, "text": "Другое замечание для анализа"}),
    )
    failed = document_queries.get_document(document["document_id"], AUTHOR)
    unrelated = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(failed, "REQUEST_REVIEW_ASSESSMENT", {}))
    view = document_queries.get_document(document["document_id"], AUTHOR)
    assert view["latest_job"]["job_id"] == unrelated["job_id"]
    assert view["protocol"]["proposals"][0]["application"] == {"status": "DEAD", "error": proposal["application"]["error"]}
    job = BusinessDocumentJobQueue.claim("analysis")
    job_completion.fail(TENANT, "analysis", job.id, {"code": "ANALYSIS_FAILED", "message": "Другая ошибка"}, job.lease_token)
    view = document_queries.get_document(document["document_id"], AUTHOR)
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(view, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id}))
    view = document_queries.get_document(document["document_id"], AUTHOR)
    result = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(view, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id}))
    assert result["document"]["protocol"]["proposals"][0]["application"] == {"status": "WAITING", "error": None}
