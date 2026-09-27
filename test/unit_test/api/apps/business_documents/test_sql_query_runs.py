from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from peewee import SqliteDatabase

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import sql_query_runs
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService
from api.apps.business_documents.sql_query_postgres import _dbapi_sql
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import User
from business_documents.application.errors import ConflictError, PermissionDeniedError

actual_current_actor_access = sql_query_runs._current_actor_access


@pytest.fixture()
def database(monkeypatch):
    database = SqliteDatabase(":memory:", check_same_thread=False)
    tables = BusinessDocumentSqlAgentService.model_tables()
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        project = BusinessDocumentSqlAgentService.create_project(
            "tenant-1",
            "author-1",
            {"schema_version": "1", "title": "Продажи", "source_request": "Покажи продажи", "locale": "ru"},
        )
        sql_query_runs.BusinessDocumentSqlQueryProject.update(stage="COMPLETE").where(sql_query_runs.BusinessDocumentSqlQueryProject.id == project["id"]).execute()
        compiled = {
            "sql": "SELECT value FROM public.sales LIMIT :row_limit",
            "parameters": {"row_limit": 2},
            "snapshot_fingerprint": "sha256:sample",
            "guard": {"status": "PASS"},
            "output_columns": ["value"],
        }
        compilation = SimpleNamespace(id="compilation-1", payload={"result": compiled}, source_proposal_id="proposal-1")
        monkeypatch.setattr(
            sql_query_runs,
            "_source",
            lambda _: (
                compilation,
                {
                    "accepted_requirements": "Покажи продажи",
                    "specification": {"select": [{"alias": "value"}]},
                },
            ),
        )
        monkeypatch.setattr(sql_query_runs, "_verify_schema", lambda *args: None)
        monkeypatch.setattr(sql_query_runs, "_current_actor_access", lambda actor_id: ("AUTHOR_CREATOR", False))
        profile = SimpleNamespace(id="profile-1", version=1, max_rows=10, statement_timeout_ms=1000, max_result_bytes=1000)
        connector = SimpleNamespace(config={})
        monkeypatch.setattr(sql_query_runs, "_binding", lambda *args: ({"status": "BOUND"}, profile, connector))
        monkeypatch.setattr(sql_query_runs, "explain_postgres", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            sql_query_runs,
            "execute_postgres",
            lambda *args, **kwargs: {
                "columns": ["value"],
                "rows": [[42]],
                "row_count": 1,
                "result_bytes": 4,
                "duration_ms": 2,
            },
        )
        yield project["id"]
        database.drop_tables(tables)
        database.close()


def test_guarded_bind_translation_preserves_postgres_casts():
    assert _dbapi_sql("SELECT :value::date LIMIT :row_limit", {"value": "2026-09-27", "row_limit": 2}) == "SELECT %(value)s::date LIMIT %(row_limit)s"


def test_preflight_checks_live_postgres_shape(database, monkeypatch):
    checked = []
    monkeypatch.setattr(sql_query_runs, "explain_postgres", lambda config, sql, parameters, **options: checked.append((sql, parameters, options)))
    result = sql_query_runs.BusinessDocumentSqlRunService.preflight("tenant-1", "author-1", database, None)
    assert result["binding"]["status"] == "BOUND"
    assert checked == [("SELECT value FROM public.sales LIMIT :row_limit", {"row_limit": 2}, {"timeout_ms": 1000})]


def test_editor_can_keep_compiled_sql_without_execution_right(database):
    preflight = sql_query_runs.BusinessDocumentSqlRunService.preflight("tenant-1", "author-1", database, None, access_role="AUTHOR_EDITOR")
    assert preflight["blocker"]["code"] == "SQL_EXECUTION_FORBIDDEN"
    with pytest.raises(PermissionDeniedError):
        sql_query_runs.BusinessDocumentSqlRunService.run(
            "tenant-1",
            "author-1",
            database,
            {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "editor-run", "selected_profile_id": None},
            access_role="AUTHOR_EDITOR",
        )
    assert sql_query_runs.BusinessDocumentSqlQueryRun.select().count() == 0


