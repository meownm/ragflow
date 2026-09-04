#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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

import logging
import os
from typing import Any

from peewee import OperationalError, ProgrammingError

from api.db.db_models import BusinessDocument, BusinessDocumentEvent, Connector, PipelineOperationLog, SyncLogs, SystemAuditEvent, User
from common.time_utils import current_timestamp


AUDIT_RETENTION_DAYS = 30
MAX_PAGE_SIZE = 100
MAX_SOURCE_EVENTS = 2000
_MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000
_SAFE_BUSINESS_DETAIL_KEYS = {
    "decision",
    "job_id",
    "job_type",
    "lifecycle_state",
    "operation_state",
    "review_cycle",
    "revision_id",
    "revision_number",
    "section_id",
}


def _truncate(value: Any, limit: int = 2000) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _normalise_status(value: Any) -> str:
    return str(value or "").strip().lower()


def classify_outcome(status: Any, action: str = "") -> str:
    normalized = _normalise_status(status)
    action_lower = action.lower()
    if normalized in {"2", "cancel", "cancelled", "canceled"} or "cancel" in action_lower:
        return "cancelled"
    if normalized in {"4", "fail", "failed", "error"} or any(token in action_lower for token in ("failed", "error")):
        return "failure"
    if normalized in {"0", "1", "5", "unstart", "running", "schedule", "scheduled", "pending", "queued"} or any(
        token in action_lower for token in ("requested", "started", "queued", "scheduled")
    ):
        return "pending"
    return "success"


def _actor(actor_id: str | None, actor_type: str, users: dict[str, dict[str, str]]) -> dict[str, str]:
    result = {"type": actor_type}
    if actor_id:
        result["id"] = actor_id
        result.update(users.get(actor_id, {}))
    return result


def _safe_business_details(payload: Any, sequence: int) -> dict[str, Any]:
    details: dict[str, Any] = {"sequence": sequence}
    if not isinstance(payload, dict):
        return details
    for key in _SAFE_BUSINESS_DETAIL_KEYS:
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                details[key] = value
    return details


def _business_error(payload: Any, document_error: Any) -> dict[str, str] | None:
    for candidate in (payload, document_error):
        if not isinstance(candidate, dict):
            continue
        message = candidate.get("message") or candidate.get("error_message") or candidate.get("error")
        code = candidate.get("code") or candidate.get("error_code")
        if isinstance(message, (str, int, float)) and message:
            error = {"message": _truncate(message)}
            if isinstance(code, (str, int, float)) and code:
                error["code"] = _truncate(code, 128)
            return error
    return None


def _matches(event: dict[str, Any], query: str, actor_query: str) -> bool:
    actor = event["actor"]
    if actor_query:
        actor_text = " ".join(str(actor.get(key, "")) for key in ("id", "nickname", "email")).lower()
        if actor_query not in actor_text:
            return False
    if not query:
        return True
    searchable = " ".join(
        str(value or "")
        for value in (
            event["action"],
            event["summary"],
            event["object"]["id"],
            event["object"]["label"],
            event.get("correlation_id"),
            event.get("causation_id"),
            event.get("request_id"),
            event.get("trace_id"),
            event.get("error_id"),
            actor.get("id"),
            actor.get("nickname"),
            actor.get("email"),
            (event.get("error") or {}).get("message"),
        )
    ).lower()
    return query in searchable


