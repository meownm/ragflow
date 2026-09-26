"""Shared workflow states and quiescence rules for Business Documents."""

from __future__ import annotations

from enum import StrEnum
from collections.abc import Mapping
from typing import Any

from business_documents.domain.review import ReviewState
from business_documents.domain.errors import RuleViolation, StateConflict


class LifecycleState(StrEnum):
    INTAKE = "INTAKE"
    REVIEW = "REVIEW"
    AGREED = "AGREED"
    ARCHIVED = "ARCHIVED"


class CommandType(StrEnum):
    REQUEST_INTAKE_ASSESSMENT = "REQUEST_INTAKE_ASSESSMENT"
    REQUEST_REVIEW_ASSESSMENT = "REQUEST_REVIEW_ASSESSMENT"
    ANSWER_QUESTION = "ANSWER_QUESTION"
    REQUEST_DRAFT = "REQUEST_DRAFT"
    DECIDE_PROPOSAL = "DECIDE_PROPOSAL"
    ADD_COMMENT = "ADD_COMMENT"
    APPLY_CHANGES = "APPLY_CHANGES"
    PREPARE_CHANGES = "PREPARE_CHANGES"
    CONFIRM_PREPARED_CHANGES = "CONFIRM_PREPARED_CHANGES"
    DISCARD_PREPARED_CHANGES = "DISCARD_PREPARED_CHANGES"
    START_REVIEW = "START_REVIEW"
    REQUEST_EXPORT = "REQUEST_EXPORT"
    ARCHIVE = "ARCHIVE"


class OperationState(StrEnum):
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    ANALYZING_REVIEW = "ANALYZING_REVIEW"
    GENERATING_DRAFT = "GENERATING_DRAFT"
    APPLYING_CHANGES = "APPLYING_CHANGES"
    EXPORTING = "EXPORTING"
    FAILED = "FAILED"


ACTIVE_JOB_STATUSES = ("PENDING", "RUNNING", "RETRY")
_QUIESCENT_OPERATION_STATES = frozenset(
    {
        OperationState.IDLE.value,
        OperationState.FAILED.value,
    }
)

CHANGE_PREVIEW_TTL_MS = 24 * 60 * 60 * 1000


def is_current_change_preview(document: Mapping[str, Any], job: Mapping[str, Any], now: int) -> bool:
    return bool(
        document["lifecycle_state"] == "REVIEW"
        and document["operation_state"] == "IDLE"
        and job["document_id"] == document["id"]
        and job["job_type"] == "PLAN_CHANGES"
        and job["status"] == "COMPLETED"
        and job["source_state_version"] == document["state_version"] - 1
        and job["base_revision_id"] == document["current_revision_id"]
        and isinstance(job["payload"], dict)
        and job["payload"].get("preview_only") is True
        and job["payload"].get("active_review_cycle") == document["active_review_cycle"]
        and isinstance(job["result"], dict)
        and now - job["update_time"] <= CHANGE_PREVIEW_TTL_MS
    )


def is_operation_quiescent(
    operation_state: object,
    has_active_job: bool = False,
) -> bool:
    """Return whether a mutation may safely proceed without invalidating work."""

    return not has_active_job and operation_state in _QUIESCENT_OPERATION_STATES


def _lifecycle_problem(document: Mapping[str, Any], required: str) -> StateConflict | None:
    if document["lifecycle_state"] != required:
        return StateConflict("LIFECYCLE_STATE_CONFLICT", f"Command requires {required} lifecycle state", {"actual": document["lifecycle_state"]})
    return None


def _idle_problem(document: Mapping[str, Any]) -> StateConflict | None:
    if not is_operation_quiescent(document["operation_state"]):
        return StateConflict("OPERATION_IN_PROGRESS", "Another operation is already in progress", {"operation_state": document["operation_state"]})
    return None