def test_worker_rechecks_execution_role_before_postgres(database, monkeypatch):
    queued = sql_query_runs.BusinessDocumentSqlRunService.run(
        "tenant-1",
        "author-1",
        database,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "queued-run", "selected_profile_id": None},
    )
    job = BusinessDocumentJobQueue.claim("revoked-role-test")
    monkeypatch.setattr(sql_query_runs, "_current_actor_access", lambda actor_id: ("AUTHOR_EDITOR", False))
    with pytest.raises(PermissionDeniedError):
        sql_query_runs.BusinessDocumentSqlRunService.execute_job(job, "revoked-role-test", job.lease_token)
    assert sql_query_runs.BusinessDocumentSqlQueryRun.get_by_id(queued["run_id"]).rows == []


def test_current_actor_access_rejects_inactive_users(database):
    with sql_query_runs.BusinessDocumentSqlQueryRun._meta.database.bind_ctx([User], bind_refs=False, bind_backrefs=False):
        User.create_table()
        User.create(id="author-1", nickname="Author", email="author@example.test", business_document_role="AUTHOR_EDITOR", is_active="1", status="1")
        assert actual_current_actor_access("author-1") == ("AUTHOR_EDITOR", False)
        User.update(is_active="0").where(User.id == "author-1").execute()
        with pytest.raises(PermissionDeniedError):
            actual_current_actor_access("author-1")
        User.drop_table()


def test_failed_latest_run_keeps_older_ready_run_available(database):
    for run_id, status in (("ready-run", "READY"), ("failed-run", "FAILED")):
        sql_query_runs.BusinessDocumentSqlQueryRun.create(
            id=run_id,
            project_id=database,
            tenant_id="tenant-1",
            compilation_id="compilation-1",
            profile_id="profile-1",
            profile_version=1,
            status=status,
            columns=["value"],
            rows=[[42]] if status == "READY" else [],
            row_count=1 if status == "READY" else 0,
            checks={"status": "PASS"} if status == "READY" else {},
            error={"code": "SQL_TIMEOUT", "message": "Timed out"} if status == "FAILED" else None,
        )
    project = BusinessDocumentSqlAgentService.get_project("tenant-1", "author-1", database)
    assert {run["id"] for run in project["runs"]} == {"ready-run", "failed-run"}
    assert project["next_action"] == "VIEW_RESULT"
    assert sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", database, "ready-run")["rows"] == [[42]]


def test_catalog_version_change_blocks_execution(monkeypatch):
    table = SimpleNamespace(id="table-1", version=2, schema_fingerprint="sha256:old", fqn="svc.db.public.sales")
    monkeypatch.setattr(sql_query_runs, "parse_schema_snapshot", lambda _: SimpleNamespace(tables=[table]))

    async def load_entities(*args):
        return {"entities": [{"entity_id": "table-1", "lookup": {"status": "OK"}, "freshness": {"stale": False}, "entity": {"version": 3, "schema_fingerprint": "sha256:new", "fqn": table.fqn}}]}

    monkeypatch.setattr(sql_query_runs.BusinessDocumentSqlQuerySchemaService, "load_entities", load_entities)
    with pytest.raises(ConflictError) as error:
        sql_query_runs._verify_schema({"schema_snapshot": {}}, "author-1", False, "AUTHOR_CREATOR")
    assert error.value.code == "SQL_SCHEMA_STALE"


def test_run_preview_and_completion_purge_rows(database):
    project_id = database
    run = sql_query_runs.BusinessDocumentSqlRunService.run(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-1", "selected_profile_id": None},
    )
    assert run["status"] == "QUEUED"
    job = BusinessDocumentJobQueue.claim("sql-test")
    assert job is not None
    output = sql_query_runs.BusinessDocumentSqlRunService.execute_job(job, "sql-test", job.lease_token)
    sql_query_runs.BusinessDocumentSqlRunService.complete_job(job, "sql-test", job.lease_token, output)
    assert sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", project_id, run["run_id"])["rows"] == [[42]]
    assert sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", project_id, run["run_id"])["checks"]["status"] == "PASS"
    completed = sql_query_runs.BusinessDocumentSqlRunService.complete(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "complete-1", "run_id": run["run_id"]},
    )
    assert completed["rows_status"] == "PURGED"
    assert "rows" not in completed["document"]
    saved = sql_query_runs.BusinessDocumentSqlQueryRun.get_by_id(run["run_id"])
    assert saved.rows == [] and saved.status == "PURGED"
    with pytest.raises(Exception):
        sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", project_id, run["run_id"])


