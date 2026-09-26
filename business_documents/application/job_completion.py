"""Finalize document jobs atomically under their current version and lease."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from business_documents.application.content import DocumentContent
from business_documents.application.change_application import ApplyChanges, ChangeMode
from business_documents.application.errors import ConflictError, ValidationError
from business_documents.application.export import ExportCommitter
from business_documents.application.persistence import DocumentWriter
from business_documents.application.queries import DocumentQueries
from business_documents.domain.assessment import canonical_tag, normalize_proposal, normalize_question, validate_comment_dispositions
from business_documents.domain.change_plan import validate_evidence_sources, validate_snapshot_sources
from business_documents.domain.errors import RuleViolation
from business_documents.domain.execution_audit import validate_audit_envelope, validate_ai_audit, validate_retrieval_audit, verify_pinned_evidence
from business_documents.domain.job import SnapshotIndex, has_current_lease, prompt_audit, requested_by


Record = Mapping[str, Any]


@dataclass(frozen=True)
class AssessmentRecords:
    questions: tuple[Record, ...]
    proposals: tuple[Record, ...]
    answered_ids: frozenset[str]
    comment_event_ids: frozenset[str]


class AssessmentWriter(DocumentWriter, Protocol):
    def assessment(self, document_id: str, review_cycle: int, *, comments: bool = False) -> AssessmentRecords: ...
    def open_questions(self, document_id: str, stage: str, review_cycle: int) -> list[str]: ...
    def insert_question(self, values: Record) -> tuple[str, bool]: ...
    def insert_proposal(self, values: Record) -> tuple[str, bool]: ...


class JobWriter(AssessmentWriter, Protocol):
    def transaction(self) -> AbstractContextManager: ...
    def lock_job(self, tenant_id: str, job_id: str) -> tuple[Record, Record]: ...
    def document(self, tenant_id: str, document_id: str) -> Record: ...
    def evidence_snapshot(self, job_id: str) -> Record | None: ...
    def finish_job(self, job: Record, worker_id: str, lease_token: str | None, completion_time: int, changes: Record) -> int: ...


class _AssessmentBatch:
    def __init__(self, writer: AssessmentWriter, content: DocumentContent, new_id: Callable[[], str], document_id: str, review_cycle: int, snapshot: SnapshotIndex, *, comments: bool = False):
        self.writer, self.content, self.new_id = writer, content, new_id
        self.document_id, self.review_cycle = document_id, review_cycle
        self.records = writer.assessment(document_id, review_cycle, comments=comments)
        self.questions = {(row["stage"], row["semantic_tag"]): row["id"] for row in self.records.questions}
        self.proposals = {(row["fingerprint"], row["source_scope_hash"]): row["id"] for row in self.records.proposals}
        self.snapshot = snapshot
        self._section_ids: frozenset[str] | None = None

    @property
    def section_ids(self) -> frozenset[str]:
        if self._section_ids is None:
            self._section_ids = frozenset(section["id"] for section in self.content.published_template()["sections"])
        return self._section_ids

    def outcome(self, stage: str) -> str:
        return "NEEDS_INPUT" if any(row_stage == stage and row_id not in self.records.answered_ids for (row_stage, _), row_id in self.questions.items()) else "COMPLETE"

    def add_question(self, raw: Record, stage: str, event_id: str, comment_sources=()) -> tuple[str, bool]:
        try:
            validate_evidence_sources(self.snapshot.evidence_refs, raw.get("evidence_refs", []))
            values = normalize_question(raw, stage, self.section_ids)
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error
        key = stage, values["semantic_tag"]
        if key in self.questions:
            return self.questions[key], False
        question_id, inserted = self.writer.insert_question(
            {
                **values,
                "id": raw.get("id") or self.new_id(),
                "document_id": self.document_id,
                "review_cycle": self.review_cycle,
                "source_event_ids": list(dict.fromkeys([event_id, *comment_sources])),
            }
        )
        self.questions[key] = question_id
        return question_id, inserted

    def add_proposal(self, raw: Record, event_id: str) -> bool:
        try:
            validate_snapshot_sources(self.snapshot.event_ids, raw["source_event_ids"])
            validate_evidence_sources(self.snapshot.evidence_refs, raw.get("evidence_refs", []))
            values = normalize_proposal(raw, self.section_ids)
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error
        key = values["fingerprint"], values["source_scope_hash"]
        if key in self.proposals:
            return False
        proposal_id, inserted = self.writer.insert_proposal(
            {
                **values,
                "id": raw.get("id") or self.new_id(),
                "document_id": self.document_id,
                "review_cycle": self.review_cycle,
                "source_event_ids": list(dict.fromkeys([event_id, *raw["source_event_ids"]])),
            }
        )
        self.proposals[key] = proposal_id
        return inserted


class JobCompletion:
    def __init__(
        self,
        writer: JobWriter,
        content: DocumentContent,
        id_factory: Callable[[], str],
        *,
        clock: Callable[[], int],
        queries: DocumentQueries,
        changes: ApplyChanges,
        exports: ExportCommitter,
        retrieval_enabled: Callable[[], bool],
    ):
        self._writer, self._content, self._new_id = writer, content, id_factory
        self._clock, self._queries, self._changes, self._exports, self._retrieval_enabled = clock, queries, changes, exports, retrieval_enabled

    def complete(self, tenant_id: str, actor_id: str, job_id: str, output: object, lease_token: str | None = None, execution_audit: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._writer.transaction():
            job, document = self._writer.lock_job(tenant_id, job_id)
            if job["status"] == "COMPLETED":
                return self._queries.project(document)
            self._require_lease(job, actor_id, lease_token)
            execution = self._execution_audit(execution_audit, job)
            if document["state_version"] != job["source_state_version"]:
                raise ConflictError("STALE_AI_RESULT", "Worker result targets an outdated document state", {"source": job["source_state_version"], "actual": document["state_version"]})
            if job["job_type"] == "GENERATE_EXPORT":
                output = self._exports.commit(output, document=document, job=job)
            elif not isinstance(output, dict):
                raise ValidationError("INVALID_JOB_OUTPUT", "Worker output must be a JSON object")
            match job["job_type"]:
                case "ASSESS_INTAKE":
                    self._intake(document, job, actor_id, output, execution)
                case "ASSESS_REVIEW":
                    self._review(document, job, actor_id, output, execution)
                case "GENERATE_DRAFT":
                    self._draft(document, job, actor_id, output, execution)
                case "PLAN_CHANGES":
                    mode = ChangeMode.PREVIEW if job["payload"].get("preview_only") is True else ChangeMode.APPLY
                    document = self._changes.execute(document, job, actor_id, output, execution, mode=mode)
                case "GENERATE_EXPORT":
                    self._export(document, job, actor_id, output)
                case _:
                    raise ValidationError("UNKNOWN_JOB_TYPE", "Unsupported business document job type", {"job_type": job["job_type"]})
            self._finalize(
                job,
                actor_id,
                lease_token,
                {
                    "status": "COMPLETED",
                    "progress": 1.0,
                    "progress_stage": "COMPLETED",
                    "progress_message": "Обработка завершена",
                    "error": None,
                    "result": {"output": output, **prompt_audit(job), **({"execution": execution} if execution else {})},
                },
            )
            if job["job_type"] == "PLAN_CHANGES":
                event_type = "preview_ready" if job["payload"].get("preview_only") is True else "applied"
                self._writer.append_job_event(job["id"], event_type, {"state_version": document["state_version"]})
        return self._queries.project(self._writer.document(tenant_id, document["id"]))

    def fail(self, tenant_id: str, actor_id: str, job_id: str, error: dict[str, Any], lease_token: str | None = None) -> dict[str, Any]:
        with self._writer.transaction():
            job, document = self._writer.lock_job(tenant_id, job_id)
            if job["status"] == "DEAD":
                return self._queries.project(document)
            self._require_lease(job, actor_id, lease_token)
            version = document["state_version"] + 1
            self._writer.update_document(document, {"operation_state": "FAILED", "last_error": error, "state_version": version})
            self._writer.append_event(document["id"], version, "BusinessDocumentJobFailed", "SYSTEM", actor_id, {"job_id": job["id"], "error": error}, job["correlation_id"])
            self._finalize(job, actor_id, lease_token, {"status": "DEAD", "progress_stage": "FAILED", "progress_message": "Не удалось завершить обработку", "error": error})
            if job["job_type"] == "PLAN_CHANGES":
                self._writer.append_job_event(job["id"], "failed", {"code": error.get("code", "WORKER_FAILURE")})
        return self._queries.project(self._writer.document(tenant_id, document["id"]))

    def _require_lease(self, job, worker_id, lease_token):
        if not has_current_lease(job, worker_id, lease_token, self._clock()):
            raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")

    def _finalize(self, job, worker_id, lease_token, changes):
        changes = {**changes, "lease_owner": None, "lease_token": None, "lease_expires_at": None}
        if self._writer.finish_job(job, worker_id, lease_token, self._clock(), changes) != 1:
            raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")

    def _execution_audit(self, value, job):
        evidence_required = bool(job["job_type"] != "GENERATE_EXPORT" and job["payload"].get("dataset_ids") and self._retrieval_enabled())
        try:
            value = validate_audit_envelope(value, evidence_required=evidence_required)
            validated = {}
            if "retrieval" in value:
                retrieval = validate_retrieval_audit(value["retrieval"])
                verify_pinned_evidence(retrieval, self._writer.evidence_snapshot(job["id"]))
                validated["retrieval"] = retrieval
            if "ai" in value:
                validated["ai"] = validate_ai_audit(value["ai"])
            return validated
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error

    def _export(self, document, job, actor_id, output):
        if document["operation_state"] != "EXPORTING" or document["lifecycle_state"] != "AGREED":
            raise ConflictError("JOB_STATE_CONFLICT", "Export is not active")
        artifact_id = output.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValidationError("INVALID_EXPORT_RESULT", "artifact_id is required")
        version = document["state_version"] + 1
        self._writer.update_document(document, {"operation_state": "IDLE", "state_version": version})
        self._writer.append_event(
            document["id"],
            version,
            "ExportGenerated",
            "SYSTEM",
            actor_id,
            {"job_id": job["id"], "artifact_id": artifact_id, "format": job["payload"].get("command_payload", {}).get("format")},
            job["correlation_id"],
        )

    def _intake(self, document: Record, job: Record, actor_id: str, output: Record, execution: Record) -> dict[str, Any]:
        if document["operation_state"] != "ANALYZING" or document["lifecycle_state"] != "INTAKE":
            raise ConflictError("JOB_STATE_CONFLICT", "Intake assessment is not active")
        self._content.validate_contract("question_batch", output)
        batch = _AssessmentBatch(self._writer, self._content, self._new_id, document["id"], 0, SnapshotIndex.build(job, execution))
        event_id = self._new_id()
        count = sum(int(batch.add_question(question, "INTAKE", event_id)[1]) for question in output["questions"])
        return self._finish(document, job, actor_id, execution, event_id, "IntakeAssessed", {"review_cycle": 0, "outcome": batch.outcome("INTAKE"), "question_count": count})

    def _review(self, document: Record, job: Record, actor_id: str, output: Record, execution: Record) -> dict[str, Any]:
        if document["operation_state"] != "ANALYZING_REVIEW" or document["lifecycle_state"] != "REVIEW":
            raise ConflictError("JOB_STATE_CONFLICT", "Review assessment is not active")
        self._content.validate_contract("review_plan", output)
        cycle = document["active_review_cycle"]
        batch = _AssessmentBatch(self._writer, self._content, self._new_id, document["id"], cycle, SnapshotIndex.build(job, execution), comments=True)
        try:
            validate_comment_dispositions(output, batch.records.comment_event_ids, batch.questions, batch.records.answered_ids)
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error
        event_id = self._new_id()
        question_sources: dict[str, list[str]] = {}
        for item in output["comment_dispositions"]:
            if item["disposition"] == "NEEDS_QUESTION":
                question_sources.setdefault(canonical_tag(item["question_semantic_tag"]), []).append(item["comment_event_id"])
        question_ids, question_count = {}, 0
        for raw in output["questions"]:
            tag = canonical_tag(raw["semantic_tag"])
            question_id, inserted = batch.add_question({**raw, "stage": "REVIEW"}, "REVIEW", event_id, question_sources.get(tag, []))
            question_ids.setdefault(tag, question_id)
            question_count += int(inserted)
        dispositions = []
        for item in output["comment_dispositions"]:
            normalized = dict(item)
            if item["disposition"] == "NEEDS_QUESTION":
                question_id = question_ids[canonical_tag(item["question_semantic_tag"])]
                if question_id in batch.records.answered_ids:
                    raise ValidationError(
                        "COMMENT_DISPOSITION_QUESTION_CLOSED", "NEEDS_QUESTION must reference an open question", {"comment_event_id": item["comment_event_id"], "question_id": question_id}
                    )
                normalized["question_id"] = question_id
            dispositions.append(normalized)
        proposal_count = sum(int(batch.add_proposal(proposal, event_id)) for proposal in output["proposals"])
        return self._finish(
            document,
            job,
            actor_id,
            execution,
            event_id,
            "ReviewAssessed",
            {
                "review_cycle": cycle,
                "outcome": batch.outcome("REVIEW"),
                "question_count": question_count,
                "proposal_count": proposal_count,
                "comment_dispositions": dispositions,
            },
        )

    def _draft(self, document: Record, job: Record, actor_id: str, output: Record, execution: Record) -> dict[str, Any]:
        if document["operation_state"] != "GENERATING_DRAFT" or document["lifecycle_state"] != "INTAKE":
            raise ConflictError("JOB_STATE_CONFLICT", "Draft generation is not active")
        if document["current_revision_id"] is not None or self._writer.open_questions(document["id"], "INTAKE", 0):
            raise ConflictError("DRAFT_PRECONDITION_FAILED", "Draft preconditions changed while the worker was running")
        draft = self._content.validate_document_ast(output.get("draft"))
        snapshot = SnapshotIndex.build(job, execution)
        try:
            for section in draft["sections"]:
                validate_evidence_sources(snapshot.evidence_refs, section.get("evidence_refs", []))
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error
        if draft["template_version"] != document["template_version"]:
            raise ValidationError("TEMPLATE_VERSION_CONFLICT", "Draft does not use the version pinned to this document")
        body = self._content.render_document_ast(draft)
        review_questions = output.get("review_questions", {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
        self._content.validate_contract("question_batch", review_questions)
        proposals = output.get("proposals", [])
        if not isinstance(proposals, list):
            raise ValidationError("INVALID_DRAFT_PROTOCOL", "proposals must be an array")
        self._content.validate_contract("review_plan", {"schema_version": "1", "questions": [], "proposals": proposals, "comment_dispositions": []})
        event_id = self._new_id()
        answer_events = [
            event["event_id"]
            for event in job["payload"].get("source_events", [])
            if isinstance(event, dict) and event.get("event_type") == "QuestionAnswered" and isinstance(event.get("event_id"), str)
        ]
        revision_id = self._writer.insert_revision(document["id"], 1, draft, body, [event_id, *answer_events], requested_by(job, document["owner_id"]), revision_id=self._new_id())
        batch = _AssessmentBatch(self._writer, self._content, self._new_id, document["id"], 1, snapshot)
        question_count = sum(int(batch.add_question(question, "REVIEW", event_id)[1]) for question in review_questions["questions"])
        proposal_count = sum(int(batch.add_proposal(proposal, event_id)) for proposal in proposals)
        return self._finish(
            document,
            job,
            actor_id,
            execution,
            event_id,
            "DraftCreated",
            {
                "revision_id": revision_id,
                "requested_by_actor_id": job["payload"].get("requested_by_actor_id"),
                "question_count": question_count,
                "proposal_count": proposal_count,
            },
            lifecycle_state="REVIEW",
            current_revision_id=revision_id,
            active_review_cycle=1,
        )

    def _finish(self, document, job, actor_id, execution, event_id, event_type, payload, **changes):
        version = document["state_version"] + 1
        updated = self._writer.update_document(document, {**changes, "operation_state": "IDLE", "state_version": version})
        self._writer.append_event(
            document["id"],
            version,
            event_type,
            "AI",
            actor_id,
            {"job_id": job["id"], **payload, **prompt_audit(job), **({"execution": execution} if execution else {})},
            job["correlation_id"],
            event_id=event_id,
        )
        return updated
