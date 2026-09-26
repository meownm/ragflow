"""Document writes on the caller's existing transaction and connection."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from peewee import JOIN, IntegrityError, fn

from api.apps.business_documents.adapters.review import load_review_state
from api.apps.business_documents.adapters.queries import find_change_preview_row
from api.db.db_models import (
    BusinessDocument,
    BusinessDocumentAnswer,
    BusinessDocumentCatalog,
    BusinessDocumentComment,
    BusinessDocumentCommand,
    BusinessDocumentEvent,
    BusinessDocumentEvidenceSnapshot,
    BusinessDocumentEvaBinding,
    BusinessDocumentExportArtifact,
    BusinessDocumentExportStage,
    BusinessDocumentJob,
    BusinessDocumentJobStreamEvent,
    BusinessDocumentProposal,
    BusinessDocumentProposalDecision,
    BusinessDocumentQuestion,
    BusinessDocumentRevision,
)
from business_documents.application.errors import BusinessDocumentError, ConflictError, NotFoundError
from business_documents.application.job_completion import AssessmentRecords
from business_documents.domain.hashing import text_hash
from business_documents.domain.eva_binding import binding_keys
from business_documents.domain.workflow import ACTIVE_JOB_STATUSES
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


DOCUMENT_TABLES = (
    BusinessDocumentCatalog,
    BusinessDocument,
    BusinessDocumentRevision,
    BusinessDocumentQuestion,
    BusinessDocumentAnswer,
    BusinessDocumentProposal,
    BusinessDocumentProposalDecision,
    BusinessDocumentComment,
    BusinessDocumentEvent,
    BusinessDocumentEvaBinding,
    BusinessDocumentCommand,
    BusinessDocumentJob,
    BusinessDocumentJobStreamEvent,
    BusinessDocumentExportArtifact,
    BusinessDocumentExportStage,
    BusinessDocumentEvidenceSnapshot,
)


def timestamps() -> dict[str, Any]:
    now, timestamp = datetime.now(), current_timestamp()
    return {"create_time": timestamp, "create_date": now, "update_time": timestamp, "update_date": now}


class PeeweeDocumentWriter:
    def append_job_event(self, job_id, event_type, payload):
        self._require_transaction()
        from api.apps.business_documents.stream_events import BusinessDocumentStreamEvents

        if not BusinessDocumentStreamEvents.append(job_id, event_type, dict(payload)):
            raise NotFoundError()

    def transaction(self):
        return BusinessDocument._meta.database.atomic()

    def _require_transaction(self):
        if not BusinessDocument._meta.database.in_transaction():
            raise RuntimeError("Document writes require the caller's transaction")

    def lock_document(self, document_id):
        self._require_transaction()
        query = BusinessDocument.select().where(BusinessDocument.id == document_id)
        if getattr(BusinessDocument._meta.database, "for_update", False):
            query = query.for_update()
        row = query.first()
        return row.__data__.copy() if row is not None else None

    def command_receipt(self, tenant_id, document_id, key):
        row = BusinessDocumentCommand.get_or_none(
            (BusinessDocumentCommand.tenant_id == tenant_id) & (BusinessDocumentCommand.document_id == document_id) & (BusinessDocumentCommand.idempotency_key == key)
        )
        return row.__data__.copy() if row is not None else None

    def has_active_job(self, document_id):
        return BusinessDocumentJob.select().where((BusinessDocumentJob.document_id == document_id) & (BusinessDocumentJob.status.in_(ACTIVE_JOB_STATUSES))).exists()

    def catalog_entry(self, entry_id):
        row = BusinessDocumentCatalog.get_or_none(BusinessDocumentCatalog.id == entry_id)
        return row.__data__.copy() if row is not None else None

    def title_exists(self, key):
        return BusinessDocument.select().where(BusinessDocument.title_key == key).exists()

    def chat_exists(self, chat_id):
        return BusinessDocument.select().where(BusinessDocument.chat_id == chat_id).exists()

    def insert_document(self, values):
        self._require_transaction()
        try:
            with BusinessDocument._meta.database.atomic():
                row = BusinessDocument.create(**values, **timestamps())
        except IntegrityError as error:
            if self.title_exists(values["title_key"]):
                raise ConflictError("DOCUMENT_TITLE_ALREADY_EXISTS", "A business document with this title already exists") from error
            if self.chat_exists(values["chat_id"]):
                raise ConflictError("CHAT_ALREADY_BOUND", "The RAGFlow chat is already bound to a business document") from error
            raise
        return row.__data__.copy()

    def binding_occupancy(self, keys):
        if not keys:
            return {}
        binding = BusinessDocumentEvaBinding
        rows = (
            binding.select(binding.page_url_key, binding.eva_identity_key, binding.document_id, BusinessDocument.title)
            .join(BusinessDocument, JOIN.LEFT_OUTER, on=(BusinessDocument.id == binding.document_id))
            .where(binding.page_url_key.in_(keys) | binding.eva_identity_key.in_(keys))
            .dicts()
        )
        result = {}
        for row in rows:
            owner = {"document_id": row["document_id"], "title": row["title"]}
            result[row["page_url_key"]] = owner
            if row["eva_identity_key"]:
                result[row["eva_identity_key"]] = owner
        return result

    def store_binding(self, document_id, binding, *, replace=False):
        self._require_transaction()
        page_key, identity = binding_keys(binding)
        model = BusinessDocumentEvaBinding
        values = {"status": str(binding.get("status") or "LINK_ONLY"), "page_url_key": page_key, "eva_identity_key": identity, "binding": dict(binding), **timestamps()}
        try:
            with model._meta.database.atomic():
                existing = model.get_or_none(model.document_id == document_id) if replace else None
                if existing is not None:
                    values.pop("create_time")
                    values.pop("create_date")
                    model.update(**values).where(model.document_id == document_id).execute()
                else:
                    model.create(document_id=document_id, **values)
        except IntegrityError as error:
            occupied = model.page_url_key == page_key
            if identity is not None:
                occupied |= model.eva_identity_key == identity
            if model.select().where(occupied & (model.document_id != document_id)).exists():
                raise ConflictError("EVA_PAGE_ALREADY_LINKED", "The selected EVA page is already linked to another document") from error
            raise

    def stage_artifact_cleanup(self, document_id):
        self._require_transaction()
        from api.apps.business_documents.exports import BusinessDocumentExportService

        artifacts = BusinessDocumentExportArtifact.select().where(BusinessDocumentExportArtifact.document_id == document_id)
        return tuple(BusinessDocumentExportService.queue_artifact_cleanup(artifact).id for artifact in artifacts)

    def delete_document_rows(self, document_id):
        self._require_transaction()
        # Durable export stages outlive their document until blob cleanup succeeds.
        for model in (
            BusinessDocumentEvaBinding,
            BusinessDocumentEvidenceSnapshot,
            BusinessDocumentExportArtifact,
            BusinessDocumentJobStreamEvent,
            BusinessDocumentJob,
            BusinessDocumentCommand,
            BusinessDocumentAnswer,
            BusinessDocumentProposalDecision,
            BusinessDocumentQuestion,
            BusinessDocumentProposal,
            BusinessDocumentComment,
            BusinessDocumentRevision,
            BusinessDocumentEvent,
        ):
            model.delete().where(model.document_id == document_id).execute()
        if BusinessDocument.delete().where(BusinessDocument.id == document_id).execute() != 1:
            raise ConflictError("DOCUMENT_DELETE_CONFLICT", "The document changed while it was being deleted")

    def receipt_after_conflict(self, error, tenant_id, document_id, key):
        # Only the exact ledger key may recover an integrity failure after rollback.
        return self.command_receipt(tenant_id, document_id, key) if isinstance(error, IntegrityError) else None

    def record_command(self, **values):
        self._require_transaction()
        BusinessDocumentCommand.create(**values, **timestamps())

    def preview(self, document, now):
        row = find_change_preview_row(document, now)
        return row.__data__.copy() if row is not None else None

    def question(self, document_id, question_id):
        row = BusinessDocumentQuestion.get_or_none((BusinessDocumentQuestion.document_id == document_id) & (BusinessDocumentQuestion.id == question_id))
        return row.__data__.copy() if row is not None else None

    def has_answer(self, document_id, question_id):
        return BusinessDocumentAnswer.select().where((BusinessDocumentAnswer.document_id == document_id) & (BusinessDocumentAnswer.question_id == question_id)).exists()

    def proposal(self, document_id, proposal_id, cycle):
        row = BusinessDocumentProposal.get_or_none(
            (BusinessDocumentProposal.document_id == document_id) & (BusinessDocumentProposal.id == proposal_id) & (BusinessDocumentProposal.review_cycle == cycle)
        )
        return row.__data__.copy() if row is not None else None

    def has_decision(self, document_id, proposal_id):
        return BusinessDocumentProposalDecision.select().where((BusinessDocumentProposalDecision.document_id == document_id) & (BusinessDocumentProposalDecision.proposal_id == proposal_id)).exists()

    def revision_or_none(self, document_id, revision_id):
        row = BusinessDocumentRevision.get_or_none((BusinessDocumentRevision.document_id == document_id) & (BusinessDocumentRevision.id == revision_id))
        return row.__data__.copy() if row is not None else None

    def insert_answer(self, **values):
        self._require_transaction()
        BusinessDocumentAnswer.create(**values, **timestamps())

    def insert_decision(self, **values):
        self._require_transaction()
        BusinessDocumentProposalDecision.create(**values, **timestamps())

    def insert_comment(self, **values):
        self._require_transaction()
        BusinessDocumentComment.create(**values, **timestamps())

    def insert_job(self, **values):
        self._require_transaction()
        BusinessDocumentJob.create(**values, **timestamps())

    def document(self, tenant_id, document_id):
        row = BusinessDocument.get_or_none((BusinessDocument.id == document_id) & (BusinessDocument.tenant_id == tenant_id))
        if row is None:
            raise NotFoundError()
        return row.__data__.copy()

    def lock_job(self, tenant_id, job_id):
        self._require_transaction()
        hint = BusinessDocumentJob.select(BusinessDocumentJob.document_id).where((BusinessDocumentJob.id == job_id) & (BusinessDocumentJob.tenant_id == tenant_id)).first()
        if hint is None:
            raise BusinessDocumentError("JOB_NOT_FOUND", "Business document job not found", 404)
        document_query = BusinessDocument.select().where((BusinessDocument.id == hint.document_id) & (BusinessDocument.tenant_id == tenant_id))
        if getattr(BusinessDocument._meta.database, "for_update", False):
            document_query = document_query.for_update()
        document = document_query.first()
        if document is None:
            raise NotFoundError()
        job_query = BusinessDocumentJob.select().where((BusinessDocumentJob.id == job_id) & (BusinessDocumentJob.document_id == document.id) & (BusinessDocumentJob.tenant_id == tenant_id))
        if getattr(BusinessDocument._meta.database, "for_update", False):
            job_query = job_query.for_update()
        job = job_query.first()
        if job is None:
            raise BusinessDocumentError("JOB_NOT_FOUND", "Business document job not found", 404)
        return job.__data__.copy(), document.__data__.copy()

    def evidence_snapshot(self, job_id):
        row = BusinessDocumentEvidenceSnapshot.get_or_none(BusinessDocumentEvidenceSnapshot.job_id == job_id)
        return row.__data__.copy() if row is not None else None

    def finish_job(self, job, worker_id, lease_token, completion_time, changes):
        self._require_transaction()
        return (
            BusinessDocumentJob.update(**changes, update_time=completion_time, update_date=datetime.now())
            .where(
                (BusinessDocumentJob.id == job["id"])
                & (BusinessDocumentJob.tenant_id == job["tenant_id"])
                & (BusinessDocumentJob.status == "RUNNING")
                & (BusinessDocumentJob.lease_owner == worker_id)
                & (BusinessDocumentJob.lease_token == lease_token)
                & (BusinessDocumentJob.lease_expires_at > completion_time)
            )
            .execute()
        )

    def review(self, document_id, review_cycle):
        return load_review_state(document_id, review_cycle)

    def assessment(self, document_id, review_cycle, *, comments=False):
        questions = tuple(
            BusinessDocumentQuestion.select(BusinessDocumentQuestion.id, BusinessDocumentQuestion.stage, BusinessDocumentQuestion.semantic_tag)
            .where((BusinessDocumentQuestion.document_id == document_id) & (BusinessDocumentQuestion.review_cycle == review_cycle))
            .dicts()
        )
        proposals = tuple(
            BusinessDocumentProposal.select(BusinessDocumentProposal.id, BusinessDocumentProposal.fingerprint, BusinessDocumentProposal.source_scope_hash)
            .where((BusinessDocumentProposal.document_id == document_id) & (BusinessDocumentProposal.review_cycle == review_cycle))
            .dicts()
        )
        answered = frozenset(row.question_id for row in BusinessDocumentAnswer.select(BusinessDocumentAnswer.question_id).where(BusinessDocumentAnswer.document_id == document_id))
        comment_events = frozenset()
        if comments:
            comment_ids = {
                row.id
                for row in BusinessDocumentComment.select(BusinessDocumentComment.id).where(
                    (BusinessDocumentComment.document_id == document_id) & (BusinessDocumentComment.review_cycle == review_cycle)
                )
            }
            comment_events = frozenset(
                row.id
                for row in BusinessDocumentEvent.select(BusinessDocumentEvent.id, BusinessDocumentEvent.payload).where(
                    (BusinessDocumentEvent.document_id == document_id) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded")
                )
                if row.payload.get("comment_id") in comment_ids
            )
        return AssessmentRecords(questions, proposals, answered, comment_events)

    def open_questions(self, document_id, stage, review_cycle):
        answered = BusinessDocumentAnswer.select(BusinessDocumentAnswer.id).where(
            (BusinessDocumentAnswer.document_id == document_id) & (BusinessDocumentAnswer.question_id == BusinessDocumentQuestion.id)
        )
        return [
            row.id
            for row in BusinessDocumentQuestion.select(BusinessDocumentQuestion.id).where(
                (BusinessDocumentQuestion.document_id == document_id) & (BusinessDocumentQuestion.stage == stage) & (BusinessDocumentQuestion.review_cycle == review_cycle) & ~fn.EXISTS(answered)
            )
        ]

    def insert_question(self, values):
        identity = (
            (BusinessDocumentQuestion.document_id == values["document_id"])
            & (BusinessDocumentQuestion.stage == values["stage"])
            & (BusinessDocumentQuestion.review_cycle == values["review_cycle"])
            & (BusinessDocumentQuestion.semantic_tag == values["semantic_tag"])
        )
        return self._insert_protocol_row(BusinessDocumentQuestion, values, identity)

    def insert_proposal(self, values):
        identity = (
            (BusinessDocumentProposal.document_id == values["document_id"])
            & (BusinessDocumentProposal.review_cycle == values["review_cycle"])
            & (BusinessDocumentProposal.fingerprint == values["fingerprint"])
            & (BusinessDocumentProposal.source_scope_hash == values["source_scope_hash"])
        )
        return self._insert_protocol_row(BusinessDocumentProposal, values, identity)

    def _insert_protocol_row(self, model, values, identity):
        self._require_transaction()
        try:
            with model._meta.database.atomic():
                model.create(**values, **timestamps())
        except IntegrityError:
            existing = model.get_or_none(identity)
            if existing is None:
                raise
            return existing.id, False
        return values["id"], True

    def revision(self, document_id, revision_id):
        return BusinessDocumentRevision.get((BusinessDocumentRevision.document_id == document_id) & (BusinessDocumentRevision.id == revision_id)).__data__.copy()

    def next_revision_number(self, document_id):
        return (BusinessDocumentRevision.select(fn.MAX(BusinessDocumentRevision.revision_number)).where(BusinessDocumentRevision.document_id == document_id).scalar() or 0) + 1

    def insert_revision(self, document_id, revision_number, document_ast, body, source_event_ids, author_id=None, *, revision_id=None):
        self._require_transaction()
        revision_id = revision_id or get_uuid()
        BusinessDocumentRevision.create(
            id=revision_id,
            document_id=document_id,
            author_id=author_id,
            revision_number=revision_number,
            document_ast=document_ast,
            body_markdown=body,
            content_hash=text_hash(body),
            source_event_ids=source_event_ids,
            **timestamps(),
        )
        return revision_id

    def update_document(self, document: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
        self._require_transaction()
        values = {**changes, "update_time": current_timestamp(), "update_date": datetime.now()}
        changed = BusinessDocument.update(**values).where((BusinessDocument.id == document["id"]) & (BusinessDocument.state_version == document["state_version"])).execute()
        if changed != 1:
            raise ConflictError("STATE_VERSION_CONFLICT", "The document changed concurrently")
        return {**document, **values}

    def append_event(self, document_id, sequence, event_type, actor_type, actor_id, payload, correlation_id, *, event_id=None):
        self._require_transaction()
        event_id = event_id or get_uuid()
        BusinessDocumentEvent.create(
            id=event_id,
            document_id=document_id,
            sequence=sequence,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            correlation_id=correlation_id,
            causation_id=None,
            **timestamps(),
        )
        return event_id
