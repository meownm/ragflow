"""Peewee batch readers for the shared Business Documents catalog."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from peewee import JOIN, fn

from api.apps.business_documents.adapters.review import load_review_state
from api.db.db_models import (
    BusinessDocument,
    BusinessDocumentAnswer,
    BusinessDocumentCatalog,
    BusinessDocumentComment,
    BusinessDocumentEvent,
    BusinessDocumentEvaBinding,
    BusinessDocumentExportArtifact,
    BusinessDocumentJob,
    BusinessDocumentProposal,
    BusinessDocumentQuestion,
    BusinessDocumentRevision,
    User,
)
from business_documents.application.queries import DocumentPage, RevisionHistory
from business_documents.domain.eva_binding import binding_from_events
from business_documents.domain.workflow import is_current_change_preview


_JOB_FIELDS = tuple(
    getattr(BusinessDocumentJob, name)
    for name in (
        "id",
        "document_id",
        "job_type",
        "status",
        "progress",
        "progress_stage",
        "progress_message",
        "attempt",
        "max_attempts",
        "available_at",
        "lease_expires_at",
        "error",
        "create_time",
        "update_time",
    )
)
_ID_BATCH_SIZE = 400


def _batches(values: Iterable[str]):
    rows = sorted(set(values))
    for offset in range(0, len(rows), _ID_BATCH_SIZE):
        yield rows[offset : offset + _ID_BATCH_SIZE]


def _by_ids(model, document_id: str, ids: set[str]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for batch in _batches(ids) for row in model.select().where((model.document_id == document_id) & model.id.in_(batch)).dicts()}


def load_eva_bindings(document_ids: set[str]) -> dict[str, dict[str, Any]]:
    bindings = {}
    for batch in _batches(document_ids):
        for row in BusinessDocumentEvaBinding.select(BusinessDocumentEvaBinding.document_id, BusinessDocumentEvaBinding.binding).where(BusinessDocumentEvaBinding.document_id.in_(batch)).dicts():
            if isinstance(row["binding"], dict):
                bindings[row["document_id"]] = dict(row["binding"])
    missing = document_ids - bindings.keys()
    for batch in _batches(missing):
        event = BusinessDocumentEvent
        boundaries = (
            event.select(event.document_id, event.event_type, fn.MIN(event.sequence).alias("first_sequence"), fn.MAX(event.sequence).alias("last_sequence"))
            .where(event.document_id.in_(batch) & event.event_type.in_(("DocumentCreated", "EvaBindingResolved", "EvaDocumentPulled")))
            .group_by(event.document_id, event.event_type)
            .alias("boundaries")
        )
        rows = (
            event.select(event)
            .join(
                boundaries,
                on=(
                    (event.document_id == boundaries.c.document_id)
                    & (event.event_type == boundaries.c.event_type)
                    & (
                        ((event.event_type == "DocumentCreated") & (event.sequence == boundaries.c.first_sequence))
                        | ((event.event_type != "DocumentCreated") & (event.sequence == boundaries.c.last_sequence))
                    )
                ),
            )
            .order_by(event.sequence.asc())
            .dicts()
        )
        events_by_document = defaultdict(list)
        for row in rows:
            events_by_document[row["document_id"]].append(row)
        for document_id, events in events_by_document.items():
            binding = binding_from_events(events)
            if binding is not None:
                bindings[document_id] = binding
    return bindings


def find_change_preview_row(document: Mapping[str, Any], now: int):
    if document["lifecycle_state"] != "REVIEW" or document["operation_state"] != "IDLE":
        return None
    job = BusinessDocumentJob
    row = (
        job.select()
        .where(
            (job.document_id == document["id"])
            & (job.job_type == "PLAN_CHANGES")
            & (job.status == "COMPLETED")
            & (job.source_state_version == document["state_version"] - 1)
            & (job.base_revision_id == document["current_revision_id"])
        )
        .order_by(job.update_time.desc(), job.id.desc())
        .first()
    )
    return row if row is not None and is_current_change_preview(document, row.__data__, now) else None


class PeeweeDocumentReader:
    def document(self, document_id: str):
        return BusinessDocument.select().where(BusinessDocument.id == document_id).dicts().first()

    def revision(self, document_id: str, revision_id: str):
        return BusinessDocumentRevision.select().where((BusinessDocumentRevision.document_id == document_id) & (BusinessDocumentRevision.id == revision_id)).dicts().first()

    def users(self, user_ids: set[str]):
        if not user_ids or User._meta.database is not BusinessDocumentRevision._meta.database:
            return {}
        try:
            return {row["id"]: row for batch in _batches(user_ids) for row in User.select(User.id, User.nickname, User.email).where(User.id.in_(batch)).dicts()}
        except Exception:
            # Saved documents remain readable after an identity is removed or unavailable.
            return {}

    def bindings(self, document_ids: set[str]):
        return load_eva_bindings(document_ids)

    def _latest_jobs(self, document_ids: set[str]):
        job = BusinessDocumentJob
        result = {}
        for batch in _batches(document_ids):
            ranked = (
                job.select(*_JOB_FIELDS, fn.ROW_NUMBER().over(partition_by=[job.document_id], order_by=[job.create_time.desc(), job.id.desc()]).alias("position"))
                .where(job.document_id.in_(batch))
                .alias("ranked_jobs")
            )
            rows = job.select(*(getattr(ranked.c, field.name) for field in _JOB_FIELDS)).from_(ranked).where(ranked.c.position == 1).dicts()
            for row in rows:
                result[row["document_id"]] = row
        return result

    def page(self, owner_id: str | None, page: int, page_size: int):
        query = BusinessDocument.select()
        if owner_id is not None:
            query = query.where(BusinessDocument.owner_id == owner_id)
        total = query.count()
        rows = tuple(query.order_by(BusinessDocument.update_time.desc(), BusinessDocument.id.desc()).paginate(page, page_size).dicts())
        document_ids = {row["id"] for row in rows}
        revision_ids = {row["current_revision_id"] for row in rows if row["current_revision_id"]}
        numbers = {
            row["id"]: row["revision_number"]
            for batch in _batches(revision_ids)
            for row in BusinessDocumentRevision.select(BusinessDocumentRevision.id, BusinessDocumentRevision.revision_number).where(BusinessDocumentRevision.id.in_(batch)).dicts()
        }
        if revision_ids - numbers.keys():
            raise BusinessDocumentRevision.DoesNotExist("A current document revision is missing")
        return DocumentPage(rows, total, self.users({row["owner_id"] for row in rows if row["owner_id"]}), numbers, self.bindings(document_ids), self._latest_jobs(document_ids))

    def revisions(self, document_id: str, revision_id: str | None = None):
        query = BusinessDocumentRevision.select().where(BusinessDocumentRevision.document_id == document_id)
        if revision_id is not None:
            query = query.where(BusinessDocumentRevision.id == revision_id)
        rows = tuple(query.order_by(BusinessDocumentRevision.revision_number.asc()).dicts())
        source_ids = {value for row in rows for value in row["source_event_ids"] or [] if isinstance(value, str)}
        events = _by_ids(BusinessDocumentEvent, document_id, source_ids)
        missing_authors = {row["id"] for row in rows if not row["author_id"]}
        if missing_authors:
            legacy = BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document_id) & BusinessDocumentEvent.event_type.in_(("DraftCreated", "ChangesApplied")))
            events.update({row["id"]: row for row in legacy.dicts() if isinstance(row["payload"], dict) and row["payload"].get("revision_id") in missing_authors})
        events = dict(sorted(events.items(), key=lambda item: item[1]["sequence"]))
        related = defaultdict(set)
        for event_id, event in events.items():
            payload = event["payload"] if isinstance(event["payload"], dict) else {}
            if event_id in source_ids:
                for key in ("question_id", "answer_id", "proposal_id", "comment_id"):
                    if isinstance(payload.get(key), str):
                        related[key].add(payload[key])
            if payload.get("revision_id") in missing_authors and not payload.get("requested_by_actor_id") and isinstance(payload.get("job_id"), str):
                related["job_id"].add(payload["job_id"])
        jobs = {
            row["id"]: row
            for batch in _batches(related["job_id"])
            for row in BusinessDocumentJob.select(BusinessDocumentJob.id, BusinessDocumentJob.payload)
            .where((BusinessDocumentJob.document_id == document_id) & BusinessDocumentJob.id.in_(batch))
            .dicts()
        }
        user_ids = {row["author_id"] for row in rows if row["author_id"]}
        for event in events.values():
            if event["actor_type"] == "USER" and event["actor_id"]:
                user_ids.add(event["actor_id"])
            if isinstance(event["payload"], dict) and isinstance(event["payload"].get("requested_by_actor_id"), str) and event["payload"]["requested_by_actor_id"]:
                user_ids.add(event["payload"]["requested_by_actor_id"])
        for job in jobs.values():
            payload = job["payload"]
            if isinstance(payload, dict) and isinstance(payload.get("requested_by_actor_id"), str) and payload["requested_by_actor_id"]:
                user_ids.add(payload["requested_by_actor_id"])
        return RevisionHistory(
            rows,
            events,
            _by_ids(BusinessDocumentQuestion, document_id, related["question_id"]),
            _by_ids(BusinessDocumentAnswer, document_id, related["answer_id"]),
            _by_ids(BusinessDocumentProposal, document_id, related["proposal_id"]),
            _by_ids(BusinessDocumentComment, document_id, related["comment_id"]),
            jobs,
            self.users(user_ids),
        )

    def review(self, document_id: str, review_cycle: int):
        return load_review_state(document_id, review_cycle)

    def job(self, document_id: str, job_id: str):
        return BusinessDocumentJob.select().where((BusinessDocumentJob.id == job_id) & (BusinessDocumentJob.document_id == document_id)).dicts().first()

    def stream_events(self, job_id: str, after: int):
        from api.apps.business_documents.stream_events import BusinessDocumentStreamEvents

        return BusinessDocumentStreamEvents.read(job_id, after)

    def jobs(self, document_id: str):
        return tuple(
            BusinessDocumentJob.select(*_JOB_FIELDS).where(BusinessDocumentJob.document_id == document_id).order_by(BusinessDocumentJob.create_time.desc(), BusinessDocumentJob.id.desc()).dicts()
        )

    def latest_job(self, document_id: str):
        job = BusinessDocumentJob
        return job.select(*_JOB_FIELDS).where(job.document_id == document_id).order_by(job.create_time.desc(), job.id.desc()).dicts().first()

    def preview(self, document: Mapping[str, Any], now: int):
        row = find_change_preview_row(document, now)
        return dict(row.__data__) if row is not None else None

    def exports(self, document_id: str):
        artifact, revision = BusinessDocumentExportArtifact, BusinessDocumentRevision
        rows = tuple(
            artifact.select(
                artifact.id.alias("artifact_id"),
                artifact.revision_id,
                revision.revision_number,
                artifact.export_format.alias("format"),
                artifact.filename,
                artifact.mime_type,
                artifact.size,
                artifact.content_hash,
                artifact.create_time,
            )
            .join(revision, JOIN.LEFT_OUTER, on=(revision.id == artifact.revision_id) & (revision.document_id == document_id))
            .where(artifact.document_id == document_id)
            .order_by(artifact.create_time.desc())
            .limit(10)
            .dicts()
        )
        if any(row["revision_number"] is None for row in rows):
            raise BusinessDocumentRevision.DoesNotExist("An exported document revision is missing")
        return rows

    def catalog(self):
        catalog = BusinessDocumentCatalog
        return tuple(
            catalog.select(catalog.id, catalog.title, catalog.title_en, catalog.description, catalog.capability_level, catalog.capability_type, catalog.hierarchy)
            .where(catalog.capability_level == "L5", catalog.is_active)
            .order_by(catalog.sort_order.asc(), catalog.id.asc())
            .dicts()
        )

    def active_users(self):
        return tuple(
            User.select(User.id, User.nickname, User.email, User.business_document_role, User.is_superuser)
            .where((User.status == "1") & (User.is_active == "1"))
            .order_by(User.nickname.asc(), User.id.asc())
            .dicts()
        )
