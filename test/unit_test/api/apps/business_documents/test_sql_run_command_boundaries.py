"""Result lifecycle failures retain rows, provenance and the command ledger."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_runs_boundaries import (
    ACTOR,
    TENANT,
    Agents,
    Service,
    command,
    project as run_project,
    ready_result,
    runs,
)
from api.db.db_models import BusinessDocumentSqlQueryRun, BusinessDocumentSqlAgentCommand, BusinessDocumentSqlQueryArtifact, BusinessDocumentJob
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def project(monkeypatch):
    yield from run_project.__wrapped__(monkeypatch)


def result_command(version=2, **fields):
    return {"schema_version": "1", "expected_state_version": version, "idempotency_key": "result-boundary", **fields}


@pytest.mark.parametrize("changes", [{"schema_version": "2"}, {"expected_state_version": True}, {"idempotency_key": ""}, {"tenant_id": "foreign"}])
def test_run_envelope_cannot_override_identity_or_use_invalid_version(project, changes):
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run(TENANT, ACTOR, project, command(**changes))
    assert caught.value.code == "INVALID_SQL_RUN_COMMAND"
    assert Agents._get_project(TENANT, project).state_version == 1
    assert BusinessDocumentSqlQueryRun.select().count() == BusinessDocumentJob.select().count() == 0
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


@pytest.mark.parametrize("selected", ["", True])
def test_run_requires_nonempty_profile_reference_before_preflight_or_job(project, monkeypatch, selected):
    explain = Mock()
    monkeypatch.setattr(runs, "explain_postgres", explain)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run(TENANT, ACTOR, project, command(selected_profile_id=selected))
    assert caught.value.code == "INVALID_SQL_RUN_COMMAND"
    explain.assert_not_called()
    assert BusinessDocumentSqlQueryRun.select().count() == BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("state", ["unbound", "aged-catalog", "row-limit"])
def test_preflight_reports_actual_blocker_without_executing_unqualified_sql(project, monkeypatch, state):
    explain = Mock()
    monkeypatch.setattr(runs, "explain_postgres", explain)
    if state == "unbound":
        monkeypatch.setattr(runs, "_binding", lambda *_: ({"status": "UNAVAILABLE"}, None, None))
    elif state == "aged-catalog":
        monkeypatch.setattr(runs, "_verify_schema", lambda *_: True)
    else:
        monkeypatch.setattr(runs, "_binding", lambda *_: ({"status": "BOUND"}, SimpleNamespace(max_rows=1), SimpleNamespace(config={})))
    result = Service.preflight(TENANT, ACTOR, project, None)
    if state == "unbound":
        assert result["binding"] == {"status": "UNAVAILABLE"}
        explain.assert_not_called()
    elif state == "row-limit":
        assert result["blocker"]["code"] == "SQL_ROW_LIMIT_EXCEEDED"
        explain.assert_not_called()
    else:
        assert "OpenMetadata" in result["warning"]
        explain.assert_called_once()
    assert BusinessDocumentSqlQueryRun.select().count() == BusinessDocumentJob.select().count() == 0


@pytest.mark.parametrize("failure,code", [("stale-version", "SQL_PROJECT_VERSION_CONFLICT"), ("missing-run", "SQL_RESULT_MISSING")])
def test_rejected_cancellation_preserves_ready_rows_and_ledger(project, failure, code):
    run = ready_result(project)
    before = run.__data__.copy()
    ledger_count = BusinessDocumentSqlAgentCommand.select().count()
    raw = result_command(version=1 if failure == "stale-version" else 2, run_id="missing" if failure == "missing-run" else run.id)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.cancel_result(TENANT, ACTOR, project, raw)
    assert caught.value.code == code
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).__data__ == before
    assert Agents._get_project(TENANT, project).state_version == 2
    assert BusinessDocumentSqlAgentCommand.select().count() == ledger_count


def test_cancellation_replay_does_not_advance_version_or_change_other_result(project):
    run = ready_result(project)
    request_body = result_command(run_id=run.id)
    first = Service.cancel_result(TENANT, ACTOR, project, request_body)
    before = Agents._get_project(TENANT, project).__data__.copy()
    replay = Service.cancel_result(TENANT, ACTOR, project, request_body)
    assert replay == first
    assert Agents._get_project(TENANT, project).__data__ == before
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).rows == []


def test_purged_result_preview_returns_conflict_without_exposing_saved_columns(project):
    run = ready_result(project)
    Service.cancel_result(TENANT, ACTOR, project, result_command(run_id=run.id))
    with pytest.raises(BusinessDocumentError) as caught:
        Service.preview(TENANT, ACTOR, project, run.id)
    assert caught.value.code == "SQL_RESULT_REMOVED" and caught.value.status == 409


@pytest.mark.parametrize("code", [None, "print('hello')", "x" * 20_001])
def test_invalid_python_program_never_starts_execution_or_creates_derived_rows(project, monkeypatch, code):
    run = ready_result(project)
    execute = Mock()
    monkeypatch.setattr(runs, "execute_result_python", execute)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run_python(TENANT, ACTOR, project, run.id, result_command(code=code))
    assert caught.value.code == "INVALID_SQL_PYTHON_CODE"
    execute.assert_not_called()
    assert BusinessDocumentSqlQueryRun.select().count() == 1
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).rows == [[42]]


def test_stale_python_request_preserves_source_without_running_program(project, monkeypatch):
    run = ready_result(project)
    execute = Mock()
    monkeypatch.setattr(runs, "execute_result_python", execute)
    with pytest.raises(BusinessDocumentError) as caught:
        Service.run_python(TENANT, ACTOR, project, run.id, result_command(version=1, code="def main(columns, rows): return {'columns': columns, 'rows': rows}"))
    assert caught.value.code == "SQL_PROJECT_VERSION_CONFLICT"
    execute.assert_not_called()
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).rows == [[42]]


def test_canceling_derived_result_preserves_sql_source_and_its_conclusion_draft(project):
    source = ready_result(project)
    derived = BusinessDocumentSqlQueryRun.create(
        id="derived",
        project_id=project,
        tenant_id=TENANT,
        compilation_id=source.compilation_id,
        profile_id=source.profile_id,
        profile_version=source.profile_version,
        kind="PYTHON",
        source_run_id=source.id,
        status="READY",
        rows=[[84]],
        columns=["value"],
    )
    draft = BusinessDocumentSqlQueryArtifact.create(
        id="conclusion",
        project_id=project,
        tenant_id=TENANT,
        kind="CONCLUSION",
        revision=1,
        payload={"source_run_id": source.id, "status": "DRAFT"},
        content_hash="sha256:draft",
        source_proposal_id=source.id,
        accepted_by=ACTOR,
    )
    Service.cancel_result(TENANT, ACTOR, project, result_command(run_id=derived.id))
    assert BusinessDocumentSqlQueryRun.get_by_id(derived.id).status == "PURGED"
    assert BusinessDocumentSqlQueryRun.get_by_id(source.id).status == "READY"
    assert BusinessDocumentSqlQueryRun.get_by_id(source.id).rows == [[42]]
    assert BusinessDocumentSqlQueryArtifact.get_by_id(draft.id).payload == draft.payload
