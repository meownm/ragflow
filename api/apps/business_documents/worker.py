#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
from datetime import datetime
from time import monotonic
from typing import Any

from peewee import fn

from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.evidence import BusinessDocumentEvidence, related_file_search_enabled
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.runtime import job_completion, document_commands
from api.apps.business_documents.migrations import resume_accepted_changes
from api.apps.business_documents.recovery import AIRecovery, is_transient_failure, retry_delay_ms
from api.apps.business_documents.sql_query_agent_worker import BusinessDocumentSqlAgentRunner
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.sql_query_runs import BusinessDocumentSqlRunService
from api.apps.business_documents.sql_query_retention import expire_due_results
from api.apps.business_documents.stream_events import BusinessDocumentStreamEvents
from api.db.db_models import BusinessDocument, BusinessDocumentJob, BusinessDocumentWorkRequest
from business_documents.application.errors import BusinessDocumentError, ConflictError
from business_documents.domain.hashing import stable_hash
from business_documents.domain.job import targets_current_document
from business_documents.domain.job_recovery import MAX_AI_SECONDS
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp

_ACTIVE_STATUSES = ("PENDING", "RETRY")
_WAKE_EVENT = threading.Event()
_START_LOCK = threading.Lock()
_MAINTENANCE_LOCK = threading.Lock()
_WORKER_THREADS: tuple[threading.Thread, ...] = ()
_MAX_WORKERS = 8


def _error_payload(error: Exception) -> dict[str, Any]:
    if isinstance(error, BusinessDocumentError):
        return {"code": error.code, "message": error.message, "details": error.details}
    return {"code": "WORKER_FAILURE", "message": str(error)[:2000] or type(error).__name__}


