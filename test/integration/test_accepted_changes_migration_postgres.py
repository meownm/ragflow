"""Upgrade enqueue and recovery on isolated PostgreSQL schemas/connections."""

import pytest

from test.integration.test_business_document_postgres import _parallel, postgres_database as postgres_fixture
from test.unit_test.api.apps.business_documents import test_accepted_changes_migration as cases
from api.apps.business_documents import migrations


@pytest.fixture()
def postgres_database():
    yield from postgres_fixture.__wrapped__()


@pytest.mark.parametrize(
    "case_name",
    [
        "test_migration_applies_each_confirmed_proposal_through_existing_worker",
        "test_busy_document_is_resumed_after_existing_job_finishes",
        "test_cancel_and_reaccept_does_not_replace_frozen_authorization",
        "test_existing_application_finishes_without_a_second_migrated_job",
        "test_prepared_preview_is_rebuilt_from_the_current_revision",
    ],
)
def test_upgrade_scenarios_on_postgres(postgres_database, case_name):
    getattr(cases, case_name)(postgres_database)


def test_interrupted_upgrade_dispatch_on_postgres(postgres_database, monkeypatch):
    cases.test_dispatch_failure_rolls_back_job_but_retains_original_cohort(postgres_database, monkeypatch)


def test_competing_upgrade_workers_enqueue_a_decision_once(postgres_database):
    cases.accept(cases._document())
    results = _parallel(postgres_database, lambda _: migrations.resume_accepted_changes())
    assert sorted(results) == [0, 1]
    assert cases.state().finished is True and len(cases.migrated_jobs()) == 1
