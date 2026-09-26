"""Execute authorized document commands with atomic changes and durable replay."""

from __future__ import annotations
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from business_documents.application.change_application import ApplyChanges, ChangeMode
from business_documents.application.content import DocumentContent
from business_documents.domain.content import render_section_text
from business_documents.application.errors import BusinessDocumentError, ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from business_documents.application.persistence import DocumentWriter
from business_documents.application.queries import DocumentQueries, project_protocol
from business_documents.domain.access import BusinessDocumentRole, DocumentAccess
from business_documents.domain.comment_anchor import validate_comment_anchor
from business_documents.domain.errors import RuleViolation, StateConflict
from business_documents.domain.hashing import stable_hash
from business_documents.domain.review import ReviewState
from business_documents.domain.workflow import CommandType, LifecycleState, OperationState, available_commands, command_problem, is_operation_quiescent

Record = Mapping[str, Any]


class CommandWriter(DocumentWriter, Protocol):
    def transaction(self) -> AbstractContextManager: ...

    def lock_document(self, document_id: str) -> Record | None: ...

    def command_receipt(self, tenant_id: str, document_id: str, key: str) -> Record | None: ...

    def receipt_after_conflict(self, error: Exception, tenant_id: str, document_id: str, key: str) -> Record | None: ...

    def record_command(self, **values: Any) -> None: ...

    def review(self, document_id: str, review_cycle: int) -> ReviewState: ...

    def preview(self, document: Record, now: int) -> Record | None: ...

    def question(self, document_id: str, question_id: str) -> Record | None: ...

    def has_answer(self, document_id: str, question_id: str) -> bool: ...

    def proposal(self, document_id: str, proposal_id: str, cycle: int) -> Record | None: ...

    def has_decision(self, document_id: str, proposal_id: str) -> bool: ...

    def revision_or_none(self, document_id: str, revision_id: str) -> Record | None: ...

    def insert_answer(self, **values: Any) -> None: ...

    def insert_decision(self, **values: Any) -> None: ...

    def insert_comment(self, **values: Any) -> None: ...

    def insert_job(self, **values: Any) -> None: ...


@dataclass(frozen=True)
class _CommandEnvelope:
    schema_version: str
    command_id: str
    idempotency_key: str
    expected_state_version: int
    type: CommandType
    payload: dict[str, Any]

    @classmethod
    def parse(cls, raw: object, content: DocumentContent) -> "_CommandEnvelope":
        content.validate_contract("command", raw)
        assert isinstance(raw, dict)
        try:
            command_type = CommandType(raw["type"])
        except (TypeError, ValueError) as exc:
            raise ValidationError("UNKNOWN_COMMAND", "Unsupported command type", {"type": raw.get("type")}) from exc
        return cls(
            schema_version="1",
            command_id=raw["command_id"].strip(),
            idempotency_key=raw["idempotency_key"].strip(),
            expected_state_version=raw["expected_state_version"],
            type=command_type,
            payload=raw["payload"],
        )