class AuditFeed:
    @classmethod
    def list_events(
        cls,
        *,
        page: int = 1,
        page_size: int = 25,
        source: str = "",
        outcome: str = "",
        query: str = "",
        actor: str = "",
        correlation_id: str = "",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(MAX_PAGE_SIZE, max(1, page_size))
        correlation_id = correlation_id.strip()
        since = current_timestamp() - AUDIT_RETENTION_DAYS * _MILLISECONDS_PER_DAY
        selected_sources = [source] if source else ["application", "business_documents", "ingestion", "connectors"]
        invalid_sources = set(selected_sources) - {"application", "business_documents", "ingestion", "connectors"}
        if invalid_sources:
            raise ValueError(f"Unknown audit source: {', '.join(sorted(invalid_sources))}")
        if outcome and outcome not in {"success", "failure", "pending", "cancelled"}:
            raise ValueError(f"Unknown audit outcome: {outcome}")

        users = cls._load_users()
        events: list[dict[str, Any]] = []
        unavailable_sources: list[str] = []
        loaders = {
            "application": cls._application_events,
            "business_documents": cls._business_events,
            "ingestion": cls._pipeline_events,
            "connectors": cls._connector_events,
        }
        for source_name in selected_sources:
            try:
                events.extend(loaders[source_name](since, users, correlation_id))
            except (OperationalError, ProgrammingError) as exc:
                logging.warning("Audit source %s is unavailable: %s", source_name, exc)
                unavailable_sources.append(source_name)

        query_lower = query.strip().lower()
        actor_lower = actor.strip().lower()
        filtered = [
            event
            for event in events
            if (not outcome or event["outcome"] == outcome)
            and (not correlation_id or event.get("correlation_id") == correlation_id)
            and _matches(event, query_lower, actor_lower)
        ]
        filtered.sort(key=lambda event: (event["occurred_at"], event["id"]), reverse=True)
        total = len(filtered)
        start = (page - 1) * page_size
        page_items = filtered[start : start + page_size]

        return {
            "items": page_items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "retention_days": AUDIT_RETENTION_DAYS,
            "unavailable_sources": unavailable_sources,
            "stats": {
                "failures": sum(event["outcome"] == "failure" for event in filtered),
                "sources": len({event["source"] for event in filtered}),
            },
            "observability": {
                "enabled": bool(os.getenv("GRAFANA_PUBLIC_URL")),
                "grafana_url": os.getenv("GRAFANA_PUBLIC_URL", "").rstrip("/"),
                "loki_datasource_uid": os.getenv("GRAFANA_LOKI_DATASOURCE_UID", "loki"),
                "tempo_datasource_uid": os.getenv("GRAFANA_TEMPO_DATASOURCE_UID", "tempo"),
            },
        }

    @staticmethod
    def _load_users() -> dict[str, dict[str, str]]:
        rows = User.select(User.id, User.nickname, User.email).dicts()
        return {row["id"]: {"nickname": row["nickname"], "email": row["email"]} for row in rows}

    @staticmethod
    def _application_events(
        since: int, users: dict[str, dict[str, str]], correlation_id: str = ""
    ) -> list[dict[str, Any]]:
        where = SystemAuditEvent.create_time >= since
        if correlation_id:
            where &= SystemAuditEvent.correlation_id == correlation_id
        rows = (
            SystemAuditEvent.select()
            .where(where)
            .order_by(SystemAuditEvent.create_time.desc())
            .limit(MAX_SOURCE_EVENTS)
            .dicts()
        )
        events = []
        for row in rows:
            reason_code = row.get("reason_code")
            events.append(
                {
                    "id": f"application:{row['id']}",
                    "occurred_at": row["create_time"],
                    "source": "application",
                    "action": row["action"],
                    "outcome": row["outcome"],
                    "summary": row.get("object_id") or row["object_type"],
                    "actor": _actor(row.get("actor_id"), row["actor_type"], users),
                    "object": {
                        "type": row["object_type"],
                        "id": row.get("object_id") or "system",
                        "label": row.get("object_id") or row["object_type"],
                    },
                    "correlation_id": row["correlation_id"],
                    "causation_id": row.get("causation_id"),
                    "request_id": row.get("request_id"),
                    "trace_id": row.get("trace_id"),
                    "span_id": row.get("span_id"),
                    "interaction_id": row.get("interaction_id"),
                    "job_id": row.get("job_id"),
                    "session_id": row.get("session_id"),
                    "error_id": row.get("error_id"),
                    "error": {"code": reason_code, "message": reason_code} if row["outcome"] == "failure" and reason_code else None,
                    "details": row.get("event_metadata") or {},
                }
            )
        return events

    @staticmethod
    def _business_events(
        since: int, users: dict[str, dict[str, str]], correlation_id: str = ""
    ) -> list[dict[str, Any]]:
        where = BusinessDocumentEvent.create_time >= since
        if correlation_id:
            where &= BusinessDocumentEvent.correlation_id == correlation_id
        rows = list(
            BusinessDocumentEvent.select(
                BusinessDocumentEvent.id,
                BusinessDocumentEvent.document_id,
                BusinessDocumentEvent.sequence,
                BusinessDocumentEvent.event_type,
                BusinessDocumentEvent.actor_type,
                BusinessDocumentEvent.actor_id,
                BusinessDocumentEvent.payload,
                BusinessDocumentEvent.correlation_id,
                BusinessDocumentEvent.causation_id,
                BusinessDocumentEvent.create_time,
            )
            .where(where)
            .order_by(BusinessDocumentEvent.create_time.desc())
            .limit(MAX_SOURCE_EVENTS)
            .dicts()
        )
        document_ids = {row["document_id"] for row in rows}
        documents = {
            row["id"]: row
            for row in BusinessDocument.select(
                BusinessDocument.id,
                BusinessDocument.title,
                BusinessDocument.last_error,
            )
            .where(BusinessDocument.id.in_(document_ids))
            .dicts()
        }
        events = []
        for row in rows:
            document = documents.get(row["document_id"], {})
            outcome = classify_outcome("", row["event_type"])
            error = _business_error(row["payload"], document.get("last_error")) if outcome == "failure" else None
            events.append(
                {
                    "id": f"business_documents:{row['id']}",
                    "occurred_at": row["create_time"],
                    "source": "business_documents",
                    "action": row["event_type"],
                    "outcome": outcome,
                    "summary": document.get("title") or row["document_id"],
                    "actor": _actor(row["actor_id"], row["actor_type"], users),
                    "object": {"type": "business_document", "id": row["document_id"], "label": document.get("title") or row["document_id"]},
                    "correlation_id": row["correlation_id"],
                    "causation_id": row["causation_id"],
                    "error": error,
                    "details": _safe_business_details(row["payload"], row["sequence"]),
                }
            )
        return events

    @staticmethod
    def _pipeline_events(
        since: int, users: dict[str, dict[str, str]], correlation_id: str = ""
    ) -> list[dict[str, Any]]:
        where = PipelineOperationLog.create_time >= since
        if correlation_id:
            where &= PipelineOperationLog.id == correlation_id
        rows = (
            PipelineOperationLog.select(
                PipelineOperationLog.id,
                PipelineOperationLog.document_id,
                PipelineOperationLog.tenant_id,
                PipelineOperationLog.kb_id,
                PipelineOperationLog.pipeline_id,
                PipelineOperationLog.pipeline_title,
                PipelineOperationLog.parser_id,
                PipelineOperationLog.document_name,
                PipelineOperationLog.source_from,
                PipelineOperationLog.progress,
                PipelineOperationLog.progress_msg,
                PipelineOperationLog.process_duration,
                PipelineOperationLog.task_type,
                PipelineOperationLog.operation_status,
                PipelineOperationLog.create_time,
            )
            .where(where)
            .order_by(PipelineOperationLog.create_time.desc())
            .limit(MAX_SOURCE_EVENTS)
            .dicts()
        )
        events = []
        for row in rows:
            outcome = classify_outcome(row["operation_status"], row["task_type"])
            progress_message = _truncate(row["progress_msg"])
            events.append(
                {
                    "id": f"ingestion:{row['id']}",
                    "occurred_at": row["create_time"],
                    "source": "ingestion",
                    "action": row["task_type"] or "ingestion",
                    "outcome": outcome,
                    "summary": row["document_name"],
                    "actor": _actor(row["tenant_id"], "TENANT", users),
                    "object": {"type": "document", "id": row["document_id"], "label": row["document_name"]},
                    "correlation_id": row["id"],
                    "causation_id": None,
                    "error": {"message": progress_message} if outcome == "failure" and progress_message else None,
                    "details": {
                        "kb_id": row["kb_id"],
                        "pipeline_id": row["pipeline_id"],
                        "pipeline_title": row["pipeline_title"],
                        "parser_id": row["parser_id"],
                        "progress": row["progress"],
                        "process_duration": row["process_duration"],
                        "source_from": row["source_from"],
                        "status": row["operation_status"],
                    },
                }
            )
        return events

    @staticmethod
    def _connector_events(
        since: int, users: dict[str, dict[str, str]], correlation_id: str = ""
    ) -> list[dict[str, Any]]:
        where = SyncLogs.create_time >= since
        if correlation_id:
            where &= SyncLogs.id == correlation_id
        rows = list(
            SyncLogs.select(
                SyncLogs.id,
                SyncLogs.connector_id,
                SyncLogs.task_type,
                SyncLogs.status,
                SyncLogs.new_docs_indexed,
                SyncLogs.total_docs_indexed,
                SyncLogs.docs_removed_from_index,
                SyncLogs.error_msg,
                SyncLogs.error_count,
                SyncLogs.kb_id,
                SyncLogs.create_time,
            )
            .where(where)
            .order_by(SyncLogs.create_time.desc())
            .limit(MAX_SOURCE_EVENTS)
            .dicts()
        )
        connector_ids = {row["connector_id"] for row in rows}
        connectors = {
            row["id"]: row
            for row in Connector.select(Connector.id, Connector.tenant_id, Connector.name, Connector.source)
            .where(Connector.id.in_(connector_ids))
            .dicts()
        }
        events = []
        for row in rows:
            connector = connectors.get(row["connector_id"], {})
            outcome = classify_outcome(row["status"], row["task_type"])
            error_message = _truncate(row["error_msg"])
            events.append(
                {
                    "id": f"connectors:{row['id']}",
                    "occurred_at": row["create_time"],
                    "source": "connectors",
                    "action": row["task_type"],
                    "outcome": outcome,
                    "summary": connector.get("name") or row["connector_id"],
                    "actor": _actor(connector.get("tenant_id"), "TENANT", users),
                    "object": {
                        "type": "connector",
                        "id": row["connector_id"],
                        "label": connector.get("name") or row["connector_id"],
                    },
                    "correlation_id": row["id"],
                    "causation_id": None,
                    "error": {"message": error_message} if outcome == "failure" and error_message else None,
                    "details": {
                        "connector_source": connector.get("source"),
                        "docs_removed": row["docs_removed_from_index"],
                        "error_count": row["error_count"],
                        "kb_id": row["kb_id"],
                        "new_docs": row["new_docs_indexed"],
                        "status": row["status"],
                        "total_docs": row["total_docs_indexed"],
                    },
                }
            )
        return events
