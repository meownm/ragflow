"""Upgrade cohort, atomic enqueue and real worker completion on disposable data."""

import pytest

from test.unit_test.api.apps.business_documents.test_accept_proposal import _document, _plan, _request_changes
from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete,
    database as service_database,
)
from api.apps.business_documents import migrations, worker as worker_module
from api.apps.business_documents.runtime import document_commands, document_queries, document_writer
from api.db.db_models import BusinessDocument, BusinessDocumentAcceptedChangesMigration, BusinessDocumentEvent, BusinessDocumentJob


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def accept(document, index=0):
    from test.unit_test.api.apps.business_documents.helpers import record_confirmed_proposal

    return record_confirmed_proposal(document, document["protocol"]["proposals"][index]["proposal_id"])["document"]


def assessment(document):
    return document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))


def finish_assessment(requested):
    return _complete(requested["job_id"], {"schema_version": "1", "questions": [], "proposals": [], "comment_dispositions": []})


def migrated_jobs():
    return [
        job
        for job in BusinessDocumentJob.select().order_by(BusinessDocumentJob.create_time, BusinessDocumentJob.id)
        if job.payload.get("command_payload", {}).get("migration_id") == migrations.MIGRATION_ID
    ]


def state():
    return BusinessDocumentAcceptedChangesMigration.get_by_id(migrations.MIGRATION_ID)


def test_migration_applies_each_confirmed_proposal_through_existing_worker(database):
    document = accept(_document())
    document = accept(document, 1)
    # A free-text comment is not an accepted proposal and must not enter this job.
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": document["current_revision"]["revision_id"],
                "section_id": None,
                "text": "Ещё не подтверждённая правка",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(document["document_id"], AUTHOR)
    assert migrations.resume_accepted_changes() == 1
    job = migrated_jobs()[0]
    first_event = document["protocol"]["proposals"][0]["decision_event_id"]
    assert job.job_type == "PLAN_CHANGES" and job.status == "PENDING"
    assert job.payload["apply_proposal_id"] == document["protocol"]["proposals"][0]["proposal_id"]
    assert job.payload["active_change_input_event_ids"] == [first_event]
    assert all(not proposal["applied"] for proposal in job.payload["protocol"]["proposals"])
    assert job.payload["requested_by_actor_id"] == AUTHOR and not job.payload.get("preview_only")
    assert len(state().pending) == 1 and state().finished is False
    assert migrations.resume_accepted_changes() == 0
    request = {"job_id": job.id, "state_version": job.source_state_version}
    current = _complete(job.id, _plan(document, request))
    assert current["protocol"]["proposals"][0]["applied"] is True
    assert current["protocol"]["proposals"][1]["applied"] is False
    assert current["current_revision"]["revision_number"] == 2
    assert current["lifecycle_state"] == "REVIEW"
    assert migrations.resume_accepted_changes() == 1
    next_job = next(job for job in migrated_jobs() if job.status == "PENDING")
    assert next_job.base_revision_id == current["current_revision"]["revision_id"]
    applied = _complete(next_job.id, _plan(current, {"job_id": next_job.id, "state_version": next_job.source_state_version}, "Порог предупреждения установлен"))
    assert all(item["applied"] for item in applied["protocol"]["proposals"])
    assert applied["protocol"]["comments"][0]["disposition"] is None
    assert state().finished is True and state().pending == []
    assert migrations.resume_accepted_changes() == 0 and len(migrated_jobs()) == 2
    events = BusinessDocumentEvent.select().where(BusinessDocumentEvent.event_type == "BusinessDocumentJobRequested")
    migrated = [event for event in events if event.payload.get("migration_id") == migrations.MIGRATION_ID]
    assert len(migrated) == 2 and all(event.actor_type == "SYSTEM" for event in migrated)


@pytest.mark.parametrize("rejected", [False, True])
def test_empty_cohort_is_finished_once_and_does_not_enroll_future_acceptances(database, rejected):
    document = _document()
    if rejected:
        document = document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "DECIDE_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"], "decision": "REJECTED"}),
        )["document"]
    assert migrations.resume_accepted_changes() == 0
    assert state().finished is True
    accept(document, 1)
    assert migrations.resume_accepted_changes() == 0 and migrated_jobs() == []


def test_busy_document_is_resumed_after_existing_job_finishes(database):
    document = accept(_document())
    requested = assessment(document)
    assert migrations.resume_accepted_changes() == 0 and len(state().pending) == 1
    assert migrations.resume_accepted_changes() == 0 and migrated_jobs() == []
    current = finish_assessment(requested)
    assert migrations.resume_accepted_changes() == 1
    assert migrated_jobs()[0].base_revision_id == current["current_revision"]["revision_id"]
    assert state().finished is True


