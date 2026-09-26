"""Command error priority and UI eligibility share the document state policy."""

from dataclasses import replace

import pytest

from business_documents.domain.errors import StateConflict
from business_documents.domain.review import build_review_state
from business_documents.domain.workflow import available_commands, command_problem
from test.unit_test.business_documents.domain.test_review import _event, _review


def _document(**changes):
    return {"lifecycle_state": "REVIEW", "operation_state": "ANALYZING_REVIEW", "current_revision_id": "revision", **changes}


@pytest.mark.parametrize(
    "command,document,review,payload,preview,code",
    [
        ("APPLY_CHANGES", _document(), _review(_event(8, "EvaDocumentPulled", review_cycle=2)), {}, {"id": "preview"}, "CHANGE_PREVIEW_CONFIRMATION_REQUIRED"),
        ("APPLY_CHANGES", _document(), _review(_event(8, "EvaDocumentPulled", review_cycle=2)), {"base_revision_id": "wrong"}, None, "REVIEW_ASSESSMENT_REQUIRED"),
        ("APPLY_CHANGES", _document(), replace(_review(), active_inputs=frozenset()), {"base_revision_id": "wrong"}, None, "BASE_REVISION_CONFLICT"),
        ("PREPARE_CHANGES", _document(), replace(_review(), active_inputs=frozenset()), {"base_revision_id": "revision"}, None, "PENDING_PROPOSAL_DECISIONS"),
        ("REQUEST_DRAFT", _document(lifecycle_state="INTAKE"), _review(), {}, None, "DRAFT_ALREADY_EXISTS"),
        ("REQUEST_REVIEW_ASSESSMENT", _document(lifecycle_state="AGREED"), _review(), {}, None, "LIFECYCLE_STATE_CONFLICT"),
        ("DECIDE_PROPOSAL", _document(lifecycle_state="AGREED"), None, {}, None, "OPERATION_IN_PROGRESS"),
        ("START_REVIEW", _document(), None, {}, None, "OPERATION_IN_PROGRESS"),
        ("REQUEST_EXPORT", _document(), None, {"revision_id": "wrong", "format": "invalid"}, None, "AGREED_REVISION_REQUIRED"),
        ("REQUEST_EXPORT", _document(lifecycle_state="AGREED"), None, {"revision_id": "wrong", "format": "invalid"}, None, "REVISION_NOT_AGREED"),
        ("REQUEST_EXPORT", _document(lifecycle_state="AGREED"), None, {"revision_id": "revision", "format": "invalid"}, None, "INVALID_EXPORT_FORMAT"),
        ("CONFIRM_PREPARED_CHANGES", _document(operation_state="FAILED"), None, {"job_id": "missing"}, None, "CHANGE_PREVIEW_STALE"),
        ("ANSWER_QUESTION", _document(lifecycle_state="ARCHIVED"), None, {}, None, "DOCUMENT_ARCHIVED"),
    ],
)
def test_first_error_preserves_command_specific_order(command, document, review, payload, preview, code):
    problem = command_problem(command, document, review, payload, preview=preview)
    assert problem.code == code
    assert isinstance(problem, StateConflict) == (code != "INVALID_EXPORT_FORMAT")


def test_completed_intake_prefers_draft_but_direct_reassessment_remains_valid():
    review = build_review_state(
        "document",
        0,
        questions=[],
        answers=[],
        proposals=[],
        decisions=[],
        comments=[],
        events=[_event(1, "IntakeAssessed", review_cycle=0, outcome="COMPLETE")],
    )
    document = _document(lifecycle_state="INTAKE", operation_state="IDLE", current_revision_id=None)
    assert available_commands(document, review) == ["ARCHIVE", "REQUEST_DRAFT"]
    assert command_problem("REQUEST_INTAKE_ASSESSMENT", document, review, {}) is None


def test_preview_replaces_apply_in_ui_but_explicit_reprepare_remains_valid():
    document, review, preview = _document(operation_state="IDLE"), _review(), {"id": "preview"}
    commands = available_commands(document, review, preview=preview)
    assert commands == ["DECIDE_PROPOSAL", "ADD_COMMENT", "ARCHIVE", "CONFIRM_PREPARED_CHANGES", "DISCARD_PREPARED_CHANGES"]
    assert all(command_problem(command, document, review, preview=preview) is None for command in commands)
    assert command_problem("PREPARE_CHANGES", document, review, {"base_revision_id": "revision"}, preview=preview) is None


def test_view_does_not_offer_an_existing_draft_again():
    review = build_review_state(
        "document",
        0,
        questions=[],
        answers=[],
        proposals=[],
        decisions=[],
        comments=[],
        events=[_event(1, "IntakeAssessed", review_cycle=0, outcome="COMPLETE")],
    )
    assert available_commands(_document(lifecycle_state="INTAKE", operation_state="IDLE"), review) == ["ARCHIVE"]
