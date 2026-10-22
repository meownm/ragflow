"""Persisted connection choice never mutates accepted SQL or existing results."""

from types import SimpleNamespace

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_runs import database as shared_database
from api.apps.business_documents import sql_query_runs as owner
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects
from api.apps.business_documents.worker import BusinessDocumentJobQueue
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError


@pytest.fixture
def database(monkeypatch):
    for project_id in shared_database.__wrapped__(monkeypatch):
        # This fixture represents the stage before SQL or compilation exists.
        owner.BusinessDocumentSqlQueryArtifact.delete().where(owner.BusinessDocumentSqlQueryArtifact.project_id == project_id).execute()
        for kind, artifact_id, payload in (
            ("REQUIREMENTS", "health-req", {"requirements": [{"statement": "Покажи продажи"}]}),
            ("SCHEMA", "health-schema", {"schema_snapshot": {}, "accepted_schema": []}),
        ):
            owner.BusinessDocumentSqlQueryArtifact.create(
                id=artifact_id,
                tenant_id="tenant-1",
                project_id=project_id,
                kind=kind,
                revision=1,
                payload=payload,
                content_hash=owner._stable_hash(payload),
                source_proposal_id=f"accepted-{kind}",
                accepted_by="author-1",
            )
        owner.BusinessDocumentSqlQueryProject.update(requirements_artifact_id="health-req", schema_artifact_id="health-schema").where(owner.BusinessDocumentSqlQueryProject.id == project_id).execute()
        yield project_id


def command(profile="profile-1", *, version=1, key="connection-1"):
    return {"schema_version": "1", "expected_state_version": version, "idempotency_key": key, "selected_profile_id": profile}


def save(project, raw=None, **options):
    return owner.BusinessDocumentSqlRunService.configure_connection("tenant-1", "author-1", project, command() if raw is None else raw, **options)


def test_choice_survives_reload_and_replay_without_changing_sql_artifacts(database):
    before = Projects.get_project("tenant-1", "author-1", database)
    artifact_count = owner.BusinessDocumentSqlQueryArtifact.select().count()
    saved = save(database)
    assert saved["execution_profile_id"] == "profile-1" and saved["state_version"] == 2
    assert Projects.get_project("tenant-1", "author-1", database)["execution_profile_id"] == "profile-1"
    assert saved["artifact_ids"] == before["artifact_ids"]
    assert owner.BusinessDocumentSqlQueryArtifact.select().count() == artifact_count
    assert save(database) == saved
    with pytest.raises(ConflictError):
        save(database, command(None))


def test_automatic_reset_is_explicit_and_needs_no_catalog_connection(database, monkeypatch):
    save(database)
    monkeypatch.setattr(owner, "_binding", lambda *args: pytest.fail("Clearing a source cannot require an accessible profile"))
    cleared = save(database, command(None, version=2, key="automatic"))
    assert cleared["execution_profile_id"] is None and cleared["state_version"] == 3


def test_preflight_and_new_job_use_saved_profile_and_job_keeps_frozen_identity(database, monkeypatch):
    save(database)
    seen = []
    profile = SimpleNamespace(id="profile-1", name="QA warehouse", version=7, max_rows=10, statement_timeout_ms=1000, max_result_bytes=1000)
    monkeypatch.setattr(owner, "_binding", lambda *args: (seen.append(args[-1]) or {"status": "BOUND"}, profile, SimpleNamespace(config={})))
    owner.BusinessDocumentSqlRunService.preflight("tenant-1", "author-1", database, None)
    queued = owner.BusinessDocumentSqlRunService.run("tenant-1", "author-1", database, command(None, version=2, key="run"))
    run = owner.BusinessDocumentSqlQueryRun.get_by_id(queued["run_id"])
    job = owner.BusinessDocumentJob.get_by_id(run.job_id)
    assert seen == ["profile-1", "profile-1"]
    assert job.payload["selected_profile_id"] == "profile-1"
    assert (run.profile_id, run.profile_version, run.profile_name) == ("profile-1", 7, "QA warehouse")
    owner.BusinessDocumentSqlQueryProject.update(execution_profile_id="later-profile").where(owner.BusinessDocumentSqlQueryProject.id == database).execute()
    assert job.payload["selected_profile_id"] == "profile-1"
    claimed = BusinessDocumentJobQueue.claim("frozen-connection")
    owner.BusinessDocumentSqlRunService.execute_job(claimed, "frozen-connection", claimed.lease_token)
    assert seen == ["profile-1", "profile-1", "profile-1"]
    summary = Projects.get_project("tenant-1", "author-1", database)["latest_run"]
    assert (summary["profile_id"], summary["profile_version"], summary["profile_name"]) == ("profile-1", 7, "QA warehouse")


