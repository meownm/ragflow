"""Project metadata/copy commands preserve accepted content and tenant boundaries."""

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_runs import database as shared_database
from api.apps.business_documents import sql_query_runs as runs
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Projects, _stable_hash
from api.db.db_models import BusinessDocumentSqlAgentCommand
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService as Schema
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError, ValidationError


@pytest.fixture
def database(monkeypatch):
    async def verified(*args):
        return False

    monkeypatch.setattr(Schema, "verify_snapshot", verified)
    yield from shared_database.__wrapped__(monkeypatch)


def command(**changes):
    return {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "registry-1", **changes}


def update(project, **changes):
    return Projects.update_project("tenant-1", "author-1", project, command(**changes))


def seed_artifacts(project, count):
    for kind, payload in list(
        (
            ("REQUIREMENTS", {"requirements": [{"statement": "Продажи"}]}),
            ("SCHEMA", {"schema_snapshot": {}, "accepted_schema": [{"entity_id": "sales"}]}),
            ("QUERY", {"sql": "SELECT value FROM sales", "parameters": []}),
        )
    )[:count]:
        artifact_id = f"accepted-{kind.lower()}"
        runs.BusinessDocumentSqlQueryArtifact.create(
            id=artifact_id, project_id=project, tenant_id="tenant-1", kind=kind, revision=4, payload=payload, content_hash=_stable_hash(payload), source_proposal_id="accepted", accepted_by="author-1"
        )
        runs.BusinessDocumentSqlQueryProject.update(**{f"{kind.lower()}_artifact_id": artifact_id}).where(runs.BusinessDocumentSqlQueryProject.id == project).execute()


def test_rename_persists_and_is_idempotent_without_altering_content(database):
    before = Projects.get_project("tenant-1", "author-1", database)
    renamed = update(database, title="  Итог продаж  ")
    assert renamed["title"] == "Итог продаж" and renamed["state_version"] == 2
    assert update(database, title="  Итог продаж  ") == renamed
    assert Projects.get_project("tenant-1", "author-1", database) == renamed
    for field in ("source_request", "artifact_ids", "artifacts", "runs", "stage"):
        assert renamed[field] == before[field]
    with pytest.raises(ConflictError):
        update(database, title="Another title")


def test_noop_does_not_invalidate_compilation_or_jobs(database):
    unchanged = update(database, title="Продажи")
    assert unchanged["state_version"] == 1
    assert runs.BusinessDocumentJob.select().count() == 0


def test_archive_restore_and_read_only_history(database, monkeypatch):
    runs.BusinessDocumentSqlQueryRun.create(
        id="old-result",
        project_id=database,
        tenant_id="tenant-1",
        compilation_id="compilation-1",
        profile_id="old",
        profile_version=1,
        status="READY",
        checks={"status": "PASS"},
        rows=[[42]],
        columns=["value"],
        row_count=1,
    )
    archived = update(database, archived=True)
    assert archived["archived_at"] > 0
    assert archived["capabilities"]["manage"] and not archived["capabilities"]["edit"] and not archived["capabilities"]["execute"]
    assert Projects.list_projects("tenant-1", "author-1") == []
    assert [item["id"] for item in Projects.list_projects("tenant-1", "author-1", archived=True)] == [database]
    assert runs.BusinessDocumentSqlRunService.preview("tenant-1", "author-1", database, "old-result", access_role="READER")["rows"] == [[42]]
    forbidden = (
        lambda: runs.BusinessDocumentSqlRunService.run("tenant-1", "author-1", database, command(selected_profile_id=None)),
        lambda: runs.BusinessDocumentSqlRunService.check_connection("tenant-1", "author-1", database, {"schema_version": "1", "selected_profile_id": None}),
        lambda: runs.BusinessDocumentSqlRunService.preflight("tenant-1", "author-1", database, None),
        lambda: Revisions.save_draft("tenant-1", "author-1", database, {"schema_version": "1", "expected_state_version": 2, "expected_draft_version": 0, "steps": []}),
        lambda: Projects.request_agent("tenant-1", "author-1", database, command(kind="QUERY", payload={})),
    )
    monkeypatch.setattr(runs, "_binding", lambda *args: pytest.fail("Archived project must not open a connector"))
    for action in forbidden:
        with pytest.raises(ConflictError) as caught:
            action()
        assert caught.value.code == "SQL_PROJECT_ARCHIVED"
    restored = Projects.update_project("tenant-1", "author-1", database, command(expected_state_version=2, idempotency_key="restore", archived=False))
    assert restored["archived_at"] is None and restored["state_version"] == 3
    assert restored["runs"][0]["id"] == "old-result" and restored["capabilities"]["edit"]


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "CANCEL_REQUESTED"])
@pytest.mark.parametrize("change", [{"archived": True}, {"title": "Rename"}])
def test_active_sql_or_derived_run_blocks_registry_changes(database, status, change):
    runs.BusinessDocumentSqlQueryRun.create(id="active", project_id=database, tenant_id="tenant-1", compilation_id="old", profile_id="old", profile_version=1, kind="PYTHON", status=status)
    with pytest.raises(ConflictError) as caught:
        update(database, **change)
    assert caught.value.code == "SQL_RUN_ACTIVE"


