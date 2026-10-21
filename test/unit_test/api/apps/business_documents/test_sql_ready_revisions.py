"""Completed results remain readable while query revisions get their own provenance."""

from copy import deepcopy
import json
from time import perf_counter
import tracemalloc
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_revisions import (
    ACTOR,
    TENANT,
    SQL,
    project as revision_project,
)
from test.unit_test.api.apps.business_documents.test_sql_query_runs import database as execution_project
from api.apps.business_documents import sql_query_revisions as revisions, sql_query_runs as runs
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from api.db.db_models import BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryRun, BusinessDocumentJob
from business_documents.application.errors import ConflictError


@pytest.fixture
def project():
    yield from revision_project.__wrapped__()


@pytest.fixture
def execution(monkeypatch):
    yield from execution_project.__wrapped__(monkeypatch)


def stored(project, status="READY", *, run_id="stored-result", tenant=TENANT, compilation="c" * 32, rows=None):
    return BusinessDocumentSqlQueryRun.create(
        id=run_id,
        project_id=project,
        tenant_id=tenant,
        compilation_id=compilation,
        profile_id="old-profile",
        profile_version=7,
        profile_name="Original source",
        status=status,
        columns=["order_id"],
        rows=[[42]] if rows is None else rows,
        row_count=1,
        result_bytes=4,
        duration_ms=2,
        checks={"status": "PASS"},
    )


def snapshot(run):
    return deepcopy({name: getattr(run, name) for name in ("status", "columns", "rows", "row_count", "result_bytes", "checks", "compilation_id", "profile_id", "profile_version", "profile_name")})


def preserved(run, before, *, tenant=TENANT, actor=ACTOR):
    assert snapshot(BusinessDocumentSqlQueryRun.get_by_id(run.id)) == before
    assert runs.BusinessDocumentSqlRunService.preview(tenant, actor, run.project_id, run.id)["rows"] == before["rows"]


@pytest.mark.parametrize("status", ["READY", "QUEUED", "RUNNING", "CANCEL_REQUESTED"])
def test_manual_revision_preserves_completed_result_but_rejects_in_progress_work(project, monkeypatch, status):
    old = stored(project, status)
    before = snapshot(old)
    old_document = BusinessDocumentSqlQueryArtifact.create(
        id="previous-document",
        project_id=project,
        tenant_id=TENANT,
        kind="DOCUMENT",
        revision=1,
        payload={"source_artifact_ids": ["r" * 32, "s" * 32, "q" * 32], "run_id": old.id},
        content_hash="sha256:old-document",
        source_proposal_id="old-document-proposal",
        accepted_by=ACTOR,
    )
    compile_query = Mock(return_value={"status": "READY", "sql": SQL, "parameters": {"row_limit": 25}, "guard": {"status": "PASS"}})
    monkeypatch.setattr(Agents, "_compile_manual_query", compile_query)
    command = {
        "schema_version": "1",
        "expected_state_version": 1,
        "idempotency_key": "manual-after-result",
        "sql": SQL,
        "parameters": [{"name": "row_limit", "type": "integer", "value": 25}],
        "confirmed_alignment": True,
    }
    count = BusinessDocumentSqlQueryArtifact.select().count()
    if status != "READY":
        with pytest.raises(ConflictError) as error:
            Agents.save_manual_query(TENANT, ACTOR, project, command)
        assert error.value.code == "SQL_RESULT_ACTIVE"
        compile_query.assert_not_called()
        assert BusinessDocumentSqlQueryArtifact.select().count() == count
        assert Agents.get_project(TENANT, ACTOR, project)["state_version"] == 1
        return
    saved = Agents.save_manual_query(TENANT, ACTOR, project, command)
    assert saved["project"]["artifact_ids"]["query"] != "q" * 32
    assert saved["compilation_id"] != old.compilation_id
    assert saved["project"]["next_action"] == "EXECUTE"
    assert BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload["parameters"][0]["value"] == 10
    preserved(old, before)
    assert BusinessDocumentSqlQueryArtifact.get_by_id(old_document.id).payload == old_document.payload
    assert Agents.save_manual_query(TENANT, ACTOR, project, command) == saved
    compile_query.assert_called_once()
    assert BusinessDocumentSqlQueryRun.select().count() == 1


