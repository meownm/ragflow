"""Apply a worker or confirmed-preview change plan in its caller's transaction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol
from enum import Enum

from business_documents.application.content import DocumentContent
from business_documents.application.errors import ConflictError, ValidationError
from business_documents.application.persistence import DocumentWriter
from business_documents.domain.change_plan import validate_change_inputs
from business_documents.domain.block_diff import with_block_ids
from business_documents.domain.errors import RuleViolation
from business_documents.domain.content import with_document_warnings
from business_documents.domain.job import SnapshotIndex, prompt_audit, requested_by
from business_documents.domain.review import ReviewState


Record = Mapping[str, Any]


class ChangeWriter(DocumentWriter, Protocol):
    def review(self, document_id: str, review_cycle: int) -> ReviewState: ...
    def revision(self, document_id: str, revision_id: str) -> Record: ...
    def next_revision_number(self, document_id: str) -> int: ...


class ChangeMode(Enum):
    APPLY = "apply"
    PREVIEW = "preview"
    CONFIRM = "confirm"


class ApplyChanges:
    def __init__(self, writer: ChangeWriter, content: DocumentContent, id_factory: Callable[[], str]):
        self._writer = writer
        self._content = content
        self._new_id = id_factory

    def execute(self, document: Record, job: Record, actor_id: str, output: object, execution: Record, *, mode: ChangeMode = ChangeMode.APPLY) -> dict[str, Any]:
        required_state = "IDLE" if mode is ChangeMode.CONFIRM else "APPLYING_CHANGES"
        if document["operation_state"] != required_state or document["lifecycle_state"] != "REVIEW":
            raise ConflictError("JOB_STATE_CONFLICT", "Change application is not active")
        if job["base_revision_id"] != document["current_revision_id"]:
            raise ConflictError("STALE_AI_RESULT", "Change plan targets an outdated revision")
        if mode is ChangeMode.CONFIRM and job["payload"].get("prompt") != self._content.prompt_descriptor("PLAN_CHANGES"):
            raise ConflictError("CHANGE_PREVIEW_STALE", "Prepare changes again with the current requirement verification policy")
        review = self._writer.review(document["id"], document["active_review_cycle"])
        plan = output.get("change_plan") if isinstance(output, dict) else None
        self._content.validate_contract("change_plan", plan)
        assert isinstance(plan, dict)
        if plan["base_revision_id"] != document["current_revision_id"] or plan["source_state_version"] != job["source_state_version"]:
            raise ConflictError("STALE_AI_RESULT", "Change plan does not match the immutable job snapshot")
        snapshot = SnapshotIndex.build(job, execution)
        proposal_id = job["payload"].get("apply_proposal_id")
        try:
            if proposal_id is not None:
                review = review.for_proposal_application(proposal_id)
            inputs = validate_change_inputs(review, plan, snapshot_source_ids=snapshot.event_ids, evidence_source_refs=snapshot.evidence_refs)
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error

        warnings = [*job["payload"].get("protocol", {}).get("warnings", []), *output.get("warnings", [])]
        draft = None
        if plan["operations"]:
            base = self._writer.revision(document["id"], document["current_revision_id"])
            draft = self._content.apply_change_plan(base["document_ast"], plan)
            if draft["template_version"] != document["template_version"]:
                raise ValidationError("TEMPLATE_VERSION_CONFLICT", "Updated document does not use the pinned template")
            draft = with_document_warnings(draft, warnings)
        elif warnings:
            base = self._writer.revision(document["id"], document["current_revision_id"])
            draft = with_document_warnings(base["document_ast"], warnings)

        next_lifecycle = "REVIEW" if proposal_id is not None else inputs.next_lifecycle
        if next_lifecycle == "AGREED" and mode is not ChangeMode.CONFIRM:
            current = draft if draft is not None else self._writer.revision(document["id"], document["current_revision_id"])["document_ast"]
            checked = self._content.validate_document_ast(current, previous_document=current) if current.get("section_scope") == "required" else current
            if checked.get("warnings") or output.get("warnings"):
                next_lifecycle = "REVIEW"

        version = document["state_version"] + 1
        event_id = self._new_id()
        changes: dict[str, Any] = {"operation_state": "IDLE", "state_version": version}
        if mode is ChangeMode.PREVIEW:
            kind = "ChangePreviewPrepared"
            payload = {"job_id": job["id"], "base_revision_id": job["base_revision_id"], "review_cycle": document["active_review_cycle"]}
        else:
            changes["lifecycle_state"] = next_lifecycle
            payload = {
                "job_id": job["id"],
                "revision_id": document["current_revision_id"],
                "review_cycle": document["active_review_cycle"],
                "review_continues": next_lifecycle == "REVIEW",
                "remaining_proposal_ids": list(inputs.pending_proposal_ids),
                "acknowledged_no_change_event_ids": list(inputs.acknowledged_event_ids),
                "source_event_ids": list(inputs.source_event_ids),
                "evidence_refs": list(inputs.evidence_refs),
                **prompt_audit(job),
                **({"execution": execution} if execution else {}),
            }
            kind = "ReviewContinuedWithoutChanges" if next_lifecycle == "REVIEW" else "ReviewAgreedWithoutChanges"
            if draft is not None and not _same_json_value(draft, with_block_ids(base["document_ast"])):
                body = self._content.render_document_ast(draft)
                revision_id = self._writer.insert_revision(
                    document["id"],
                    self._writer.next_revision_number(document["id"]),
                    draft,
                    body,
                    list(dict.fromkeys([event_id, *inputs.source_event_ids])),
                    requested_by(job, document["owner_id"]),
                    revision_id=self._new_id(),
                )
                changes["current_revision_id"] = revision_id
                kind = "ChangesApplied"
                payload.update(
                    base_revision_id=job["base_revision_id"],
                    revision_id=revision_id,
                    requested_by_actor_id=requested_by(job, document["owner_id"]),
                )
        updated = self._writer.update_document(document, changes)
        self._writer.append_event(document["id"], version, kind, "AI", actor_id, payload, job["correlation_id"], event_id=event_id)
        return updated


def _same_json_value(left: object, right: object) -> bool:
    """Compare persisted JSON without equating booleans with numbers."""

    if type(left) is not type(right):
        return type(left) in (int, float) and type(right) in (int, float) and left == right
    if isinstance(left, dict):
        return len(left) == len(right) and all(key in right and _same_json_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_value(a, b) for a, b in zip(left, right))
    return left == right