@pytest.mark.parametrize("state", ["RUNNING", "REVIEW"])
def test_agent_operation_blocks_archive(database, state):
    runs.BusinessDocumentSqlQueryProject.update(operation_state=state).where(runs.BusinessDocumentSqlQueryProject.id == database).execute()
    with pytest.raises(ConflictError):
        update(database, archived=True)


@pytest.mark.parametrize("role", ["READER", "AUTHOR_EDITOR"])
def test_roles_and_foreign_tenants_cannot_copy(database, role):
    with pytest.raises(PermissionDeniedError):
        Projects.copy_project("tenant-1", "author-1", database, command(), access_role=role)
    with pytest.raises(BusinessDocumentError) as caught:
        Projects.copy_project("other-tenant", "author-1", database, command())
    assert caught.value.status == 404


def test_search_is_literal_scoped_and_archive_aware(database):
    model = runs.BusinessDocumentSqlQueryProject
    for key, tenant, owner, title in (("p2", "tenant-1", "author-1", "Total 50%_Done"), ("p3", "tenant-1", "author-2", "Private sales"), ("p4", "tenant-2", "author-1", "Foreign sales")):
        model.create(id=key, tenant_id=tenant, owner_id=owner, title=title, source_request="request")
    assert [item["id"] for item in Projects.list_projects("tenant-1", "author-1", search="50%_dONE")] == ["p2"]
    assert Projects.list_projects("tenant-1", "author-1", search="sales") == []
    assert len(Projects.list_projects("tenant-1", "author-1")) == 2
    update(database, archived=True)
    assert Projects.list_projects("tenant-1", "author-1", archived=True)[0]["id"] == database


@pytest.mark.parametrize("count,stage", [(0, "REQUIREMENTS"), (1, "SCHEMA"), (2, "QUERY"), (3, "COMPLETE")])
def test_copy_only_current_accepted_artifacts_with_independent_ids(database, count, stage):
    seed_artifacts(database, count)
    runs.BusinessDocumentSqlQueryProject.update(execution_profile_id="private-profile").where(runs.BusinessDocumentSqlQueryProject.id == database).execute()
    before = Projects.get_project("tenant-1", "author-1", database)
    copied = Projects.copy_project("tenant-1", "author-1", database, command())
    assert copied["id"] != database and copied["stage"] == stage and copied["state_version"] == 1
    assert copied["execution_profile_id"] is None and copied["compilation"] is None and copied["runs"] == []
    assert copied["current_job"] is None and copied["pending_proposal"] is None
    assert copied["copy_origin"] == {"project_id": database, "state_version": 1, "artifact_ids": [item for item in before["artifact_ids"].values() if item]}
    assert copied["artifacts"] == before["artifacts"]
    assert not set(item for item in copied["artifact_ids"].values() if item).intersection(before["artifact_ids"].values())
    assert Projects.get_project("tenant-1", "author-1", database) == before
    assert Projects.copy_project("tenant-1", "author-1", database, command()) == copied
    assert runs.BusinessDocumentSqlQueryProject.select().count() == 2
    assert runs.BusinessDocumentJob.select().count() == 0