class BusinessDocumentJobQueue:
    """Database-backed multi-process job lease and recovery operations."""

    @classmethod
    def checkpoint_ai(cls, job: BusinessDocumentJob, worker_id: str, state: dict[str, Any]) -> None:
        now = current_timestamp()
        document = BusinessDocument.get_or_none(BusinessDocument.id == job.document_id)
        if document is None or not targets_current_document(document.__data__, job.__data__):
            raise ConflictError("STALE_AI_RESULT", "Документ изменился; результат задания больше не актуален")
        changed = (
            BusinessDocumentJob.update(
                result={"recovery": state},
                **(
                    {"progress": 0.65, "progress_stage": "VALIDATING", "progress_message": "Проверяем выполнение принятого предложения"}
                    if job.job_type == "PLAN_CHANGES" and state.get("candidate") is not None
                    else {}
                ),
            )
            .where(
                (BusinessDocumentJob.id == job.id)
                & (BusinessDocumentJob.status == "RUNNING")
                & (BusinessDocumentJob.lease_owner == worker_id)
                & (BusinessDocumentJob.lease_token == job.lease_token)
                & (BusinessDocumentJob.lease_expires_at > now)
            )
            .execute()
        )
        if changed != 1:
            raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")
        previous = (job.result or {}).get("recovery", {}).get("feedback", {})
        if job.job_type == "PLAN_CHANGES" and "generation" in state["feedback"] and "generation" not in previous:
            if not BusinessDocumentStreamEvents.append(job.id, "retry", {"reason": "format_repair"}, lease_token=job.lease_token, expected_attempt=job.attempt):
                raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")
        job.result = {"recovery": state}

    @classmethod
    def claim(cls, worker_id: str, *, lease_ms: int = 60_000, now_ms: int | None = None) -> BusinessDocumentJob | None:
        now_ms = current_timestamp() if now_ms is None else now_ms
        database = BusinessDocumentJob._meta.database
        with database.atomic():
            candidates = (
                BusinessDocumentJob.select(BusinessDocumentJob.id)
                .where(
                    (BusinessDocumentJob.status.in_(_ACTIVE_STATUSES))
                    & (BusinessDocumentJob.attempt < BusinessDocumentJob.max_attempts)
                    & (BusinessDocumentJob.available_at <= now_ms)
                    & ((BusinessDocumentJob.lease_expires_at.is_null()) | (BusinessDocumentJob.lease_expires_at <= now_ms))
                )
                .order_by(BusinessDocumentJob.available_at.asc(), BusinessDocumentJob.create_time.asc())
                .limit(8)
            )
            for candidate in candidates:
                lease_token = get_uuid()
                changed = (
                    BusinessDocumentJob.update(
                        status="RUNNING",
                        progress=0.05,
                        progress_stage="STARTING",
                        progress_message="Запускаем обработку",
                        attempt=BusinessDocumentJob.attempt + 1,
                        lease_owner=worker_id,
                        lease_token=lease_token,
                        lease_expires_at=now_ms + lease_ms,
                        update_time=now_ms,
                        update_date=datetime.now(),
                    )
                    .where(
                        (BusinessDocumentJob.id == candidate.id)
                        & (BusinessDocumentJob.status.in_(_ACTIVE_STATUSES))
                        & (BusinessDocumentJob.attempt < BusinessDocumentJob.max_attempts)
                        & (BusinessDocumentJob.available_at <= now_ms)
                        & ((BusinessDocumentJob.lease_expires_at.is_null()) | (BusinessDocumentJob.lease_expires_at <= now_ms))
                    )
                    .execute()
                )
                if changed == 1:
                    job = BusinessDocumentJob.get_by_id(candidate.id)
                    if job.job_type == "PLAN_CHANGES":
                        if job.attempt > 1:
                            BusinessDocumentStreamEvents.append(job.id, "retry", {"reason": "new_attempt"}, expected_attempt=job.attempt)
                        BusinessDocumentStreamEvents.append(job.id, "stage", {"stage": "STARTING", "message": "Запускаем обработку"}, expected_attempt=job.attempt)
                    return job
        return None

    @classmethod
    def retry(
        cls,
        job_id: str,
        worker_id: str,
        lease_token: str,
        error: dict[str, Any],
        *,
        delay_ms: int,
        now_ms: int | None = None,
    ) -> bool:
        now_ms = current_timestamp() if now_ms is None else now_ms
        database = BusinessDocumentJob._meta.database
        with database.atomic():
            changed = (
                BusinessDocumentJob.update(
                    status="RETRY",
                    progress=0.02,
                    progress_stage="RETRY_WAIT",
                    progress_message="Ожидает повторного запуска",
                    available_at=now_ms + max(0, delay_ms),
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    error=error,
                    update_time=now_ms,
                    update_date=datetime.now(),
                )
                .where(
                    (BusinessDocumentJob.id == job_id)
                    & (BusinessDocumentJob.status == "RUNNING")
                    & (BusinessDocumentJob.lease_owner == worker_id)
                    & (BusinessDocumentJob.lease_token == lease_token)
                    & (BusinessDocumentJob.lease_expires_at > now_ms)
                )
                .execute()
            )
            if changed and BusinessDocumentJob.get_by_id(job_id).job_type == "PLAN_CHANGES":
                BusinessDocumentStreamEvents.append(job_id, "retry", {"reason": error.get("code", "RETRY"), "waiting": True})
        return changed == 1

    @classmethod
    def renew(cls, job_id: str, worker_id: str, lease_token: str, *, lease_ms: int) -> bool:
        now_ms = current_timestamp()
        changed = (
            BusinessDocumentJob.update(
                lease_expires_at=now_ms + lease_ms,
                update_time=now_ms,
                update_date=datetime.now(),
            )
            .where(
                (BusinessDocumentJob.id == job_id)
                & (BusinessDocumentJob.status == "RUNNING")
                & (BusinessDocumentJob.lease_owner == worker_id)
                & (BusinessDocumentJob.lease_token == lease_token)
                & (BusinessDocumentJob.lease_expires_at > now_ms)
            )
            .execute()
        )
        return changed == 1

    @classmethod
    def update_progress(
        cls,
        job_id: str,
        worker_id: str,
        lease_token: str,
        progress: float,
        stage: str,
        message: str,
    ) -> bool:
        """Persist a user-facing milestone while the worker still owns the job."""

        progress = min(max(float(progress), 0.0), 0.99)
        now_ms = current_timestamp()
        database = BusinessDocumentJob._meta.database
        with database.atomic():
            changed = (
                BusinessDocumentJob.update(
                    progress=progress,
                    progress_stage=stage,
                    progress_message=message,
                    update_time=now_ms,
                    update_date=datetime.now(),
                )
                .where(
                    (BusinessDocumentJob.id == job_id)
                    & (BusinessDocumentJob.status == "RUNNING")
                    & (BusinessDocumentJob.lease_owner == worker_id)
                    & (BusinessDocumentJob.lease_token == lease_token)
                    & (BusinessDocumentJob.lease_expires_at > now_ms)
                )
                .execute()
            )
            if changed and BusinessDocumentJob.get_by_id(job_id).job_type == "PLAN_CHANGES":
                BusinessDocumentStreamEvents.append(job_id, "stage", {"stage": stage, "message": message, "progress": progress})
        return changed == 1

    @classmethod
    def recover_stale(cls, *, now_ms: int | None = None) -> tuple[int, int]:
        """Release expired leases or permanently fail exhausted jobs."""

        now_ms = current_timestamp() if now_ms is None else now_ms
        retry_count = 0
        dead_count = 0
        stale = list(
            BusinessDocumentJob.select().where((BusinessDocumentJob.status == "RUNNING") & (BusinessDocumentJob.lease_expires_at.is_null(False)) & (BusinessDocumentJob.lease_expires_at <= now_ms))
        )
        for job in stale:
            error = {"code": "JOB_LEASE_EXPIRED", "message": "Worker lease expired before completion"}
            if job.job_type == "SQL_QUERY_RUN" or job.attempt >= job.max_attempts:
                recovery_owner = get_uuid()
                recovery_token = get_uuid()
                claimed = (
                    BusinessDocumentJob.update(
                        lease_owner=recovery_owner,
                        lease_token=recovery_token,
                        lease_expires_at=now_ms + 60_000,
                        update_time=now_ms,
                        update_date=datetime.now(),
                    )
                    .where(
                        (BusinessDocumentJob.id == job.id)
                        & (BusinessDocumentJob.status == "RUNNING")
                        & (BusinessDocumentJob.lease_token == job.lease_token)
                        & (BusinessDocumentJob.lease_expires_at == job.lease_expires_at)
                    )
                    .execute()
                )
                if claimed != 1:
                    continue
                try:
                    if job.job_type == "SQL_QUERY_RUN":
                        recovered_job = BusinessDocumentJob.get_by_id(job.id)
                        BusinessDocumentSqlRunService.fail_job(recovered_job, recovery_owner, recovery_token, error)
                    elif job.job_type.startswith("SQL_AGENT_"):
                        recovered_job = BusinessDocumentJob.get_by_id(job.id)
                        BusinessDocumentSqlAgentService.fail_job(recovered_job, recovery_owner, recovery_token, error)
                    else:
                        job_completion.fail(job.tenant_id, recovery_owner, job.id, error, recovery_token)
                    dead_count += 1
                except BusinessDocumentError:
                    logging.exception("Unable to dead-letter stale business document job %s", job.id)
                continue
            with BusinessDocumentJob._meta.database.atomic():
                changed = (
                    BusinessDocumentJob.update(
                        status="RETRY",
                        progress=0.02,
                        progress_stage="RETRY_WAIT",
                        progress_message="Ожидает повторного запуска",
                        available_at=now_ms,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        error=error,
                        update_time=now_ms,
                        update_date=datetime.now(),
                    )
                    .where(
                        (BusinessDocumentJob.id == job.id)
                        & (BusinessDocumentJob.status == "RUNNING")
                        & (BusinessDocumentJob.lease_token == job.lease_token)
                        & (BusinessDocumentJob.lease_expires_at == job.lease_expires_at)
                    )
                    .execute()
                )
                if changed == 1 and job.job_type == "PLAN_CHANGES":
                    BusinessDocumentStreamEvents.append(job.id, "retry", {"reason": "JOB_LEASE_EXPIRED", "waiting": True})
            retry_count += int(changed == 1)
        return retry_count, dead_count