def test_revoked_saved_profile_never_falls_back_to_another_source(database, monkeypatch):
    save(database)
    seen = []
    monkeypatch.setattr(owner, "_binding", lambda *args: (seen.append(args[-1]) or {"status": "UNAVAILABLE"}, None, None))
    with pytest.raises(ConflictError, match="Select an available"):
        owner.BusinessDocumentSqlRunService.run("tenant-1", "author-1", database, command(None, version=2, key="revoked-run"))
    assert seen == ["profile-1"]
    assert owner.BusinessDocumentSqlQueryRun.select().count() == 0


def test_preflight_offers_recovery_candidates_without_executing_or_switching_saved_source(database, monkeypatch):
    save(database)
    seen = []

    def binding(*args):
        selected = args[-1]
        seen.append(selected)
        if selected == "profile-1":
            raise ValidationError("INVALID_SQL_EXECUTION_BINDING_REQUEST", "No longer compatible")
        return {"status": "BOUND", "candidates": [{"id": "profile-2", "name": "Available"}], "selection": {"profile": {"id": "profile-2"}}}, SimpleNamespace(id="profile-2"), SimpleNamespace(config={})

    monkeypatch.setattr(owner, "_binding", binding)
    monkeypatch.setattr(owner, "explain_postgres", lambda *args, **kwargs: pytest.fail("An alternative profile must not be used implicitly"))
    result = owner.BusinessDocumentSqlRunService.preflight("tenant-1", "author-1", database, None)
    assert result["binding"]["status"] == "UNAVAILABLE" and result["binding"]["selection"] is None
    assert result["binding"]["candidates"] == [{"id": "profile-2", "name": "Available"}]
    assert result["blocker"]["code"] == "SQL_PROFILE_UNAVAILABLE"
    assert seen == ["profile-1", None]
    assert Projects.get_project("tenant-1", "author-1", database)["execution_profile_id"] == "profile-1"


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "CANCEL_REQUESTED"])
def test_active_execution_blocks_connection_changes(database, status):
    owner.BusinessDocumentSqlQueryRun.create(id="active", tenant_id="tenant-1", project_id=database, compilation_id="compiled", profile_id="old", profile_version=1, status=status)
    with pytest.raises(ConflictError) as caught:
        save(database)
    assert caught.value.code == "SQL_RUN_ACTIVE"
    assert Projects.get_project("tenant-1", "author-1", database)["execution_profile_id"] is None


def test_ready_result_keeps_original_source_when_project_chooses_a_new_one(database):
    owner.BusinessDocumentSqlQueryRun.create(
        id="ready",
        tenant_id="tenant-1",
        project_id=database,
        compilation_id="compiled",
        profile_id="old",
        profile_name="Original",
        profile_version=4,
        status="READY",
        rows=[[42]],
        columns=["value"],
        row_count=1,
    )
    saved = save(database)
    assert saved["latest_run"]["profile_name"] == "Original"
    assert owner.BusinessDocumentSqlQueryRun.get_by_id("ready").rows == [[42]]
    assert saved["execution_profile_id"] == "profile-1"


@pytest.mark.parametrize("role", ["READER", "AUTHOR_EDITOR"])
def test_read_only_or_non_executing_role_cannot_configure_a_source(database, role):
    with pytest.raises(PermissionDeniedError):
        save(database, access_role=role)
    preflight = owner.BusinessDocumentSqlRunService.preflight("tenant-1", "author-1", database, None, access_role=role)
    assert preflight["blocker"]["code"] == "SQL_EXECUTION_FORBIDDEN"
    assert preflight["binding"] == {"status": "UNAVAILABLE"}


@pytest.mark.parametrize("value", ["", " ", "x" * 33, 1, True, [], {}])
def test_invalid_profile_reference_cannot_modify_project(database, value):
    with pytest.raises(ValidationError):
        save(database, command(value))
    assert Projects.get_project("tenant-1", "author-1", database)["state_version"] == 1


@pytest.mark.parametrize("tenant,actor,status", [("foreign", "author-1", 404), ("tenant-1", "peer", 403)])
def test_connection_has_the_same_project_and_tenant_boundaries(database, tenant, actor, status):
    with pytest.raises(BusinessDocumentError) as caught:
        owner.BusinessDocumentSqlRunService.configure_connection(tenant, actor, database, command())
    assert caught.value.status == status


def test_change_during_catalog_check_fails_cas_without_overwriting_the_new_state(database, monkeypatch):
    def resolve(*args):
        owner.BusinessDocumentSqlQueryProject.update(state_version=2, execution_profile_id="newer").where(owner.BusinessDocumentSqlQueryProject.id == database).execute()
        return {"status": "BOUND"}, SimpleNamespace(id="profile-1"), SimpleNamespace(config={})

    monkeypatch.setattr(owner, "_binding", resolve)
    with pytest.raises(ConflictError):
        save(database)
    assert Projects.get_project("tenant-1", "author-1", database)["execution_profile_id"] == "newer"
