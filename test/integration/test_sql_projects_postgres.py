"""Exact SQL arithmetic on a dedicated disposable PostgreSQL instance."""

from pathlib import Path
import sys
from types import ModuleType
import pytest

from test.asyncio_policy import event_loop_policy as event_loop_policy

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[2] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.sql_query_postgres import execute_postgres
from api.apps.business_documents.sql_query_revision_ai import propose_revision
from test.evals.sql_projects.corpus import SQL_ORACLES, query_parameters, result_errors
from test.evals.sql_projects.golden import BASE, PROPOSALS
from test.evals.sql_projects.database import disposable_database
from test.evals.sql_projects.discriminators import PACK_ID, SETUP_SQL, oracle_for
from test.evals.sql_projects.scoring import score_turn
from test.evals.sql_projects.golden import Turn
from test.evals.sql_projects.schema import accepted_requirements, accepted_schema
from business_documents.sql_query.project_compilation import build_project_compile_command
from business_documents.sql_query.query_specification import compile_query_payload
from business_documents.sql_query.exports import result_json_chunks
from business_documents.sql_query.result_validation import validate_result
import json


@pytest.fixture(scope="module")
def database():
    with disposable_database() as config:
        yield config


@pytest.fixture(scope="module")
def boundary_database():
    with disposable_database(extra_sql=SETUP_SQL) as config:
        yield config


def test_driver_types_and_download_preserve_postgres_precision(database):
    result = execute_postgres(
        database,
        "SELECT 9223372036854775807::bigint AS identifier, 12345678901234567890.00100::numeric AS amount, '2026-10-21 12:30:00.123456+00'::timestamptz AS moment, false AS present, NULL::text AS absent, ''::text AS empty",
        {},
        timeout_ms=5000,
        max_rows=1,
        max_result_bytes=1000,
    )
    assert result["column_types"] == [{"postgres_oid": value} for value in [20, 1700, 1184, 16, 25, 25]]
    checked = validate_result(result, expected_columns=["identifier", "amount", "moment", "present", "absent", "empty"], row_limit=1, max_result_bytes=1000)
    exported = json.loads(b"".join(result_json_chunks({}, checked["columns"], checked["rows"], checked["checks"]["column_types"])))
    assert exported["rows"] == [
        [
            {"type": "integer", "value": "9223372036854775807"},
            {"type": "decimal", "value": "12345678901234567890.00100"},
            {"type": "timestamp_with_timezone", "value": "2026-10-21 12:30:00.123456+00:00"},
            {"type": "boolean", "value": False},
            {"type": "null", "value": None},
            {"type": "string", "value": ""},
        ]
    ]