def test_cancel_result_purges_without_document(database):
    project_id = database
    run = sql_query_runs.BusinessDocumentSqlRunService.run(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-1", "selected_profile_id": None},
    )
    result = sql_query_runs.BusinessDocumentSqlRunService.cancel_result(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "cancel-1", "run_id": run["run_id"]},
    )
    assert result["rows_status"] == "PURGED"
    assert sql_query_runs.BusinessDocumentSqlQueryRun.get_by_id(run["run_id"]).rows == []
    assert sql_query_runs.BusinessDocumentJob.get_by_id(sql_query_runs.BusinessDocumentSqlQueryRun.get_by_id(run["run_id"]).job_id).status == "CANCELED"


def test_owner_result_quota_blocks_a_new_run(database, monkeypatch):
    monkeypatch.setattr(sql_query_runs, "_MAX_OWNER_ACTIVE_RESULTS", 0)
    with pytest.raises(ConflictError) as error:
        sql_query_runs.BusinessDocumentSqlRunService.run(
            "tenant-1",
            "author-1",
            database,
            {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-quota", "selected_profile_id": None},
        )
    assert error.value.code == "SQL_RESULT_OWNER_QUOTA"


def test_python_creates_derived_result_without_changing_sql_rows(database, monkeypatch):
    project_id = database
    source = sql_query_runs.BusinessDocumentSqlRunService.run(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-1", "selected_profile_id": None},
    )
    job = BusinessDocumentJobQueue.claim("sql-test")
    output = sql_query_runs.BusinessDocumentSqlRunService.execute_job(job, "sql-test", job.lease_token)
    sql_query_runs.BusinessDocumentSqlRunService.complete_job(job, "sql-test", job.lease_token, output)
    monkeypatch.setattr(
        sql_query_runs,
        "execute_result_python",
        lambda code, columns, rows: {
            "columns": ["doubled"],
            "rows": [[rows[0][0] * 2]],
        },
    )
    derived = sql_query_runs.BusinessDocumentSqlRunService.run_python(
        "tenant-1",
        "author-1",
        project_id,
        source["run_id"],
        {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "python-1", "code": "def main(columns, rows):\n    return {'columns': ['doubled'], 'rows': [[rows[0][0] * 2]]}"},
    )
    assert sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", project_id, derived["run_id"])["rows"] == [[84]]
    assert sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", project_id, source["run_id"])["rows"] == [[42]]
    with pytest.raises(ConflictError):
        sql_query_runs.BusinessDocumentSqlRunService.complete(
            "tenant-1",
            "author-1",
            project_id,
            {"schema_version": "1", "expected_state_version": 3, "idempotency_key": "complete-derived", "run_id": derived["run_id"]},
        )
    sql_query_runs.BusinessDocumentSqlRunService.cancel_result(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 3, "idempotency_key": "cancel-source", "run_id": source["run_id"]},
    )
    assert sql_query_runs.BusinessDocumentSqlQueryRun.get_by_id(derived["run_id"]).status == "PURGED"
    assert sql_query_runs.BusinessDocumentSqlQueryRun.get_by_id(derived["run_id"]).rows == []


