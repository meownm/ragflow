"""Creation fails before persistence when authorization or EVA input is unsafe."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_worker_exports import _minimal_ast
from api.apps.business_documents.runtime import document_creation
from business_documents.application.documents import CreateDocument, DeleteDocument, MAX_EVA_MARKDOWN_SIZE
from business_documents.application.errors import BusinessDocumentError, PermissionDeniedError, ValidationError
from business_documents.domain.eva_binding import binding_keys


@pytest.fixture()
def creation():
    writer = Mock()
    writer.transaction.side_effect = nullcontext
    writer.title_exists.return_value = False
    writer.chat_exists.return_value = False
    writer.binding_occupancy.return_value = {}
    eva = Mock()
    eva.find_title_matches.return_value = []
    queries = Mock()
    scenario = CreateDocument(writer, document_creation._content, Mock(), eva, queries, Mock(return_value="new-document"), lambda: 1000)
    return scenario, writer, eva, queries


def create_request(**changes):
    return {"schema_version": "1", "document_type": "business_requirements", "title": "A document", "idea": "A valid idea", **changes}


@pytest.mark.parametrize("role", ["AUTHOR_EDITOR", "ADMIN", "unknown-role"])
def test_unprivileged_creation_fails_before_validation_or_external_access(creation, role):
    scenario, writer, eva, queries = creation
    with pytest.raises(PermissionDeniedError):
        scenario.execute("tenant", "actor", None, access_role=role)
    assert writer.mock_calls == []
    assert eva.mock_calls == []
    assert queries.mock_calls == []
    assert scenario._datasets.mock_calls == []


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"chat_id": "caller-selected-chat"}, "CHAT_ID_NOT_ALLOWED"),
        ({"eva_page_url": "https://eva.invalid/page"}, "EVA_BINDING_REQUIRES_SCHEMA_V2"),
        ({"schema_version": "2", "eva_page_url": "https://eva.invalid/page", "eva_decision": {"mode": "SKIP"}}, "EVA_BINDING_DECISION_REQUIRED"),
        ({"schema_version": "2", "eva_page_url": "https://eva.invalid/page", "eva_decision": {"mode": "BIND"}}, "EVA_REPLACE_CONFIRMATION_REQUIRED"),
        ({"template_version": "unpublished-template"}, "TEMPLATE_NOT_PUBLISHED"),
        ({"policy_version": "unpublished-policy"}, "POLICY_NOT_PUBLISHED"),
    ],
)
def test_creation_rejects_untrusted_binding_and_configuration(creation, changes, code):
    scenario, writer, eva, _ = creation
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "actor", create_request(**changes))
    assert caught.value.code == code
    writer.insert_document.assert_not_called()
    writer.append_event.assert_not_called()
    eva.resolve_page_url.assert_not_called()


@pytest.mark.parametrize("collision", ["title", "chat"])
def test_creation_rechecks_unique_identity_inside_transaction(creation, collision):
    scenario, writer, _, queries = creation
    writer.title_exists.side_effect = [False, collision == "title"]
    writer.chat_exists.return_value = collision == "chat"
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "actor", create_request())
    assert caught.value.code == ("DOCUMENT_TITLE_ALREADY_EXISTS" if collision == "title" else "CHAT_ALREADY_BOUND")
    writer.transaction.assert_called_once()
    writer.insert_document.assert_not_called()
    queries.get_document.assert_not_called()


@pytest.mark.parametrize("status, code", [("LINK_ONLY", "EVA_IMPORT_UNAVAILABLE"), ("DISCONNECTED", "EVA_IMPORT_UNAVAILABLE")])
def test_import_requires_verified_connected_page_before_reading(creation, status, code):
    scenario, writer, eva, _ = creation
    with pytest.raises(BusinessDocumentError) as caught:
        scenario._prepare_import("actor", "3", "A document", {"status": status})
    assert caught.value.code == code
    eva.read_connected_page.assert_not_called()
    writer.insert_revision.assert_not_called()


@pytest.mark.parametrize("code", ["EVA_PERMISSION_DENIED", "EVA_DOCUMENT_TEMPLATE_MISMATCH"])
def test_import_does_not_treat_other_validation_errors_as_empty_page(creation, code):
    scenario, _, eva, _ = creation
    problem = ValidationError(code, "Cannot import this EVA page")
    eva.read_connected_page.side_effect = problem
    with pytest.raises(ValidationError) as caught:
        scenario._prepare_import("actor", "3", "A document", {"status": "CONNECTED"})
    assert caught.value is problem


@pytest.mark.parametrize("damage, code", [("changed_title", "EVA_PAGE_TITLE_CHANGED"), ("oversized", "EVA_DOCUMENT_TOO_LARGE")])
def test_refreshed_eva_page_is_revalidated_before_import(creation, damage, code):
    scenario, writer, eva, _ = creation
    title = "Another document" if damage == "changed_title" else "A document"
    markdown = "x" * (MAX_EVA_MARKDOWN_SIZE + 1) if damage == "oversized" else "irrelevant"
    eva.read_connected_page.return_value = ({"status": "CONNECTED", "document_name": title}, markdown)
    with pytest.raises(BusinessDocumentError) as caught:
        scenario._prepare_import("actor", "3", "A document", {"status": "CONNECTED"})
    assert caught.value.code == code
    writer.insert_document.assert_not_called()
    writer.insert_revision.assert_not_called()


def test_import_size_limit_accepts_exact_boundary_and_pins_refreshed_identity(creation):
    scenario, _, eva, _ = creation
    content = Mock(wraps=scenario._content)
    content.import_document_markdown.return_value = _minimal_ast()
    scenario._content = content
    refreshed = {"status": "CONNECTED", "document_name": "A document", "remote_content_hash": "remote-sha", "remote_version": "v2"}
    eva.read_connected_page.return_value = (refreshed, "x" * MAX_EVA_MARKDOWN_SIZE)
    imported = scenario._prepare_import("actor", "3", "A document", {"status": "CONNECTED", "remote_version": "v1"})
    assert imported.binding["remote_version"] == "v2"
    assert imported.binding["last_pulled_content_hash"] == "remote-sha"
    assert imported.binding["last_pulled_at"] == 1000
    assert imported.binding["last_pull_event_id"] == imported.event_id
    content.import_document_markdown.assert_called_once_with("x" * MAX_EVA_MARKDOWN_SIZE)
    assert imported.body_markdown


def test_occupied_eva_match_requires_explicit_decision_and_preserves_linked_owner(creation):
    scenario, writer, eva, _ = creation
    match = {"page_url": "https://eva.invalid/page", "web_url": "https://eva.invalid/page", "eva_origin": "https://eva.invalid", "project_id": "project", "document_id": "page"}
    eva.find_title_matches.return_value = [match]
    _, identity = binding_keys(match)
    owner = {"document_id": "existing-business-document", "title": "A document"}
    writer.binding_occupancy.return_value = {identity: owner}
    with pytest.raises(BusinessDocumentError) as caught:
        scenario._resolve_binding("actor", {}, "3", "A document")
    assert caught.value.code == "EVA_BINDING_DECISION_REQUIRED"
    assert caught.value.details["matches"][0]["binding_available"] is False
    assert caught.value.details["matches"][0]["linked_document"] == owner
    eva.resolve_page_url.assert_not_called()


def test_delete_missing_document_never_stages_cleanup():
    writer, cleanup = Mock(), Mock()
    writer.transaction.side_effect = nullcontext
    writer.lock_document.return_value = None
    with pytest.raises(BusinessDocumentError) as caught:
        DeleteDocument(writer, cleanup).execute("admin", "absent-document", is_admin=True)
    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    assert caught.value.status == 404
    writer.stage_artifact_cleanup.assert_not_called()
    writer.delete_document_rows.assert_not_called()
    cleanup.after_delete.assert_not_called()
