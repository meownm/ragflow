"""Durable admission, feedback isolation and automatic per-proposal application."""

import pytest

from test.unit_test.api.apps.business_documents.test_accept_proposal import _document, _plan
from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, TENANT, _command, _complete, database as service_database
from api.apps.business_documents.runtime import document_commands, document_queries, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocument, BusinessDocumentJob, BusinessDocumentWorkRequest, BusinessDocumentRevision, BusinessDocumentProposalDecision
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def execute(document, kind, payload=None):
    return document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, kind, payload))


def accept(document, index=0):
    return execute(document, "ACCEPT_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][index]["proposal_id"]})


def comment(document, revision_id=None):
    return execute(document, "ADD_COMMENT", {"revision_id": revision_id or document["current_revision"]["revision_id"], "section_id": None, "text": "Позднее уточнение", "anchor": None})


def test_two_applications_are_serial_and_use_latest_revision_with_late_feedback(database):
    original = _document()
    first = accept(original)
    command = _command(first["document"], "ACCEPT_PROPOSAL", {"proposal_id": original["protocol"]["proposals"][1]["proposal_id"]})
    second = document_commands.execute(TENANT, AUTHOR, original["document_id"], command)
    replay = document_commands.execute(TENANT, AUTHOR, original["document_id"], command)
    assert replay["idempotent_replay"] is True
    assert [item["status"] for item in second["document"]["background_work"]] == ["RUNNING", "QUEUED"]
    late = comment(second["document"])["document"]
    job = BusinessDocumentJob.get_by_id(first["job_id"])
    assert len(job.payload["active_change_input_event_ids"]) == 1
    assert len(job.payload["protocol"]["comments"]) == 0
    changed = _complete(job.id, _plan(original, first))
    assert changed["current_revision"]["revision_number"] == 2
    assert len(changed["protocol"]["comments"]) == 1
    assert changed["protocol"]["proposals"][0]["applied"] is True
    next_request = document_commands.advance_work(original["document_id"])
    next_job = BusinessDocumentJob.get_by_id(next_request["job_id"])
    assert next_job.base_revision_id == changed["current_revision"]["revision_id"]
    assert next_job.payload["apply_proposal_id"] == original["protocol"]["proposals"][1]["proposal_id"]
    final = _complete(next_job.id, _plan(changed, next_request, "Контроль и порог предупреждения включены"))
    assert final["current_revision"]["revision_number"] == 3
    assert all(proposal["applied"] for proposal in final["protocol"]["proposals"])
    assert final["protocol"]["comments"][0]["revision_id"] == late["current_revision"]["revision_id"]
    assert final["background_work"] == []
    assert document_commands.advance_work(original["document_id"]) is None


def test_analysis_discards_old_result_and_coalesces_new_feedback(database):
    document = _document(False)
    request = execute(document, "REQUEST_REVIEW_ASSESSMENT")
    current = comment(request["document"])["document"]
    current = comment(current)["document"]
    assert len(current["background_work"]) == 2
    # Even an invalid old result cannot publish entities over newer feedback.
    finished = _complete(request["job_id"], {"questions": [{"text": "Stale question"}]})
    assert finished["last_analysis"]["superseded"] is True
    assert len(finished["protocol"]["comments"]) == 2
    assert finished["protocol"]["questions"] == []
    next_request = document_commands.advance_work(document["document_id"])
    snapshot = BusinessDocumentJob.get_by_id(next_request["job_id"]).payload
    assert len(snapshot["protocol"]["comments"]) == 2
    sources = [item["source_event_id"] for item in snapshot["protocol"]["comments"]]
    result = _complete(
        next_request["job_id"], {"schema_version": "1", "questions": [], "proposals": [], "comment_dispositions": [{"comment_event_id": source, "disposition": "NO_CHANGE"} for source in sources]}
    )
    assert result["last_analysis"]["applied"] is True
    assert result["last_analysis"]["proposal_count"] == 0
    assert "execution" not in result["last_analysis"]
    updated = comment(result)["document"]
    assert updated["last_analysis"]["superseded"] is True


def test_cancel_queued_proposal_preserves_running_proposal(database):
    document = _document()
    first = accept(document)
    second = accept(first["document"], 1)
    proposal_id = document["protocol"]["proposals"][1]["proposal_id"]
    cancelled = execute(second["document"], "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id})["document"]
    assert cancelled["protocol"]["proposals"][1]["decision"] == "PENDING"
    with pytest.raises(BusinessDocumentError, match="being applied"):
        execute(cancelled, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"]})
    _complete(first["job_id"], _plan(document, first))
    assert document_commands.advance_work(document["document_id"]) is None


def test_failure_does_not_block_other_work_and_retry_preserves_decision(database):
    document = _document()
    first = accept(document)
    accept(first["document"], 1)
    job = BusinessDocumentJobQueue.claim("failed-worker")
    failed = job_completion.fail(TENANT, "failed-worker", job.id, {"code": "MODEL_UNAVAILABLE", "message": "Unavailable"}, job.lease_token)
    assert failed["protocol"]["proposals"][0]["application"]["status"] == "DEAD"
    next_request = document_commands.advance_work(document["document_id"])
    changed = _complete(next_request["job_id"], _plan(document, next_request))
    retried = accept(changed)
    assert retried["job_id"] != first["job_id"]
    assert BusinessDocumentProposalDecision.select().count() == 2
    result = _complete(retried["job_id"], _plan(changed, retried, "Первая правка после повтора"))
    assert all(item["applied"] for item in result["protocol"]["proposals"])
    assert BusinessDocumentWorkRequest.select().where(BusinessDocumentWorkRequest.status == "FAILED").count() == 0


def test_comment_on_prior_saved_revision_survives_automatic_save(database):
    document = _document()
    first = accept(document)
    changed = _complete(first["job_id"], _plan(document, first))
    revision_id = document["current_revision"]["revision_id"]
    text = document["current_revision"]["section_texts"]["5.5"]
    anchor = {"revision_id": revision_id, "section_id": "5.5", "selected_text": text[:8], "prefix": "", "suffix": text[8:72], "start_offset": 0, "end_offset": 8}
    result = execute(changed, "ADD_COMMENT", {"revision_id": revision_id, "section_id": "5.5", "text": "Комментарий к прежней цитате", "anchor": anchor})["document"]
    assert result["protocol"]["comments"][0]["revision_id"] == document["current_revision"]["revision_id"]
    assert result["protocol"]["comments"][0]["anchor"] == anchor
    assert result["protocol"]["comments"][0]["anchor_status"] == "ORPHANED"
    assert BusinessDocumentRevision.select().count() == 2


def test_real_worker_verifies_and_saves_automatic_application(database):
    from api.apps.business_documents.ai import BusinessDocumentAI
    from api.apps.business_documents.worker import BusinessDocumentWorker
    from test.unit_test.api.apps.business_documents.test_structured_changes import PlanAdapter

    document = _document()
    requested = accept(document)
    adapter = PlanAdapter(_plan(document, requested)["change_plan"], None)
    generate = adapter.generate

    def inspect_stage(tenant_id, system, payload, **kwargs):
        if payload["job_input"]["task_type"] == "VERIFY_REQUIREMENT_COVERAGE":
            assert BusinessDocumentJob.get_by_id(requested["job_id"]).progress_stage == "VALIDATING"
        return generate(tenant_id, system, payload, **kwargs)

    adapter.generate = inspect_stage
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    current = document_queries.get_document(document["document_id"], AUTHOR)
    assert current["protocol"]["proposals"][0]["applied"] is True
    assert [item["job_input"]["task_type"] for item in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]


def test_cancelling_failed_application_clears_its_work_warning(database):
    original = _document()
    accept(original)
    job = BusinessDocumentJobQueue.claim("worker")
    failed = job_completion.fail(TENANT, "worker", job.id, {"code": "MODEL_UNAVAILABLE", "message": "Unavailable"}, job.lease_token)
    result = execute(failed, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": original["protocol"]["proposals"][0]["proposal_id"]})["document"]
    assert result["background_work"] == []
    assert result["protocol"]["proposals"][0]["decision"] == "PENDING"


def test_queued_work_blocks_structural_commands_but_allows_feedback(database):
    document = _document()
    first = accept(document)
    accept(first["document"], 1)
    current = _complete(first["job_id"], _plan(document, first))
    assert current["operation_state"] == "IDLE"
    assert "ARCHIVE" not in current["allowed_commands"]
    with pytest.raises(BusinessDocumentError) as blocked:
        execute(current, "ARCHIVE")
    assert blocked.value.code == "OPERATION_IN_PROGRESS"
    current = comment(current)["document"]
    assert len(current["protocol"]["comments"]) == 1


@pytest.mark.parametrize("field,value", [("owner_id", "another"), ("current_revision_id", "other"), ("active_review_cycle", 2), ("active_job_id", "other")])
def test_background_result_still_fences_ownership_content_cycle_and_job(database, field, value):
    document = _document()
    first = accept(document)
    job = BusinessDocumentJobQueue.claim("worker")
    BusinessDocument.update(**{field: value}).where(BusinessDocument.id == document["document_id"]).execute()
    with pytest.raises(BusinessDocumentError) as error:
        job_completion.complete(TENANT, "worker", job.id, _plan(document, first), job.lease_token)
    assert error.value.code == "STALE_AI_RESULT"
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.parametrize("previous_status", ["FAILED", "CANCELLED"])
def test_new_owner_retries_failed_application_with_current_authorization(database, previous_status):
    from api.apps.business_documents.adapters.assignment import assign_business_document
    from api.db.db_models import User

    original = _document()
    first = accept(original)
    job = BusinessDocumentJobQueue.claim("worker")
    failed = job_completion.fail(TENANT, "worker", job.id, {"code": "MODEL_UNAVAILABLE", "message": "Unavailable"}, job.lease_token)
    # CANCELLED also covers requests left behind by a rejected old-owner snapshot.
    BusinessDocumentWorkRequest.update(status=previous_status).where(BusinessDocumentWorkRequest.job_id == job.id).execute()
    User.create(id="new-owner", nickname="New owner", email="new-owner@example.test")
    assigned = assign_business_document("moderator", original["document_id"], {"owner_id": "new-owner", "expected_state_version": failed["state_version"]}, access_role="EXTENDED_MODERATOR")
    proposal_id = original["protocol"]["proposals"][0]["proposal_id"]
    command = _command(assigned, "ACCEPT_PROPOSAL", {"proposal_id": proposal_id})
    with pytest.raises(BusinessDocumentError) as denied:
        document_commands.execute(TENANT, AUTHOR, original["document_id"], command)
    assert denied.value.code == "DOCUMENT_PERMISSION_DENIED"
    result = document_commands.execute(TENANT, "new-owner", original["document_id"], command)
    assert result["job_id"] != first["job_id"]
    retried = BusinessDocumentJob.get_by_id(result["job_id"])
    work = BusinessDocumentWorkRequest.get_by_id(result["document"]["background_work"][0]["request_id"])
    assert work.payload["owner_id"] == work.actor_id == "new-owner"
    assert retried.payload["owner_id"] == retried.payload["requested_by_actor_id"] == "new-owner"
    replay = document_commands.execute(TENANT, "new-owner", original["document_id"], command)
    assert replay["idempotent_replay"] is True and replay["job_id"] == result["job_id"]
    assert BusinessDocumentWorkRequest.select().count() == 1
    assert BusinessDocumentProposalDecision.select().count() == 1
    assert BusinessDocumentProposalDecision.get().actor_id == AUTHOR
    applied = _complete(retried.id, _plan(assigned, result))
    assert applied["protocol"]["proposals"][0]["applied"] is True
    assert BusinessDocumentRevision.get_by_id(applied["current_revision"]["revision_id"]).author_id == "new-owner"


def test_unapplied_analysis_is_superseded_only_by_later_feedback(database):
    document = comment(_document(False))["document"]
    requested = execute(document, "REQUEST_REVIEW_ASSESSMENT")
    result = _complete(requested["job_id"], {"unapplied_assessment": "No usable assessment", "warnings": []})
    assert result["last_analysis"]["applied"] is False
    assert result["last_analysis"]["superseded"] is False
    assert result["background_work"][0]["status"] == "FAILED"
    assert result["background_work"][0]["error"]["code"] == "RESULT_NOT_APPLIED"
    assert comment(result)["document"]["last_analysis"]["superseded"] is True