def test_cancel_and_reaccept_does_not_replace_frozen_authorization(database):
    document = accept(_document())
    requested = assessment(document)
    assert migrations.resume_accepted_changes() == 0
    document = finish_assessment(requested)
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "CANCEL_PROPOSAL_ACCEPTANCE", {"proposal_id": proposal_id}))
    accept(document_queries.get_document(document["document_id"], AUTHOR))
    assert migrations.resume_accepted_changes() == 0
    assert state().finished is True and migrated_jobs() == []


@pytest.mark.parametrize("damage", ["owner", "tenant", "cycle", "archived", "missing"])
def test_frozen_target_is_revalidated_under_document_lock(database, damage):
    document = accept(_document())
    requested = assessment(document)
    assert migrations.resume_accepted_changes() == 0
    finish_assessment(requested)
    updates = {"owner": {"owner_id": "new-owner"}, "tenant": {"tenant_id": "another-tenant"}, "cycle": {"active_review_cycle": 2}, "archived": {"lifecycle_state": "ARCHIVED"}}
    if damage == "missing":
        BusinessDocument.delete().where(BusinessDocument.id == document["document_id"]).execute()
    else:
        BusinessDocument.update(**updates[damage]).where(BusinessDocument.id == document["document_id"]).execute()
    assert migrations.resume_accepted_changes() == 0
    assert state().finished is True and migrated_jobs() == []


def test_active_job_is_not_duplicated_even_if_document_flag_is_idle(database):
    document = accept(_document())
    assessment(document)
    BusinessDocument.update(operation_state="IDLE").where(BusinessDocument.id == document["document_id"]).execute()
    assert migrations.resume_accepted_changes() == 0
    assert state().finished is False and migrated_jobs() == []


def test_dispatch_failure_rolls_back_job_but_retains_original_cohort(database, monkeypatch):
    document = accept(_document())
    original = document_writer.insert_job

    def fail_after_insert(**values):
        original(**values)
        raise RuntimeError("interrupted dispatch")

    with monkeypatch.context() as patch:
        patch.setattr(document_writer, "insert_job", fail_after_insert)
        with pytest.raises(RuntimeError, match="interrupted dispatch"):
            migrations.resume_accepted_changes()
    assert migrated_jobs() == [] and len(state().pending) == 1
    current = document_queries.get_document(document["document_id"], AUTHOR)
    assert current["operation_state"] == "IDLE" and current["state_version"] == document["state_version"]
    accept(current, 1)
    assert migrations.resume_accepted_changes() == 1
    assert state().finished is True and len(migrated_jobs()) == 1
    assert migrations.resume_accepted_changes() == 0


def test_migration_never_restarts_a_terminal_migrated_job(database):
    document = accept(_document())
    assert migrations.resume_accepted_changes() == 1
    job = migrated_jobs()[0]
    BusinessDocumentJob.update(status="DEAD").where(BusinessDocumentJob.id == job.id).execute()
    BusinessDocument.update(operation_state="FAILED").where(BusinessDocument.id == document["document_id"]).execute()
    assert migrations.resume_accepted_changes() == 0 and len(migrated_jobs()) == 1


def test_already_applied_acceptance_is_excluded_from_initial_cohort(database):
    document, requested = _request_changes(accept(_document()), preview=False)
    _complete(requested["job_id"], _plan(document, requested))
    assert migrations.resume_accepted_changes() == 0
    assert state().finished is True and migrated_jobs() == []


def test_existing_application_finishes_without_a_second_migrated_job(database):
    document, requested = _request_changes(accept(_document()), preview=False)
    assert migrations.resume_accepted_changes() == 0 and len(state().pending) == 1
    _complete(requested["job_id"], _plan(document, requested))
    assert migrations.resume_accepted_changes() == 0
    assert state().finished is True and migrated_jobs() == []


def test_prepared_preview_is_rebuilt_from_the_current_revision(database):
    document, requested = _request_changes(accept(_document()), preview=True)
    prepared = _complete(requested["job_id"], _plan(document, requested, "Прежнее превью"))
    assert prepared["protocol"]["proposals"][0]["application"]["status"] == "PREPARED"
    assert migrations.resume_accepted_changes() == 1
    job = migrated_jobs()[0]
    assert job.id != requested["job_id"] and not job.payload.get("preview_only")
    assert job.base_revision_id == prepared["current_revision"]["revision_id"]
    result = _complete(job.id, _plan(prepared, {"job_id": job.id, "state_version": job.source_state_version}, "Новая проверенная правка"))
    assert "Новая проверенная правка" in result["current_revision"]["body_markdown"]
    assert "Прежнее превью" not in result["current_revision"]["body_markdown"]


def test_worker_maintenance_runs_the_upgrade_without_manual_commands(database, monkeypatch):
    accept(_document())
    worker = worker_module.BusinessDocumentWorker()
    monkeypatch.setattr(worker, "recover_stale", lambda: (0, 0))
    monkeypatch.setattr(worker, "reconcile_exports", lambda: {"cleaned": 0})
    monkeypatch.setattr(worker_module, "expire_due_results", lambda: 0)
    assert worker._maintain() is True
    assert worker._maintain() is False
    assert len(migrated_jobs()) == 1
