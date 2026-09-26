"""Review history and change-plan invariants, independent of database/bootstrap."""

from dataclasses import replace

import pytest

from business_documents.domain.change_plan import validate_change_inputs
from business_documents.domain.errors import RuleViolation
from business_documents.domain.review import build_review_state
from business_documents.domain.workflow import available_commands


def _event(sequence, kind, **payload):
    return {"id": f"e{sequence}", "sequence": sequence, "event_type": kind, "payload": payload}


def _review(*extra_events, dispositions=None):
    return build_review_state(
        "document",
        2,
        questions=[
            {"id": "old-question", "review_cycle": 1, "stage": "REVIEW", "target_section_id": "3.1"},
            {"id": "question", "review_cycle": 2, "stage": "REVIEW", "target_section_id": "3.1"},
        ],
        answers=[{"id": "answer", "question_id": "question", "create_time": 1}],
        proposals=[
            {"id": "proposal", "review_cycle": 2, "target_section_id": "3.1"},
            {"id": "pending", "review_cycle": 2, "target_section_id": "3.1"},
        ],
        decisions=[{"id": "decision", "proposal_id": "proposal", "decision": "ACCEPTED", "create_time": 2}],
        comments=[{"id": "comment", "review_cycle": 2, "section_id": "3.1"}],
        events=[
            _event(1, "QuestionAnswered", question_id="old-question", answer_id="old-answer"),
            _event(2, "QuestionAnswered", question_id="question", answer_id="answer"),
            _event(3, "ProposalDecided", proposal_id="proposal", decision="ACCEPTED"),
            _event(4, "AuthorCommentAdded", comment_id="comment"),
            _event(5, "EvaDocumentPulled", review_cycle=2),
            _event(6, "EvaDocumentPulled", review_cycle=2),
            _event(7, "ReviewAssessed", review_cycle=2, outcome="COMPLETE", comment_dispositions=dispositions or [{"comment_event_id": "e4", "disposition": "CONFIRMED_CHANGE"}]),
            *extra_events,
        ],
    )


def _operation(*sources, section="3.1"):
    return {"section_id": section, "source_event_ids": list(sources)}


def _validate(review, operations, acknowledged=(), snapshot=None):
    return validate_change_inputs(
        review,
        {"operations": operations, "acknowledged_no_change_event_ids": list(acknowledged)},
        snapshot_source_ids=frozenset(review.events_by_id if snapshot is None else snapshot),
        evidence_source_refs=frozenset(),
    )


def test_current_inputs_exclude_old_cycle_superseded_eva_and_already_applied_sources():
    review = _review(_event(8, "ChangesApplied", source_event_ids=["e2", "e3"], acknowledged_no_change_event_ids=[]))
    assert review.active_inputs == {"e4", "e6"}
    assert review.accepted_inputs == {"e3"}
    assert review.pending_proposal_ids == ("pending",)
    assert review.open_question_ids("REVIEW") == []
    assert review.protocol_events["answers"]["answer"] == "e2"
    assert review.review_complete


def test_new_eva_input_invalidates_assessment_and_available_commands():
    review = _review(_event(8, "EvaDocumentPulled", review_cycle=2))
    assert not review.review_complete
    assert review.has_unassessed_feedback
    assert review.active_inputs == {"e2", "e3", "e4", "e8"}
    assert available_commands({"lifecycle_state": "REVIEW", "operation_state": "IDLE", "current_revision_id": "revision"}, review) == [
        "DECIDE_PROPOSAL",
        "ADD_COMMENT",
        "ARCHIVE",
        "REQUEST_REVIEW_ASSESSMENT",
    ]


def test_latest_incomplete_assessment_cannot_fall_back_to_an_earlier_complete_one():
    review = _review(_event(8, "ReviewAssessed", review_cycle=2, outcome="NEEDS_INPUT", comment_dispositions=[]))
    assert not review.review_complete
    assert review.comment_dispositions == {}
    assert not review.has_unassessed_feedback


def test_one_confirmed_comment_may_authorize_multiple_sections_and_order_is_preserved():
    inputs = _validate(_review(), [_operation("e2", "e3", "e4"), _operation("e4", section="5.5")], ["e6"])
    assert inputs.source_event_ids == ("e2", "e3", "e4", "e4")
    assert inputs.acknowledged_event_ids == ("e6",)
    assert inputs.next_lifecycle == "REVIEW"
    assert replace(inputs, pending_proposal_ids=()).next_lifecycle == "AGREED"


@pytest.mark.parametrize(
    ("operations", "acknowledged", "code"),
    [
        ([_operation("e1")], [], "CHANGE_SOURCE_REVIEW_CYCLE_CONFLICT"),
        ([_operation("e5")], [], "CHANGE_SOURCE_NOT_ACTIVE"),
        ([_operation("e7")], [], "INVALID_CHANGE_SOURCE_TYPE"),
        ([_operation("e2", section="5.5")], [], "CHANGE_SOURCE_SECTION_CONFLICT"),
        ([_operation("e2", "e3", "e4")], ["e6", "e4"], "CHANGE_INPUT_DISPOSITION_CONFLICT"),
        ([_operation("e2", "e4")], ["e3", "e6"], "ACCEPTED_PROPOSAL_ACKNOWLEDGED_NO_CHANGE"),
        ([_operation("e2", "e4")], ["e6"], "ACCEPTED_PROPOSAL_OMITTED"),
        ([_operation("e3", "e4")], ["e6"], "CHANGE_INPUT_OMITTED"),
        ([_operation("e2", "e3")], ["e4", "e6"], "COMMENT_NO_CHANGE_NOT_CONFIRMED"),
        ([_operation("e2", "e3", "e4")], ["e6", "foreign"], "INVALID_NO_CHANGE_ACKNOWLEDGEMENT"),
    ],
)
def test_plan_authorization_rejects_invalid_dispositions(operations, acknowledged, code):
    with pytest.raises(RuleViolation) as caught:
        _validate(_review(), operations, acknowledged)
    assert caught.value.code == code


def test_snapshot_membership_is_checked_before_live_event_scope():
    with pytest.raises(RuleViolation) as caught:
        _validate(_review(), [_operation("e1")], snapshot={"e2", "e3", "e4", "e6"})
    assert caught.value.code == "SOURCE_NOT_IN_JOB_SNAPSHOT"
    assert caught.value.details == {"event_ids": ["e1"]}


def test_resolved_accepted_proposal_cannot_authorize_another_change():
    review = _review(_event(8, "ChangesApplied", source_event_ids=["e3"]))
    with pytest.raises(RuleViolation) as caught:
        _validate(review, [_operation("e3")])
    assert caught.value.code == "CHANGE_SOURCE_NOT_ACTIVE"


def test_no_change_comment_can_be_acknowledged_but_cannot_authorize_an_operation():
    review = _review(dispositions=[{"comment_event_id": "e4", "disposition": "NO_CHANGE"}])
    inputs = _validate(review, [_operation("e2", "e3")], ["e4", "e6"])
    assert inputs.acknowledged_event_ids == ("e4", "e6")
    with pytest.raises(RuleViolation) as caught:
        _validate(review, [_operation("e4")])
    assert caught.value.code == "COMMENT_CHANGE_NOT_CONFIRMED"