def test_lookup_rejects_duplicate_target_keys_without_changing_source(database, monkeypatch):
    project_id = database
    source = sql_query_runs.BusinessDocumentSqlRunService.run(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-1", "selected_profile_id": None},
    )
    job = BusinessDocumentJobQueue.claim("sql-test")
    output = sql_query_runs.BusinessDocumentSqlRunService.execute_job(job, "sql-test", job.lease_token)
    sql_query_runs.BusinessDocumentSqlRunService.complete_job(job, "sql-test", job.lease_token, output)
    compilation, command = sql_query_runs._source(None)
    monkeypatch.setattr(
        sql_query_runs,
        "_source",
        lambda _: (
            compilation,
            {
                **command,
                "schema_snapshot": {},
                "accepted_schema": [{"entity_id": "lookup-table"}],
            },
        ),
    )
    columns = {name: SimpleNamespace(id=f"lookup-table.{name}", name=name) for name in ("id", "label")}
    table = SimpleNamespace(
        id="lookup-table",
        physical_relation="public.lookup_table",
        technical_name="lookup_table",
        schema_fingerprint="sha256:sample",
        column=lambda value: next((item for item in columns.values() if item.id == value), None),
    )
    monkeypatch.setattr(sql_query_runs, "parse_schema_snapshot", lambda _: SimpleNamespace(table=lambda _: table))
    monkeypatch.setattr(sql_query_runs, "guard_read_only_sql", lambda *args, **kwargs: {"status": "PASS"})
    monkeypatch.setattr(
        sql_query_runs,
        "execute_postgres",
        lambda *args, **kwargs: {
            "columns": ["lookup_key", "value_0"],
            "rows": [[42, "first"], [42, "second"]],
        },
    )
    lookup = {
        "schema_version": "1",
        "expected_state_version": 2,
        "idempotency_key": "lookup-1",
        "source_column": "value",
        "target_entity_id": "lookup-table",
        "target_key_column_id": "lookup-table.id",
        "target_value_column_ids": ["lookup-table.label"],
    }
    with pytest.raises(Exception, match="not unique"):
        sql_query_runs.BusinessDocumentSqlRunService.run_lookup("tenant-1", "author-1", project_id, source["run_id"], lookup)
    assert sql_query_runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", project_id, source["run_id"])["rows"] == [[42]]


def test_conclusion_requires_human_confirmation_and_survives_only_in_document(database, monkeypatch):
    project_id = database
    source = sql_query_runs.BusinessDocumentSqlRunService.run(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-1", "selected_profile_id": None},
    )
    job = BusinessDocumentJobQueue.claim("sql-test")
    output = sql_query_runs.BusinessDocumentSqlRunService.execute_job(job, "sql-test", job.lease_token)
    sql_query_runs.BusinessDocumentSqlRunService.complete_job(job, "sql-test", job.lease_token, output)
    monkeypatch.setattr(
        sql_query_runs,
        "propose_conclusion",
        lambda *args: {
            "text": "Значение 42.",
            "citations": [{"row_index": 0, "column": "value"}],
        },
    )
    draft = sql_query_runs.BusinessDocumentSqlRunService.propose_conclusion(
        "tenant-1",
        "author-1",
        project_id,
        source["run_id"],
        {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "draft-1"},
    )
    assert draft["text"] == "Значение 42."
    confirmed = sql_query_runs.BusinessDocumentSqlRunService.confirm_conclusion(
        "tenant-1",
        "author-1",
        project_id,
        source["run_id"],
        {"schema_version": "1", "expected_state_version": 3, "idempotency_key": "confirm-1", "proposal_id": draft["proposal_id"], "text": "Проверенное значение 42."},
    )
    assert confirmed["text"] == "Проверенное значение 42."
    completed = sql_query_runs.BusinessDocumentSqlRunService.complete(
        "tenant-1",
        "author-1",
        project_id,
        {"schema_version": "1", "expected_state_version": 4, "idempotency_key": "complete-1", "run_id": source["run_id"]},
    )
    assert completed["document"]["confirmed_conclusion"]["text"] == "Проверенное значение 42."
    assert "rows" not in completed["document"]
    assert (
        not sql_query_runs.BusinessDocumentSqlQueryArtifact.select()
        .where((sql_query_runs.BusinessDocumentSqlQueryArtifact.project_id == project_id) & (sql_query_runs.BusinessDocumentSqlQueryArtifact.kind == "CONCLUSION"))
        .exists()
    )
