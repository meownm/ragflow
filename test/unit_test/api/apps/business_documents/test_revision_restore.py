"""Historical restore preserves snapshots, audit, authorization and command guards."""

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, TENANT, _command, database as service_database
from test.unit_test.api.apps.business_documents.test_accept_proposal import _document, _plan
from test.unit_test.api.apps.business_documents.test_business_document_service import _complete, _complete_review_assessment
from api.apps.business_documents.runtime import document_commands, document_queries, document_writer
from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentJob, BusinessDocumentRevision
from business_documents.application.errors import BusinessDocumentError, ConflictError


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def _history(database):
    document = _document(open_questions=False)
    source = document["current_revision"]
    with database.atomic():
        revision_id = document_writer.insert_revision(document["document_id"], source["revision_number"] + 1, source["document_ast"], source["body_markdown"] + "\nИзменение", [], AUTHOR)
        BusinessDocument.update(current_revision_id=revision_id).where(BusinessDocument.id == document["document_id"]).execute()
    return document_queries.get_document(document["document_id"], AUTHOR), source


@pytest.mark.parametrize("lifecycle", ["REVIEW", "AGREED"])
def test_restore_copies_exact_snapshot_into_new_review_and_replays(database, lifecycle):
    document, source = _history(database)
    BusinessDocument.update(lifecycle_state=lifecycle).where(BusinessDocument.id == document["document_id"]).execute()
    original_rows = list(BusinessDocumentRevision.select().order_by(BusinessDocumentRevision.revision_number).dicts())
    command = _command(document, "RESTORE_REVISION", {"revision_id": source["revision_id"]})
    result = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    restored = result["document"]
    revision = restored["current_revision"]
    assert restored["lifecycle_state"] == "REVIEW"
    assert restored["operation_state"] == "IDLE"
    assert restored["active_review_cycle"] == document["active_review_cycle"] + 1
    assert restored["state_version"] == document["state_version"] + 1
    assert revision["revision_number"] == document["current_revision"]["revision_number"] + 1
    assert revision["revision_id"] != source["revision_id"]
    assert revision["document_ast"] == source["document_ast"]
    assert revision["body_markdown"] == source["body_markdown"]
    assert revision["author_id"] == AUTHOR
    assert revision["change_basis"][0]["type"] == "RESTORE"
    assert restored["protocol"]["proposals"] == []
    assert list(BusinessDocumentRevision.select().order_by(BusinessDocumentRevision.revision_number).limit(2).dicts()) == original_rows
    event = BusinessDocumentEvent.get_by_id(result["event_id"])
    assert event.payload["restored_from_revision_id"] == source["revision_id"]
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], command)["idempotent_replay"]
    assert BusinessDocumentRevision.select().count() == 3
    history = document_queries.list_revisions(document["document_id"])
    assert history[-1]["change_basis"][0]["summary"] == f"Восстановлена ревизия {source['revision_number']}"


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("stale", "STATE_VERSION_CONFLICT"),
        ("current", "REVISION_ALREADY_CURRENT"),
        ("foreign", "REVISION_NOT_FOUND"),
        ("reader", "DOCUMENT_PERMISSION_DENIED"),
        ("other_author", "DOCUMENT_PERMISSION_DENIED"),
        ("archived", "DOCUMENT_ARCHIVED"),
        ("busy", "OPERATION_IN_PROGRESS"),
        ("active_job", "OPERATION_IN_PROGRESS"),
    ],
)
def test_restore_rejects_unauthorized_or_invalid_state_without_writes(database, scenario, code):
    document, source = _history(database)
    payload = {"revision_id": source["revision_id"]}
    actor, role = AUTHOR, "AUTHOR_CREATOR"
    if scenario == "current":
        payload["revision_id"] = document["current_revision"]["revision_id"]
    if scenario == "foreign":
        foreign = BusinessDocumentRevision.get_by_id(source["revision_id"]).__data__.copy()
        foreign.update(id="foreign-revision", document_id="foreign-document")
        BusinessDocumentRevision.create(**foreign)
        payload["revision_id"] = "foreign-revision"
    if scenario == "reader":
        role = "READER"
    if scenario == "other_author":
        actor = "other-author"
    if scenario == "archived":
        BusinessDocument.update(lifecycle_state="ARCHIVED").where(BusinessDocument.id == document["document_id"]).execute()
    if scenario == "busy":
        BusinessDocument.update(operation_state="APPLYING_CHANGES").where(BusinessDocument.id == document["document_id"]).execute()
    if scenario == "active_job":
        BusinessDocumentJob.update(status="RUNNING").where(BusinessDocumentJob.document_id == document["document_id"]).execute()
    before = BusinessDocument.get_by_id(document["document_id"]).__data__.copy()
    revisions, events = BusinessDocumentRevision.select().count(), BusinessDocumentEvent.select().count()
    command = _command(document, "RESTORE_REVISION", payload, expected=document["state_version"] - 1 if scenario == "stale" else None)
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, actor, document["document_id"], command, access_role=role)
    assert caught.value.code == code
    assert BusinessDocumentRevision.select().count() == revisions
    assert BusinessDocumentEvent.select().count() == events
    assert BusinessDocument.get_by_id(document["document_id"]).__data__ == before


def test_restore_invalidates_prepared_changes(database):
    document, source = _history(database)
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "ACCEPTED"}))
    document = _complete_review_assessment(document_queries.get_document(document["document_id"], AUTHOR))
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}))
    prepared = _complete(requested["job_id"], _plan(document, requested))
    result = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(prepared, "RESTORE_REVISION", {"revision_id": source["revision_id"]}))
    assert result["document"]["change_preview"] is None
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(result["document"], "CONFIRM_PREPARED_CHANGES", {"job_id": requested["job_id"]}))
    assert caught.value.code == "CHANGE_PREVIEW_STALE"


def test_restore_rolls_back_insert_when_document_update_fails(database, monkeypatch):
    document, source = _history(database)

    def reject(*args, **kwargs):
        raise ConflictError("STATE_VERSION_CONFLICT", "Injected conflict")

    monkeypatch.setattr(document_writer, "update_document", reject)
    with pytest.raises(BusinessDocumentError):
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "RESTORE_REVISION", {"revision_id": source["revision_id"]}))
    assert BusinessDocumentRevision.select().count() == 2
    assert BusinessDocument.get_by_id(document["document_id"]).current_revision_id == document["current_revision"]["revision_id"]
