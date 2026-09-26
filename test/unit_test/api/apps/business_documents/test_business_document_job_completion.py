"""Batch query cost, immutable identities and atomic result publication."""

from copy import deepcopy

import pytest
from peewee import IntegrityError
from playhouse.test_utils import count_queries

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete,
    _create,
    _request_and_complete_draft,
    database as service_database,
)
from api.apps.business_documents.runtime import document_queries, document_writer, job_completion
from api.apps.business_documents.runtime import document_commands
from test.unit_test.api.apps.business_documents.helpers import append_document_event
from api.db.db_models import BusinessDocument, BusinessDocumentComment, BusinessDocumentEvent, BusinessDocumentJob, BusinessDocumentProposal, BusinessDocumentQuestion
from business_documents.application.errors import BusinessDocumentError, ConflictError


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def prepare_review(size):
    document = _request_and_complete_draft(_create())
    document_id = document["document_id"]
    comment_events = []
    with BusinessDocument._meta.database.atomic():
        for index in range(size):
            comment_id = f"comment-{index}"
            BusinessDocumentComment.create(id=comment_id, document_id=document_id, revision_id=document["current_revision"]["revision_id"], review_cycle=1, actor_id=AUTHOR, text=f"Feedback {index}")
            comment_events.append(
                append_document_event(
                    document_id,
                    document["state_version"] + index + 1,
                    "AuthorCommentAdded",
                    "USER",
                    AUTHOR,
                    {"comment_id": comment_id},
                    "test-review",
                )
            )
        BusinessDocument.update(state_version=document["state_version"] + size).where(BusinessDocument.id == document_id).execute()
    document = document_queries.get_document(document_id, AUTHOR)
    requested = document_commands.execute(TENANT, AUTHOR, document_id, _command(document, "REQUEST_REVIEW_ASSESSMENT"))
    question = {
        "semantic_tag": " ＳＣＯＰＥ ",
        "target_section_id": "5.5",
        "text": "First question",
        "options": [{"option_id": "all", "label": "All"}, {"option_id": "some", "label": "Some"}],
        "allow_custom_answer": True,
    }
    proposals = [{"target_section_id": "5.5", "text": f"Proposal {index}", "rationale": "First rationale", "source_event_ids": [event_id]} for index, event_id in enumerate(comment_events)]
    output = {
        "schema_version": "1",
        "questions": [question, {**question, "semantic_tag": "scope", "text": "Must not replace first"}],
        "proposals": [*proposals, *[{**row, "text": f"  {row['text'].upper()}  ", "rationale": "Must not replace first"} for row in proposals]],
        "comment_dispositions": [{"comment_event_id": event_id, "disposition": "NEEDS_QUESTION", "question_semantic_tag": "SCOPE"} for event_id in comment_events],
    }
    return BusinessDocument.get_by_id(document_id), BusinessDocumentJob.get_by_id(requested["job_id"]), output, comment_events


@pytest.mark.parametrize("size", [1, 10, 100])
def test_review_completion_reads_once_per_record_type_and_keeps_first_duplicates(database, size):
    document, job, output, comment_events = prepare_review(size)
    original = deepcopy(output)
    with database.atomic(), count_queries(only_select=True) as queries:
        result = job_completion._review(document.__data__, job.__data__, "worker", output, {})
    assert queries.count <= 5
    assert output == original
    assert result["state_version"] == document.state_version + 1
    question = BusinessDocumentQuestion.get(BusinessDocumentQuestion.document_id == document.id)
    assert question.semantic_tag == "scope" and question.text == "First question"
    assert BusinessDocumentQuestion.select().where(BusinessDocumentQuestion.document_id == document.id).count() == 1
    rows = list(BusinessDocumentProposal.select().where(BusinessDocumentProposal.document_id == document.id))
    assert len(rows) == size
    assert all(row.rationale == "First rationale" for row in rows)
    assessed = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document.id) & (BusinessDocumentEvent.event_type == "ReviewAssessed"))
    assert assessed.payload["question_count"] == 1 and assessed.payload["proposal_count"] == size
    assert assessed.payload["outcome"] == "NEEDS_INPUT"
    assert question.source_event_ids == [assessed.id, *comment_events]
    assert [item["comment_event_id"] for item in assessed.payload["comment_dispositions"]] == comment_events
    assert {item["question_id"] for item in assessed.payload["comment_dispositions"]} == {question.id}


@pytest.mark.parametrize("failure", ["cas", "lease"])
def test_generated_protocol_and_event_roll_back_on_failed_publication(database, monkeypatch, failure):
    document, job, output, _ = prepare_review(3)
    original_events = BusinessDocumentEvent.select().count()
    if failure == "cas":

        def lose_cas(*_args):
            raise ConflictError("STATE_VERSION_CONFLICT", "The document changed concurrently")

        monkeypatch.setattr(document_writer, "update_document", lose_cas)
        expected = "STATE_VERSION_CONFLICT"
    else:
        append = document_writer.append_event

        def expire_after_event(*args, **kwargs):
            event_id = append(*args, **kwargs)
            BusinessDocumentJob.update(lease_expires_at=0).where(BusinessDocumentJob.id == job.id).execute()
            return event_id

        monkeypatch.setattr(document_writer, "append_event", expire_after_event)
        expected = "JOB_LEASE_LOST"
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(job.id, output)
    assert caught.value.code == expected
    stored = BusinessDocument.get_by_id(document.id)
    assert stored.state_version == document.state_version and stored.operation_state == "ANALYZING_REVIEW"
    assert BusinessDocumentQuestion.select().where(BusinessDocumentQuestion.document_id == document.id).count() == 0
    assert BusinessDocumentProposal.select().where(BusinessDocumentProposal.document_id == document.id).count() == 0
    assert BusinessDocumentEvent.select().count() == original_events
    assert BusinessDocumentJob.get_by_id(job.id).status == "RUNNING"


def test_question_uniqueness_conflict_is_replayed_but_an_unrelated_id_collision_is_not(database):
    document = _create()
    row = {
        "id": "first-question",
        "document_id": document["document_id"],
        "review_cycle": 0,
        "stage": "INTAKE",
        "semantic_tag": "scope",
        "target_section_id": "3.1",
        "text": "First",
        "options": [],
        "allow_custom_answer": True,
        "source_event_ids": [],
        "evidence_refs": [],
    }
    with database.atomic():
        assert document_writer.insert_question(row) == ("first-question", True)
        assert document_writer.insert_question({**row, "id": "duplicate", "text": "Other"}) == ("first-question", False)
        with pytest.raises(IntegrityError):
            document_writer.insert_question({**row, "semantic_tag": "different"})
    assert BusinessDocumentQuestion.get_by_id("first-question").text == "First"
    assert BusinessDocumentQuestion.select().count() == 1
