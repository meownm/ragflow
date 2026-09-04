#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#

import logging
import os
import time
from typing import Any

from api.db.db_models import SystemAuditEvent
from common.misc_utils import get_uuid
from common.observability import get_log_context
from common.time_utils import current_timestamp
from common.versions import get_ragflow_version


AUDIT_RETENTION_DAYS = 30
_RETENTION_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_next_retention_check = 0.0
_SAFE_METADATA_KEYS = {
    "method",
    "path",
    "status_code",
    "duration_ms",
    "component",
    "stage",
    "dependency",
    "retryable",
    "attempt",
    "browser",
    "route",
    "line",
    "column",
}


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key not in _SAFE_METADATA_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = str(value)[:512] if isinstance(value, str) else value
    return result


def record_audit_event(
    *,
    action: str,
    outcome: str,
    actor_id: str | None = None,
    actor_type: str = "SYSTEM",
    auth_type: str | None = None,
    tenant_id: str | None = None,
    object_type: str = "system",
    object_id: str | None = None,
    reason_code: str | None = None,
    error_id: str | None = None,
    causation_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Best-effort append; observability must never break a user request."""
    _purge_expired_audit_events_if_due()
    context = get_log_context()
    correlation_id = context.get("correlation_id") or context.get("request_id") or get_uuid()
    try:
        SystemAuditEvent.create(
            id=get_uuid(),
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            auth_type=auth_type,
            action=action[:128],
            object_type=object_type[:64],
            object_id=(object_id or "")[:128] or None,
            outcome=outcome[:16],
            reason_code=(reason_code or "")[:64] or None,
            error_id=(error_id or "")[:32] or None,
            correlation_id=correlation_id[:128],
            causation_id=(causation_id or "")[:128] or None,
            request_id=context.get("request_id"),
            trace_id=context.get("trace_id"),
            span_id=context.get("span_id"),
            interaction_id=context.get("interaction_id"),
            job_id=context.get("job_id"),
            session_id=(session_id or context.get("session_id") or "")[:128] or None,
            service=os.getenv("OTEL_SERVICE_NAME", "ragflow")[:64],
            service_version=get_ragflow_version()[:64],
            event_metadata=_safe_metadata(metadata),
        )
        return correlation_id
    except Exception:
        logging.exception("Failed to append system audit event action=%s", action)
        return None


def purge_expired_audit_events(retention_days: int = AUDIT_RETENTION_DAYS) -> int:
    cutoff = current_timestamp() - retention_days * 24 * 60 * 60 * 1000
    return SystemAuditEvent.delete().where(SystemAuditEvent.create_time < cutoff).execute()


def _purge_expired_audit_events_if_due() -> None:
    global _next_retention_check
    now = time.monotonic()
    if now < _next_retention_check:
        return
    _next_retention_check = now + _RETENTION_CHECK_INTERVAL_SECONDS
    try:
        purged = purge_expired_audit_events()
        if purged:
            logging.info("Purged %s audit events outside the 30-day retention window", purged)
    except Exception:
        logging.exception("Audit retention cleanup failed")