class _LeaseHeartbeat:
    def __init__(self, job: BusinessDocumentJob, worker_id: str, lease_ms: int):
        self._job_id = job.id
        self._worker_id = worker_id
        self._lease_token = job.lease_token
        self._lease_ms = lease_ms
        self._lost_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._lease_token:
            raise RuntimeError("Claimed job has no lease token")
        interval = max(1.0, self._lease_ms / 3000)

        def run():
            while not self._stop_event.wait(interval):
                try:
                    with BusinessDocumentJob._meta.database.connection_context():
                        renewed = BusinessDocumentJobQueue.renew(
                            self._job_id,
                            self._worker_id,
                            self._lease_token,
                            lease_ms=self._lease_ms,
                        )
                    if not renewed:
                        self._lost_event.set()
                        return
                except Exception:
                    self._lost_event.set()
                    logging.exception("Unable to renew business document job lease %s", self._job_id)
                    return

        self._thread = threading.Thread(target=run, daemon=True, name=f"business-document-lease-{self._job_id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def ensure_current(self) -> None:
        if self._lost_event.is_set():
            raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")


class BusinessDocumentWorker:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        ai: BusinessDocumentAI | None = None,
        evidence: BusinessDocumentEvidence | None = None,
        export_service: type[BusinessDocumentExportService] = BusinessDocumentExportService,
        storage=None,
        sql_agent_runner: BusinessDocumentSqlAgentRunner | None = None,
        lease_ms: int = 60_000,
        retry_base_ms: int = 5_000,
        maintenance_interval_seconds: float = 60.0,
    ):
        self.worker_id = worker_id or get_uuid()
        self.ai = ai or BusinessDocumentAI()
        self.evidence = evidence or BusinessDocumentEvidence()
        self.export_service = export_service
        self.storage = storage
        self.sql_agent_runner = sql_agent_runner or BusinessDocumentSqlAgentRunner()
        self.lease_ms = lease_ms
        self.retry_base_ms = retry_base_ms
        self.maintenance_interval_seconds = max(1.0, maintenance_interval_seconds)

    def recover_stale(self, *, now_ms: int | None = None) -> tuple[int, int]:
        return BusinessDocumentJobQueue.recover_stale(now_ms=now_ms)

    def reconcile_exports(self, *, now_ms: int | None = None) -> dict[str, int]:
        BusinessDocumentStreamEvents.cleanup_expired()
        return self.export_service.reconcile_staging(storage=self.storage, now_ms=now_ms)

    def _set_progress(self, job: BusinessDocumentJob, progress: float, stage: str, message: str) -> None:
        if not job.lease_token:
            raise RuntimeError("Claimed job has no lease token")
        if not BusinessDocumentJobQueue.update_progress(job.id, self.worker_id, job.lease_token, progress, stage, message):
            raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")

    def _process_ai(self, job: BusinessDocumentJob, evidence_snapshot: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict | None]:
        def publish(preview: dict[str, Any]) -> None:
            if not BusinessDocumentStreamEvents.append(job.id, "section_preview", preview, lease_token=job.lease_token, expected_attempt=job.attempt):
                raise ConflictError("JOB_LEASE_LOST", "Worker no longer owns a current lease for this job")

        stream = getattr(self.ai, "process_stream", None)
        if isinstance(self.ai, BusinessDocumentAI):
            state = (job.result or {}).get("recovery")
            snapshot_hash = stable_hash(job.payload)
            if state is not None and state.get("snapshot_hash") != snapshot_hash:
                raise ConflictError("INVALID_JOB_SNAPSHOT", "Снимок задания изменился после начала генерации")
            recovery = AIRecovery(state, save=lambda value: BusinessDocumentJobQueue.checkpoint_ai(job, self.worker_id, value))
            recovery.state["snapshot_hash"] = snapshot_hash
            output = self.ai.process(job, evidence_snapshot, publish, recovery=recovery)
        elif job.job_type == "PLAN_CHANGES" and job.payload.get("preview_only") is True and callable(stream):
            output = stream(job, evidence_snapshot, publish)
        else:
            output = self.ai.process(job) if evidence_snapshot is None else self.ai.process(job, evidence_snapshot)
        consumer = getattr(self.ai, "consume_execution_audit", None)
        return output, consumer() if callable(consumer) else None

    def run_once(self, *, now_ms: int | None = None) -> bool:
        candidates = (
            BusinessDocumentWorkRequest.select(BusinessDocumentWorkRequest.document_id)
            .join(BusinessDocument, on=(BusinessDocumentWorkRequest.document_id == BusinessDocument.id))
            .where((BusinessDocumentWorkRequest.status == "QUEUED") & BusinessDocument.operation_state.in_(("IDLE", "FAILED")))
            .group_by(BusinessDocumentWorkRequest.document_id)
            .order_by(fn.MIN(BusinessDocumentWorkRequest.sequence))
            .limit(32)
        )
        for document_id in dict.fromkeys(row.document_id for row in candidates):
            document_commands.advance_work(document_id)
        job = BusinessDocumentJobQueue.claim(self.worker_id, lease_ms=self.lease_ms, now_ms=now_ms)
        if job is None:
            return False
        claimed_lease_token = job.lease_token
        heartbeat = None
        prepared_export = None
        try:
            heartbeat = _LeaseHeartbeat(job, self.worker_id, self.lease_ms)
            heartbeat.start()
            if job.job_type.startswith("SQL_AGENT_"):
                self._set_progress(job, 0.4, "GENERATING", "Агент анализирует подтверждённый контекст")
                output = self.sql_agent_runner.process(job)
                execution_audit = None
            elif job.job_type == "SQL_QUERY_RUN":
                self._set_progress(job, 0.4, "EXECUTING", "Выполняем проверенный PostgreSQL запрос")
                output = BusinessDocumentSqlRunService.execute_job(job, self.worker_id, claimed_lease_token)
                execution_audit = None
            elif job.job_type == "GENERATE_EXPORT":
                self._set_progress(job, 0.35, "EXPORTING", "Формируем файл")
                prepared_export = self.export_service.generate(
                    job,
                    storage=self.storage,
                    ensure_current=heartbeat.ensure_current,
                )
                output = prepared_export
                execution_audit = None
            else:
                dataset_ids = job.payload.get("dataset_ids", []) if isinstance(job.payload, dict) else []
                if dataset_ids and related_file_search_enabled():
                    self._set_progress(job, 0.15, "RETRIEVING", "Подбираем связанные материалы")
                    evidence_snapshot = self.evidence.retrieve(job)
                    self._set_progress(job, 0.32, "RETRIEVED", "Связанные материалы подготовлены")
                    execution_audit = self.evidence.audit(evidence_snapshot, job.attempt)
                    self._set_progress(job, 0.4, "GENERATING", "Формируем результат")
                    output, ai_audit = self._process_ai(job, evidence_snapshot)
                else:
                    execution_audit = None
                    self._set_progress(job, 0.4, "GENERATING", "Формируем результат")
                    output, ai_audit = self._process_ai(job)
                if ai_audit:
                    execution_audit = {**(execution_audit or {}), "ai": ai_audit}
                self._set_progress(job, 0.82, "VALIDATING", "Проверяем результат")
            self._set_progress(job, 0.92, "PERSISTING", "Сохраняем результат")
            heartbeat.ensure_current()
            if job.job_type == "SQL_QUERY_RUN" or job.job_type.startswith("SQL_AGENT_"):
                with BusinessDocumentJob._meta.database.atomic():
                    if job.job_type == "SQL_QUERY_RUN":
                        BusinessDocumentSqlRunService.complete_job(job, self.worker_id, claimed_lease_token, output)
                    else:
                        BusinessDocumentSqlAgentService.complete_job(job, self.worker_id, job.lease_token, output)
                    from api.apps.sql_assistant.conversation import advance

                    advance(job)
            else:
                job_completion.complete(
                    job.tenant_id,
                    self.worker_id,
                    job.id,
                    output,
                    job.lease_token,
                    execution_audit,
                )
        except Exception as error:
            if not isinstance(error, BusinessDocumentError) or error.code != "JOB_LEASE_LOST":
                payload = _error_payload(error)
                logging.error(
                    "Business document job failed job_id=%s document_id=%s job_type=%s attempt=%s code=%s correlation_id=%s",
                    job.id,
                    job.document_id,
                    job.job_type,
                    job.attempt,
                    payload.get("code", "WORKER_FAILURE"),
                    job.correlation_id,
                    exc_info=not isinstance(error, BusinessDocumentError),
                )
                current_job = BusinessDocumentJob.get_or_none(BusinessDocumentJob.id == job.id)
                if current_job is not None and current_job.lease_owner == self.worker_id and current_job.lease_token == claimed_lease_token:
                    document_job = current_job.job_type in {"ASSESS_INTAKE", "ASSESS_REVIEW", "GENERATE_DRAFT", "PLAN_CHANGES", "GENERATE_EXPORT"}
                    delay = self.retry_base_ms * (2 ** max(0, current_job.attempt - 1))
                    if document_job:
                        delay = retry_delay_ms(error, delay)
                    recovery = (current_job.result or {}).get("recovery")
                    deadline_reached = bool(recovery and current_timestamp() + delay >= (recovery["started_at"] + MAX_AI_SECONDS) * 1000)
                    if deadline_reached and is_transient_failure(error):
                        payload = {"code": "AI_TIME_BUDGET_EXCEEDED", "message": "Повторная попытка выходит за предел времени восстановления документа"}
                    # Research-chat failures require an explicit user action. Format
                    # repair belongs inside the bounded model operation, not a new job.
                    from api.db.db_models import SqlAssistantSession

                    assistant_job = (
                        current_job.job_type.startswith("SQL_AGENT_")
                        and SqlAssistantSession.select().where((SqlAssistantSession.id == current_job.document_id) & (SqlAssistantSession.tenant_id == current_job.tenant_id)).exists()
                    )
                    if assistant_job and not isinstance(error, BusinessDocumentError):
                        payload = {"code": "SQL_ASSISTANT_SERVICE_UNAVAILABLE", "message": "Подготовка SQL временно недоступна. Принятые условия сохранены; повторите текущий шаг позже."}
                    if (
                        assistant_job
                        or current_job.job_type == "SQL_QUERY_RUN"
                        or current_job.attempt >= current_job.max_attempts
                        or deadline_reached
                        or (document_job and not is_transient_failure(error))
                    ):
                        try:
                            if current_job.job_type == "SQL_QUERY_RUN":
                                BusinessDocumentSqlRunService.fail_job(
                                    current_job,
                                    self.worker_id,
                                    claimed_lease_token,
                                    payload,
                                )
                            elif current_job.job_type.startswith("SQL_AGENT_"):
                                BusinessDocumentSqlAgentService.fail_job(
                                    current_job,
                                    self.worker_id,
                                    claimed_lease_token,
                                    payload,
                                )
                            else:
                                job_completion.fail(
                                    current_job.tenant_id,
                                    self.worker_id,
                                    current_job.id,
                                    payload,
                                    claimed_lease_token,
                                )
                        except BusinessDocumentError:
                            logging.exception("Unable to dead-letter business document job %s", current_job.id)
                    else:
                        BusinessDocumentJobQueue.retry(
                            current_job.id,
                            self.worker_id,
                            claimed_lease_token,
                            payload,
                            delay_ms=delay,
                        )
        finally:
            if heartbeat is not None:
                heartbeat.stop()
            if prepared_export is not None:
                self.export_service.discard(prepared_export, storage=self.storage)
        return True

    def _maintain(self) -> bool:
        recovered = dead = 0
        try:
            recovered, dead = self.recover_stale()
        except Exception:
            logging.exception("Business document stale-job recovery failed")
        migrated = 0
        try:
            migrated = resume_accepted_changes()
        except Exception:
            logging.exception("Business document accepted-change migration failed")
        reconciled = {"cleaned": 0}
        try:
            reconciled = self.reconcile_exports()
        except Exception:
            logging.exception("Business document export-stage reconciliation failed")
        expired = 0
        try:
            expired = expire_due_results()
        except Exception:
            logging.exception("SQL result retention cleanup failed")
        return bool(recovered or dead or migrated or reconciled["cleaned"] or expired)

    def run_forever(self, stop_event: threading.Event, *, poll_seconds: float = 2.0, maintenance_lock: threading.Lock | None = None) -> None:
        database = BusinessDocumentJob._meta.database
        maintenance_lock = maintenance_lock if maintenance_lock is not None else threading.Lock()
        maintenance = False
        next_maintenance_at = 0.0
        try:
            while not stop_event.is_set():
                worked = False
                try:
                    # Peewee connections are thread-local. Return each worker's
                    # connection before sleeping or taking another job.
                    with database.connection_context():
                        if not maintenance and maintenance_lock.acquire(blocking=False):
                            maintenance = True
                            self._maintain()
                            next_maintenance_at = monotonic() + self.maintenance_interval_seconds
                        worked = self.run_once()
                        if maintenance and (not worked or monotonic() >= next_maintenance_at):
                            changed = self._maintain()
                            next_maintenance_at = monotonic() + self.maintenance_interval_seconds
                            worked = worked or changed
                except asyncio.CancelledError:
                    # run_once has stopped the heartbeat. Leave the durable job
                    # fenced until lease recovery, and let another member maintain.
                    logging.warning("Business document operation cancelled for worker %s", self.worker_id)
                    if maintenance:
                        maintenance = False
                        maintenance_lock.release()
                    worked = False
                except Exception:
                    logging.exception("Business document worker poll failed")
                if worked:
                    continue
                _WAKE_EVENT.wait(poll_seconds)
                _WAKE_EVENT.clear()
        finally:
            if maintenance:
                maintenance_lock.release()


