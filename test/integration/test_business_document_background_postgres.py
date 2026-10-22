"""Independent connections exercise background admission and completion races."""

import pytest

from test.integration.test_business_document_postgres import _parallel, postgres_database as postgres_fixture
from test.unit_test.api.apps.business_documents import test_background_work as cases
from api.apps.business_documents.runtime import document_commands, document_queries, job_completion
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentJob, BusinessDocumentWorkRequest
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def postgres_database():
    yield from postgres_fixture.__wrapped__()


@pytest.mark.parametrize(
    "case",
    [
        "test_two_applications_are_serial_and_use_latest_revision_with_late_feedback",
        "test_analysis_discards_old_result_and_coalesces_new_feedback",
        "test_cancel_queued_proposal_preserves_running_proposal",
        "test_failure_does_not_block_other_work_and_retry_preserves_decision",
        "test_comment_on_prior_saved_revision_survives_automatic_save",
    ],
)
def test_background_scenario(postgres_database, case):
    getattr(cases, case)(postgres_database)


def test_competing_workers_admit_one_queued_request(postgres_database):
    original = cases._document()
    first = cases.accept(original)
    cases.accept(first["document"], 1)
    cases._complete(first["job_id"], cases._plan(original, first))
    outcomes = _parallel(postgres_database, lambda _: document_commands.advance_work(original["document_id"]))
    assert sum(isinstance(item, dict) and "job_id" in item for item in outcomes) == 1
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.status == "PENDING").count() == 1
    assert BusinessDocumentWorkRequest.select().where(BusinessDocumentWorkRequest.status == "RUNNING").count() == 1


def test_feedback_and_save_are_atomic_on_separate_connections(postgres_database):
    original = cases._document()
    first = cases.accept(original)
    job = BusinessDocumentJobQueue.claim("worker")
    outcomes = _parallel(
        postgres_database, lambda index: cases.comment(first["document"]) if index == 0 else job_completion.complete(cases.TENANT, "worker", job.id, cases._plan(original, first), job.lease_token)
    )
    current = document_queries.get_document(original["document_id"], cases.AUTHOR)
    assert current["current_revision"]["revision_number"] == 2
    assert not isinstance(outcomes[1], BusinessDocumentError)
    if isinstance(outcomes[0], BusinessDocumentError):
        assert outcomes[0].code == "STATE_VERSION_CONFLICT"
        current = cases.comment(current, original["current_revision"]["revision_id"])["document"]
    assert len(current["protocol"]["comments"]) == 1
    assert current["protocol"]["comments"][0]["revision_id"] == original["current_revision"]["revision_id"]


@pytest.mark.parametrize("previous_status", ["FAILED", "CANCELLED"])
def test_new_owner_retry_on_postgres(postgres_database, previous_status):
    cases.test_new_owner_retries_failed_application_with_current_authorization(postgres_database, previous_status)


def test_unapplied_analysis_status_on_postgres(postgres_database):
    cases.test_unapplied_analysis_is_superseded_only_by_later_feedback(postgres_database)
