"""SQL execution rechecks source access and fences result publication."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_runs import database as run_database
from peewee import SqliteDatabase
from api.apps.business_documents import sql_query_runs as runs
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryProject, BusinessDocumentSqlQueryRun
from business_documents.application.errors import BusinessDocumentError

Service = runs.BusinessDocumentSqlRunService
TENANT, ACTOR = "tenant-1", "author-1"
accepted_source = runs._source


@pytest.fixture()
def project(monkeypatch):
    yield from run_database.__wrapped__(monkeypatch)


def command(**extra):
    return {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "run-boundary", "selected_profile_id": None, **extra}


def attempt(project):
    queued = Service.run(TENANT, ACTOR, project, command())
    job = BusinessDocumentJobQueue.claim("run-boundary-worker", lease_ms=60_000)
    assert job.payload["run_id"] == queued["run_id"]
    return job, BusinessDocumentSqlQueryRun.get_by_id(queued["run_id"])


@pytest.mark.parametrize("action", ["preflight", "run", "preview"])
@pytest.mark.parametrize("foreign", ["tenant", "owner"])
def test_foreign_project_access_is_rejected_before_database_execution(project, monkeypatch, action, foreign):
    execute = Mock()
    monkeypatch.setattr(runs, "execute_postgres", execute)
    tenant, actor = ("foreign-tenant", ACTOR) if foreign == "tenant" else (TENANT, "foreign-owner")
    with pytest.raises(BusinessDocumentError) as caught:
        if action == "preflight":
            Service.preflight(tenant, actor, project, None)
        elif action == "run":
            Service.run(tenant, actor, project, command())
        else:
            Service.preview(tenant, actor, project, "foreign-run")
    assert caught.value.status == (404 if foreign == "tenant" else 403)
    execute.assert_not_called()
    assert BusinessDocumentSqlQueryRun.select().count() == 0
    assert BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("prerequisite", ["unbound", "row_limit"])
def test_missing_execution_prerequisite_blocks_run_without_job(project, monkeypatch, prerequisite):
    if prerequisite == "unbound":
        monkeypatch.setattr(runs, "_binding", lambda *_: ({"status": "UNAVAILABLE"}, None, None))
    else:
        monkeypatch.setattr(runs, "_binding", lambda *_: ({"status": "BOUND"}, SimpleNamespace(max_rows=1), SimpleNamespace(config={})))
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run(TENANT, ACTOR, project, command())
    assert caught.value.code == ("SQL_EXECUTION_BINDING_REQUIRED" if prerequisite == "unbound" else "SQL_ROW_LIMIT_EXCEEDED")
    assert BusinessDocumentSqlQueryRun.select().count() == 0
    assert BusinessDocumentJob.select().count() == 0
    assert Agents._get_project(TENANT, project).state_version == 1


def test_run_rechecks_version_after_preflight_without_leaking_queued_rows(project, monkeypatch):
    def concurrent_change(*_args, **_kwargs):
        BusinessDocumentSqlQueryProject.update(state_version=2).where(BusinessDocumentSqlQueryProject.id == project).execute()

    monkeypatch.setattr(runs, "explain_postgres", concurrent_change)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run(TENANT, ACTOR, project, command())
    assert caught.value.code == "SQL_PROJECT_VERSION_CONFLICT"
    assert BusinessDocumentSqlQueryRun.select().count() == 0
    assert BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("status, expected", [("PURGED", "PURGED"), ("CANCEL_REQUESTED", "CANCELED")])
def test_canceled_run_never_enters_postgres(project, monkeypatch, status, expected):
    job, run = attempt(project)
    BusinessDocumentSqlQueryRun.update(status=status).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    execute = Mock()
    monkeypatch.setattr(runs, "execute_postgres", execute)
    assert Service.execute_job(job, job.lease_owner, job.lease_token) == {"status": expected}
    execute.assert_not_called()


@pytest.mark.parametrize(
    "damage, code",
    [
        ("wrong_job", "SQL_RUN_JOB_MISMATCH"),
        ("finished_run", "SQL_RUN_STATE_CHANGED"),
        ("changed_compilation", "SQL_COMPILATION_STALE"),
        ("changed_profile", "SQL_PROFILE_STALE"),
        ("reduced_row_limit", "SQL_ROW_LIMIT_EXCEEDED"),
    ],
)
def test_worker_rechecks_current_execution_prerequisites(project, monkeypatch, damage, code):
    job, run = attempt(project)
    execute = Mock()
    monkeypatch.setattr(runs, "execute_postgres", execute)
    if damage == "wrong_job":
        BusinessDocumentSqlQueryRun.update(job_id="another-job").where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    elif damage == "finished_run":
        BusinessDocumentSqlQueryRun.update(status="READY").where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    elif damage == "changed_compilation":
        original = runs._source

        def changed(project):
            compilation, request = original(project)
            compilation.id = "new-compilation"
            return compilation, request

        monkeypatch.setattr(runs, "_source", changed)
    else:
        original = runs._binding

        def changed(*args):
            binding, profile, connector = original(*args)
            if damage == "changed_profile":
                profile.version += 1
            else:
                profile.max_rows = 1
            return binding, profile, connector

        monkeypatch.setattr(runs, "_binding", changed)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.execute_job(job, job.lease_owner, job.lease_token)
    assert caught.value.code == code
    execute.assert_not_called()
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).rows == []


def test_invalid_database_result_is_not_saved(project, monkeypatch):
    job, run = attempt(project)
    monkeypatch.setattr(runs, "execute_postgres", lambda *_args, **_kwargs: {"columns": ["unexpected"], "rows": [[42]], "row_count": 1, "result_bytes": 4, "duration_ms": 2})
    with pytest.raises(BusinessDocumentError) as caught:
        Service.execute_job(job, job.lease_owner, job.lease_token)
    assert caught.value.code == "SQL_RESULT_CHECK_FAILED"
    assert caught.value.status == 422
    stored = BusinessDocumentSqlQueryRun.get_by_id(run.id)
    assert stored.status == "RUNNING" and stored.rows == []


def test_cancellation_wins_over_late_valid_result(project):
    job, run = attempt(project)
    output = Service.execute_job(job, job.lease_owner, job.lease_token)
    canceled = Service.cancel_result(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "cancel-late", "run_id": run.id})
    assert canceled["rows_status"] == "CANCEL_REQUESTED"
    Service.complete_job(job, job.lease_owner, job.lease_token, output)
    stored = BusinessDocumentSqlQueryRun.get_by_id(run.id)
    assert stored.status == "PURGED" and stored.rows == stored.columns == [] and stored.checks == {}
    assert BusinessDocumentJob.get_by_id(job.id).status == "CANCELED"


@pytest.mark.parametrize("action", ["complete", "fail"])
def test_lost_lease_cannot_publish_or_purge_current_result(project, action):
    job, run = attempt(project)
    output = Service.execute_job(job, job.lease_owner, job.lease_token)
    BusinessDocumentJob.update(lease_token="new-worker-token").where(BusinessDocumentJob.id == job.id).execute()
    before = BusinessDocumentSqlQueryRun.get_by_id(run.id).__data__.copy()
    with pytest.raises(BusinessDocumentError) as caught:
        if action == "complete":
            Service.complete_job(job, job.lease_owner, job.lease_token, output)
        else:
            Service.fail_job(job, job.lease_owner, job.lease_token, {"code": "DATABASE_UNAVAILABLE"})
    assert caught.value.code == "SQL_AGENT_JOB_LEASE_LOST"
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).__data__ == before
    assert BusinessDocumentJob.get_by_id(job.id).lease_token == "new-worker-token"


@pytest.mark.parametrize("code", ["SQL_CANCELED", "POSTGRES_ERROR"])
def test_failure_classification_purges_rows_and_releases_lease(project, code):
    job, run = attempt(project)
    Service.fail_job(job, job.lease_owner, job.lease_token, {"code": code})
    stored = BusinessDocumentSqlQueryRun.get_by_id(run.id)
    finished = BusinessDocumentJob.get_by_id(job.id)
    assert stored.status == ("PURGED" if code == "SQL_CANCELED" else "FAILED")
    assert stored.rows == stored.columns == [] and stored.checks == {}
    assert stored.error == ({} if code == "SQL_CANCELED" else {"code": code})
    assert finished.status == ("CANCELED" if code == "SQL_CANCELED" else "FAILED")
    assert finished.lease_owner is finished.lease_token is finished.lease_expires_at is None


def test_claimed_queued_job_is_canceled_by_worker_instead_of_being_abandoned(project):
    job, run = attempt(project)
    result = Service.cancel_result(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "cancel-claimed", "run_id": run.id})
    assert result["rows_status"] == "CANCEL_REQUESTED"
    assert BusinessDocumentJob.get_by_id(job.id).status == "RUNNING"
    output = Service.execute_job(job, job.lease_owner, job.lease_token)
    Service.complete_job(job, job.lease_owner, job.lease_token, output)
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).status == "PURGED"
    assert BusinessDocumentJob.get_by_id(job.id).status == "CANCELED"


@pytest.mark.parametrize("offset", [True, -1, "0"])
def test_preview_requires_numeric_nonnegative_offset(project, offset):
    with pytest.raises(BusinessDocumentError) as caught:
        Service.preview(TENANT, ACTOR, project, "absent-run", offset)
    assert caught.value.code == "INVALID_SQL_PREVIEW"


def test_preview_missing_run_is_404(project):
    with pytest.raises(BusinessDocumentError) as caught:
        Service.preview(TENANT, ACTOR, project, "absent-run")
    assert caught.value.code == "SQL_RUN_NOT_FOUND" and caught.value.status == 404


def ready_result(project):
    job, run = attempt(project)
    Service.complete_job(job, job.lease_owner, job.lease_token, Service.execute_job(job, job.lease_owner, job.lease_token))
    return BusinessDocumentSqlQueryRun.get_by_id(run.id)


@pytest.mark.parametrize(
    "damage, code",
    [
        ("unverified", "SQL_VERIFIED_RESULT_REQUIRED"),
        ("oversized", "SQL_PYTHON_INPUT_TOO_LARGE"),
        ("invalid_output", "SQL_PYTHON_RESULT_INVALID"),
        ("invalid_shape", "SQL_PYTHON_RESULT_INVALID"),
        ("purged_during_transform", "SQL_PROJECT_VERSION_CONFLICT"),
    ],
)
def test_python_transform_preserves_source_and_rejects_untrusted_result(project, monkeypatch, damage, code):
    run = ready_result(project)
    if damage == "unverified":
        BusinessDocumentSqlQueryRun.update(checks={"status": "FAIL"}).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    elif damage == "oversized":
        monkeypatch.setattr(runs, "_MAX_PYTHON_INPUT_BYTES", 1)
    calls = []

    def transform(*args):
        calls.append(args)
        if damage == "invalid_output":
            return {"columns": "not-an-array", "rows": []}
        if damage == "invalid_shape":
            return {"columns": ["value"], "rows": [[42, "extra-column"]], "row_count": 1, "result_bytes": 4}
        if damage == "purged_during_transform":
            BusinessDocumentSqlQueryRun.update(status="PURGED", rows=[]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
        return {"columns": ["value"], "rows": [[42]], "row_count": 1, "result_bytes": 4}

    monkeypatch.setattr(runs, "execute_result_python", transform)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run_python(
            TENANT,
            ACTOR,
            project,
            run.id,
            {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "python-boundary", "code": "def main(columns, rows):\n    return {'columns': columns, 'rows': rows}"},
        )
    assert caught.value.code == code
    assert bool(calls) is (damage not in {"unverified", "oversized"})
    assert BusinessDocumentSqlQueryRun.select().count() == 1
    stored = BusinessDocumentSqlQueryRun.get_by_id(run.id)
    assert stored.rows == ([] if damage == "purged_during_transform" else [[42]])
    assert Agents._get_project(TENANT, project).state_version == 2


def test_failed_cancellation_ledger_write_rolls_back_result_and_version(project, monkeypatch):
    run = ready_result(project)
    before = BusinessDocumentSqlQueryRun.get_by_id(run.id).__data__.copy()

    def reject_ledger(*_args, **_kwargs):
        raise RuntimeError("Idempotency ledger unavailable")

    monkeypatch.setattr(Agents, "_record_command", reject_ledger)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        Service.cancel_result(TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "cancel-rollback", "run_id": run.id})
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).__data__ == before
    assert Agents._get_project(TENANT, project).state_version == 2


@pytest.fixture()
def source_project():
    database = SqliteDatabase(":memory:", check_same_thread=False)
    tables = Agents.model_tables()
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        created = Agents.create_project(
            "tenant-revision",
            "author-revision",
            {
                "schema_version": "1",
                "title": "Orders",
                "source_request": "Show orders",
                "locale": "en",
            },
        )

        def artifact(kind, artifact_id, payload):
            return runs.BusinessDocumentSqlQueryArtifact.create(
                id=artifact_id,
                project_id=created["id"],
                tenant_id="tenant-revision",
                kind=kind,
                revision=1,
                payload=payload,
                content_hash="sha256:test",
                source_proposal_id="p" * 32,
                accepted_by="author-revision",
            )

        requirements = artifact("REQUIREMENTS", "r" * 32, {"requirements": [{"statement": "Show orders"}]})
        schema = artifact("SCHEMA", "s" * 32, {"schema_snapshot": {}, "accepted_schema": []})
        sql = "SELECT o.order_id AS order_id FROM dwh.order_fact AS o LIMIT :row_limit"
        query = artifact(
            "QUERY",
            "q" * 32,
            {
                "mode": "manual",
                "sql": sql,
                "parameters": [{"name": "row_limit", "type": "integer", "value": 10}],
                "confirmed_alignment": True,
            },
        )
        artifact(
            "COMPILATION",
            "c" * 32,
            {
                "source_artifact_ids": [requirements.id, schema.id, query.id],
                "result": {"sql": sql, "parameters": {"row_limit": 10}, "guard": {"status": "PASS"}},
            },
        )
        BusinessDocumentSqlQueryProject.update(
            stage="COMPLETE",
            requirements_artifact_id=requirements.id,
            schema_artifact_id=schema.id,
            query_artifact_id=query.id,
        ).where(BusinessDocumentSqlQueryProject.id == created["id"]).execute()
        try:
            yield created["id"]
        finally:
            database.drop_tables(tables)
            database.close()


@pytest.mark.parametrize(
    "damage, code",
    [
        ("incomplete_decisions", "SQL_PROJECT_INCOMPLETE"),
        ("foreign_artifact", "SQL_PROJECT_ARTIFACT_MISMATCH"),
        ("missing_compilation", "SQL_COMPILATION_STALE"),
        ("old_artifact_ids", "SQL_COMPILATION_STALE"),
        ("changed_compiled_sql", "SQL_COMPILATION_STALE"),
        ("blocked_fresh_guard", "SQL_COMPILATION_STALE"),
    ],
)
def test_execution_source_rejects_untrusted_or_stale_artifact_chain(source_project, monkeypatch, damage, code):
    artifact_model = runs.BusinessDocumentSqlQueryArtifact
    project = BusinessDocumentSqlQueryProject.get_by_id(source_project)
    compilation = artifact_model.get_by_id("c" * 32)
    fresh = {"status": "READY", **compilation.payload["result"]}
    compilation.payload = {**compilation.payload, "result": fresh}
    compilation.save()
    build = Mock(return_value={"verified": "accepted source command"})
    compile_query = Mock(return_value=fresh)
    monkeypatch.setattr(runs, "build_project_compile_command", build)
    monkeypatch.setattr(runs, "compile_query_payload", compile_query)
    if damage == "incomplete_decisions":
        project.stage = "SCHEMA"
    elif damage == "foreign_artifact":
        artifact_model.update(tenant_id="foreign-tenant").where(artifact_model.id == project.query_artifact_id).execute()
    elif damage == "missing_compilation":
        compilation.delete_instance()
    elif damage == "old_artifact_ids":
        compilation.payload = {**compilation.payload, "source_artifact_ids": ["old-requirements", "old-schema", "old-query"]}
        compilation.save()
    elif damage == "changed_compiled_sql":
        compile_query.return_value = {**fresh, "sql": "SELECT other_value FROM another_table"}
    else:
        compile_query.return_value = {**fresh, "guard": {"status": "FAIL"}}
    with pytest.raises(BusinessDocumentError) as caught:
        accepted_source(project)
    assert caught.value.code == code
    assert BusinessDocumentSqlQueryRun.select().count() == 0
    assert BusinessDocumentJob.select().count() == 0
    if damage not in {"changed_compiled_sql", "blocked_fresh_guard"}:
        build.assert_not_called()
        compile_query.assert_not_called()
