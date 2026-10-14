"""EVA public scenarios recheck local permission and remote capabilities."""

from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import Mock

import pytest

from business_documents.application.documents import MAX_EVA_MARKDOWN_SIZE
from business_documents.application.errors import BusinessDocumentError, ValidationError
from business_documents.application.eva_sync import EvaSynchronization
from test.unit_test.business_documents.application.test_queries_boundaries import document


def binding(**changes):
    return {
        "status": "CONNECTED",
        "capabilities": ["PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "connector",
        "document_id": "eva-document",
        "page_url": "https://eva/page",
        "remote_content_hash": "original-hash",
        "remote_version": 4,
        **changes,
    }


@pytest.fixture()
def eva():
    reader, writer, source, queries = Mock(), Mock(), Mock(), Mock()
    reader.document.return_value = document(lifecycle_state="AGREED")
    reader.bindings.return_value = {"doc": binding()}
    reader.revision.return_value = {"source_event_ids": []}
    writer.transaction.side_effect = nullcontext
    writer.lock_document.side_effect = lambda identifier: deepcopy(reader.document.return_value)
    writer.update_document.side_effect = lambda current, changes: {**current, **changes}
    writer.append_event.return_value = "pull-event"
    source.read_connected_page.return_value = ({"remote_content_hash": "new-hash", "remote_version": 5}, "Remote content")
    source.resolve_page_url.return_value = binding(connector_id="replacement")
    queries.project.side_effect = lambda current, access: {"state_version": current["state_version"], "permissions": access.permissions(current["owner_id"])}
    return EvaSynchronization(reader, writer, source, queries, lambda: "event-id", lambda: 1000), reader, writer, source, queries


@pytest.mark.parametrize("raw", [None, [], {}, {"expected_state_version": True}, {"expected_state_version": "7"}])
@pytest.mark.parametrize("operation,code", [("pull_from_eva", "INVALID_EVA_SYNC"), ("rebind_eva", "INVALID_EVA_BINDING")])
def test_eva_mutation_rejects_invalid_version_before_any_resource_access(eva, raw, operation, code):
    scenario, reader, writer, source, _ = eva
    with pytest.raises(ValidationError) as caught:
        getattr(scenario, operation)("tenant", "owner", "doc", raw)
    assert caught.value.code == code
    assert reader.mock_calls == writer.mock_calls == source.mock_calls == []


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"current_revision_id": None}, "EVA_SYNC_REVIEW_REQUIRED"),
        ({"lifecycle_state": "INTAKE"}, "EVA_SYNC_REVIEW_REQUIRED"),
        ({"operation_state": "EXPORTING"}, "OPERATION_IN_PROGRESS"),
        ({"state_version": 8}, "STATE_VERSION_CONFLICT"),
    ],
)
def test_pull_prerequisites_block_network_and_writes(eva, changes, code):
    scenario, reader, writer, source, _ = eva
    reader.document.return_value.update(changes)
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.pull_from_eva("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value.code == code
    source.read_connected_page.assert_not_called()
    writer.transaction.assert_not_called()


@pytest.mark.parametrize("missing", [True, False])
def test_pull_requires_loaded_capability_before_reading_remote(eva, missing):
    scenario, reader, writer, source, _ = eva
    reader.bindings.return_value = {} if missing else {"doc": binding(capabilities=["OPEN"])}
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.pull_from_eva("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value.code == "EVA_SYNC_UNAVAILABLE"
    source.read_connected_page.assert_not_called()
    writer.transaction.assert_not_called()


def test_remote_errors_keep_cause_and_do_not_start_local_transaction(eva):
    scenario, _, writer, source, _ = eva
    problem = ValidationError("EVA_PERMISSION_DENIED", "Remote access revoked")
    source.read_connected_page.side_effect = problem
    with pytest.raises(ValidationError) as caught:
        scenario.pull_from_eva("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value is problem
    writer.transaction.assert_not_called()


def test_remote_size_limit_rejects_content_without_version_or_event_write(eva):
    scenario, _, writer, source, _ = eva
    source.read_connected_page.return_value = ({"remote_content_hash": "hash"}, "x" * (MAX_EVA_MARKDOWN_SIZE + 1))
    with pytest.raises(ValidationError) as caught:
        scenario.pull_from_eva("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value.code == "EVA_DOCUMENT_TOO_LARGE"
    assert caught.value.details == {"maximum": MAX_EVA_MARKDOWN_SIZE}
    writer.transaction.assert_not_called()


@pytest.mark.parametrize("change,code", [("missing-binding", "EVA_BINDING_CONFLICT"), ("lost-capability", "EVA_SYNC_UNAVAILABLE"), ("missing-revision", "DOCUMENT_NOT_FOUND")])
def test_pull_rechecks_remote_prerequisites_under_local_lock(eva, change, code):
    scenario, reader, writer, source, _ = eva
    original = binding()
    current = {} if change == "missing-binding" else {"doc": binding(capabilities=[] if change == "lost-capability" else original["capabilities"])}
    reader.bindings.side_effect = [{"doc": original}, current]
    if change == "missing-revision":
        reader.revision.return_value = None
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.pull_from_eva("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value.code == code
    source.read_connected_page.assert_called_once()
    writer.lock_document.assert_called_once_with("doc")
    writer.update_document.assert_not_called()
    writer.append_event.assert_not_called()
    writer.store_binding.assert_not_called()


@pytest.mark.parametrize(
    "lifecycle,revision_id,actor,remote_hash,expected",
    [
        ("AGREED", "revision", "owner", "new-hash", (True, True)),
        ("REVIEW", "revision", "reader", "new-hash", (True, False)),
        ("ARCHIVED", "revision", "owner", "original-hash", (False, False)),
        ("INTAKE", None, "owner", None, (False, False)),
    ],
)
def test_remote_update_check_is_read_only_and_pull_permission_is_accurate(eva, lifecycle, revision_id, actor, remote_hash, expected):
    scenario, reader, writer, source, _ = eva
    reader.document.return_value.update(lifecycle_state=lifecycle, current_revision_id=revision_id)
    source.read_connected_page.return_value = ({"remote_content_hash": remote_hash, "remote_version": 5}, "Unused body")
    result = scenario.check_eva_update("tenant", actor, "doc")
    assert (result["changed"], result["can_pull"]) == expected
    assert (result["remote_version"], result["baseline_version"]) == (5, 4)
    assert writer.mock_calls == []


def test_check_prefers_last_consumed_hash_over_connector_baseline(eva):
    scenario, reader, writer, source, _ = eva
    reader.bindings.return_value = {"doc": binding(last_pulled_content_hash="consumed-hash")}
    source.read_connected_page.return_value = ({"remote_content_hash": "consumed-hash"}, "Body")
    assert scenario.check_eva_update("tenant", "owner", "doc")["changed"] is False
    assert writer.mock_calls == []


@pytest.mark.parametrize("markdown", ["", " \n\t"])
def test_empty_remote_page_is_available_without_update_or_pull(eva, markdown):
    scenario, _, writer, source, _ = eva
    source.read_connected_page.return_value = ({"remote_content_hash": "empty-hash", "remote_version": 5}, markdown)
    result = scenario.check_eva_update("tenant", "owner", "doc")
    assert result["changed"] is False
    assert result["can_pull"] is False
    assert result["remote_version"] == 5
    assert writer.mock_calls == []


def test_update_check_propagates_real_access_errors(eva):
    scenario, _, writer, source, _ = eva
    problem = ValidationError("EVA_PERMISSION_DENIED", "Remote access revoked")
    source.read_connected_page.side_effect = problem
    with pytest.raises(ValidationError) as caught:
        scenario.check_eva_update("tenant", "owner", "doc")
    assert caught.value is problem
    assert writer.mock_calls == []


def test_check_unknown_document_never_resolves_eva(eva):
    scenario, reader, writer, source, _ = eva
    reader.document.return_value = None
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.check_eva_update("tenant", "owner", "doc")
    assert caught.value.status == 404
    source.read_connected_page.assert_not_called()
    reader.bindings.assert_not_called()
    assert writer.mock_calls == []


@pytest.mark.parametrize("state", ["ARCHIVED", "INTAKE"])
def test_rebind_archived_rejects_but_intake_can_reconnect_without_revision(eva, state):
    scenario, reader, writer, source, _ = eva
    reader.document.return_value.update(lifecycle_state=state, current_revision_id=None)
    if state == "ARCHIVED":
        with pytest.raises(BusinessDocumentError) as caught:
            scenario.rebind_eva("tenant", "owner", "doc", {"expected_state_version": 7})
        assert caught.value.code == "LIFECYCLE_STATE_CONFLICT"
        source.resolve_page_url.assert_not_called()
        writer.append_event.assert_not_called()
    else:
        result = scenario.rebind_eva("tenant", "owner", "doc", {"expected_state_version": 7})
        assert result["state_version"] == 8
        source.resolve_page_url.assert_called_once_with("owner", "https://eva/page")
        args = writer.append_event.call_args.args
        assert args[:5] == ("doc", 8, "EvaBindingResolved", "USER", "owner")
        assert args[5]["previous_eva_binding"]["connector_id"] == "connector"
        assert args[5]["eva_binding"]["connector_id"] == "replacement"
        writer.store_binding.assert_called_once_with("doc", source.resolve_page_url.return_value, replace=True)


@pytest.mark.parametrize("damage,code", [("missing-binding", "EVA_BINDING_MISSING"), ("disconnected", "EVA_BINDING_UNAVAILABLE"), ("link-race", "EVA_BINDING_CONFLICT")])
def test_rebind_failure_does_not_publish_binding_or_event(eva, damage, code):
    scenario, reader, writer, source, _ = eva
    if damage == "missing-binding":
        reader.bindings.return_value = {}
    elif damage == "disconnected":
        source.resolve_page_url.return_value = {"status": "LINK_ONLY", "document_code": "EVA-123"}
    else:
        reader.bindings.side_effect = [{"doc": binding()}, {"doc": binding(page_url="https://eva/replaced")}]
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.rebind_eva("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value.code == code
    if damage == "disconnected":
        assert caught.value.details == {"document_code": "EVA-123"}
        writer.transaction.assert_not_called()
    writer.update_document.assert_not_called()
    writer.append_event.assert_not_called()
    writer.store_binding.assert_not_called()


@pytest.mark.parametrize(
    "changes,code", [({"lifecycle_state": state}, "LIFECYCLE_STATE_CONFLICT") for state in ("INTAKE", "ARCHIVED")] + [({"current_revision_id": None}, "CURRENT_REVISION_REQUIRED")]
)
def test_remote_change_requires_current_local_revision(eva, changes, code):
    scenario, reader, writer, source, queries = eva
    reader.document.return_value.update(changes)
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.create_eva_change_from_revision("tenant", "owner", "doc", {"expected_state_version": 7})
    assert caught.value.code == code
    queries.revision.assert_not_called()
    source.create_change.assert_not_called()
    assert writer.mock_calls == []


@pytest.mark.parametrize("basis,expected", [([], ""), ([{"summary": "First"}, {"summary": ""}, {"summary": "Third"}, {"summary": "Fourth"}], ": First; Third")])
@pytest.mark.parametrize("lifecycle", ["REVIEW", "AGREED"])
def test_remote_change_uses_current_revision_and_first_three_basis_items(eva, basis, expected, lifecycle):
    scenario, reader, writer, source, queries = eva
    reader.document.return_value["lifecycle_state"] = lifecycle
    queries.revision.return_value = {"revision_number": 3, "change_basis": basis, "body_markdown": "Agreed body"}
    source.create_change.return_value = {"change_id": "remote-change"}
    result = scenario.create_eva_change_from_revision("tenant", "owner", "doc", {"expected_state_version": 7})
    assert result == {"change_id": "remote-change"}
    args = source.create_change.call_args.args
    assert args[:2] == ("tenant", "owner")
    assert args[2] == {
        "connector_id": "connector",
        "document_id": "eva-document",
        "document_name": "Requirements",
        "change_summary": "Синхронизация ревизии 3 документа «Requirements»" + expected,
        "draft_markdown": "Agreed body",
    }
    assert writer.mock_calls == []


def test_remote_change_summary_is_bounded_without_truncating_the_agreed_body(eva):
    scenario, _, _, source, queries = eva
    body = "Agreed body" * 6000
    queries.revision.return_value = {"revision_number": 3, "change_basis": [{"summary": "x" * 60_000}], "body_markdown": body}
    scenario.create_eva_change_from_revision("tenant", "owner", "doc", {"expected_state_version": 7})
    payload = source.create_change.call_args.args[2]
    assert len(payload["change_summary"]) == 50_000
    assert payload["draft_markdown"] == body
