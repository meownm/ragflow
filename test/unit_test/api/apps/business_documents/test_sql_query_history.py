"""Historical restore is a new guarded commit, never a pointer rollback or Run."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_access import ACTOR, project as stored_project, source as stored_source
from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService as Agents, _stable_hash
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService as Schema
from api.apps.business_documents.sql_query_revisions import BusinessDocumentSqlRevisionService as Revisions
from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryProject as Project, BusinessDocumentSqlQueryRun as Run
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from test.evals.sql_projects.golden import BASE


@pytest.fixture
def project():
    yield from stored_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from stored_source.__wrapped__(project, monkeypatch)


def command(version=1, compilation="c" * 32, key="restore-old"):
    return {"schema_version": "1", "expected_state_version": version, "idempotency_key": key, "compilation_id": compilation}


def snapshot():
    return {table.__name__: deepcopy(list(table.select().order_by(table.id).dicts())) for table in Agents.model_tables()}


def restore(project, raw=None, actor=ACTOR, tenant=None, role="AUTHOR_CREATOR"):
    return Agents.restore_query(tenant or project.tenant_id, actor, project.id, raw or command(), access_role=role)


def next_query(project):
    bindings = deepcopy(BASE.bindings())
    next(item for item in bindings if item["name"] == "row_limit")["value"] = 25
    return Agents.save_manual_query(
        project.tenant_id,
        ACTOR,
        project.id,
        {"schema_version": "1", "expected_state_version": 1, "idempotency_key": "edit-limit", "sql": BASE.oracle.sql, "parameters": bindings, "confirmed_alignment": True},
    )


def test_restore_creates_a_new_query_and_compilation_and_keeps_original_rows_and_history(source):
    project, _, _ = source
    old_query = deepcopy(Artifact.get_by_id("q" * 32).__data__)
    old_compilation = deepcopy(Artifact.get_by_id("c" * 32).__data__)
    columns = old_compilation["payload"]["result"]["output_columns"]
    old_run = Run.create(
        id="old-result",
        project_id=project.id,
        tenant_id=project.tenant_id,
        compilation_id="c" * 32,
        profile_id="original-profile",
        profile_version=7,
        status="READY",
        columns=columns,
        rows=[[42] * len(columns)],
        row_count=1,
        checks={"status": "PASS"},
    )
    run_before = deepcopy(Run.get_by_id(old_run.id).__data__)
    edited = next_query(project)
    response = restore(project, command(version=edited["project"]["state_version"]))
    query = Artifact.get_by_id(response["project"]["artifact_ids"]["query"])
    compilation = Artifact.get_by_id(response["compilation_id"])
    assert query.id not in {"q" * 32, edited["project"]["artifact_ids"]["query"]}
    assert query.revision == 3 and query.accepted_by == ACTOR
    assert response["compilation"] == old_compilation["payload"]["result"]
    assert query.payload["parameters"] == BASE.bindings()
    assert query.payload["restored_from"] == {"query_artifact_id": "q" * 32, "compilation_id": "c" * 32, "requirements_artifact_id": "r" * 32, "schema_artifact_id": "s" * 32}
    assert compilation.payload["source_artifact_ids"] == ["r" * 32, "s" * 32, query.id]
    assert Artifact.get_by_id("q" * 32).__data__ == old_query
    assert Artifact.get_by_id("c" * 32).__data__ == old_compilation
    assert Run.get_by_id(old_run.id).__data__ == run_before
    assert Run.select().count() == 1
    assert query.content_hash == _stable_hash(query.payload)


@pytest.mark.parametrize("role", ["AUTHOR_CREATOR", "AUTHOR_EDITOR"])
def test_restore_replay_is_exact_and_does_not_create_another_revision(source, role):
    project, _, _ = source
    response = restore(project, role=role)
    after = snapshot()
    assert restore(project, role=role) == response
    assert snapshot() == after
    with pytest.raises(ConflictError):
        restore(project, command(compilation=response["compilation_id"]), role=role)
    assert snapshot() == after


def test_history_exposes_exact_parameters_author_time_context_and_restore_provenance(source):
    project, _, _ = source
    changed = next_query(project)
    workspace = Revisions.workspace(project.tenant_id, ACTOR, project.id)
    assert len(workspace["query_revisions"]) == 2
    values = {item["id"]: item for item in workspace["query_revisions"]}
    old, current = values["q" * 32], values[changed["project"]["artifact_ids"]["query"]]
    assert old["sql"] == current["sql"]
    assert next(item for item in old["parameters"] if item["name"] == "row_limit")["value"] == 100
    assert next(item for item in current["parameters"] if item["name"] == "row_limit")["value"] == 25
    assert old["accepted_by"] == ACTOR and old["created_at"]
    assert old["source_artifact_ids"] == ["r" * 32, "s" * 32]
    restored = restore(project, command(version=changed["project"]["state_version"]))
    history = Revisions.workspace(project.tenant_id, ACTOR, project.id)["query_revisions"]
    assert next(item for item in history if item["id"] == restored["project"]["artifact_ids"]["query"])["restored_from"]["query_artifact_id"] == "q" * 32


def test_history_selects_compilation_revision_independently_of_clock_order(source):
    project, _, _ = source
    payload = deepcopy(Artifact.get_by_id("c" * 32).payload)
    Artifact.create(
        id="latest-compilation",
        project_id=project.id,
        tenant_id=project.tenant_id,
        kind="COMPILATION",
        revision=2,
        payload=payload,
        content_hash=_stable_hash(payload),
        source_proposal_id="latest",
        accepted_by=ACTOR,
    )
    Artifact.update(create_time=1).where(Artifact.id == "latest-compilation").execute()
    version = Revisions.workspace(project.tenant_id, ACTOR, project.id)["query_revisions"][0]
    assert version["compilation_id"] == "latest-compilation"


def test_restore_replay_rechecks_reader_role_and_current_catalog_grant(source, monkeypatch):
    project, _, _ = source
    restore(project)
    after = snapshot()
    with pytest.raises(PermissionDeniedError):
        restore(project, role="READER")

    async def revoked(*_args):
        raise PermissionDeniedError("Source access revoked")

    monkeypatch.setattr(Schema, "load_entities", revoked)
    with pytest.raises(PermissionDeniedError):
        restore(project)
    assert snapshot() == after


@pytest.mark.parametrize("identity", ["reader", "foreign-owner", "foreign-tenant", "archive"])
def test_restore_denies_identity_and_archive_before_catalog_io(source, monkeypatch, identity):
    project, _, _ = source
    if identity == "archive":
        Project.update(archived_at=1).where(Project.id == project.id).execute()

    async def forbidden(*_args):
        pytest.fail("Unauthorized restore must not touch the catalog")

    monkeypatch.setattr(Schema, "load_entities", forbidden)
    before = snapshot()
    with pytest.raises(BusinessDocumentError) as failure:
        restore(
            project,
            actor="other" if identity == "foreign-owner" else ACTOR,
            tenant="other" if identity == "foreign-tenant" else project.tenant_id,
            role="READER" if identity == "reader" else "AUTHOR_CREATOR",
        )
    assert failure.value.status == (404 if identity == "foreign-tenant" else 409 if identity == "archive" else 403)
    assert snapshot() == before


def test_stale_restore_is_rejected_before_catalog_and_has_no_effect(source):
    project, _, calls = source
    next_query(project)
    before = snapshot()
    with pytest.raises(ConflictError) as failure:
        restore(project)
    assert failure.value.code == "SQL_PROJECT_VERSION_CONFLICT"
    assert calls == [] and snapshot() == before


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING", "CANCEL_REQUESTED"])
def test_restore_does_not_mutate_active_results(source, state):
    project, _, _ = source
    Run.create(id="active-result", project_id=project.id, tenant_id=project.tenant_id, compilation_id="c" * 32, profile_id="profile", profile_version=1, status=state)
    before = snapshot()
    with pytest.raises(ConflictError) as failure:
        restore(project)
    assert failure.value.code == "SQL_RESULT_ACTIVE"
    assert snapshot() == before


def test_restore_rechecks_current_catalog_schema_instead_of_trusting_old_pass(source):
    project, tables, calls = source
    tables["orders"]["version"] = 2
    before = snapshot()
    with pytest.raises(ConflictError) as failure:
        restore(project)
    assert failure.value.code == "SQL_SCHEMA_STALE"
    assert len(calls) == 2 and snapshot() == before


def test_restore_compiles_against_current_accepted_schema_and_records_both_contexts(source):
    project, tables, _ = source
    current = Artifact.get_by_id("s" * 32)
    payload = deepcopy(current.payload)
    tables["orders"].update(version=2, schema_fingerprint="sha256:orders-v2")
    selected = next(item["selected_table"] for item in payload["schema_snapshot"]["requirements"] if item["selected_table"]["id"] == "orders")
    selected.update(version=2, schema_fingerprint="sha256:orders-v2")
    next(item for item in payload["accepted_schema"] if item["entity_id"] == "orders").update(version=2, schema_fingerprint="sha256:orders-v2")
    Artifact.create(
        id="new-schema",
        project_id=project.id,
        tenant_id=project.tenant_id,
        kind="SCHEMA",
        revision=2,
        payload=payload,
        content_hash=_stable_hash(payload),
        source_proposal_id="schema-proposal",
        accepted_by=ACTOR,
    )
    Project.update(schema_artifact_id="new-schema").where(Project.id == project.id).execute()
    response = restore(project)
    query = Artifact.get_by_id(response["project"]["artifact_ids"]["query"])
    compilation = Artifact.get_by_id(response["compilation_id"])
    assert response["compilation"]["snapshot_fingerprint"] != Artifact.get_by_id("c" * 32).payload["result"]["snapshot_fingerprint"]
    assert compilation.payload["source_artifact_ids"] == ["r" * 32, "new-schema", query.id]
    assert query.payload["restored_from"]["schema_artifact_id"] == "s" * 32


def test_restore_actual_guard_rejects_a_column_removed_from_the_current_schema(source):
    project, tables, _ = source
    payload = deepcopy(Artifact.get_by_id("s" * 32).payload)
    table = next(item["selected_table"] for item in payload["schema_snapshot"]["requirements"] if item["selected_table"]["id"] == "orders")
    table["columns"] = [column for column in table["columns"] if column["name"] != "created_at"]
    table.update(version=2, schema_fingerprint="sha256:orders-without-created-at", column_count=len(table["columns"]), loaded_column_count=len(table["columns"]))
    tables["orders"] = deepcopy(table)
    next(item for item in payload["accepted_schema"] if item["entity_id"] == "orders").update(version=2, schema_fingerprint=table["schema_fingerprint"])
    Artifact.create(
        id="schema-without-created-at",
        project_id=project.id,
        tenant_id=project.tenant_id,
        kind="SCHEMA",
        revision=2,
        payload=payload,
        content_hash=_stable_hash(payload),
        source_proposal_id="schema-proposal",
        accepted_by=ACTOR,
    )
    Project.update(schema_artifact_id="schema-without-created-at").where(Project.id == project.id).execute()
    before = snapshot()
    with pytest.raises(BusinessDocumentError) as failure:
        restore(project)
    assert failure.value.code == "SQL_QUERY_BLOCKED"
    assert snapshot() == before


def test_restore_rolls_back_new_artifacts_when_the_final_project_cas_loses(source, monkeypatch):
    project, _, _ = source
    before = snapshot()
    original_update = Project.update

    class LostUpdate:
        def where(self, *_args):
            return self

        def execute(self):
            return 0

    def concurrent_update(*args, **kwargs):
        return LostUpdate() if "query_artifact_id" in kwargs else original_update(*args, **kwargs)

    monkeypatch.setattr(Project, "update", concurrent_update)
    with pytest.raises(ConflictError) as failure:
        restore(project)
    assert failure.value.code == "SQL_PROJECT_VERSION_CONFLICT"
    assert snapshot() == before


@pytest.mark.parametrize("damage", ["foreign-source", "missing-source", "current-schema-missing", "current-source-foreign", "compiler-blocked"])
def test_invalid_restore_source_or_current_context_has_no_partial_effects(source, monkeypatch, damage):
    project, _, _ = source
    if damage == "foreign-source":
        Artifact.update(tenant_id="other").where(Artifact.id == "c" * 32).execute()
    elif damage == "missing-source":
        Artifact.delete().where(Artifact.id == "q" * 32).execute()
    elif damage == "current-schema-missing":
        Project.update(schema_artifact_id=None).where(Project.id == project.id).execute()
    elif damage == "current-source-foreign":
        foreign = Artifact.get_by_id("s" * 32)
        Artifact.create(
            id="foreign-schema",
            project_id="other",
            tenant_id=project.tenant_id,
            kind="SCHEMA",
            revision=1,
            payload=foreign.payload,
            content_hash=foreign.content_hash,
            source_proposal_id="foreign",
            accepted_by=ACTOR,
        )
        Project.update(schema_artifact_id="foreign-schema").where(Project.id == project.id).execute()
    else:
        monkeypatch.setattr(Agents, "_compile_manual_query", lambda *_args: {"status": "BLOCKED", "blocking_issues": [{"code": "FIELD_REMOVED"}]})
    before = snapshot()
    with pytest.raises(BusinessDocumentError):
        restore(project)
    assert snapshot() == before


@pytest.mark.parametrize("changes", [{"schema_version": "2"}, {"expected_state_version": True}, {"compilation_id": ""}, {"sql": "SELECT untrusted"}, {"idempotency_key": ""}])
def test_restore_command_is_closed_versioned_and_source_only(source, changes):
    project, _, _ = source
    before = snapshot()
    with pytest.raises(BusinessDocumentError) as failure:
        restore(project, {**command(), **changes})
    assert failure.value.status == 422
    assert snapshot() == before
