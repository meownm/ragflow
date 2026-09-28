import sys
from pathlib import Path

from peewee import SqliteDatabase


ADMIN_SERVER = Path(__file__).parents[3] / "admin" / "server"
if str(ADMIN_SERVER) not in sys.path:
    sys.path.insert(0, str(ADMIN_SERVER))

import document_quality
from api.db.db_models import BusinessDocumentJob

summarize_jobs = document_quality.summarize_jobs


def test_summary_uses_persisted_ai_audit_and_marks_missing_measurements():
    rows = [
        {
            "id": "complete-1",
            "document_id": "doc-1",
            "tenant_id": "tenant-1",
            "job_type": "GENERATE_DRAFT",
            "status": "COMPLETED",
            "create_time": 800,
            "update_time": 2000,
            "error": None,
            "result": {"execution": {"ai": {"provider": "Ollama", "model": "qwen", "duration_ms": 1200, "token_usage": {"total_tokens": 42}}}},
        },
        {
            "id": "complete-2",
            "document_id": "doc-2",
            "tenant_id": "tenant-1",
            "job_type": "GENERATE_DRAFT",
            "status": "COMPLETED",
            "create_time": 1700,
            "update_time": 1900,
            "error": None,
            "result": {},
        },
        {
            "id": "dead-1",
            "document_id": "doc-3",
            "tenant_id": "tenant-2",
            "job_type": "ASSESS_REVIEW",
            "status": "DEAD",
            "create_time": 1000,
            "update_time": 1800,
            "error": {"code": "MODEL_ERROR", "message": "secret"},
            "result": None,
        },
    ]

    summary = summarize_jobs(rows, days=7, truncated=False)

    assert summary["completed"] == 2
    assert summary["failed"] == 1
    assert summary["failure_rate"] == 1 / 3
    assert summary["latency_p95_ms"] == 1200
    assert summary["measured_latency_jobs"] == 3
    assert summary["measured_model_latency_jobs"] == 1
    assert summary["model_latency_p95_ms"] == 1200
    assert summary["failed_documents"] == 1
    assert summary["affected_tenants"] == 1
    assert summary["total_tokens"] == 42
    assert summary["measured_token_jobs"] == 1
    assert summary["models"] == [{"provider": "Ollama", "model": "qwen", "count": 1}]
    assert summary["errors"] == [{"task_type": "ASSESS_REVIEW", "error_code": "MODEL_ERROR", "count": 1}]
    assert "secret" not in str(summary)


def test_empty_summary_has_no_misleading_zero_rate_or_latency():
    summary = summarize_jobs([], days=1, truncated=False)
    assert summary["failure_rate"] is None
    assert summary["latency_p95_ms"] is None
    assert summary["models"] == []


def test_dashboard_counts_all_job_types_and_active_jobs_in_window(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(document_quality, "current_timestamp", lambda: 1_000_000_000)
    with database.bind_ctx([BusinessDocumentJob], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentJob])
        try:
            for identifier, job_type, status, finished_at in (
                ("included", "GENERATE_DRAFT", "COMPLETED", 999_999_000),
                ("old", "GENERATE_DRAFT", "DEAD", 800_000_000),
                ("export", "GENERATE_EXPORT", "COMPLETED", 999_999_000),
                ("pending", "ASSESS_REVIEW", "PENDING", 999_999_000),
            ):
                BusinessDocumentJob.create(
                    id=identifier, document_id="doc", tenant_id="qa", job_type=job_type,
                    status=status, dedupe_key=identifier, source_state_version=1,
                    payload={}, available_at=finished_at, correlation_id=identifier,
                    create_time=finished_at, update_time=finished_at,
                )
                database.execute_sql(
                    f'UPDATE "{BusinessDocumentJob._meta.table_name}" SET "{BusinessDocumentJob.update_time.column_name}" = ? WHERE "{BusinessDocumentJob.id.column_name}" = ?',
                    (finished_at, identifier),
                )
            summary = document_quality.document_quality_dashboard(days=1)
            assert summary["sampled_jobs"] == 2, summary["tasks"]
            assert summary["terminal_jobs"] == 2
            assert summary["pending"] == 1
            assert {row["category"] for row in summary["tasks"]} == {"DOCUMENT_AI", "EXPORT"}
        finally:
            database.drop_tables([BusinessDocumentJob])
            database.close()


def test_failed_job_table_filters_all_matching_rows_and_omits_error_message(monkeypatch):
    database = SqliteDatabase(":memory:")
    monkeypatch.setattr(document_quality, "current_timestamp", lambda: 1_000_000_000)
    with database.bind_ctx([BusinessDocumentJob], bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables([BusinessDocumentJob])
        try:
            for identifier, job_type, code in (
                ("sql-1", "SQL_AGENT_QUERY", "INVALID_AI_JSON"),
                ("sql-2", "SQL_AGENT_QUERY", "TIMEOUT"),
                ("export", "GENERATE_EXPORT", "INVALID_AI_JSON"),
            ):
                BusinessDocumentJob.create(
                    id=identifier, document_id="doc", tenant_id="qa", job_type=job_type,
                    status="DEAD", dedupe_key=identifier, source_state_version=1,
                    payload={}, error={"code": code, "message": "private document text"},
                    attempt=3, max_attempts=3, available_at=999_999_000, correlation_id=identifier,
                    create_time=999_998_000, update_time=999_999_000,
                )
                database.execute_sql(
                    f'UPDATE "{BusinessDocumentJob._meta.table_name}" SET "{BusinessDocumentJob.update_time.column_name}" = ? WHERE "{BusinessDocumentJob.id.column_name}" = ?',
                    (999_999_000, identifier),
                )
            page = document_quality.failed_jobs_page(days=7, category="SQL_AGENT", error_code="INVALID_AI_JSON")
            assert page["total"] == 1
            assert page["jobs"][0]["id"] == "sql-1"
            assert page["jobs"][0]["attempt"] == 3
            assert page["error_codes"] == ["INVALID_AI_JSON", "TIMEOUT"]
            assert "private document text" not in str(page)
        finally:
            database.drop_tables([BusinessDocumentJob])
            database.close()
