"""EVA snapshots are committed only against the current authorized document."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR, TENANT, _create, _draft, _request_and_complete_draft, database as service_database
from api.apps.business_documents import assets
from api.apps.business_documents.runtime import document_writer, eva_synchronization
from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentEvaBinding, BusinessDocumentRevision
from business_documents.application.errors import BusinessDocumentError, ValidationError
from business_documents.domain.content import apply_change_plan, section_hash
from business_documents.domain.access import BusinessDocumentRole
from api.apps.business_documents.adapters.queries import load_eva_bindings


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def connected_document(monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    binding = {
        "page_url": "https://eva.example.test/project/Document/42",
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "eva-connector",
        "project_id": "eva-project",
        "document_id": "eva-page",
        "document_name": "EVA sync test",
    }
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: deepcopy(binding)))
    document = _request_and_complete_draft(_create(title=binding["document_name"], schema_version="2", eva_page_url=binding["page_url"], eva_decision={"mode": "BIND", "confirm_replace": True}))
    remote = {**binding, "remote_version": "2", "remote_content_hash": "sha256:remote", "page_url": "https://eva.example.test/renamed"}
    monkeypatch.setattr(eva_synchronization._source, "read_connected_page", lambda *_args: (deepcopy(remote), "Remote content"))
    return document, remote


def pull(document, actor_id=AUTHOR):
    return eva_synchronization.pull_from_eva(TENANT, actor_id, document["document_id"], {"expected_state_version": document["state_version"]})


@pytest.mark.parametrize("consumed", [False, True], ids=["pending-review", "consumed-by-revision"])
def test_same_eva_content_is_a_noop_without_replacing_binding_metadata(database, monkeypatch, consumed):
    document, _ = connected_document(monkeypatch)
    first = pull(document)
    document = first["document"]
    if consumed:
        revision = BusinessDocumentRevision.get_by_id(document["current_revision"]["revision_id"])
        BusinessDocumentRevision.update(source_event_ids=[*revision.source_event_ids, first["sync"]["event_id"]]).where(BusinessDocumentRevision.id == revision.id).execute()
        BusinessDocument.update(lifecycle_state="AGREED").where(BusinessDocument.id == document["document_id"]).execute()
    events = BusinessDocumentEvent.select().count()
    revisions = BusinessDocumentRevision.select().count()
    second = pull(document)
    assert second["sync"] == {"changed": False, "direction": "FROM_EVA", "remote_version": "2"}
    assert second["document"]["state_version"] == document["state_version"]
    assert second["document"]["eva_binding"]["page_url"] == "https://eva.example.test/project/Document/42"
    assert BusinessDocumentEvent.select().count() == events
    assert BusinessDocumentRevision.select().count() == revisions


def test_same_hash_in_a_new_unconsumed_cycle_is_a_new_review_input(database, monkeypatch):
    document, _ = connected_document(monkeypatch)
    first = pull(document)
    document = first["document"]
    BusinessDocument.update(active_review_cycle=document["active_review_cycle"] + 1).where(BusinessDocument.id == document["document_id"]).execute()
    second = pull(document)
    assert second["sync"]["changed"] is True
    assert second["sync"]["event_id"] != first["sync"]["event_id"]
    assert second["document"]["state_version"] == document["state_version"] + 1


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("owner", "DOCUMENT_PERMISSION_DENIED"),
        ("version", "STATE_VERSION_CONFLICT"),
        ("binding", "EVA_BINDING_CONFLICT"),
        ("busy", "OPERATION_IN_PROGRESS"),
        ("archived", "EVA_SYNC_REVIEW_REQUIRED"),
        ("deleted", "DOCUMENT_NOT_FOUND"),
    ],
)
def test_eva_noop_rechecks_state_after_network_read(database, monkeypatch, mutation, code):
    document, remote = connected_document(monkeypatch)
    document = pull(document)["document"]
    event_count = BusinessDocumentEvent.select().count()

    def read(*_args):
        assert not database.in_transaction()
        document_id = document["document_id"]
        if mutation == "binding":
            binding = BusinessDocumentEvaBinding.get_by_id(document_id).binding
            with document_writer.transaction():
                document_writer.store_binding(document_id, {**binding, "page_url": "https://eva.example.test/another"}, replace=True)
        elif mutation == "deleted":
            BusinessDocument.delete().where(BusinessDocument.id == document_id).execute()
        else:
            changes = {
                "owner": {"owner_id": "new-owner", "state_version": document["state_version"] + 1},
                "version": {"state_version": document["state_version"] + 1},
                "busy": {"operation_state": "ANALYZING_REVIEW"},
                "archived": {"lifecycle_state": "ARCHIVED"},
            }[mutation]
            BusinessDocument.update(**changes).where(BusinessDocument.id == document_id).execute()
        return remote, "Remote content"

    monkeypatch.setattr(eva_synchronization._source, "read_connected_page", read)
    with pytest.raises(BusinessDocumentError) as caught:
        pull(document)
    assert caught.value.code == code
    assert BusinessDocumentEvent.select().count() == event_count


@pytest.mark.parametrize("operation", ["pull_from_eva", "rebind_eva"])
def test_eva_binding_failure_rolls_back_version_event_and_binding(database, monkeypatch, operation):
    document, _ = connected_document(monkeypatch)
    document_id = document["document_id"]
    before_binding = BusinessDocumentEvaBinding.get_by_id(document_id).binding
    events = BusinessDocumentEvent.select().count()
    original = document_writer.store_binding

    def store(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after binding write")

    monkeypatch.setattr(document_writer, "store_binding", store)
    with pytest.raises(RuntimeError, match="after binding write"):
        getattr(eva_synchronization, operation)(TENANT, AUTHOR, document_id, {"expected_state_version": document["state_version"]})
    assert BusinessDocument.get_by_id(document_id).state_version == document["state_version"]
    assert BusinessDocumentEvaBinding.get_by_id(document_id).binding == before_binding
    assert BusinessDocumentEvent.select().count() == events


@pytest.mark.parametrize("actor,can_pull", [(AUTHOR, True), ("other-reader", False)])
def test_eva_check_is_shared_and_does_not_persist_remote_metadata(database, monkeypatch, actor, can_pull):
    document, _ = connected_document(monkeypatch)
    binding = BusinessDocumentEvaBinding.get_by_id(document["document_id"]).binding
    events = BusinessDocumentEvent.select().count()
    result = eva_synchronization.check_eva_update("other-tenant", actor, document["document_id"])
    assert result["changed"] is True and result["can_pull"] is can_pull
    assert BusinessDocumentEvaBinding.get_by_id(document["document_id"]).binding == binding
    assert BusinessDocumentEvent.select().count() == events
    assert BusinessDocument.get_by_id(document["document_id"]).state_version == document["state_version"]


def test_eva_pull_preserves_empty_page_error(database, monkeypatch):
    document, _ = connected_document(monkeypatch)

    def read(*_args):
        return {"remote_content_hash": "sha256:empty"}, ""

    monkeypatch.setattr(eva_synchronization._source, "read_connected_page", read)
    with pytest.raises(ValidationError) as caught:
        pull(document)
    assert caught.value.message == "The linked EVA document has no published content"


@pytest.mark.parametrize("markdown", ["", " \n\t"])
def test_empty_eva_import_does_not_create_a_revision(database, monkeypatch, markdown):
    from api.apps.business_documents.runtime import document_creation

    binding = {"status": "CONNECTED", "document_name": "An empty page"}
    monkeypatch.setattr(document_creation._eva, "read_connected_page", lambda *_args: (binding, markdown))
    events = BusinessDocumentEvent.select().count()
    revisions = BusinessDocumentRevision.select().count()
    assert document_creation._prepare_import(AUTHOR, "3", binding["document_name"], binding) is None
    assert BusinessDocumentEvent.select().count() == events
    assert BusinessDocumentRevision.select().count() == revisions


def test_review_revision_can_initialize_empty_eva_page(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import BusinessDocumentEvaChange, BusinessDocumentEvaChangeEvent
    from api.db.services.connector_service import ConnectorService
    from test.unit_test.api.apps.business_documents.test_eva_document_changes import CONNECTOR, EVA_CHANGES_MODULE, FakeEvaClient

    document, remote = connected_document(monkeypatch)
    assert document["lifecycle_state"] == "REVIEW"
    monkeypatch.setattr(EVA_CHANGES_MODULE, "get_documents_eva_connection", lambda **_kwargs: None)
    monkeypatch.setattr(EVA_CHANGES_MODULE, "get_business_documents_eva_connector_id", lambda: remote["connector_id"])
    monkeypatch.setattr(ConnectorService, "accessible", lambda *_args: True)
    client = FakeEvaClient()
    client.document.update(id=remote["document_id"], project_id=remote["project_id"], html="", version="1||2026-08-26T09:00:00+03:00")
    monkeypatch.setattr(EvaDocumentChangeService, "_connector", lambda *_args: (CONNECTOR, client))
    monkeypatch.setattr(EvaDocumentChangeService, "_mutation_client", lambda *_args: (client, 1))
    monkeypatch.setattr(eva_synchronization._source, "read_connected_page", lambda actor, binding: EvaDocumentChangeService.read_connected_page(actor, binding))
    events = BusinessDocumentEvent.select().count()
    tables = [BusinessDocumentEvaChange, BusinessDocumentEvaChangeEvent]
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.create_tables(tables)
        try:
            status = eva_synchronization.check_eva_update(TENANT, AUTHOR, document["document_id"])
            assert status["changed"] is False and status["can_pull"] is False
            created = eva_synchronization.create_eva_change_from_revision(TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"]})
            assert created["base_markdown"] == ""
            assert created["draft_markdown"] == document["current_revision"]["body_markdown"]
            assert client.document["html"] == ""
            approved = EvaDocumentChangeService.approve(TENANT, AUTHOR, created["change_id"], {"expected_state_version": created["state_version"]})
            prepared = EvaDocumentChangeService.prepare_eva_draft(TENANT, AUTHOR, created["change_id"], {"expected_state_version": approved["state_version"]})
            published = EvaDocumentChangeService.publish(TENANT, AUTHOR, created["change_id"], {"expected_state_version": prepared["state_version"]})
            assert published["workflow_state"] == "PUBLISHED"
            assert client.publish_calls == 1
            assert client.document["html"]
            assert client.document["name"] == document["title"]
        finally:
            database.drop_tables(tables)
    stored = BusinessDocument.get_by_id(document["document_id"])
    assert stored.lifecycle_state == "REVIEW"
    assert stored.state_version == document["state_version"]
    assert BusinessDocumentEvent.select().count() == events


@pytest.mark.parametrize("operation", ["pull_from_eva", "rebind_eva", "create_eva_change_from_revision"])
def test_eva_mutations_reject_nonowners_before_external_io(database, monkeypatch, operation):
    document, _ = connected_document(monkeypatch)

    def forbidden_io(*_args):
        pytest.fail("Unauthorized request reached EVA")

    for method in ("read_connected_page", "resolve_page_url", "create_change"):
        monkeypatch.setattr(eva_synchronization._source, method, forbidden_io)
    with pytest.raises(BusinessDocumentError) as caught:
        getattr(eva_synchronization, operation)(TENANT, "another-user", document["document_id"], {"expected_state_version": document["state_version"]})
    assert caught.value.code == "DOCUMENT_PERMISSION_DENIED"


def test_eva_rebind_rechecks_owner_after_resolving_page(database, monkeypatch):
    document, remote = connected_document(monkeypatch)

    def resolve(*_args):
        assert not database.in_transaction()
        BusinessDocument.update(owner_id="another-owner", state_version=document["state_version"] + 1).where(BusinessDocument.id == document["document_id"]).execute()
        return remote

    monkeypatch.setattr(eva_synchronization._source, "resolve_page_url", resolve)
    with pytest.raises(BusinessDocumentError) as caught:
        eva_synchronization.rebind_eva(TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"]})
    assert caught.value.code == "DOCUMENT_PERMISSION_DENIED"
    assert not BusinessDocumentEvent.select().where(BusinessDocumentEvent.event_type == "EvaBindingResolved").exists()


def test_attach_preserves_document_and_reconstructs_binding_from_history(database, monkeypatch):
    document = _request_and_complete_draft(_create())
    page = {
        "page_url": "https://eva.example.test/project/Document/new",
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "eva-connector",
        "document_id": "eva-new-page",
    }
    monkeypatch.setattr(eva_synchronization._source, "resolve_page_url", lambda *_: deepcopy(page))
    before = deepcopy(BusinessDocument.get_by_id(document["document_id"]).__data__)
    revisions = BusinessDocumentRevision.select().count()
    attached = eva_synchronization.rebind_eva(
        TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"], "eva_page_url": page["page_url"]}, access_role=BusinessDocumentRole.AUTHOR_EDITOR
    )
    assert attached["eva_binding"] == page
    after = BusinessDocument.get_by_id(document["document_id"]).__data__
    for key in ("owner_id", "current_revision_id", "active_review_cycle", "lifecycle_state", "title"):
        assert after[key] == before[key]
    assert BusinessDocumentRevision.select().count() == revisions
    BusinessDocumentEvaBinding.delete().where(BusinessDocumentEvaBinding.document_id == document["document_id"]).execute()
    assert load_eva_bindings({document["document_id"]}) == {document["document_id"]: page}


def test_attach_occupied_page_rolls_back_version_and_event(database, monkeypatch):
    existing, page = connected_document(monkeypatch)
    other = _request_and_complete_draft(_create(title="Other document"))
    monkeypatch.setattr(eva_synchronization._source, "resolve_page_url", lambda *_: deepcopy(existing["eva_binding"]))
    events = BusinessDocumentEvent.select().count()
    with pytest.raises(BusinessDocumentError) as caught:
        eva_synchronization.rebind_eva(TENANT, AUTHOR, other["document_id"], {"expected_state_version": other["state_version"], "eva_page_url": page["page_url"]})
    assert caught.value.code == "EVA_PAGE_ALREADY_LINKED"
    assert BusinessDocument.get_by_id(other["document_id"]).state_version == other["state_version"]
    assert BusinessDocumentEvent.select().count() == events
    assert not BusinessDocumentEvaBinding.select().where(BusinessDocumentEvaBinding.document_id == other["document_id"]).exists()


def test_applied_ast_owns_changed_and_unchanged_content_without_mutating_inputs():
    base = _draft()
    section = next(section for section in base["sections"] if section["id"] == "5.5")
    plan = {
        "operations": [
            {
                "section_id": "5.5",
                "expected_section_hash": section_hash(section),
                "content": {"blocks": [{"type": "paragraph", "text": "Changed"}]},
                "evidence_refs": ["ragflow://dataset/d/document/doc/chunk/one"],
            }
        ]
    }
    original_base, original_plan = deepcopy(base), deepcopy(plan)
    result = apply_change_plan(base, plan, assets.published_template(), assets.contract_schema("document_draft"))
    changed = next(section for section in result["sections"] if section["id"] == "5.5")
    changed["blocks"][0]["text"] = "Modified result"
    changed["evidence_refs"].append("source-2")
    result["sections"][0]["blocks"].append({"type": "paragraph", "text": "Extra"})
    assert base == original_base
    assert plan == original_plan
