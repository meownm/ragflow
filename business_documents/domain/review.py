"""Request-local review facts calculated without persistence or bootstrap imports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


Record = Mapping[str, Any]
REVIEW_FEEDBACK = frozenset({"QuestionAnswered", "ProposalDecided", "AuthorCommentAdded", "EvaDocumentPulled"})
_REVIEW_BOUNDARIES = frozenset({"DraftCreated", "ReviewCycleStarted", "ReviewAssessed"})
_RESOLUTION_EVENTS = frozenset({"ChangesApplied", "ReviewAgreedWithoutChanges", "ReviewContinuedWithoutChanges"})


def _latest_by(rows: Iterable[Record], key: str) -> dict[str, Record]:
    result: dict[str, Record] = {}
    for row in rows:
        previous = result.get(row[key])
        if previous is None or (row.get("create_time") or 0, row["id"]) > (previous.get("create_time") or 0, previous["id"]):
            result[row[key]] = row
    return result


@dataclass(frozen=True)
class ReviewState:
    document_id: str
    review_cycle: int
    questions: tuple[Record, ...]
    answers: Mapping[str, Record]
    proposals: tuple[Record, ...]
    decisions: Mapping[str, Record]
    comments: tuple[Record, ...]
    events: tuple[Record, ...]
    events_by_id: Mapping[str, Record]
    event_scopes: Mapping[str, tuple[int | None, str | None]]
    protocol_events: Mapping[str, Mapping[str, str]]
    comment_events: Mapping[str, Record]
    comment_dispositions: Mapping[str, Record]
    accepted_inputs: frozenset[str]
    active_inputs: frozenset[str]
    pending_proposal_ids: tuple[str, ...]
    assessments: Mapping[tuple[str, int], Record]
    latest_sequences: Mapping[str, int]
    has_unassessed_feedback: bool

    def open_question_ids(self, stage: str) -> list[str]:
        return [row["id"] for row in self.questions if row["stage"] == stage and row["id"] not in self.answers]

    def assessment_is_current(self, event_type: str, invalidating_types: Iterable[str], review_cycle: int) -> bool:
        assessment = self.assessments.get((event_type, review_cycle))
        return bool(assessment and assessment["payload"].get("outcome") == "COMPLETE" and assessment["sequence"] > max((self.latest_sequences.get(kind, 0) for kind in invalidating_types), default=0))

    @property
    def intake_complete(self) -> bool:
        return self.assessment_is_current("IntakeAssessed", {"QuestionAnswered"}, 0)

    @property
    def review_complete(self) -> bool:
        return self.assessment_is_current("ReviewAssessed", REVIEW_FEEDBACK, self.review_cycle)


def build_review_state(
    document_id: str,
    review_cycle: int,
    *,
    questions: Iterable[Record],
    answers: Iterable[Record],
    proposals: Iterable[Record],
    decisions: Iterable[Record],
    comments: Iterable[Record],
    events: Iterable[Record],
) -> ReviewState:
    """Index ordered document records once, preserving published protocol order."""

    questions_by_id = {row["id"]: row for row in questions}
    proposals_by_id = {row["id"]: row for row in proposals}
    comments_by_id = {row["id"]: row for row in comments}
    current_questions = tuple(row for row in questions_by_id.values() if row["review_cycle"] == review_cycle)
    current_proposals = tuple(row for row in proposals_by_id.values() if row["review_cycle"] == review_cycle)
    current_comments = tuple(row for row in comments_by_id.values() if row["review_cycle"] == review_cycle)
    answer_index = _latest_by(answers, "question_id")
    decision_index = _latest_by(decisions, "proposal_id")
    ordered_events = tuple(events)
    event_index = {}
    scopes = {}
    protocol_events: dict[str, dict[str, str]] = {"answers": {}, "decisions": {}, "comments": {}}
    comment_events = {}
    assessments = {}
    latest_sequences = {}
    accepted: set[str] = set()
    related: set[str] = set()
    resolved: set[str] = set()
    latest_review_assessment = None
    latest_eva_pull = None
    latest_review_event_type = None
    for event in ordered_events:
        event_id, kind = event["id"], event["event_type"]
        payload = event["payload"] if isinstance(event["payload"], dict) else {}
        event_index[event_id] = event
        latest_sequences[kind] = event["sequence"]
        if kind in REVIEW_FEEDBACK | _REVIEW_BOUNDARIES:
            latest_review_event_type = kind
        if kind in {"IntakeAssessed", "ReviewAssessed"} and isinstance(payload.get("review_cycle"), int):
            assessments[kind, payload["review_cycle"]] = event
        if kind == "ReviewAssessed":
            latest_review_assessment = payload
        if kind in _RESOLUTION_EVENTS:
            for key in ("source_event_ids", "acknowledged_no_change_event_ids"):
                values = payload.get(key, [])
                if isinstance(values, list):
                    resolved.update(value for value in values if isinstance(value, str))
        if kind == "EvaDocumentPulled":
            scopes[event_id] = payload.get("review_cycle"), None
            latest_eva_pull = event
            continue
        if kind == "QuestionAnswered":
            row = questions_by_id.get(payload.get("question_id"))
            if payload.get("answer_id"):
                protocol_events["answers"][payload["answer_id"]] = event_id
            if row and row["review_cycle"] == review_cycle and row["stage"] == "REVIEW":
                related.add(event_id)
        elif kind == "ProposalDecided":
            row = proposals_by_id.get(payload.get("proposal_id"))
            if payload.get("proposal_id"):
                protocol_events["decisions"][payload["proposal_id"]] = event_id
            if row and row["review_cycle"] == review_cycle and payload.get("decision") == "ACCEPTED":
                accepted.add(event_id)
        elif kind == "AuthorCommentAdded":
            row = comments_by_id.get(payload.get("comment_id"))
            if payload.get("comment_id"):
                protocol_events["comments"][payload["comment_id"]] = event_id
            if row and row["review_cycle"] == review_cycle:
                related.add(event_id)
                comment_events[event_id] = event
        else:
            continue
        if row:
            scopes[event_id] = row["review_cycle"], row.get("section_id") if kind == "AuthorCommentAdded" else row.get("target_section_id")
    if latest_eva_pull is not None and latest_eva_pull["payload"].get("review_cycle") == review_cycle:
        related.add(latest_eva_pull["id"])
    dispositions = {}
    if latest_review_assessment is not None and latest_review_assessment.get("review_cycle") == review_cycle:
        dispositions = {item["comment_event_id"]: item for item in latest_review_assessment.get("comment_dispositions", []) if isinstance(item, dict) and isinstance(item.get("comment_event_id"), str)}
    return ReviewState(
        document_id=document_id,
        review_cycle=review_cycle,
        questions=current_questions,
        answers=answer_index,
        proposals=current_proposals,
        decisions=decision_index,
        comments=current_comments,
        events=ordered_events,
        events_by_id=event_index,
        event_scopes=scopes,
        protocol_events=protocol_events,
        comment_events=comment_events,
        comment_dispositions=dispositions,
        accepted_inputs=frozenset(accepted),
        active_inputs=frozenset((accepted | related) - resolved),
        pending_proposal_ids=tuple(row["id"] for row in current_proposals if row["id"] not in decision_index),
        assessments=assessments,
        latest_sequences=latest_sequences,
        has_unassessed_feedback=latest_review_event_type in REVIEW_FEEDBACK,
    )
