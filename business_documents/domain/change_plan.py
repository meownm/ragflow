"""Authorization and complete disposition of an immutable review change plan."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from business_documents.domain.errors import RuleViolation
from business_documents.domain.review import ReviewState


@dataclass(frozen=True)
class ChangeInputs:
    source_event_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    acknowledged_event_ids: tuple[str, ...]
    pending_proposal_ids: tuple[str, ...]

    @property
    def next_lifecycle(self) -> str:
        return "REVIEW" if self.pending_proposal_ids else "AGREED"


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
        if kind == "AuthorCommentAdded" and review.comment_dispositions.get(event_id, {}).get("disposition") != "CONFIRMED_CHANGE":
            raise RuleViolation(
                "COMMENT_CHANGE_NOT_CONFIRMED",
                "A comment can authorize a change only when the current review assessment says CONFIRMED_CHANGE",
                {"event_id": event_id},
            )
        source_cycle, source_section = review.event_scopes.get(event_id, (None, None))
        if source_cycle != review.review_cycle:
            raise RuleViolation("CHANGE_SOURCE_REVIEW_CYCLE_CONFLICT", "Change source is outside the active review cycle", {"event_id": event_id})
        # Comment anchors identify feedback, not the complete scope of a request.
        if kind != "AuthorCommentAdded" and source_section is not None and source_section != operation["section_id"]:
            raise RuleViolation(
                "CHANGE_SOURCE_SECTION_CONFLICT",
                "Change source targets a different template section",
                {"event_id": event_id, "source_section_id": source_section, "operation_section_id": operation["section_id"]},
            )
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
    """Validate a schema-checked plan in source order, without performing I/O."""

    sources: list[str] = []
    evidence: list[str] = []
    for operation in plan["operations"]:
        if not isinstance(operation, dict) or not isinstance(operation.get("source_event_ids"), list) or not operation["source_event_ids"]:
            raise RuleViolation("UNSUPPORTED_CHANGE", "Every change operation requires source_event_ids")
        validate_snapshot_sources(snapshot_source_ids, operation["source_event_ids"])
        validate_evidence_sources(evidence_source_refs, operation.get("evidence_refs", []))
        _validate_operation_sources(review, operation)
        sources.extend(operation["source_event_ids"])
        evidence.extend(operation.get("evidence_refs", []))
    acknowledgements = plan.get("acknowledged_no_change_event_ids", [])
    acknowledged = set(acknowledgements)
    invalid = sorted(acknowledged - review.active_inputs)
    if invalid:
        raise RuleViolation(
            "INVALID_NO_CHANGE_ACKNOWLEDGEMENT",
            "Only authorizing events from the active review cycle can be acknowledged as no-change",
            {"event_ids": invalid},
        )
    used = set(sources)
    duplicate = sorted(acknowledged & used)
    if duplicate:
        raise RuleViolation(
            "CHANGE_INPUT_DISPOSITION_CONFLICT",
            "A review input cannot both authorize a change and be acknowledged as no-change",
            {"event_ids": duplicate},
        )
    accepted_no_change = sorted(acknowledged & review.accepted_inputs)
    if accepted_no_change:
        raise RuleViolation("ACCEPTED_PROPOSAL_ACKNOWLEDGED_NO_CHANGE", "An accepted proposal must authorize a concrete change operation", {"event_ids": accepted_no_change})
    invalid_comments = sorted(
        event_id for event_id in acknowledged if review.events_by_id[event_id]["event_type"] == "AuthorCommentAdded" and review.comment_dispositions.get(event_id, {}).get("disposition") != "NO_CHANGE"
    )
    if invalid_comments:
        raise RuleViolation(
            "COMMENT_NO_CHANGE_NOT_CONFIRMED",
            "A comment can be acknowledged as no-change only when the current review assessment says NO_CHANGE",
            {"event_ids": invalid_comments},
        )
    missing = review.active_inputs - used - acknowledged
    missing_accepted = sorted(missing & review.accepted_inputs)
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
    return ChangeInputs(tuple(sources), tuple(dict.fromkeys(evidence)), tuple(acknowledgements), review.pending_proposal_ids)
