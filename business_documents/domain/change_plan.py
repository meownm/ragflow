"""Authorization and complete disposition of an immutable review change plan."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from business_documents.domain.errors import RuleViolation
from business_documents.domain.review import ReviewState, applicable_change_inputs


@dataclass(frozen=True)
class ChangeInputs:
    source_event_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    acknowledged_event_ids: tuple[str, ...]
    pending_proposal_ids: tuple[str, ...]
    pending_question_ids: tuple[str, ...]

    @property
    def next_lifecycle(self) -> str:
        return "REVIEW" if self.pending_proposal_ids or self.pending_question_ids else "AGREED"


def validate_snapshot_sources(allowed: frozenset[str], source_event_ids: Iterable[str]) -> None:
    unknown = [event_id for event_id in source_event_ids if event_id not in allowed]
    if unknown:
        raise RuleViolation(
            "SOURCE_NOT_IN_JOB_SNAPSHOT",
            "AI output references an event that was not present in its immutable job snapshot",
            {"event_ids": unknown},
        )


def validate_evidence_sources(allowed: frozenset[str], evidence_refs: object) -> None:
    if not evidence_refs:
        return
    if not isinstance(evidence_refs, list) or not all(isinstance(item, str) for item in evidence_refs):
        raise RuleViolation("INVALID_EVIDENCE_REFS", "evidence_refs must be an array of source references")
    unknown = [source_ref for source_ref in evidence_refs if source_ref not in allowed]
    if unknown:
        raise RuleViolation(
            "EVIDENCE_REF_NOT_IN_SNAPSHOT",
            "AI output cites evidence outside the pinned retrieval snapshot",
            {"source_refs": unknown},
        )


def validate_comment_change_sources(dispositions: Mapping[str, Mapping[str, Any]], source_event_ids: Iterable[str], *, no_change: bool = False) -> None:
    """Keep plan attribution consistent with the pinned review decision."""
    expected = "NO_CHANGE" if no_change else "CONFIRMED_CHANGE"
    invalid = sorted(event_id for event_id in source_event_ids if dispositions.get(event_id, {}).get("disposition") != expected)
    if invalid:
        raise RuleViolation(
            "COMMENT_NO_CHANGE_NOT_CONFIRMED" if no_change else "COMMENT_CHANGE_NOT_CONFIRMED",
            "A comment can be acknowledged as no-change only when the current review assessment says NO_CHANGE"
            if no_change
            else "A comment can authorize a change only when the current review assessment says CONFIRMED_CHANGE",
            {"event_ids": invalid} if no_change else {"event_id": invalid[0]},
        )


def _validate_operation_sources(review: ReviewState, operation: Mapping[str, Any]) -> None:
    source_ids = operation["source_event_ids"]
    missing = [event_id for event_id in source_ids if event_id not in review.events_by_id]
    if missing:
        raise RuleViolation("UNKNOWN_CHANGE_SOURCE", "Result references unknown document events", {"event_ids": missing})
    for event_id in source_ids:
        event = review.events_by_id[event_id]
        kind = event["event_type"]
        if kind not in {"QuestionAnswered", "ProposalDecided", "AuthorCommentAdded", "EvaDocumentPulled"}:
            raise RuleViolation("INVALID_CHANGE_SOURCE_TYPE", "Event type cannot authorize a document change", {"event_id": event_id})
        if kind == "ProposalDecided" and event["payload"].get("decision") != "ACCEPTED":
            raise RuleViolation("REJECTED_PROPOSAL_SOURCE", "Rejected proposals cannot support a change", {"event_id": event_id})
        if kind == "AuthorCommentAdded":
            validate_comment_change_sources(review.comment_dispositions, [event_id])
        source_cycle, _ = review.event_scopes.get(event_id, (None, None))
        if source_cycle != review.review_cycle:
            raise RuleViolation("CHANGE_SOURCE_REVIEW_CYCLE_CONFLICT", "Change source is outside the active review cycle", {"event_id": event_id})
        if event_id not in review.active_inputs:
            raise RuleViolation(
                "CHANGE_SOURCE_NOT_ACTIVE",
                "A resolved or superseded review input cannot authorize another document change",
                {"event_id": event_id},
            )


def validate_change_inputs(
    review: ReviewState,
    plan: Mapping[str, Any],
    *,
    snapshot_source_ids: frozenset[str],
    evidence_source_refs: frozenset[str],
) -> ChangeInputs:
    """Revalidate attribution and current review state in the commit transaction."""
    sources, evidence, acknowledgements = validate_plan_sources(
        plan,
        snapshot_source_ids=snapshot_source_ids,
        evidence_source_refs=evidence_source_refs,
        active_inputs=review.active_inputs,
        accepted_inputs=review.accepted_inputs,
        dispositions={event_id: review.comment_dispositions.get(event_id, {}) for event_id in review.comment_events},
        review=review,
    )
    return ChangeInputs(sources, evidence, acknowledgements, review.pending_proposal_ids, tuple(review.open_question_ids("REVIEW")))


def validate_plan_sources(
    plan: Mapping[str, Any],
    *,
    snapshot_source_ids: frozenset[str],
    evidence_source_refs: frozenset[str],
    active_inputs: frozenset[str],
    accepted_inputs: frozenset[str],
    dispositions: Mapping[str, Mapping[str, Any]],
    review: ReviewState | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """One attribution/completeness policy for pinned generation and current commit.

    A commit additionally checks event kinds, review cycles and resolved inputs
    against its locked review; generation uses its already pinned active sources.
    """

    sources: list[str] = []
    evidence: list[str] = []
    for operation in plan["operations"]:
        if not isinstance(operation, dict) or not isinstance(operation.get("source_event_ids"), list) or not operation["source_event_ids"]:
            raise RuleViolation("UNSUPPORTED_CHANGE", "Every change operation requires source_event_ids")
        validate_snapshot_sources(snapshot_source_ids, operation["source_event_ids"])
        validate_evidence_sources(evidence_source_refs, operation.get("evidence_refs", []))
        if review is not None:
            _validate_operation_sources(review, operation)
        else:
            validate_comment_change_sources(dispositions, (event_id for event_id in operation["source_event_ids"] if event_id in dispositions))
        sources.extend(operation["source_event_ids"])
        evidence.extend(operation.get("evidence_refs", []))
    acknowledgements = plan.get("acknowledged_no_change_event_ids", [])
    if review is None:
        validate_snapshot_sources(snapshot_source_ids, acknowledgements)
    acknowledged = set(acknowledgements)
    invalid = sorted(acknowledged - active_inputs)
    if invalid:
        raise RuleViolation(
            "INVALID_NO_CHANGE_ACKNOWLEDGEMENT",
            "Only authorizing events from the active review cycle can be acknowledged as no-change",
            {"event_ids": invalid},
        )
    validate_snapshot_sources(snapshot_source_ids, acknowledgements)
    used = set(sources)
    duplicate = sorted(acknowledged & used)
    if duplicate:
        raise RuleViolation(
            "CHANGE_INPUT_DISPOSITION_CONFLICT",
            "A review input cannot both authorize a change and be acknowledged as no-change",
            {"event_ids": duplicate},
        )
    accepted_no_change = sorted(acknowledged & accepted_inputs)
    if accepted_no_change:
        raise RuleViolation("ACCEPTED_PROPOSAL_ACKNOWLEDGED_NO_CHANGE", "An accepted proposal must authorize a concrete change operation", {"event_ids": accepted_no_change})
    validate_comment_change_sources(
        dispositions,
        (event_id for event_id in acknowledged if event_id in dispositions),
        no_change=True,
    )
    applicable = applicable_change_inputs(active_inputs, dispositions)
    missing = applicable - used - acknowledged
    missing_accepted = sorted(missing & accepted_inputs)
    if missing_accepted:
        raise RuleViolation(
            "ACCEPTED_PROPOSAL_OMITTED",
            "Every accepted proposal in the active review cycle must be represented in the change plan",
            {"event_ids": missing_accepted},
        )
    if missing:
        raise RuleViolation(
            "CHANGE_INPUT_OMITTED",
            "Every active review answer and comment must authorize a change or be explicitly acknowledged as no-change",
            {"event_ids": sorted(missing)},
        )
    return tuple(sources), tuple(dict.fromkeys(evidence)), tuple(acknowledgements)


def active_change_input_event_ids(job_payload: dict[str, Any]) -> list[str]:
    pinned = job_payload.get("active_change_input_event_ids")
    if isinstance(pinned, list) and all(isinstance(event_id, str) for event_id in pinned):
        return list(dict.fromkeys(pinned))
    protocol = job_payload.get("protocol")
    if not isinstance(protocol, dict):
        return []
    event_ids: list[str] = []
    for question in protocol.get("questions", []):
        answer = question.get("answer") if isinstance(question, dict) else None
        if isinstance(answer, dict) and isinstance(answer.get("source_event_id"), str):
            event_ids.append(answer["source_event_id"])
    for proposal in protocol.get("proposals", []):
        if isinstance(proposal, dict) and proposal.get("decision") == "ACCEPTED" and isinstance(proposal.get("decision_event_id"), str):
            event_ids.append(proposal["decision_event_id"])
    for comment in protocol.get("comments", []):
        if isinstance(comment, dict) and isinstance(comment.get("source_event_id"), str):
            event_ids.append(comment["source_event_id"])
    return list(dict.fromkeys(event_ids))


def comment_dispositions(job_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        comment["source_event_id"]: comment.get("disposition") or {}
        for comment in (job_payload.get("protocol") or {}).get("comments", [])
        if isinstance(comment, dict) and isinstance(comment.get("source_event_id"), str)
    }
