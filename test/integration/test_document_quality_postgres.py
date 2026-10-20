"""Owned quality migration and stale-worker CAS on disposable PostgreSQL only.

No runtime tenant, model, MinIO, shared schema or application records are used.
Each test creates one random schema and independent pooled worker connections.
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from peewee import DataError
from playhouse.migrate import PostgresqlMigrator

from api.db.db_models import (
    BusinessDocumentQualityCampaign as Campaign,
)
from api.db.db_models import (
    BusinessDocumentQualityRun as Run,
)
from api.db.db_models import (
    RetryingPooledPostgresqlDatabase,
    TechnicalSpecification,
    TechnicalSpecificationRevision,
    _migrate_document_quality_types,
)
from api.db.db_models import (
    TechnicalSpecificationAlignmentObservation as Observation,
)

ADMIN = Path(__file__).resolve().parents[2] / "admin/server"
if str(ADMIN) not in sys.path:
    sys.path.insert(0, str(ADMIN))
import document_quality_runs as runs
import technical_specification_operations as operations

MODELS = (Campaign, Run)
TABLES = ("business_document_quality_campaign", "business_document_quality_run")
NOW = 60_000_000
REVISION = "a" * 40


@pytest.fixture
def postgres_alignment(postgres_quality):
    from api.technical_specifications.storage import SpecificationRepository

    database = postgres_quality.database
    models = (TechnicalSpecification, TechnicalSpecificationRevision, Observation)
    with database.bind_ctx(models, bind_refs=False, bind_backrefs=False):
        database.create_tables(models)
        database.create_tables(models)  # Repeat startup must preserve existing observations.
        repository = SpecificationRepository(workspace_access=lambda *_: {"tenant_id": "synthetic", "owner_id": "author"})
        yield SimpleNamespace(database=database, repository=repository)


def test_alignment_projection_and_observation_rollback_together_then_expire_once(postgres_alignment, monkeypatch):
    from api.technical_specifications.domain import SpecificationError, empty_sections

    pg = postgres_alignment
    document = pg.repository.create("author", "workspace", "Synthetic TZ", empty_sections())
    accepted = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    item = {"attempt": "a" * 32, "id": "request", "created_at": accepted, "status": "PROCESSING", "budget": {"calls": 1, "spent_tokens": 0, "reserved_tokens": 30, "elapsed_ms": 10}}
    original = pg.database.execute_sql

    def fail_observation(sql, params=None, commit=True):
        if sql.startswith('INSERT INTO "technical_specification_alignment_observation"'):
            return original("SELECT 1 / 0", None, commit)
        return original(sql, params, commit)

    def accept(value):
        value["alignment"] = [deepcopy(item)]

    with monkeypatch.context() as injection:
        injection.setattr(pg.database, "execute_sql", fail_observation)
        with pytest.raises(DataError):
            pg.repository.change("author", "workspace", document["id"], document["version"], accept, "alignment")
    assert TechnicalSpecification.get_by_id(document["id"]).version == document["version"]
    assert Observation.select().count() == 0
    saved = pg.repository.change("author", "workspace", document["id"], document["version"], accept, "alignment")
    assert Observation.get().reserved_tokens == 30 and not Observation.get().usage_known
    recovered = pg.repository.alignment_document("author", "workspace", document["id"])
    assert recovered["alignment"][0]["status"] == "INCOMPLETE"
    observed = Observation.get()
    assert observed.error_code == "EXECUTION_LEASE_EXPIRED" and observed.duration_ms is None
    assert observed.reserved_tokens == 30 and not observed.usage_known
    with pytest.raises(SpecificationError):
        pg.repository.change("author", "workspace", document["id"], saved["version"], accept, "alignment")
    assert Observation.select().count() == 1 and Observation.get().state == "INCOMPLETE"


def test_alignment_analytics_repeatable_snapshot_survives_concurrent_completion(postgres_alignment, monkeypatch):
    database = postgres_alignment.database
    now = 1_800_000_000_000
    monkeypatch.setattr(operations, "current_timestamp", lambda: now)
    Observation.create(id="a" * 32, accepted_at_ms=now - 1000, state="PROCESSING", execution_status="UNKNOWN", spent_tokens=0, reserved_tokens=30, usage_known=False)
    selected, committed = Event(), Event()
    original = database.execute_sql
    isolation = []

    def after_snapshot(sql, params=None, commit=True):
        cursor = original(sql, params, commit)
        if sql.startswith('SELECT COUNT("t1"."id") AS "total"'):
            isolation.append(original("SHOW transaction_isolation").fetchone()[0])
            selected.set()
            assert committed.wait(10)
        return cursor

    def finish():
        assert selected.wait(10)
        with database.connection_context():
            Observation.update(state="FINISHED", execution_status="COMPLETE", report_complete=True, duration_ms=200, model_duration_ms=100, spent_tokens=15, reserved_tokens=0, usage_known=True).where(
                Observation.id == "a" * 32
            ).execute()
        committed.set()

    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(finish)
        with monkeypatch.context() as injection:
            injection.setattr(database, "execute_sql", after_snapshot)
            result = operations.alignment_operations_dashboard(days=1)
        future.result(timeout=20)
    assert isolation == ["repeatable read"]
    assert result["counts"]["processing"] == 1 and result["terminal_attempts"] == 0
    assert result["total_tokens"] is None and result["reserved_tokens"] == 30
    assert result["durations"]["check"]["measured"] == 0
    completed = operations.alignment_operations_dashboard(days=1)
    assert completed["counts"]["processing"] == 0 and completed["total_tokens"] == 15
    assert completed["durations"]["check"] == {"measured": 1, "mean_ms": 200.0, "p95_ms": 200}


@pytest.fixture
def postgres_quality(monkeypatch, tmp_path):
    if os.environ.get("DOCUMENT_QUALITY_TEST_DISPOSABLE") != "1":
        pytest.skip("Set DOCUMENT_QUALITY_TEST_DISPOSABLE=1 for a disposable loopback PostgreSQL")
    dsn = os.environ.get("DOCUMENT_QUALITY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.fail("Missing DOCUMENT_QUALITY_TEST_POSTGRES_DSN")
    address = urlsplit(dsn)
    if address.scheme not in {"postgres", "postgresql"} or address.hostname not in {"127.0.0.1", "localhost"} or not address.path.lstrip("/") or address.query or address.fragment:
        pytest.fail("Only a loopback PostgreSQL DSN without query overrides is supported")
    schema = "document_quality_" + uuid4().hex
    database = RetryingPooledPostgresqlDatabase(
        unquote(address.path.lstrip("/")),
        host=address.hostname,
        port=address.port or 5432,
        user=unquote(address.username) if address.username else None,
        password=unquote(address.password) if address.password else None,
        options=f"-csearch_path={schema} -cstatement_timeout=15000 -clock_timeout=10000",
        max_connections=8,
        max_retries=1,
        retry_delay=0,
    )
    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr(runs, "_configured_tenant", lambda: "synthetic-qa")
    monkeypatch.setattr(runs, "_source_revision", lambda: REVISION)
    monkeypatch.setattr(runs, "_source_fingerprint", lambda: "f" * 64)
    monkeypatch.setattr(runs, "current_timestamp", lambda: NOW)
    monkeypatch.setattr(runs, "quality_model_catalog", lambda _: {"models": [{"name": "synthetic-model", "digest": "c" * 64, "aliases": ["synthetic-model"]}], "errors": []})
    monkeypatch.setattr(runs, "_run_campaign_baseline", lambda _: pytest.fail("Unexpected baseline execution"))
    monkeypatch.setattr(runs, "_execute", lambda _: pytest.fail("Unexpected model execution"))
    database.connect()
    database.execute_sql(f'CREATE SCHEMA "{schema}"')
    try:
        with database.bind_ctx(MODELS, bind_refs=False, bind_backrefs=False):
            database.create_tables(MODELS)
            yield SimpleNamespace(database=database, schema=schema, migrator=PostgresqlMigrator(database))
    finally:
        database.execute_sql(f'DROP SCHEMA "{schema}" CASCADE')
        assert database.execute_sql("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone() is None
        database.close_all()


def _campaign(identifier, *, product="business_requirements", status="COMPLETE", baseline="PASS"):
    return Campaign.create(
        id=identifier,
        document_type=product,
        schedule_key="nightly:" + identifier,
        status=status,
        baseline_status=baseline,
        source_revision=REVISION,
        models={"models": [{"name": "synthetic-model", "digest": "c" * 64, "aliases": ["synthetic-model"]}], "errors": []},
        baseline_report={"literal": "Сохранённый отчёт БТ", "counter": 3},
        started_at=1,
        create_time=1,
        update_time=1,
    )


def _run(identifier, *, product="business_requirements", campaign=None, status="PASS", started=1):
    return Run.create(
        id=identifier,
        document_type=product,
        schedule_key="manual:" + identifier,
        trigger="MANUAL",
        campaign_id=campaign,
        status=status,
        started_at=started,
        source_revision=REVISION,
        report={"status": "PASS", "literal": "Исторический результат\nБез изменений"},
        create_time=1,
        update_time=1,
    )


def _drop_product_columns(database):
    for table in TABLES:
        database.execute_sql(f'ALTER TABLE "{table}" DROP COLUMN "document_type"')


def _snapshot(database):
    return {
        "campaign": database.execute_sql("SELECT id, schedule_key, status, models, baseline_report, source_revision FROM business_document_quality_campaign ORDER BY id").fetchall(),
        "run": database.execute_sql("SELECT id, schedule_key, status, report, source_revision FROM business_document_quality_run ORDER BY id").fetchall(),
    }


def _assert_column_contract(pg):
    for table in TABLES:
        columns = {column.name: column for column in pg.database.get_columns(table, schema=pg.schema)}
        assert columns["document_type"].null is False
        assert columns["document_type"].default is None  # Current writers use ORM defaults; no SQL DEFAULT is added.
        indexes = pg.database.get_indexes(table, schema=pg.schema)
        assert any(index.columns == ["document_type"] and not index.unique for index in indexes)


def test_legacy_quality_rows_backfill_and_repeated_upgrade_preserves_products(postgres_quality):
    pg = postgres_quality
    _campaign("legacy-campaign")
    _run("legacy-run", campaign="legacy-campaign")
    before = _snapshot(pg.database)
    _drop_product_columns(pg.database)
    _migrate_document_quality_types(pg.migrator)
    _assert_column_contract(pg)
    assert Campaign.get_by_id("legacy-campaign").document_type == "business_requirements"
    assert Run.get_by_id("legacy-run").document_type == "business_requirements"
    assert _snapshot(pg.database) == before
    _campaign("tz-campaign", product="technical_specification")
    _run("tz-run", product="technical_specification", campaign="tz-campaign")
    complete = _snapshot(pg.database)
    _migrate_document_quality_types(pg.migrator)
    _migrate_document_quality_types(pg.migrator)
    _assert_column_contract(pg)
    assert _snapshot(pg.database) == complete
    assert Run.get_by_id("tz-run").document_type == "technical_specification"
    assert {item["id"] for item in runs.list_quality_runs()["runs"]} == {"legacy-run"}
    assert {item["id"] for item in runs.list_quality_runs("technical_specification")["runs"]} == {"tz-run"}
    assert runs.get_quality_campaign("tz-campaign") is None
    assert runs.get_quality_run("legacy-run", "technical_specification") is None
    Campaign.create(id="orm-default-campaign", schedule_key="manual:orm-default-campaign", models={})
    Run.create(id="orm-default-run", trigger="MANUAL")
    assert Campaign.get_by_id("orm-default-campaign").document_type == "business_requirements"
    assert Run.get_by_id("orm-default-run").document_type == "business_requirements"


def test_interrupted_nullable_backfill_repairs_without_retyping_existing_tz(postgres_quality):
    pg = postgres_quality
    _campaign("legacy-campaign")
    _run("legacy-run", campaign="legacy-campaign")
    _campaign("tz-campaign", product="technical_specification")
    _run("tz-run", product="technical_specification", campaign="tz-campaign")
    before = _snapshot(pg.database)
    _drop_product_columns(pg.database)
    for table, identifier in zip(TABLES, ("tz-campaign", "tz-run"), strict=True):
        pg.database.execute_sql(f'ALTER TABLE "{table}" ADD COLUMN "document_type" VARCHAR(32)')
        pg.database.execute_sql(f'UPDATE "{table}" SET "document_type" = %s WHERE id = %s', ("technical_specification", identifier))
    _migrate_document_quality_types(pg.migrator)
    _migrate_document_quality_types(pg.migrator)
    _assert_column_contract(pg)
    assert _snapshot(pg.database) == before
    assert Campaign.get_by_id("legacy-campaign").document_type == "business_requirements"
    assert Run.get_by_id("legacy-run").document_type == "business_requirements"
    assert Campaign.get_by_id("tz-campaign").document_type == "technical_specification"
    assert Run.get_by_id("tz-run").document_type == "technical_specification"


def test_quality_migration_failure_rolls_back_both_tables_then_recovers(postgres_quality, monkeypatch):
    pg = postgres_quality
    _campaign("legacy-campaign")
    _run("legacy-run", campaign="legacy-campaign")
    before = _snapshot(pg.database)
    _drop_product_columns(pg.database)
    original = pg.database.execute_sql

    def server_failure(sql, params=None, commit=True):
        if sql.startswith('ALTER TABLE "business_document_quality_run" ADD COLUMN'):
            return original("SELECT 1 / 0", None, commit)
        return original(sql, params, commit)

    with monkeypatch.context() as injection:
        injection.setattr(pg.database, "execute_sql", server_failure)
        with pytest.raises(DataError):
            _migrate_document_quality_types(pg.migrator)
    for table in TABLES:
        assert "document_type" not in {column.name for column in pg.database.get_columns(table, schema=pg.schema)}
    assert _snapshot(pg.database) == before
    _migrate_document_quality_types(pg.migrator)
    _assert_column_contract(pg)
    assert _snapshot(pg.database) == before


def _race_reclassifier_after_finish(pg, monkeypatch, table, finish):
    selected, committed = Event(), Event()
    original = pg.database.execute_sql
    pids = {}

    def stop_before_stale_write(sql, params=None, commit=True):
        if sql.startswith(f'UPDATE "{table}"') and "WORKER_STOPPED" in (params or ()):
            selected.set()
            assert committed.wait(10), "Finisher did not commit before stale write"
        return original(sql, params, commit)

    monkeypatch.setattr(pg.database, "execute_sql", stop_before_stale_write)

    def classify():
        with pg.database.connection_context():
            pids["classifier"] = pg.database.execute_sql("SELECT pg_backend_pid()").fetchone()[0]
            return runs.process_next_run()

    def complete():
        assert selected.wait(10), "Reclassifier did not reach its stale snapshot write"
        with pg.database.connection_context():
            pids["finisher"] = pg.database.execute_sql("SELECT pg_backend_pid()").fetchone()[0]
            with pg.database.atomic():
                assert finish() == 1
            committed.set()

    with ThreadPoolExecutor(max_workers=2) as workers:
        classifier, finisher = workers.submit(classify), workers.submit(complete)
        finisher.result(timeout=20)
        classifier.result(timeout=20)
    assert len(set(pids.values())) == 2


@pytest.mark.parametrize("completion", ["PASS", "RENEWED"])
def test_stale_run_reclassifier_cannot_overwrite_committed_finish_or_new_generation(postgres_quality, monkeypatch, completion):
    pg = postgres_quality
    _run("old-running", status="RUNNING", started=1)
    _run("tz-protected", product="technical_specification")
    protected = Run.get_by_id("tz-protected").__data__.copy()

    def finish():
        values = {"status": "PASS", "finished_at": NOW, "report": {"status": "PASS", "proof": "completed"}} if completion == "PASS" else {"started_at": NOW}
        return Run.update(**values).where((Run.id == "old-running") & (Run.status == "RUNNING") & (Run.started_at == 1)).execute()

    _race_reclassifier_after_finish(pg, monkeypatch, TABLES[1], finish)
    saved = Run.get_by_id("old-running")
    if completion == "PASS":
        assert saved.status == "PASS" and saved.report == {"status": "PASS", "proof": "completed"}
    else:
        assert saved.status == "RUNNING" and saved.started_at == NOW
    assert saved.reason_code is None
    assert Run.get_by_id("tz-protected").__data__ == protected


def test_stale_baseline_reclassifier_cannot_erase_pass_or_fail_children(postgres_quality, monkeypatch):
    pg = postgres_quality
    _campaign("old-baseline", status="RUNNING", baseline=None)
    _run("waiting-child", campaign="old-baseline", status="PENDING", started=None)
    _campaign("tz-protected", product="technical_specification")
    protected = Campaign.get_by_id("tz-protected").__data__.copy()
    executed = []

    def execute(row):
        executed.append(row.id)
        return "PASS", {"status": "PASS"}, None

    monkeypatch.setattr(runs, "_execute", execute)

    def finish():
        return (
            Campaign.update(baseline_status="PASS")
            .where((Campaign.id == "old-baseline") & (Campaign.status == "RUNNING") & (Campaign.started_at == 1) & Campaign.baseline_status.is_null(True))
            .execute()
        )

    _race_reclassifier_after_finish(pg, monkeypatch, TABLES[0], finish)
    assert Campaign.get_by_id("old-baseline").baseline_status == "PASS"
    child = Run.get_by_id("waiting-child")
    assert child.status == "PASS" and child.reason_code is None
    assert executed == ["waiting-child"]
    assert Campaign.get_by_id("tz-protected").__data__ == protected