def command_problem(
    command: str,
    document: Mapping[str, Any],
    review: ReviewState | None,
    payload: Mapping[str, Any] | None = None,
    *,
    preview: Mapping[str, Any] | None = None,
) -> RuleViolation | None:
    """First command-state failure; omitted payload checks eligibility for the UI.

    The caller supplies a preview already checked against version, cycle and TTL.
    Entity-specific answer, proposal and comment validation follows this gate.
    """

    if document["lifecycle_state"] == "ARCHIVED":
        return StateConflict("DOCUMENT_ARCHIVED", "Archived documents cannot be changed")
    match command:
        case CommandType.REQUEST_INTAKE_ASSESSMENT:
            assert review is not None
            if review.open_question_ids("INTAKE"):
                return StateConflict("OPEN_INTAKE_QUESTIONS", "Answer open intake questions before reassessment")
            return _idle_problem(document) or _lifecycle_problem(document, "INTAKE")
        case CommandType.REQUEST_REVIEW_ASSESSMENT:
            if problem := _lifecycle_problem(document, "REVIEW"):
                return problem
            assert review is not None
            if review.open_question_ids("REVIEW") and not review.has_unassessed_feedback:
                return StateConflict("OPEN_REVIEW_QUESTIONS", "Add new review feedback before reassessment while questions remain open")
            return _idle_problem(document)
        case CommandType.REQUEST_DRAFT:
            if problem := _lifecycle_problem(document, "INTAKE"):
                return problem
            if document["current_revision_id"]:
                return StateConflict("DRAFT_ALREADY_EXISTS", "The document already has a draft")
            assert review is not None
            if questions := review.open_question_ids("INTAKE"):
                return StateConflict("OPEN_INTAKE_QUESTIONS", "Draft cannot be created while intake questions are open", {"question_ids": questions})
            if not review.intake_complete:
                return StateConflict("INTAKE_ASSESSMENT_REQUIRED", "Draft requires a current COMPLETE intake assessment")
            return _idle_problem(document)
        case CommandType.APPLY_CHANGES | CommandType.PREPARE_CHANGES:
            if problem := _lifecycle_problem(document, "REVIEW"):
                return problem
            if command == CommandType.APPLY_CHANGES and preview is not None:
                return StateConflict("CHANGE_PREVIEW_CONFIRMATION_REQUIRED", "Prepared changes must be confirmed or discarded before direct application")
            assert review is not None
            if questions := review.open_question_ids("REVIEW"):
                return StateConflict("OPEN_REVIEW_QUESTIONS", "Changes cannot be applied while review questions are open", {"question_ids": questions})
            if not review.review_complete:
                return StateConflict("REVIEW_ASSESSMENT_REQUIRED", "Changes require a current COMPLETE review assessment")
            if payload is not None and payload.get("base_revision_id") != document["current_revision_id"]:
                return StateConflict(
                    "BASE_REVISION_CONFLICT", "The change request does not target the current revision", {"expected": document["current_revision_id"], "actual": payload.get("base_revision_id")}
                )
            if review.pending_proposal_ids and not review.active_inputs:
                return StateConflict("PENDING_PROPOSAL_DECISIONS", "Decide a pending proposal or add review feedback before applying changes", {"proposal_ids": list(review.pending_proposal_ids)})
            return _idle_problem(document)
        case CommandType.CONFIRM_PREPARED_CHANGES | CommandType.DISCARD_PREPARED_CHANGES:
            if problem := _lifecycle_problem(document, "REVIEW") or _idle_problem(document):
                return problem
            if preview is None or (payload is not None and preview["id"] != payload.get("job_id")):
                return StateConflict("CHANGE_PREVIEW_STALE", "The prepared changes are no longer current")
            return None
        case CommandType.DECIDE_PROPOSAL | CommandType.ADD_COMMENT:
            return _idle_problem(document) or _lifecycle_problem(document, "REVIEW")
        case CommandType.START_REVIEW:
            return _idle_problem(document) or _lifecycle_problem(document, "AGREED")
        case CommandType.REQUEST_EXPORT:
            if document["lifecycle_state"] != "AGREED":
                return StateConflict("AGREED_REVISION_REQUIRED", "Export requires an agreed document revision")
            if payload is not None:
                if payload.get("revision_id") != document["current_revision_id"]:
                    return StateConflict("REVISION_NOT_AGREED", "Only the current agreed revision can be exported")
                if payload.get("format") not in {"MARKDOWN", "DOCX", "EVA_WIKI"}:
                    return RuleViolation("INVALID_EXPORT_FORMAT", "Unsupported export format")
            return _idle_problem(document)
        case CommandType.ANSWER_QUESTION | CommandType.ARCHIVE:
            return _idle_problem(document)
        case _:
            return RuleViolation("UNKNOWN_COMMAND", "Unsupported command type")


def available_commands(document: Mapping[str, Any], review: ReviewState, *, preview: Mapping[str, Any] | None = None) -> list[str]:
    """Choose ordered UI actions, then apply the same state rules as execution."""

    lifecycle = document["lifecycle_state"]
    if not is_operation_quiescent(document["operation_state"]) or lifecycle == "ARCHIVED":
        return []
    if lifecycle == "INTAKE":
        commands = ["ARCHIVE"]
        if review.open_question_ids("INTAKE"):
            commands.append("ANSWER_QUESTION")
        elif review.intake_complete:
            commands.append("REQUEST_DRAFT")
        else:
            commands.append("REQUEST_INTAKE_ASSESSMENT")
    elif lifecycle == "REVIEW":
        commands = ["DECIDE_PROPOSAL", "ADD_COMMENT", "ARCHIVE"]
        if review.open_question_ids("REVIEW"):
            commands.append("ANSWER_QUESTION")
            if review.has_unassessed_feedback:
                commands.append("REQUEST_REVIEW_ASSESSMENT")
        elif review.review_complete:
            commands.extend(("APPLY_CHANGES", "PREPARE_CHANGES"))
        else:
            commands.append("REQUEST_REVIEW_ASSESSMENT")
    elif lifecycle == "AGREED":
        commands = ["START_REVIEW", "REQUEST_EXPORT", "ARCHIVE"]
    else:
        return []
    if preview is not None:
        commands = [command for command in commands if command not in {"APPLY_CHANGES", "PREPARE_CHANGES"}]
        commands.extend(("CONFIRM_PREPARED_CHANGES", "DISCARD_PREPARED_CHANGES"))
    return [command for command in commands if command_problem(command, document, review, preview=preview) is None]