@pytest.mark.parametrize("status", ["READY", "QUEUED", "RUNNING", "CANCEL_REQUESTED"])
def test_dialogue_revision_can_start_with_completed_rows_only(project, status):
    old = stored(project, status)
    before = snapshot(old)
    thread = Revisions.create_thread(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "result-thread", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Change limit"}
    )
    command = {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "request-after-result"}
    if status != "READY":
        with pytest.raises(ConflictError) as error:
            Revisions.request_change(TENANT, ACTOR, project, thread["id"], command)
        assert error.value.code == "SQL_RESULT_ACTIVE"
        assert BusinessDocumentJob.select().count() == 0
        return
    requested = Revisions.request_change(TENANT, ACTOR, project, thread["id"], command)
    assert requested["operation_state"] == "RUNNING"
    assert BusinessDocumentJob.select().count() == 1
    preserved(old, before)


@pytest.mark.parametrize("in_progress", [None, "QUEUED", "RUNNING", "CANCEL_REQUESTED"])
def test_accepting_dialogue_revision_keeps_old_rows_and_compilation(project, monkeypatch, in_progress):
    old = stored(project)
    before = snapshot(old)
    thread = Revisions.create_thread(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "apply-thread", "target": "QUERY", "anchor": {"kind": "WHOLE"}, "text": "Change limit"}
    )
    Revisions.request_change(TENANT, ACTOR, project, thread["id"], {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "apply-request"})
    job = BusinessDocumentJobQueue.claim("ready-revision")
    Agents.complete_job(
        job,
        "ready-revision",
        job.lease_token,
        {
            "proposal": {"sql": SQL, "parameters": [{"name": "row_limit", "type": "integer", "value": 25}], "summary": "Limit25", "checks": {"status": "PASS"}},
            "source": {"target": "QUERY", "base_artifact_id": "q" * 32, "thread_id": thread["id"]},
        },
    )
    monkeypatch.setattr(revisions, "build_project_compile_command", lambda *_: {})
    monkeypatch.setattr(revisions, "compile_query_payload", lambda *_: {"status": "READY", "sql": SQL, "parameters": {"row_limit": 25}, "guard": {"status": "PASS"}})
    current = Agents.get_project(TENANT, ACTOR, project)
    command = {"schema_version": "1", "expected_state_version": current["state_version"], "idempotency_key": "accept-after-result", "decision": "ACCEPT"}
    if in_progress:
        stored(project, in_progress, run_id="concurrent-run")
        count = BusinessDocumentSqlQueryArtifact.select().count()
        with pytest.raises(ConflictError) as error:
            Revisions.decide_change(TENANT, ACTOR, project, current["pending_proposal"]["id"], command)
        assert error.value.code == "SQL_RESULT_ACTIVE"
        unchanged = Agents.get_project(TENANT, ACTOR, project)
        assert unchanged["artifact_ids"]["query"] == "q" * 32
        assert unchanged["pending_proposal"]["id"] == current["pending_proposal"]["id"]
        assert BusinessDocumentSqlQueryArtifact.select().count() == count
        preserved(old, before)
        return
    accepted = Revisions.decide_change(
        TENANT,
        ACTOR,
        project,
        current["pending_proposal"]["id"],
        command,
    )
    assert accepted["artifact_ids"]["query"] != "q" * 32
    assert accepted["compilation"]["id"] != old.compilation_id
    assert accepted["next_action"] == "EXECUTE"
    preserved(old, before)


def test_question_can_restart_while_completed_result_remains_in_history(project):
    old = stored(project)
    before = snapshot(old)
    revised = Agents.revise_question(
        TENANT, ACTOR, project, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "new-question-with-result", "source_request": "Use a new reporting period"}
    )
    assert revised["stage"] == "REQUIREMENTS" and revised["next_action"] == "CONTINUE"
    preserved(old, before)