def test_copy_rechecks_catalog_and_rolls_back_on_denial(database, monkeypatch):
    seed_artifacts(database, 2)

    async def denied(*args):
        raise PermissionDeniedError("Catalog access revoked")

    monkeypatch.setattr(Schema, "verify_snapshot", denied)
    with pytest.raises(PermissionDeniedError):
        Projects.copy_project("tenant-1", "author-1", database, command())
    assert runs.BusinessDocumentSqlQueryProject.select().count() == 1
    assert BusinessDocumentSqlAgentCommand.select().count() == 0


@pytest.mark.parametrize(
    "patch",
    [{}, {"title": " "}, {"title": "x" * 256}, {"archived": 1}, {"archived": None}, {"owner_id": "another"}, {"title": "OK", "expected_state_version": True}, {"title": "OK", "idempotency_key": ""}],
)
def test_registry_closed_payloads(database, patch):
    with pytest.raises(ValidationError):
        update(database, **patch)


def test_foreign_owner_denied_before_archive_status(database):
    update(database, archived=True)
    with pytest.raises(PermissionDeniedError):
        Projects.update_project("tenant-1", "author-2", database, command(title="Secret"))


def test_stale_copy_does_not_create_partial_project(database):
    update(database, title="Renamed")
    with pytest.raises(ConflictError):
        Projects.copy_project("tenant-1", "author-1", database, command(idempotency_key="copy"))
    assert runs.BusinessDocumentSqlQueryProject.select().count() == 1


def test_changing_a_copy_does_not_change_source_or_accepted_history(database):
    seed_artifacts(database, 3)
    source = Projects.get_project("tenant-1", "author-1", database)
    copied = Projects.copy_project("tenant-1", "author-1", database, command())
    revised = Projects.revise_question("tenant-1", "author-1", copied["id"], command(idempotency_key="copy-question", source_request="Новая независимая задача"))
    assert revised["stage"] == "REQUIREMENTS" and revised["source_request"] == "Новая независимая задача"
    assert all(item is None for item in revised["artifact_ids"].values())
    assert Projects.get_project("tenant-1", "author-1", database) == source
    assert runs.BusinessDocumentSqlQueryArtifact.select().where(runs.BusinessDocumentSqlQueryArtifact.project_id == copied["id"]).count() == 3


@pytest.mark.parametrize("damage", [{"tenant_id": "foreign"}, {"project_id": "foreign"}, {"content_hash": "sha256:corrupt"}])
def test_copy_rejects_artifacts_from_another_scope_or_with_changed_content(database, damage):
    seed_artifacts(database, 1)
    runs.BusinessDocumentSqlQueryArtifact.update(**damage).where(runs.BusinessDocumentSqlQueryArtifact.id == "accepted-requirements").execute()
    with pytest.raises(ConflictError) as caught:
        Projects.copy_project("tenant-1", "author-1", database, command())
    assert caught.value.code == "SQL_PROJECT_ARTIFACT_MISMATCH"
    assert runs.BusinessDocumentSqlQueryProject.select().count() == 1


def test_copy_rejects_a_broken_accepted_artifact_chain(database):
    runs.BusinessDocumentSqlQueryProject.update(query_artifact_id="orphan-query").where(runs.BusinessDocumentSqlQueryProject.id == database).execute()
    with pytest.raises(ConflictError) as caught:
        Projects.copy_project("tenant-1", "author-1", database, command())
    assert caught.value.code == "SQL_PROJECT_ARTIFACT_MISMATCH"
    assert runs.BusinessDocumentSqlQueryProject.select().count() == 1