def wake_business_document_worker() -> None:
    """Wake idle pool members; the durable row remains the source of truth."""

    _WAKE_EVENT.set()


def start_business_document_workers(stop_event: threading.Event) -> tuple[threading.Thread, ...]:
    """Start a bounded pool per API process; DB leases fence every execution."""

    global _WORKER_THREADS
    if os.environ.get("BUSINESS_DOCUMENT_WORKER_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return ()
    with _START_LOCK:
        if any(thread.is_alive() for thread in _WORKER_THREADS):
            if all(thread.is_alive() for thread in _WORKER_THREADS):
                return _WORKER_THREADS
            concurrency = len(_WORKER_THREADS)
        else:
            concurrency = int(os.environ.get("BUSINESS_DOCUMENT_WORKER_CONCURRENCY", "4"))
        if not 1 <= concurrency <= _MAX_WORKERS:
            raise ValueError(f"BUSINESS_DOCUMENT_WORKER_CONCURRENCY must be between 1 and {_MAX_WORKERS}")
        poll_seconds = float(os.environ.get("BUSINESS_DOCUMENT_WORKER_POLL_SECONDS", "2"))
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("BUSINESS_DOCUMENT_WORKER_POLL_SECONDS must be positive and finite")
        # Adapters retain per-job audits and recovery state; do not share them.
        # Surviving members retain their identity when a dead slot is replaced.
        threads = []
        started = []
        for index in range(concurrency):
            if index < len(_WORKER_THREADS) and _WORKER_THREADS[index].is_alive():
                threads.append(_WORKER_THREADS[index])
                continue
            worker = BusinessDocumentWorker()
            thread = threading.Thread(
                target=worker.run_forever,
                args=(stop_event,),
                kwargs={"poll_seconds": poll_seconds, "maintenance_lock": _MAINTENANCE_LOCK},
                daemon=True,
                name=f"business-document-worker-{index + 1}",
            )
            threads.append(thread)
            started.append(thread)
        _WORKER_THREADS = tuple(threads)
        for thread in started:
            thread.start()
        logging.info("Started %s business document workers in this process", concurrency)
        return _WORKER_THREADS