def test_schema_can_reopen_without_deleting_completed_rows(project):
    old = stored(project)
    before = snapshot(old)
    revised = Revisions.reopen_schema(
        TENANT,
        ACTOR,
        project,
        {
            "schema_version": "1",
            "expected_state_version": 1,
            "idempotency_key": "schema-after-result",
        },
    )
    assert revised["stage"] == "SCHEMA" and revised["next_action"] == "CONTINUE"
    assert revised["compilation"] is None
    preserved(old, before)


def test_current_result_is_the_next_action_and_an_in_progress_run_takes_priority(project):
    stored(project)
    assert Agents.get_project(TENANT, ACTOR, project)["next_action"] == "VIEW_RESULT"
    stored(project, "QUEUED", run_id="newer-run", compilation="different-compilation")
    assert Agents.get_project(TENANT, ACTOR, project)["next_action"] == "WAIT"


def test_new_run_has_new_compilation_and_retains_the_three_result_quota(execution, monkeypatch):
    from api.apps.business_documents import worker

    monkeypatch.setattr(worker, "wake_business_document_worker", lambda: None)
    tenant, actor = "tenant-1", "author-1"
    old = stored(execution, tenant=tenant, compilation="compilation-1")
    before = snapshot(old)
    previous, command = runs._source(None)
    new = SimpleNamespace(id="compilation-2", payload=previous.payload, source_proposal_id="second-proposal")
    monkeypatch.setattr(runs, "_source", lambda _: (new, command))
    with pytest.raises(ConflictError) as stale_document:
        runs.BusinessDocumentSqlRunService.complete(
            tenant,
            actor,
            execution,
            {
                "schema_version": "1",
                "expected_state_version": 1,
                "idempotency_key": "stale-result-document",
                "run_id": old.id,
            },
        )
    assert stale_document.value.code == "SQL_RESULT_STALE"
    assert not BusinessDocumentSqlQueryArtifact.select().where(BusinessDocumentSqlQueryArtifact.kind == "DOCUMENT").exists()
    preserved(old, before, tenant=tenant, actor=actor)
    queued = runs.BusinessDocumentSqlRunService.run(tenant, actor, execution, {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "new-query-run", "selected_profile_id": None})
    assert BusinessDocumentSqlQueryRun.get_by_id(queued["run_id"]).compilation_id == "compilation-2"
    preserved(old, before, tenant=tenant, actor=actor)
    with pytest.raises(ConflictError):
        runs.BusinessDocumentSqlRunService.run(tenant, actor, execution, {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "parallel-query", "selected_profile_id": None})
    BusinessDocumentSqlQueryRun.update(status="READY").where(BusinessDocumentSqlQueryRun.id == queued["run_id"]).execute()
    stored(execution, tenant=tenant, compilation="third-compilation", run_id="third-result")
    with pytest.raises(ConflictError) as quota:
        runs.BusinessDocumentSqlRunService.run(tenant, actor, execution, {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "fourth-query", "selected_profile_id": None})
    assert quota.value.code == "SQL_ACTIVE_RESULT_QUOTA"
    assert BusinessDocumentSqlQueryRun.select().count() == runs._MAX_ACTIVE_RESULTS == 3
    preserved(old, before, tenant=tenant, actor=actor)


def test_project_projection_does_not_load_retained_row_payloads(project, monkeypatch, tmp_path):
    blob = "x" * 2_000_000
    for number in range(3):
        stored(project, run_id=f"result-{number}", rows=[[blob]])
    db = BusinessDocumentSqlQueryRun._meta.database
    execute = db.execute_sql
    statements = []

    def track(sql, *args, **kwargs):
        if sql.startswith("SELECT"):
            statements.append(sql)
        return execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute_sql", track)
    tracemalloc.start()
    started = perf_counter()
    result = Agents.get_project(TENANT, ACTOR, project)
    elapsed = (perf_counter() - started) * 1000
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    metrics = {"peak_bytes": peak, "milliseconds": elapsed, "select_queries": len(statements)}
    (tmp_path / "projection-metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    print(json.dumps(metrics))
    assert len(result["runs"]) == 3
    assert all(item["profile_name"] == "Original source" and item["compilation_id"] == "c" * 32 and "rows" not in item for item in result["runs"])
    assert all('"rows"' not in sql for sql in statements if '"business_document_sql_query_run"' in sql)
    assert peak < len(blob) // 2
