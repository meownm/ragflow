"""Query budgets and saved-data behavior of shared document read scenarios."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from playhouse.test_utils import count_queries

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete_review_assessment,
    _create,
    _request_and_complete_draft,
    database as service_database,
)
from api.apps.business_documents.adapters.queries import load_eva_bindings
from api.apps.business_documents.runtime import document_queries
from api.apps.business_documents.runtime import document_reader
from api.apps.business_documents.runtime import document_commands
from test.unit_test.api.apps.business_documents.helpers import append_document_event
from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentEvaBinding, BusinessDocumentJob, BusinessDocumentRevision, User


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def _job(document_id, job_id, created, *, requested_by=AUTHOR):
    job = BusinessDocumentJob.create(
        id=job_id,
        document_id=document_id,
        tenant_id=TENANT,
        job_type="GENERATE_DRAFT",
        dedupe_key=job_id,
        source_state_version=1,
        payload={"requested_by_actor_id": requested_by},
        available_at=created,
        correlation_id=job_id,
        create_time=created,
    )
    # DataBaseModel.insert replaces create_time with its clock.
    BusinessDocumentJob.update(create_time=created).where(BusinessDocumentJob.id == job.id).execute()
    job.create_time = created
    return job


def test_document_page_has_a_fixed_query_budget_with_revisions_owners_jobs_and_legacy_bindings(database):
    User.create(id=AUTHOR, nickname="First", email="first@example.test")
    User.create(id="other-owner", nickname="Second", email="second@example.test")
    document_ids = []
    for number in range(100):
        document = _create(title=f"Page budget {number}")
        document_id = document["document_id"]
        document_ids.append(document_id)
        revision_id = f"page-revision-{number}"
        BusinessDocumentRevision.create(id=revision_id, document_id=document_id, revision_number=3, document_ast={}, body_markdown="", content_hash="hash", source_event_ids=[])
        with patch("api.db.db_models.current_timestamp", return_value=number + 1):
            BusinessDocument.update(current_revision_id=revision_id, owner_id=AUTHOR if number % 2 == 0 else "other-owner", tenant_id=f"tenant-{number}").where(
                BusinessDocument.id == document_id
            ).execute()
        for attempt in range(3):
            _job(document_id, f"page-job-{number:03}-{attempt}", number + 100)
        binding = {"page_url": f"https://eva.example.test/{number}"}
        if number % 2:
            BusinessDocumentEvaBinding.create(document_id=document_id, page_url_key=f"key-{number}", status="LINK_ONLY", binding=binding)
        else:
            created = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document_id) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
            BusinessDocumentEvent.update(payload={**created.payload, "eva_binding": binding}).where(BusinessDocumentEvent.id == created.id).execute()

    for page_size in (1, 10, 20, 100):
        with count_queries(only_select=True) as queries:
            result = document_queries.list_documents(AUTHOR, page_size=page_size)
        assert queries.count <= 7
        assert result["total"] == 100
        assert [row["document_id"] for row in result["items"]] == list(reversed(document_ids))[:page_size]
        for item in result["items"]:
            number = document_ids.index(item["document_id"])
            assert item["current_revision_number"] == 3
            assert item["owner_name"] == ("First" if number % 2 == 0 else "Second")
            assert item["eva_page_url"] == f"https://eva.example.test/{number}"
            assert item["latest_job"]["job_id"] == f"page-job-{number:03}-2"
            assert item["permissions"]["edit"] == (number % 2 == 0)
    assert document_queries.list_documents(AUTHOR, scope="mine")["total"] == 50


def test_binding_batch_preserves_resolution_boundary_and_prefers_saved_projection(database):
    first, second, third = (_create(title=f"Binding {number}") for number in range(3))
    page = {"page_url": "https://eva.example.test/old", "remote_version": "created"}
    for document in (first, second):
        created = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
        BusinessDocumentEvent.update(payload={**created.payload, "eva_binding": page}).where(BusinessDocumentEvent.id == created.id).execute()
        for sequence, kind, payload in (
            (2, "EvaDocumentPulled", {"remote_version": "before", "remote_content_hash": "hash-before", "review_cycle": 1}),
            (3, "EvaBindingResolved", {"eva_binding": {"page_url": "https://eva.example.test/new", "remote_version": "resolved"}}),
        ):
            append_document_event(document["document_id"], sequence, kind, "USER", AUTHOR, payload, "binding-test")
    append_document_event(first["document_id"], 4, "EvaDocumentPulled", "USER", AUTHOR, {"remote_version": "after", "remote_content_hash": "hash-after", "review_cycle": 2}, "binding-test")
    projected = {"page_url": "https://eva.example.test/projection", "remote_version": "saved"}
    BusinessDocumentEvaBinding.create(document_id=third["document_id"], page_url_key="projected", status="LINK_ONLY", binding=projected)
    with count_queries(only_select=True) as queries:
        bindings = load_eva_bindings({item["document_id"] for item in (first, second, third)})
    assert queries.count == 2
    assert bindings[first["document_id"]]["remote_version"] == "after"
    assert bindings[first["document_id"]]["last_pull_review_cycle"] == 2
    assert bindings[second["document_id"]] == {"page_url": "https://eva.example.test/new", "remote_version": "resolved"}
    assert bindings[third["document_id"]] == projected


def test_latest_job_does_not_drop_a_saved_row_without_a_timestamp(database):
    document = _create()
    job = _job(document["document_id"], "timestamp-missing", 100)
    BusinessDocumentJob.update(create_time=None).where(BusinessDocumentJob.id == job.id).execute()
    assert document_queries.list_documents(AUTHOR)["items"][0]["latest_job"]["job_id"] == job.id
    assert document_queries.get_document(document["document_id"], AUTHOR)["latest_job"]["job_id"] == job.id


def test_single_latest_job_matches_page_order_for_ties_and_missing_timestamps(database):
    document = _create()
    other = _create(title="Other job owner")
    _job(other["document_id"], "foreign-job", 1000)
    for job_id, timestamp in (("job-a", 100), ("job-b", 100), ("job-c", 99), ("job-d", None)):
        job = _job(document["document_id"], job_id, timestamp or 1)
        if timestamp is None:
            BusinessDocumentJob.update(create_time=None).where(BusinessDocumentJob.id == job.id).execute()
        single = document_reader.latest_job(document["document_id"])
        page = document_queries.list_documents(AUTHOR)
        assert single["id"] == next(row for row in page["items"] if row["document_id"] == document["document_id"])["latest_job"]["job_id"]
    assert document_reader.latest_job("missing-document") is None


def test_revision_content_read_is_scoped_to_its_document(database):
    first = _request_and_complete_draft(_create())
    second = _create(title="Another document")
    revision_id = first["current_revision"]["revision_id"]
    assert document_reader.revision(first["document_id"], revision_id)["document_ast"] == first["current_revision"]["document_ast"]
    assert document_reader.revision(second["document_id"], revision_id) is None


def test_revision_history_batches_sources_and_restores_legacy_authors_without_reordering(database):
    User.create(id=AUTHOR, nickname="Original author", email="original@example.test")
    User.create(id="legacy-author", nickname="Legacy author", email="legacy@example.test")
    document = _request_and_complete_draft(_create())
    for number in range(10):
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "text": f"Feedback {number}", "anchor": None}),
        )
        document = document_queries.get_document(document["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    source_ids = [item["source_event_id"] for item in reversed(document["protocol"]["comments"])]
    base = BusinessDocumentRevision.get_by_id(document["current_revision"]["revision_id"])
    legacy_job = _job(document["document_id"], "legacy-job", 100, requested_by="legacy-author")
    for number in range(2, 22):
        revision_id = f"history-revision-{number}"
        BusinessDocumentRevision.create(
            id=revision_id,
            document_id=document["document_id"],
            revision_number=number,
            document_ast=deepcopy(base.document_ast),
            body_markdown=base.body_markdown,
            content_hash=base.content_hash,
            source_event_ids=source_ids,
            author_id=AUTHOR if number % 2 else None,
        )
        append_document_event(document["document_id"], 100 + number, "ChangesApplied", "AI", "worker", {"revision_id": revision_id, "job_id": legacy_job.id}, "history-test")

    with count_queries(only_select=True) as queries:
        revisions = document_queries.list_revisions(document["document_id"])
    assert queries.count <= 7
    assert len(revisions) == 21
    assert [row["revision_number"] for row in revisions] == list(range(1, 22))
    for row in revisions[1:]:
        assert [item["event_id"] for item in row["change_basis"]] == source_ids
        assert [item["summary"] for item in row["change_basis"]] == [f"Feedback {number}" for number in range(9, -1, -1)]
        assert row["author_id"] == (AUTHOR if row["revision_number"] % 2 else "legacy-author")
        assert row["author_name"] == ("Original author" if row["revision_number"] % 2 else "Legacy author")
        assert all(item["actor_name"] == "Original author" for item in row["change_basis"])
