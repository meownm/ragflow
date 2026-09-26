"""Durable delivery of temporary business-document job progress."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from peewee import fn

from api.db.db_models import BusinessDocumentJob, BusinessDocumentJobStreamEvent
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp


class BusinessDocumentStreamEvents:
    @staticmethod
    def append(
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        lease_token: str | None = None,
        expected_attempt: int | None = None,
    ) -> bool:
        """Append under the job lock so cursors stay ordered across retries."""

        database = BusinessDocumentJob._meta.database
        with database.atomic():
            query = BusinessDocumentJob.select().where(BusinessDocumentJob.id == job_id)
            if getattr(database, "for_update", False):
                query = query.for_update()
            job = query.first()
            if job is None:
                return False
            if lease_token is not None and (job.status != "RUNNING" or job.lease_token != lease_token or not job.lease_expires_at or job.lease_expires_at <= current_timestamp()):
                return False
            if expected_attempt is not None and job.attempt != expected_attempt:
                return False
            sequence = (BusinessDocumentJobStreamEvent.select(fn.MAX(BusinessDocumentJobStreamEvent.sequence)).where(BusinessDocumentJobStreamEvent.job_id == job_id).scalar() or 0) + 1
            if sequence > 1000:
                raise ValueError("Business document job has too many stream events")
            now_ms = current_timestamp()
            BusinessDocumentJobStreamEvent.create(
                id=get_uuid(),
                job_id=job.id,
                document_id=job.document_id,
                tenant_id=job.tenant_id,
                sequence=sequence,
                attempt=job.attempt,
                base_revision_id=job.base_revision_id,
                event_type=event_type,
                payload=payload,
                create_time=now_ms,
                create_date=datetime.now(),
                update_time=now_ms,
                update_date=datetime.now(),
            )
        return True

    @staticmethod
    def read(job_id: str, after: int, *, limit: int = 100) -> tuple[list[dict[str, Any]], str | None]:
        job = BusinessDocumentJob.get_or_none(BusinessDocumentJob.id == job_id)
        if job is None:
            return [], None
        rows = (
            BusinessDocumentJobStreamEvent.select()
            .where((BusinessDocumentJobStreamEvent.job_id == job_id) & (BusinessDocumentJobStreamEvent.sequence > after))
            .order_by(BusinessDocumentJobStreamEvent.sequence)
            .limit(limit)
        )
        return [
            {
                "id": row.sequence,
                "job_id": row.job_id,
                "attempt": row.attempt,
                "base_revision_id": row.base_revision_id,
                "type": row.event_type,
                "payload": row.payload,
            }
            for row in rows
        ], job.status

    @staticmethod
    def cleanup_expired(*, retention_ms: int = 24 * 60 * 60 * 1000) -> int:
        terminal_ids = BusinessDocumentJob.select(BusinessDocumentJob.id).where(BusinessDocumentJob.status.in_(("COMPLETED", "DEAD")))
        return (
            BusinessDocumentJobStreamEvent.delete()
            .where((BusinessDocumentJobStreamEvent.create_time < current_timestamp() - retention_ms) & (BusinessDocumentJobStreamEvent.job_id.in_(terminal_ids)))
            .execute()
        )