@pytest.mark.parametrize("oracle", SQL_ORACLES, ids=lambda case: case.id)
def test_exact_postgres_result(database, oracle):
    result = execute_postgres(database, oracle.sql, query_parameters(oracle.sql), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    assert result_errors(result, oracle) == []


@pytest.mark.parametrize("case_id", ["payments_exists", "payments_absent", "group_regions"])
@pytest.mark.parametrize("dataset", ["database", "boundary_database"])
def test_equivalent_exists_projection_and_null_order_execute_exactly(request, dataset, case_id):
    expected = next(item[3] for item in PROPOSALS if item[0] == case_id)
    sql = expected.oracle.sql
    if case_id == "group_regions":
        sql = sql.replace("c.region NULLS FIRST", "c.region IS NULL DESC,c.region ASC")
    else:
        sql = sql.replace("SELECT p.id FROM public.payments p", "SELECT 1 FROM public.payments p")
    assert sql != expected.oracle.sql
    compiled = compile_query_payload(
        build_project_compile_command(
            accepted_requirements(),
            accepted_schema(),
            {
                "mode": "manual",
                "sql": sql,
                "parameters": expected.bindings(),
                "confirmed_alignment": True,
            },
        )
    )
    assert compiled["status"] == "READY" and compiled["guard"]["status"] == "PASS"
    config = request.getfixturevalue(dataset)
    result = execute_postgres(config, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    oracle = oracle_for(expected.oracle) if dataset == "boundary_database" else expected.oracle
    assert result_errors(result, oracle) == []


def test_postgres_read_only_transaction_rejects_writes(database):
    from business_documents.application.errors import BusinessDocumentError

    with pytest.raises(BusinessDocumentError):
        execute_postgres(database, "DELETE FROM public.orders", {}, timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    oracle = SQL_ORACLES[0]
    assert result_errors(execute_postgres(database, oracle.sql, query_parameters(oracle.sql), timeout_ms=5000, max_rows=100, max_result_bytes=100000), oracle) == []


def test_connection_probe_uses_real_read_only_transaction_and_closes_it(database, monkeypatch):
    import psycopg2
    from api.apps.business_documents import sql_query_postgres as adapter

    connect = psycopg2.connect
    connections, observed = [], []

    class AuditedConnection(psycopg2.extensions.connection):
        def rollback(self):
            with self.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                readonly = cursor.fetchone()[0]
                cursor.execute("SHOW statement_timeout")
                observed.append((readonly, cursor.fetchone()[0]))
            return super().rollback()

    def audited(**options):
        connection = connect(**options, connection_factory=AuditedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(adapter.psycopg2, "connect", audited)
    assert adapter.check_postgres_connection(database, timeout_ms=250) >= 0
    assert observed == [("on", "250ms")]
    assert len(connections) == 1 and connections[0].closed


def test_connection_health_does_not_require_a_valid_user_query(database):
    from api.apps.business_documents.sql_query_postgres import check_postgres_connection, explain_postgres
    from business_documents.application.errors import BusinessDocumentError

    with pytest.raises(BusinessDocumentError) as caught:
        explain_postgres(database, "SELECT missing_column FROM public.missing_qa_table LIMIT :row_limit", {"row_limit": 1}, timeout_ms=500)
    assert caught.value.code == "SQL_PHYSICAL_SCHEMA_INVALID"
    assert check_postgres_connection(database, timeout_ms=500) >= 0


def test_real_connection_failures_have_safe_auth_and_network_codes(database):
    from copy import deepcopy
    import socket
    from uuid import uuid4
    from api.apps.business_documents.sql_query_postgres import check_postgres_connection
    from business_documents.application.errors import BusinessDocumentError

    invalid_role = deepcopy(database)
    invalid_role["credentials"]["username"] = "qa_absent_" + uuid4().hex
    with pytest.raises(BusinessDocumentError) as auth:
        check_postgres_connection(invalid_role, timeout_ms=500)
    assert auth.value.code == "SQL_CONNECTION_AUTH_FAILED"
    assert invalid_role["credentials"]["username"] not in str(auth.value)
    with socket.socket() as unused_port:
        unused_port.bind(("127.0.0.1", 0))
        unreachable = {**database, "host": "127.0.0.1", "port": unused_port.getsockname()[1]}
        with pytest.raises(BusinessDocumentError) as network:
            check_postgres_connection(unreachable, timeout_ms=500)
        assert network.value.code == "SQL_CONNECTION_NETWORK_FAILED"


def test_cancel_closes_real_postgres_connection_and_does_not_become_timeout(database, monkeypatch):
    from time import monotonic
    from api.apps.business_documents import sql_query_postgres as adapter
    from business_documents.application.errors import BusinessDocumentError

    connect = adapter._connect
    opened = []

    def observe(config):
        connection = connect(config)
        opened.append(connection)
        return connection

    monkeypatch.setattr(adapter, "_connect", observe)
    started = monotonic()
    with pytest.raises(BusinessDocumentError) as caught:
        adapter.execute_postgres(database, "SELECT pg_sleep(:delay)", {"delay": 2}, timeout_ms=5000, max_rows=1, max_result_bytes=100, should_cancel=lambda: monotonic() - started > 0.1)
    assert caught.value.code == "SQL_CANCELED" and caught.value.status == 409
    assert len(opened) == 1 and opened[0].closed


def test_probe_timeout_from_real_postgres_is_typed_and_closes_connection(database, monkeypatch):
    import psycopg2
    from api.apps.business_documents import sql_query_postgres as adapter
    from business_documents.application.errors import BusinessDocumentError

    connect = psycopg2.connect
    opened = []

    class DelayedCursor(psycopg2.extensions.cursor):
        def execute(self, sql, parameters=None):
            return super().execute("SELECT pg_sleep(1)" if sql == "SELECT 1" else sql, parameters)

    def delayed(**options):
        connection = connect(**options, cursor_factory=DelayedCursor)
        opened.append(connection)
        return connection

    monkeypatch.setattr(adapter.psycopg2, "connect", delayed)
    with pytest.raises(BusinessDocumentError) as caught:
        adapter.check_postgres_connection(database, timeout_ms=20)
    assert caught.value.code == "SQL_CONNECTION_TIMEOUT" and caught.value.status == 504
    assert len(opened) == 1 and opened[0].closed


def test_project_connection_migration_preserves_legacy_rows_rolls_back_and_repeats(monkeypatch):
    from peewee import PostgresqlDatabase
    from playhouse.migrate import PostgresqlMigrator
    from api.db.db_models import BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun, _migrate_sql_project_connection

    with disposable_database() as config:
        db = PostgresqlDatabase(config["database"], host=config["host"], port=config["port"], user=config["credentials"]["username"], password=config["credentials"]["password"])
        with db.bind_ctx([BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun], bind_refs=False, bind_backrefs=False):
            db.connect()
            try:
                db.create_tables([BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun])
                BusinessDocumentSqlQueryProject.create(id="legacy", tenant_id="qa", owner_id="qa", title="Legacy query", source_request="Preserve this request")
                BusinessDocumentSqlQueryRun.create(
                    id="legacy-run",
                    tenant_id="qa",
                    project_id="legacy",
                    compilation_id="compilation",
                    profile_id="old-profile",
                    profile_version=3,
                    status="READY",
                    rows=[[42]],
                    columns=["value"],
                    row_count=1,
                )
                db.execute_sql("ALTER TABLE business_document_sql_query_project DROP COLUMN execution_profile_id")
                db.execute_sql("ALTER TABLE business_document_sql_query_run DROP COLUMN profile_name")
                original = db.execute_sql

                def interrupted(statement, *args, **kwargs):
                    if 'ALTER TABLE "business_document_sql_query_run"' in statement:
                        raise RuntimeError("Injected migration interruption")
                    return original(statement, *args, **kwargs)

                monkeypatch.setattr(db, "execute_sql", interrupted)
                with pytest.raises(RuntimeError, match="Injected"):
                    _migrate_sql_project_connection(PostgresqlMigrator(db))
                monkeypatch.setattr(db, "execute_sql", original)
                assert (
                    db.execute_sql(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='business_document_sql_query_project' AND column_name='execution_profile_id'"
                    ).fetchone()[0]
                    == 0
                )
                _migrate_sql_project_connection(PostgresqlMigrator(db))
                _migrate_sql_project_connection(PostgresqlMigrator(db))
                project = BusinessDocumentSqlQueryProject.get_by_id("legacy")
                run = BusinessDocumentSqlQueryRun.get_by_id("legacy-run")
                assert project.execution_profile_id is None and project.source_request == "Preserve this request"
                assert run.profile_name is None and run.rows == [[42]] and run.profile_id == "old-profile" and run.profile_version == 3
            finally:
                db.close()


def test_project_registry_migration_rolls_back_repeats_and_preserves_legacy_rows(monkeypatch):
    from peewee import PostgresqlDatabase
    from playhouse.migrate import PostgresqlMigrator
    from api.db.db_models import BusinessDocumentSqlQueryProject as Project, _migrate_sql_project_registry

    with disposable_database() as config:
        db = PostgresqlDatabase(config["database"], host=config["host"], port=config["port"], user=config["credentials"]["username"], password=config["credentials"]["password"])
        with db.bind_ctx([Project], bind_refs=False, bind_backrefs=False):
            db.connect()
            try:
                db.create_tables([Project])
                Project.create(id="legacy", tenant_id="qa", owner_id="qa", title="Продажи 50%_ДНЯ", source_request="Keep accepted decisions", execution_profile_id="saved-profile")
                db.execute_sql("ALTER TABLE business_document_sql_query_project DROP COLUMN archived_at, DROP COLUMN copy_origin")
                original = db.execute_sql

                def interrupted(statement, *args, **kwargs):
                    if 'ADD COLUMN IF NOT EXISTS "copy_origin"' in statement:
                        raise RuntimeError("Injected registry migration interruption")
                    return original(statement, *args, **kwargs)

                monkeypatch.setattr(db, "execute_sql", interrupted)
                with pytest.raises(RuntimeError, match="Injected registry"):
                    _migrate_sql_project_registry(PostgresqlMigrator(db))
                monkeypatch.setattr(db, "execute_sql", original)
                assert db.execute_sql("SELECT count(*) FROM information_schema.columns WHERE table_name='business_document_sql_query_project' AND column_name='archived_at'").fetchone()[0] == 0
                _migrate_sql_project_registry(PostgresqlMigrator(db))
                _migrate_sql_project_registry(PostgresqlMigrator(db))
                project = Project.get_by_id("legacy")
                assert project.archived_at is None and not project.copy_origin
                assert db.execute_sql("SELECT archived_at IS NULL, copy_origin IS NULL FROM business_document_sql_query_project WHERE id='legacy'").fetchone() == (True, True)
                assert project.execution_profile_id == "saved-profile" and project.state_version == 1
                # Exercise the same escaped case-insensitive predicate on PostgreSQL.
                from peewee import NodeList, SQL, Value, fn

                found = Project.select().where(fn.LOWER(Project.title).ilike(NodeList((Value("%50\\%\\_дня%"), SQL("ESCAPE '\\'")))))
                assert [item.id for item in found] == ["legacy"]
            finally:
                db.close()


@pytest.mark.parametrize("expected", [item[3] for item in PROPOSALS], ids=lambda case: case.oracle.id)
def test_authored_product_query_oracle_on_real_postgres(database, expected):
    result = execute_postgres(database, expected.oracle.sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    assert result_errors(result, expected.oracle) == [], {"case": expected.oracle.id, "actual": result, "expected": expected.oracle.rows}


@pytest.mark.parametrize("expected", [item[3] for item in PROPOSALS], ids=lambda case: case.oracle.id)
def test_independent_threshold_pack_has_correct_authored_rows(boundary_database, expected):
    oracle = oracle_for(expected.oracle)
    result = execute_postgres(boundary_database, oracle.sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    assert result_errors(result, oracle) == [], {"case": oracle.id, "actual": result, "expected": oracle.rows}


@pytest.mark.parametrize("damage", ["strict_threshold", "unrequested_filter", "missing_order"])
def test_wrong_sql_that_matches_base_fixture_still_fails_the_full_scorer(database, boundary_database, damage):
    expected = PROPOSALS[0][3]
    if damage == "strict_threshold":
        sql = expected.oracle.sql.replace(">=:minimum", ">:minimum")
    elif damage == "missing_order":
        sql = expected.oracle.sql.replace("ORDER BY o.id", "")
    else:
        sql = expected.oracle.sql.replace("ORDER BY", "AND o.amount<=o.id ORDER BY")
    candidate = {"mode": "manual", "sql": sql, "parameters": expected.bindings(), "confirmed_alignment": True}
    compiled = compile_query_payload(build_project_compile_command(accepted_requirements(), accepted_schema(), candidate))
    assert compiled["status"] == "READY" and compiled["guard"]["status"] == "PASS"
    base_result = execute_postgres(database, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    assert result_errors(base_result, expected.oracle) == []
    boundary_result = execute_postgres(boundary_database, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    response = {"proposal": {"sql": sql, "parameters": expected.bindings(), "summary": "Damaged control", "checks": {"status": "PASS"}}}
    assert score_turn(Turn("Не меньше50", "PROPOSAL", expected), response, result=base_result, discriminator_results={PACK_ID: boundary_result})


@pytest.mark.parametrize("case_id", ["october", "empty_period"])
def test_retained_status_filter_cannot_be_lost_in_another_or_empty_period(database, boundary_database, case_id):
    expected = next(item[3] for item in PROPOSALS if item[0] == case_id)
    sql = expected.oracle.sql.replace("o.status = :paid", "(o.status=:paid OR o.status IS NOT NULL)")
    assert sql != expected.oracle.sql
    candidate = {"mode": "manual", "sql": sql, "parameters": expected.bindings(), "confirmed_alignment": True}
    compiled = compile_query_payload(build_project_compile_command(accepted_requirements(), accepted_schema(), candidate))
    assert compiled["status"] == "READY" and compiled["guard"]["status"] == "PASS"
    base_result = execute_postgres(database, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    assert result_errors(base_result, expected.oracle) == []
    boundary_result = execute_postgres(boundary_database, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    response = {"proposal": {"sql": sql, "parameters": expected.bindings(), "summary": "Damaged preserved filter", "checks": {"status": "PASS"}}}
    assert score_turn(Turn("Сохрани статус", "PROPOSAL", expected), response, result=base_result, discriminator_results={PACK_ID: boundary_result})


@pytest.mark.parametrize("order", ["id", "ORDER_ID", "1"])
def test_equivalent_postgres_ordering_is_accepted_with_real_guard_and_both_datasets(database, boundary_database, order):
    expected = PROPOSALS[0][3]
    sql = expected.oracle.sql.replace("ORDER BY o.id", f"ORDER BY {order}")
    candidate = {"mode": "manual", "sql": sql, "parameters": expected.bindings(), "confirmed_alignment": True}
    compiled = compile_query_payload(build_project_compile_command(accepted_requirements(), accepted_schema(), candidate))
    assert compiled["status"] == "READY" and compiled["guard"]["status"] == "PASS"
    base_result = execute_postgres(database, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    boundary_result = execute_postgres(boundary_database, sql, expected.values(), timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    response = {"proposal": {"sql": sql, "parameters": expected.bindings(), "summary": "Equivalent order control", "checks": {"status": "PASS"}}}
    assert score_turn(Turn("Сохрани порядок", "PROPOSAL", expected), response, result=base_result, discriminator_results={PACK_ID: boundary_result}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("item", PROPOSALS, ids=lambda item: item[0])
async def test_controlled_revision_response_executes_and_scores_on_both_real_datasets(database, boundary_database, item):
    """Actual parser/guard/output execution; the controlled model is not inference."""
    _, _, text, expected = item

    class ModelControl:
        async def async_generate(self, tenant_id, system, payload):
            return {"sql": expected.oracle.sql, "parameters": expected.bindings(), "summary": text}

    response = await propose_revision(
        "synthetic-control",
        {
            "target": "QUERY",
            "locale": "ru",
            "sql": BASE.oracle.sql,
            "parameters": BASE.bindings(),
            "requirements": accepted_requirements(),
            "schema": accepted_schema(),
            "comments": [{"text": text}],
        },
        llm=ModelControl(),
    )
    candidate = response["proposal"]
    assert candidate["checks"]["status"] == "PASS"
    values = {item["name"]: item["value"] for item in candidate["parameters"]}
    base_result = execute_postgres(database, candidate["sql"], values, timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    boundary_result = execute_postgres(boundary_database, candidate["sql"], values, timeout_ms=5000, max_rows=100, max_result_bytes=100000)
    assert score_turn(Turn(text, "PROPOSAL", expected), response, result=base_result, discriminator_results={PACK_ID: boundary_result}) == []
