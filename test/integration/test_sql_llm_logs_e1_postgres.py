"""Real driver, column grants, count arithmetic and cancel/lost-ACK transport."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import psycopg2
from peewee import PostgresqlDatabase
from playhouse.migrate import PostgresqlMigrator
from uuid import uuid4
import pytest

from test.unit_test.api.apps.business_documents.test_sql_llm_logs_e1 import pilot as base_pilot, create, queue, TENANT
from test.evals.sql_projects.database import connection_parameters
from api.apps.business_documents import sql_query_postgres as postgres, sql_query_runs as runs
from api.db.db_models import BusinessDocumentSqlQueryRun, User
from business_documents.application.errors import BusinessDocumentError
from api.db.db_models import _migrate_sql_project_execution

EXECUTE, CHECK, EXPLAIN = postgres.execute_postgres, postgres.check_source_access, postgres.explain_postgres


@pytest.fixture
def pilot(monkeypatch, pilot_config, pilot_database):
    yield from base_pilot.__wrapped__(monkeypatch, pilot_config, pilot_database)


@pytest.fixture(scope="module")
def source_db():
    params = connection_parameters()
    with closing(psycopg2.connect(**params)) as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE ROLE llm_reader LOGIN PASSWORD 'qa-only'")
            cursor.execute(
                "CREATE TABLE public.llm_requests_log(log_id bigint PRIMARY KEY, model_name varchar(100), request_timestamp_start timestamptz NOT NULL, is_success boolean NOT NULL, prompt text)"
            )
            cursor.execute(
                "INSERT INTO public.llm_requests_log VALUES (1,'alpha','2026-07-31 23:59:59+00',true,'restricted'),(2,'alpha','2026-08-01 00:00+00',true,'restricted'),(3,'alpha','2026-08-02 00:00+00',false,'restricted'),(4,'beta','2026-08-31 23:59:59+00',true,'restricted'),(5,'beta','2026-09-01 00:00+00',true,'restricted'),(6,NULL,'2026-08-15 00:00+00',true,'restricted')"
            )
            cursor.execute("GRANT USAGE ON SCHEMA public TO llm_reader")
            cursor.execute("GRANT SELECT(log_id,model_name,request_timestamp_start) ON public.llm_requests_log TO llm_reader")
    yield params


@pytest.fixture
def pilot_config(source_db):
    return {"host": source_db["hostaddr"], "port": int(source_db["port"]), "database": source_db["dbname"], "credentials": {"username": "llm_reader", "password": "qa-only"}}


@pytest.fixture
def pilot_database(source_db):
    name = "e1_metadata_" + uuid4().hex
    with closing(psycopg2.connect(**source_db)) as control:
        control.autocommit = True
        with control.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{name}"')
        database = PostgresqlDatabase(source_db["dbname"], host=source_db["hostaddr"], port=int(source_db["port"]), user=source_db["user"], options=f"-c search_path={name},public")
        try:
            with database.bind_ctx((User,), bind_refs=False, bind_backrefs=False), database.connection_context():
                User.create_table()
                User.create(id=TENANT, nickname="QA", email="qa@example.test", is_active="1", status="1")
                yield database
        finally:
            database.close()
            with control.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA "{name}" CASCADE')


def real_adapter(monkeypatch):
    monkeypatch.setattr(runs, "execute_postgres", EXECUTE)
    monkeypatch.setattr(runs, "explain_postgres", EXPLAIN)
    monkeypatch.setattr(postgres, "check_source_access", CHECK)


def test_count_pipeline_uses_real_postgres_and_an_independent_oracle(pilot, source_db, monkeypatch):
    service, _ = pilot
    real_adapter(monkeypatch)
    session = create(service)
    job, _ = queue(service, session)
    output = runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.complete_job(job, "pilot-worker", job.lease_token, output)
    actual = service.result(session["id"], job.payload["run_id"])
    with closing(psycopg2.connect(**source_db)) as connection:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT model_name,COUNT(log_id) FROM public.llm_requests_log WHERE request_timestamp_start >= %s AND request_timestamp_start < %s GROUP BY model_name ORDER BY model_name",
                ("2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"),
            )
            expected = [list(row) for row in cursor.fetchall()]
    assert expected == [["alpha", 2], ["beta", 1], [None, 1]]
    assert [row["values"] for row in actual["rows"]] == expected
    assert actual["source_completeness"] == "UNKNOWN"
    saved = BusinessDocumentSqlQueryRun.get_by_id(job.payload["run_id"])
    assert saved.execution["phase"] == "SUCCEEDED" and saved.execution["backend"]["pid"] > 0


def test_duplicate_worker_delivery_dispatches_one_select(pilot, pilot_database, monkeypatch):
    service, _ = pilot
    real_adapter(monkeypatch)
    session = create(service)
    job, _ = queue(service, session)
    gate = threading.Barrier(2)
    dispatched = []

    def execute(*args, **kwargs):
        dispatched.append(threading.get_ident())
        return EXECUTE(*args, **kwargs)

    monkeypatch.setattr(runs, "execute_postgres", execute)

    def deliver():
        with pilot_database.connection_context():
            gate.wait(5)
            try:
                return runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
            except BusinessDocumentError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: deliver(), range(2)))
    success = [item for item in outcomes if isinstance(item, dict)]
    assert len(dispatched) == len(success) == 1
    assert [item for item in outcomes if isinstance(item, str)] == ["SQL_RUN_STATE_CHANGED"]
    runs.BusinessDocumentSqlRunService.complete_job(job, "pilot-worker", job.lease_token, success[0])
    assert service.result(session["id"], job.payload["run_id"])["row_count"] == 3


def test_empty_period_with_offsets_returns_no_groups(pilot, monkeypatch):
    service, _ = pilot
    real_adapter(monkeypatch)
    session = service.create_llm_count({"client_request_id": "empty", "from": "2026-09-02T03:00:00+03:00", "to": "2026-09-03T03:00:00+03:00"})
    assert session["project"]["compilation"]["result"]["parameters"]["period_from"] == "2026-09-02T00:00:00+00:00"
    job, _ = queue(service, session)
    output = runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.complete_job(job, "pilot-worker", job.lease_token, output)
    result = service.result(session["id"], job.payload["run_id"])
    assert result["rows"] == [] and result["row_count"] == 0


@pytest.mark.parametrize("change", ["revoke", "widen"])
def test_actual_grant_changes_block_history(pilot, source_db, monkeypatch, change):
    service, _ = pilot
    real_adapter(monkeypatch)
    session = create(service)
    job, _ = queue(service, session)
    output = runs.BusinessDocumentSqlRunService.execute_job(job, "pilot-worker", job.lease_token)
    runs.BusinessDocumentSqlRunService.complete_job(job, "pilot-worker", job.lease_token, output)
    with closing(psycopg2.connect(**source_db)) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("REVOKE SELECT(model_name) ON public.llm_requests_log FROM llm_reader" if change == "revoke" else "GRANT SELECT ON public.llm_requests_log TO llm_reader")
        try:
            with pytest.raises(BusinessDocumentError) as failure:
                service.result(session["id"], job.payload["run_id"])
            assert failure.value.code == "SQL_SOURCE_ACCESS_DENIED"
        finally:
            with admin.cursor() as cursor:
                cursor.execute("REVOKE SELECT ON public.llm_requests_log FROM llm_reader")
                cursor.execute("GRANT SELECT(log_id,model_name,request_timestamp_start) ON public.llm_requests_log TO llm_reader")


@pytest.mark.parametrize("interrupt", ["cancel", "disconnect"])
def test_actual_running_query_has_distinct_cancellation_and_unknown_outcomes(source_db, interrupt):
    config = {"host": source_db["hostaddr"], "port": int(source_db["port"]), "database": source_db["dbname"], "credentials": {"username": source_db["user"], "password": "qa-only"}}
    dispatched, stop = threading.Event(), threading.Event()
    identity, outcomes = {}, []

    def dispatch(value):
        identity.update(value)
        dispatched.set()

    def query():
        try:
            EXECUTE(
                config,
                "SELECT pg_sleep(:delay) AS waiting LIMIT :row_limit",
                {"delay": 10, "row_limit": 1},
                timeout_ms=15000,
                max_rows=1,
                max_result_bytes=1000,
                should_cancel=stop.is_set,
                on_dispatch=dispatch,
            )
        except BusinessDocumentError as error:
            outcomes.append(error.code)

    thread = threading.Thread(target=query)
    thread.start()
    try:
        assert dispatched.wait(5)
        with closing(psycopg2.connect(**source_db)) as admin:
            admin.autocommit = True
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with admin.cursor() as cursor:
                    cursor.execute("SELECT state,query FROM pg_stat_activity WHERE pid=%s", (identity["pid"],))
                    row = cursor.fetchone()
                if row and row[0] == "active" and "pg_sleep" in row[1]:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("SQL was not observed running")
            if interrupt == "cancel":
                stop.set()
            else:
                with admin.cursor() as cursor:
                    cursor.execute("SELECT pg_terminate_backend(%s)", (identity["pid"],))
        thread.join(5)
        assert not thread.is_alive()
        assert outcomes == ["SQL_CANCELED" if interrupt == "cancel" else "SQL_EXECUTION_UNKNOWN"]
        observation = postgres.observe_postgres(config, identity)
        assert observation["backend_present"] is False
    finally:
        stop.set()
        thread.join(16)


def test_execution_journal_migration_preserves_legacy_rows_and_inserts(source_db):
    name = "e1_migration_" + uuid4().hex
    db = PostgresqlDatabase(source_db["dbname"], host=source_db["hostaddr"], port=int(source_db["port"]), user=source_db["user"])
    try:
        db.execute_sql(f'CREATE SCHEMA "{name}"')
        db.execute_sql(f'SET search_path TO "{name}"')
        db.execute_sql("CREATE TABLE business_document_sql_query_run(id text PRIMARY KEY)")
        db.execute_sql("INSERT INTO business_document_sql_query_run(id) VALUES ('before')")
        _migrate_sql_project_execution(PostgresqlMigrator(db))
        _migrate_sql_project_execution(PostgresqlMigrator(db))
        db.execute_sql("INSERT INTO business_document_sql_query_run(id) VALUES ('legacy-writer')")
        assert db.execute_sql("SELECT id,execution FROM business_document_sql_query_run ORDER BY id").fetchall() == [("before", "{}"), ("legacy-writer", "{}")]
        with db.bind_ctx((BusinessDocumentSqlQueryRun,), bind_refs=False, bind_backrefs=False):
            rows = list(BusinessDocumentSqlQueryRun.select(BusinessDocumentSqlQueryRun.id, BusinessDocumentSqlQueryRun.execution).order_by(BusinessDocumentSqlQueryRun.id).dicts())
            assert rows == [{"id": "before", "execution": {}}, {"id": "legacy-writer", "execution": {}}]
    finally:
        db.execute_sql("SET search_path TO public")
        db.execute_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        db.close()
