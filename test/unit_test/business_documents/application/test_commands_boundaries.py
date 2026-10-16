"""Public commands fence immutable feedback and retain rejected-command replay."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from business_documents.application.commands import DocumentCommands
from business_documents.application.errors import BusinessDocumentError
from business_documents.domain.review import build_review_state
from test.unit_test.api.apps.business_documents.test_business_document_worker_exports import document_commands as composed_commands
from test.unit_test.business_documents.application.test_queries_boundaries import document, empty_review, revision


def command(kind="ANSWER_QUESTION", **payload):
    return {
        "schema_version": "1",
        "command_id": "command-id",
        "idempotency_key": "replay-key",
        "expected_state_version": 7,
        "type": kind,
        "payload": {"question_id": "question", "selected_option_id": "option", "custom_answer": None, **payload} if kind == "ANSWER_QUESTION" else payload,
    }


@pytest.fixture()
def commands():
    writer, queries, changes = Mock(), Mock(), Mock()
    writer.transaction.side_effect = nullcontext
    writer.lock_document.return_value = document()
    writer.command_receipt.return_value = None
    writer.receipt_after_conflict.return_value = None
    writer.review.return_value = empty_review()
    writer.preview.return_value = None
    writer.question.return_value = {"id": "question", "stage": "REVIEW", "review_cycle": 2, "options": [None, {"option_id": "option"}], "allow_custom_answer": True}
    writer.has_answer.return_value = False
    writer.proposal.return_value = {"id": "proposal"}
    writer.has_decision.return_value = False
    writer.revision_or_none.return_value = revision()
    writer.update_document.side_effect = lambda current, update: {**current, **update}
    writer.append_event.return_value = "event"
    scenario = DocumentCommands(writer, composed_commands._content, queries, changes, lambda: "generated-id", lambda: 1000)
    return scenario, writer, queries, changes


def assert_no_feedback_write(writer):
    writer.insert_answer.assert_not_called()
    writer.insert_comment.assert_not_called()
    writer.insert_decision.assert_not_called()
    writer.update_document.assert_not_called()
    writer.append_event.assert_not_called()
    writer.insert_job.assert_not_called()


@pytest.mark.parametrize(
    "damage,code",
    [
        ("missing", "QUESTION_NOT_FOUND"),
        ("wrong-stage", "QUESTION_NOT_ACTIVE"),
        ("wrong-cycle", "QUESTION_NOT_ACTIVE"),
        ("answered", "QUESTION_ALREADY_CLOSED"),
        ("neither", "INVALID_ANSWER"),
        ("both", "INVALID_ANSWER"),
        ("unknown-option", "INVALID_OPTION"),
        ("custom-disabled", "CUSTOM_ANSWER_NOT_ALLOWED"),
    ],
)
def test_question_rejection_is_receipted_without_overwriting_feedback(commands, damage, code):
    scenario, writer, _, _ = commands
    request = command()
    if damage == "missing":
        writer.question.return_value = None
    elif damage == "wrong-stage":
        writer.question.return_value["stage"] = "INTAKE"
    elif damage == "wrong-cycle":
        writer.question.return_value["review_cycle"] = 1
    elif damage == "answered":
        writer.has_answer.return_value = True
    elif damage == "neither":
        request["payload"].update(selected_option_id=None, custom_answer="  ")
    elif damage == "both":
        request["payload"]["custom_answer"] = "Custom choice"
    elif damage == "unknown-option":
        request["payload"]["selected_option_id"] = "not-published"
    else:
        request["payload"].update(selected_option_id=None, custom_answer="Custom choice")
        writer.question.return_value["allow_custom_answer"] = False
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "doc", request)
    assert caught.value.code == code
    assert_no_feedback_write(writer)
    receipt = writer.record_command.call_args.kwargs
    assert receipt["tenant_id"] == "tenant" and receipt["document_id"] == "doc"
    assert receipt["response"]["accepted"] is False
    assert receipt["response"]["state_version"] == 7
    assert receipt["response"]["error"]["code"] == code
    writer.command_receipt.return_value = receipt
    writer.question.reset_mock()
    writer.record_command.reset_mock()
    with pytest.raises(BusinessDocumentError) as replay:
        scenario.execute("tenant", "owner", "doc", request)
    assert (replay.value.code, replay.value.status, replay.value.details) == (caught.value.code, caught.value.status, caught.value.details)
    writer.question.assert_not_called()
    writer.record_command.assert_not_called()


@pytest.mark.parametrize("custom", [None, "  Bespoke scope  "])
def test_answer_preserves_persona_and_normalizes_only_custom_whitespace(commands, custom):
    scenario, writer, _, _ = commands
    request = command(selected_option_id=None if custom else "option", custom_answer=custom)
    result = scenario.execute("tenant", "owner", "doc", request)
    assert result["accepted"] is True and result["state_version"] == 8
    answer = writer.insert_answer.call_args.kwargs
    assert answer["actor_id"] == "owner" and answer["document_id"] == "doc"
    assert answer["custom_answer"] == ("Bespoke scope" if custom else None)
    assert answer["selected_option_id"] == (None if custom else "option")
    assert writer.append_event.call_args.args[:5] == ("doc", 8, "QuestionAnswered", "USER", "owner")


@pytest.mark.parametrize("damage", ["missing", "decided"])
def test_proposals_are_scoped_to_cycle_and_existing_decisions_are_immutable(commands, damage):
    scenario, writer, _, _ = commands
    if damage == "missing":
        writer.proposal.return_value = None
    else:
        writer.has_decision.return_value = True
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "doc", command("DECIDE_PROPOSAL", proposal_id="proposal", decision="ACCEPTED"))
    assert caught.value.code == ("PROPOSAL_NOT_FOUND" if damage == "missing" else "PROPOSAL_ALREADY_DECIDED")
    writer.proposal.assert_called_once_with("doc", "proposal", 2)
    assert_no_feedback_write(writer)
    assert writer.record_command.call_args.kwargs["response"]["accepted"] is False


@pytest.mark.parametrize(
    "damage,code",
    [
        ("blank-text", "INVALID_COMMENT"),
        ("old-revision", "COMMENT_REVISION_CONFLICT"),
        ("missing-revision", "COMMENT_REVISION_CONFLICT"),
        ("missing-section", "COMMENT_SECTION_NOT_FOUND"),
        ("anchor-revision", "INVALID_COMMENT_ANCHOR"),
        ("anchor-section", "INVALID_COMMENT_ANCHOR"),
        ("anchor-text", "INVALID_COMMENT_ANCHOR"),
        ("anchor-context", "INVALID_COMMENT_ANCHOR"),
        ("split-unicode", "INVALID_COMMENT_ANCHOR"),
    ],
)
def test_comments_reject_stale_revision_section_and_browser_selection_before_write(commands, damage, code):
    scenario, writer, _, _ = commands
    payload = {"revision_id": "revision", "section_id": None, "text": "A comment", "anchor": None}
    if damage == "blank-text":
        payload["text"] = "  "
    elif damage == "old-revision":
        payload["revision_id"] = "old-revision"
    elif damage == "missing-revision":
        writer.revision_or_none.return_value = None
    elif damage == "missing-section":
        payload["section_id"] = "removed-section"
        payload["anchor"] = {"revision_id": "revision", "section_id": "removed-section", "selected_text": "Before", "prefix": "", "suffix": "", "start_offset": 0, "end_offset": 6}
    else:
        payload["section_id"] = "1"
        payload["anchor"] = {"revision_id": "revision", "section_id": "1", "selected_text": "Before", "prefix": "", "suffix": "", "start_offset": 0, "end_offset": 6}
        if damage == "anchor-revision":
            payload["anchor"]["revision_id"] = "old-revision"
        elif damage == "anchor-section":
            payload["anchor"]["section_id"] = "removed-section"
        elif damage == "anchor-text":
            payload["anchor"]["selected_text"] = "Changed"
        elif damage == "anchor-context":
            payload["anchor"]["suffix"] = "Foreign context"
        else:
            writer.revision_or_none.return_value["document_ast"]["sections"][0]["blocks"][0]["text"] = "😀Before"
            payload["anchor"].update(selected_text="😀", start_offset=1, end_offset=2, suffix="Before")
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "doc", command("ADD_COMMENT", **payload))
    assert caught.value.code == code
    assert_no_feedback_write(writer)
    assert writer.record_command.call_args.kwargs["response"]["error"]["code"] == code


def test_receipted_success_replays_without_new_mutation_but_revoked_owner_cannot_replay(commands):
    scenario, writer, _, _ = commands
    request = command()
    first = scenario.execute("tenant", "owner", "doc", request)
    receipt = writer.record_command.call_args.kwargs
    writer.command_receipt.return_value = receipt
    writer.lock_document.return_value["state_version"] = 8
    writer.insert_answer.reset_mock()
    writer.update_document.reset_mock()
    writer.record_command.reset_mock()
    second = scenario.execute("tenant", "owner", "doc", request)
    assert second == {**first, "idempotent_replay": True}
    assert receipt["response"] == first
    writer.insert_answer.assert_not_called()
    writer.update_document.assert_not_called()
    writer.record_command.assert_not_called()
    writer.command_receipt.reset_mock()
    writer.lock_document.return_value["owner_id"] = "new-owner"
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "doc", request)
    assert caught.value.status == 403
    writer.command_receipt.assert_not_called()


def test_version_conflict_is_durable_but_does_not_dispatch_or_reserve_job(commands):
    scenario, writer, queries, changes = commands
    request = command("REQUEST_EXPORT", revision_id="revision", format="DOCX")
    writer.lock_document.return_value["state_version"] = 9
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "doc", request)
    assert caught.value.code == "STATE_VERSION_CONFLICT"
    assert caught.value.details == {"expected": 7, "actual": 9}
    assert_no_feedback_write(writer)
    assert queries.mock_calls == changes.mock_calls == []
    receipt = writer.record_command.call_args.kwargs
    assert receipt["response"]["state_version"] == 9
    assert receipt["response"]["error"]["status"] == 409


def test_deleted_document_cannot_replay_a_receipt_or_reserve_feedback(commands):
    scenario, writer, _, _ = commands
    writer.lock_document.return_value = None
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "deleted", command())
    assert caught.value.status == 404
    writer.command_receipt.assert_not_called()
    writer.record_command.assert_not_called()
    assert_no_feedback_write(writer)


@pytest.mark.parametrize(
    "kind,lifecycle,open_stage,payload,code",
    [
        ("REQUEST_INTAKE_ASSESSMENT", "INTAKE", "INTAKE", {}, "OPEN_INTAKE_QUESTIONS"),
        ("REQUEST_DRAFT", "REVIEW", None, {}, "LIFECYCLE_STATE_CONFLICT"),
        ("REQUEST_DRAFT", "INTAKE", "INTAKE", {}, "OPEN_INTAKE_QUESTIONS"),
        ("PREPARE_CHANGES", "AGREED", None, {"base_revision_id": "revision"}, "LIFECYCLE_STATE_CONFLICT"),
        ("CONFIRM_PREPARED_CHANGES", "AGREED", None, {"job_id": "preview"}, "LIFECYCLE_STATE_CONFLICT"),
    ],
)
def test_workflow_prerequisites_reject_without_starting_model_or_artifact_work(commands, kind, lifecycle, open_stage, payload, code):
    scenario, writer, queries, changes = commands
    writer.lock_document.return_value.update(lifecycle_state=lifecycle, current_revision_id=None if lifecycle == "INTAKE" else "revision")
    questions = [{"id": "unanswered", "stage": open_stage, "review_cycle": 2}] if open_stage else []
    writer.review.return_value = build_review_state("doc", 2, questions=questions, answers=(), proposals=(), decisions=(), comments=(), events=())
    with pytest.raises(BusinessDocumentError) as caught:
        scenario.execute("tenant", "owner", "doc", command(kind, **payload))
    assert caught.value.code == code
    assert_no_feedback_write(writer)
    assert queries.mock_calls == changes.mock_calls == []
    assert writer.record_command.call_args.kwargs["response"]["error"]["code"] == code


def test_discard_preview_advances_version_and_audits_both_document_and_job(commands):
    scenario, writer, _, changes = commands
    writer.preview.side_effect = [{"id": "preview"}, None]
    result = scenario.execute("tenant", "owner", "doc", command("DISCARD_PREPARED_CHANGES", job_id="preview"))
    assert result["accepted"] is True and result["state_version"] == 8
    assert "CONFIRM_PREPARED_CHANGES" not in result["allowed_commands"]
    writer.update_document.assert_called_once_with(document(), {"state_version": 8})
    writer.append_event.assert_called_once_with("doc", 8, "ChangePreviewDiscarded", "USER", "owner", {"job_id": "preview"}, "command-id")
    writer.append_job_event.assert_called_once_with("preview", "discarded", {"state_version": 8})
    assert changes.mock_calls == []


def test_archive_success_has_no_follow_up_actions_and_no_job_work(commands):
    scenario, writer, _, _ = commands
    result = scenario.execute("tenant", "owner", "doc", command("ARCHIVE"))
    assert result["lifecycle_state"] == "ARCHIVED" and result["allowed_commands"] == []
    writer.append_event.assert_called_once_with("doc", 8, "DocumentArchived", "USER", "owner", {}, "command-id")
    writer.insert_job.assert_not_called()
    writer.review.assert_not_called()


@pytest.mark.parametrize("actor,is_admin,role", [("owner", False, "AUTHOR_CREATOR"), ("moderator", False, "MODERATOR_CREATOR"), ("admin", True, "AUTHOR_EDITOR")])
def test_confirmation_returns_current_view_with_callers_access_on_success_and_replay(commands, actor, is_admin, role):
    scenario, writer, queries, changes = commands
    writer.preview.side_effect = [{"id": "preview", "result": {"output": {}, "execution": {}}}, None]
    changes.execute.return_value = document(state_version=8, current_revision_id="revision-4")
    queries.get_document.return_value = {"document_id": "doc", "state_version": 8}
    request = command("CONFIRM_PREPARED_CHANGES", job_id="preview")
    result = scenario.execute("tenant", actor, "doc", request, is_admin, role)
    assert result["document"] == queries.get_document.return_value
    queries.get_document.assert_called_once_with("doc", actor, is_admin, role)
    receipt = writer.record_command.call_args.kwargs
    assert "document" not in receipt["response"]

    writer.command_receipt.return_value = receipt
    queries.get_document.return_value = {"document_id": "doc", "state_version": 9}
    replayed = scenario.execute("tenant", actor, "doc", request, is_admin, role)
    assert replayed["idempotent_replay"] is True
    assert replayed["document"]["state_version"] == 9
    assert queries.get_document.call_args.args == ("doc", actor, is_admin, role)
    changes.execute.assert_called_once()
    writer.record_command.assert_called_once()
