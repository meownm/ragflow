"""Apply a worker or confirmed-preview change plan in its caller's transaction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol
from enum import Enum

from business_documents.application.content import DocumentContent
from business_documents.application.errors import ConflictError, ValidationError
from business_documents.application.persistence import DocumentWriter
from business_documents.domain.change_plan import validate_change_inputs
from business_documents.domain.errors import RuleViolation
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
        review = self._writer.review(document["id"], document["active_review_cycle"])
        if review.open_question_ids("REVIEW"):
            raise ConflictError("OPEN_REVIEW_QUESTIONS", "Changes cannot be applied while review questions are open")
        plan = output.get("change_plan") if isinstance(output, dict) else None
        self._content.validate_contract("change_plan", plan)
        assert isinstance(plan, dict)
        if plan["base_revision_id"] != document["current_revision_id"] or plan["source_state_version"] != job["source_state_version"]:
            raise ConflictError("STALE_AI_RESULT", "Change plan does not match the immutable job snapshot")
        snapshot = SnapshotIndex.build(job, execution)
        try:
            inputs = validate_change_inputs(review, plan, snapshot_source_ids=snapshot.event_ids, evidence_source_refs=snapshot.evidence_refs)
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error

        draft = None
        if plan["operations"]:
            base = self._writer.revision(document["id"], document["current_revision_id"])
            draft = self._content.apply_change_plan(base["document_ast"], plan)
            if draft["template_version"] != document["template_version"]:
                raise ValidationError("TEMPLATE_VERSION_CONFLICT", "Updated document does not use the pinned template")

        version = document["state_version"] + 1
        event_id = self._new_id()
        changes: dict[str, Any] = {"operation_state": "IDLE", "state_version": version}
        if mode is ChangeMode.PREVIEW:
            kind = "ChangePreviewPrepared"
            payload = {"job_id": job["id"], "base_revision_id": job["base_revision_id"], "review_cycle": document["active_review_cycle"]}
        else:
            changes["lifecycle_state"] = inputs.next_lifecycle
            payload = {
                "job_id": job["id"],
                "revision_id": document["current_revision_id"],
                "review_cycle": document["active_review_cycle"],
                "review_continues": bool(inputs.pending_proposal_ids),
                "remaining_proposal_ids": list(inputs.pending_proposal_ids),
                "acknowledged_no_change_event_ids": list(inputs.acknowledged_event_ids),
                **prompt_audit(job),
                **({"execution": execution} if execution else {}),
            }
            kind = "ReviewContinuedWithoutChanges" if inputs.pending_proposal_ids else "ReviewAgreedWithoutChanges"
            if draft is not None:
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
                    source_event_ids=list(inputs.source_event_ids),
                    evidence_refs=list(inputs.evidence_refs),
                )
        updated = self._writer.update_document(document, changes)
        self._writer.append_event(document["id"], version, kind, "AI", actor_id, payload, job["correlation_id"], event_id=event_id)
        return updated