class DocumentCommands:
    def __init__(self, writer: CommandWriter, content: DocumentContent, queries: DocumentQueries, changes: ApplyChanges, new_id: Callable[[], str], clock: Callable[[], int]):
        self._writer, self._content, self._queries, self._changes = (writer, content, queries, changes)
        self._new_id, self._clock = (new_id, clock)

    def execute(
        self, tenant_id: str, actor_id: str, document_id: str, raw: object, is_admin: bool = False, access_role: BusinessDocumentRole | str = BusinessDocumentRole.AUTHOR_CREATOR
    ) -> dict[str, Any]:
        envelope = _CommandEnvelope.parse(raw, self._content)
        request_hash = stable_hash(
            {
                "schema_version": envelope.schema_version,
                "command_id": envelope.command_id,
                "expected_state_version": envelope.expected_state_version,
                "type": envelope.type.value,
                "payload": envelope.payload,
            }
        )
        captured_error: BusinessDocumentError | None = None
        response: dict[str, Any]
        document_tenant_id = tenant_id
        try:
            with self._writer.transaction():
                access = DocumentAccess(actor_id, access_role, is_admin)
                document = self._writer.lock_document(document_id)
                if document is None:
                    raise NotFoundError()
                if not access.permissions(document["owner_id"])["edit"]:
                    raise PermissionDeniedError("Only the document owner or a moderator can edit this business document")
                document_tenant_id = document["tenant_id"]
                existing = self._writer.command_receipt(document_tenant_id, document_id, envelope.idempotency_key)
                if existing is not None:
                    return self._replay_command(existing, request_hash)
                try:
                    # Roll back every child write on rejection while retaining the receipt.
                    # The original value snapshot also survives a failure after CAS.
                    with self._writer.transaction():
                        if document["state_version"] != envelope.expected_state_version:
                            raise ConflictError(
                                "STATE_VERSION_CONFLICT", "The document changed since it was loaded", {"expected": envelope.expected_state_version, "actual": document["state_version"]}
                            )
                        response = self._dispatch(document, actor_id, envelope)
                except BusinessDocumentError as exc:
                    captured_error = exc
                    response = {
                        "accepted": False,
                        "document_id": document_id,
                        "state_version": document["state_version"],
                        "error": {"code": exc.code, "message": exc.message, "status": exc.status, "details": exc.details},
                    }
                self._writer.record_command(
                    id=self._new_id(), document_id=document_id, tenant_id=document_tenant_id, idempotency_key=envelope.idempotency_key, request_hash=request_hash, response=response
                )
        except Exception as error:
            # The adapter recognizes exact ledger collisions; all other failures propagate.
            existing = self._writer.receipt_after_conflict(error, document_tenant_id, document_id, envelope.idempotency_key)
            if existing is None:
                raise
            return self._replay_command(existing, request_hash)
        if captured_error is not None:
            raise captured_error
        return response

    @staticmethod
    def _replay_command(existing: Record, request_hash: str) -> dict[str, Any]:
        if existing["request_hash"] != request_hash:
            raise ConflictError("IDEMPOTENCY_CONFLICT", "idempotency_key was already used for a different command")
        response = dict(existing["response"])
        response["idempotent_replay"] = True
        if not response.get("accepted", False):
            error = response["error"]
            raise BusinessDocumentError(error["code"], error["message"], error["status"], error.get("details"))
        return response

    def _dispatch(self, document: Record, actor_id: str, envelope: _CommandEnvelope) -> dict[str, Any]:
        review_commands = {CommandType.REQUEST_INTAKE_ASSESSMENT, CommandType.REQUEST_REVIEW_ASSESSMENT, CommandType.REQUEST_DRAFT, CommandType.APPLY_CHANGES, CommandType.PREPARE_CHANGES}
        review = self._writer.review(document["id"], document["active_review_cycle"]) if envelope.type in review_commands and document["lifecycle_state"] != "ARCHIVED" else None
        preview = self._writer.preview(document, self._clock()) if envelope.type in {CommandType.APPLY_CHANGES, CommandType.CONFIRM_PREPARED_CHANGES, CommandType.DISCARD_PREPARED_CHANGES} else None
        if problem := command_problem(envelope.type, document, review, envelope.payload, preview=preview):
            error_type = ConflictError if isinstance(problem, StateConflict) else ValidationError
            raise error_type(problem.code, problem.message, problem.details)
        match envelope.type:
            case CommandType.REQUEST_INTAKE_ASSESSMENT:
                return self._request_job(document, actor_id, envelope, "ASSESS_INTAKE", OperationState.ANALYZING, review=review)
            case CommandType.REQUEST_REVIEW_ASSESSMENT:
                return self._request_job(document, actor_id, envelope, "ASSESS_REVIEW", OperationState.ANALYZING_REVIEW, review=review)
            case CommandType.REQUEST_DRAFT:
                return self._request_job(document, actor_id, envelope, "GENERATE_DRAFT", OperationState.GENERATING_DRAFT, review=review)
            case CommandType.ANSWER_QUESTION:
                return self._answer_question(document, actor_id, envelope)
            case CommandType.DECIDE_PROPOSAL:
                return self._decide_proposal(document, actor_id, envelope)
            case CommandType.ADD_COMMENT:
                return self._add_comment(document, actor_id, envelope)
            case CommandType.APPLY_CHANGES | CommandType.PREPARE_CHANGES:
                return self._request_job(document, actor_id, envelope, "PLAN_CHANGES", OperationState.APPLYING_CHANGES, review=review)
            case CommandType.CONFIRM_PREPARED_CHANGES | CommandType.DISCARD_PREPARED_CHANGES:
                assert preview is not None
                if envelope.type == CommandType.CONFIRM_PREPARED_CHANGES:
                    result = preview["result"] if isinstance(preview["result"], dict) else {}
                    document = self._changes.execute(document, preview, actor_id, result.get("output"), result.get("execution") or {}, mode=ChangeMode.CONFIRM)
                    self._writer.append_job_event(preview["id"], "applied", {"state_version": document["state_version"]})
                else:
                    version = document["state_version"] + 1
                    document = self._writer.update_document(document, {"state_version": version})
                    self._writer.append_event(document["id"], version, "ChangePreviewDiscarded", "USER", actor_id, {"job_id": preview["id"]}, envelope.command_id)
                    self._writer.append_job_event(preview["id"], "discarded", {"state_version": document["state_version"]})
                return {
                    "accepted": True,
                    "document_id": document["id"],
                    "state_version": document["state_version"],
                    "lifecycle_state": document["lifecycle_state"],
                    "operation_state": document["operation_state"],
                    "allowed_commands": self._allowed_commands(document, include_preview=True),
                }
            case CommandType.START_REVIEW:
                return self._transition(document, actor_id, envelope, "ReviewCycleStarted", lifecycle_state="REVIEW", active_review_cycle=document["active_review_cycle"] + 1)
            case CommandType.REQUEST_EXPORT:
                return self._request_job(document, actor_id, envelope, "GENERATE_EXPORT", OperationState.EXPORTING)
            case CommandType.ARCHIVE:
                return self._transition(document, actor_id, envelope, "DocumentArchived", lifecycle_state="ARCHIVED")
        raise AssertionError("Command policy accepted an unsupported command")

    def _request_job(self, document: Record, actor_id: str, envelope: _CommandEnvelope, job_type: str, operation_state: OperationState, *, review=None) -> dict[str, Any]:
        new_version = document["state_version"] + 1
        job_id = self._new_id()
        snapshot = self._job_snapshot(document, envelope.payload, review)
        snapshot["task_type"] = job_type
        if envelope.type == CommandType.PREPARE_CHANGES:
            snapshot["preview_only"] = True
        snapshot["requested_by_actor_id"] = actor_id
        prompt = self._content.prompt_descriptor(job_type)
        if prompt is not None:
            snapshot["prompt"] = prompt
        snapshot["expected_contracts"] = {
            "ASSESS_INTAKE": ["question_batch.v1"],
            "ASSESS_REVIEW": ["review_plan.v1"],
            "GENERATE_DRAFT": ["document_draft.v1", "question_batch.v1", "review_plan.v1"],
            "PLAN_CHANGES": ["change_plan.v1"],
            "GENERATE_EXPORT": [],
        }[job_type]
        dedupe_key = stable_hash({"document_id": document["id"], "job_type": job_type, "source_state_version": new_version})
        now = self._clock()
        # Claim the version before inserting the unique job, within the command savepoint.
        document = self._writer.update_document(document, {"operation_state": operation_state.value, "last_error": None, "state_version": new_version})
        self._writer.insert_job(
            id=job_id,
            document_id=document["id"],
            tenant_id=document["tenant_id"],
            job_type=job_type,
            status="PENDING",
            progress=0.02,
            progress_stage="QUEUED",
            progress_message="Ожидает запуска",
            dedupe_key=dedupe_key,
            source_state_version=new_version,
            base_revision_id=document["current_revision_id"],
            payload=snapshot,
            result=None,
            attempt=0,
            max_attempts=3,
            available_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            error=None,
            correlation_id=envelope.command_id,
        )
        event_id = self._writer.append_event(
            document["id"],
            new_version,
            "BusinessDocumentJobRequested",
            "USER",
            actor_id,
            {
                "job_id": job_id,
                "job_type": job_type,
                "source_state_version": new_version,
                **({"prompt_name": prompt["name"], "prompt_version": prompt["version"], "prompt_hash": prompt["content_hash"]} if prompt is not None else {}),
            },
            envelope.command_id,
        )
        return {
            "accepted": True,
            "document_id": document["id"],
            "state_version": new_version,
            "lifecycle_state": document["lifecycle_state"],
            "operation_state": operation_state.value,
            "job_id": job_id,
            "event_id": event_id,
            "allowed_commands": [],
        }

    def _answer_question(self, document: Record, actor_id: str, envelope: _CommandEnvelope) -> dict[str, Any]:
        question_id = envelope.payload.get("question_id")
        question = self._writer.question(document["id"], question_id)
        if question is None:
            raise ValidationError("QUESTION_NOT_FOUND", "Question does not belong to this document")
        expected_stage = "INTAKE" if document["lifecycle_state"] == LifecycleState.INTAKE.value else "REVIEW"
        if question["stage"] != expected_stage or question["review_cycle"] != document["active_review_cycle"]:
            raise ConflictError("QUESTION_NOT_ACTIVE", "Question does not belong to the active workflow stage")
        if self._writer.has_answer(document["id"], question["id"]):
            raise ConflictError("QUESTION_ALREADY_CLOSED", "Published question answers are immutable")
        selected_option_id = envelope.payload.get("selected_option_id")
        custom_answer = envelope.payload.get("custom_answer")
        if bool(selected_option_id) == bool(isinstance(custom_answer, str) and custom_answer.strip()):
            raise ValidationError("INVALID_ANSWER", "Provide exactly one selected option or a custom answer")
        if selected_option_id:
            option_ids = {option.get("option_id") for option in question["options"] if isinstance(option, dict)}
            if selected_option_id not in option_ids:
                raise ValidationError("INVALID_OPTION", "selected_option_id is not one of the question options")
        elif not question["allow_custom_answer"]:
            raise ValidationError("CUSTOM_ANSWER_NOT_ALLOWED", "This question does not allow a custom answer")
        new_version = document["state_version"] + 1
        answer_id = self._new_id()
        self._writer.insert_answer(
            id=answer_id,
            document_id=document["id"],
            question_id=question["id"],
            actor_id=actor_id,
            selected_option_id=selected_option_id,
            custom_answer=custom_answer.strip() if isinstance(custom_answer, str) else None,
        )
        document = self._writer.update_document(document, {"state_version": new_version})
        event_id = self._writer.append_event(
            document["id"], new_version, "QuestionAnswered", "USER", actor_id, {"question_id": question["id"], "answer_id": answer_id, "selected_option_id": selected_option_id}, envelope.command_id
        )
        return self._command_response(document, new_version, event_id)

    def _decide_proposal(self, document: Record, actor_id: str, envelope: _CommandEnvelope) -> dict[str, Any]:
        proposal_id = envelope.payload.get("proposal_id")
        proposal = self._writer.proposal(document["id"], proposal_id, document["active_review_cycle"])
        if proposal is None:
            raise ValidationError("PROPOSAL_NOT_FOUND", "Proposal does not belong to the active review cycle")
        if self._writer.has_decision(document["id"], proposal["id"]):
            raise ConflictError("PROPOSAL_ALREADY_DECIDED", "Published proposal decisions are immutable")
        decision = envelope.payload.get("decision")
        if decision not in {"ACCEPTED", "REJECTED"}:
            raise ValidationError("INVALID_PROPOSAL_DECISION", "decision must be ACCEPTED or REJECTED")
        new_version = document["state_version"] + 1
        decision_id = self._new_id()
        self._writer.insert_decision(id=decision_id, document_id=document["id"], proposal_id=proposal["id"], actor_id=actor_id, decision=decision)
        document = self._writer.update_document(document, {"state_version": new_version})
        event_id = self._writer.append_event(
            document["id"], new_version, "ProposalDecided", "USER", actor_id, {"proposal_id": proposal["id"], "decision_id": decision_id, "decision": decision}, envelope.command_id
        )
        return self._command_response(document, new_version, event_id)

    def _add_comment(self, document: Record, actor_id: str, envelope: _CommandEnvelope) -> dict[str, Any]:
        text = envelope.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("INVALID_COMMENT", "Comment text must be non-empty")
        revision_id = envelope.payload.get("revision_id")
        if revision_id != document["current_revision_id"]:
            raise ConflictError("COMMENT_REVISION_CONFLICT", "Comment must target the current revision")
        revision = self._writer.revision_or_none(document["id"], revision_id)
        if revision is None:
            raise ConflictError("COMMENT_REVISION_CONFLICT", "Comment must target the current revision")
        section_id = envelope.payload.get("section_id")
        revision_sections = {section.get("id"): section for section in revision["document_ast"].get("sections", []) if isinstance(section, dict) and isinstance(section.get("id"), str)}
        if section_id is not None and section_id not in revision_sections:
            raise ValidationError("COMMENT_SECTION_NOT_FOUND", "Comment section does not exist in the target revision")
        anchor = envelope.payload.get("anchor")
        section_text = render_section_text(revision_sections[section_id]) if anchor and section_id in revision_sections else ""
        try:
            validate_comment_anchor(anchor, revision_id, section_id, section_text)
        except RuleViolation as error:
            raise ValidationError(error.code, error.message, error.details) from error
        new_version = document["state_version"] + 1
        comment_id = self._new_id()
        self._writer.insert_comment(
            id=comment_id, document_id=document["id"], review_cycle=document["active_review_cycle"], revision_id=revision_id, actor_id=actor_id, section_id=section_id, text=text.strip(), anchor=anchor
        )
        document = self._writer.update_document(document, {"state_version": new_version})
        event_id = self._writer.append_event(document["id"], new_version, "AuthorCommentAdded", "USER", actor_id, {"comment_id": comment_id, "revision_id": revision_id}, envelope.command_id)
        return self._command_response(document, new_version, event_id)

    def _transition(self, document, actor_id, envelope, event_type, **changes):
        version = document["state_version"] + 1
        document = self._writer.update_document(document, {**changes, "state_version": version})
        event_id = self._writer.append_event(document["id"], version, event_type, "USER", actor_id, envelope.payload, envelope.command_id)
        return self._command_response(document, version, event_id)

    def _job_snapshot(self, document, command_payload, review=None):
        review = review or self._writer.review(document["id"], document["active_review_cycle"])
        revision = None
        if document["current_revision_id"]:
            revision = self._queries.revision_snapshot(document, document["current_revision_id"])
        source_events = review.events
        created = next((event for event in source_events if event["event_type"] == "DocumentCreated"), None)
        return {
            "schema_version": "1",
            "document_id": document["id"],
            "owner_id": document["owner_id"],
            "document_type": document["document_type"],
            "catalog_entry_id": document["catalog_entry_id"],
            "title": document["title"],
            "idea": document["idea"],
            "dataset_ids": document["dataset_ids"],
            "idea_source_event_id": created["id"] if created else None,
            "source_events": [
                {"event_id": event["id"], "sequence": event["sequence"], "event_type": event["event_type"], "actor_type": event["actor_type"], "payload": event["payload"]} for event in source_events
            ],
            "active_change_input_event_ids": sorted(review.active_inputs),
            "lifecycle_state": document["lifecycle_state"],
            "state_version": document["state_version"] + 1,
            "template_version": document["template_version"],
            "policy_version": document["policy_version"],
            "active_review_cycle": document["active_review_cycle"],
            "current_revision": revision,
            "protocol": project_protocol(review, document["current_revision_id"]),
            "command_payload": command_payload,
        }

    def _allowed_commands(self, document, *, include_preview=False):
        if not is_operation_quiescent(document["operation_state"]) or document["lifecycle_state"] == "ARCHIVED":
            return []
        review = self._writer.review(document["id"], document["active_review_cycle"])
        preview = self._writer.preview(document, self._clock()) if include_preview else None
        return available_commands(document, review, preview=preview)

    def _command_response(self, document, new_version, event_id):
        return {
            "accepted": True,
            "document_id": document["id"],
            "state_version": new_version,
            "lifecycle_state": document["lifecycle_state"],
            "operation_state": document["operation_state"],
            "event_id": event_id,
            "allowed_commands": self._allowed_commands(document),
        }
