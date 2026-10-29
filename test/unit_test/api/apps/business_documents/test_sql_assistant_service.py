"""SQLite persistence with real SQL artifacts and controlled catalog authorization."""

from copy import deepcopy

import pytest
import sqlglot

from test.unit_test.api.apps.business_documents.test_sql_query_access import project as accepted_project, source as accepted_source
from test.unit_test.api.apps.business_documents.test_sql_query_revisions import ACTOR, TENANT
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService
from api.apps.sql_assistant.service import SqlAssistantService
from api.db.db_models import BusinessDocumentJob, BusinessDocumentSqlQueryArtifact, BusinessDocumentSqlQueryRun, SqlAssistantCommand, SqlAssistantSession, SqlAssistantDefinitionVersion
from business_documents.application.errors import BusinessDocumentError, ConflictError, PermissionDeniedError
from business_documents.sql_assistant import default_view, fingerprint


@pytest.fixture
def project():
    yield from accepted_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from accepted_source.__wrapped__(project, monkeypatch)


@pytest.fixture
def research(source, monkeypatch):
    from api.db.services.managed_resource_service import ManagedResourceService

    monkeypatch.setattr(ManagedResourceService, "owner_id", lambda: "registry-owner")
    project, tables, calls = source
    database = project._meta.database
    with database.bind_ctx((SqlAssistantSession, SqlAssistantCommand)):
        database.create_tables((SqlAssistantSession, SqlAssistantCommand))
        session = SqlAssistantSession.create(
            id=project.id,
            tenant_id=TENANT,
            owner_id=ACTOR,
            create_request_id="initial",
            create_request_hash=fingerprint({"question": "Orders"}),
            state={"messages": [{"id": "m1", "text": "Orders", "parent_result_id": None}], "selected_result_id": None, "views": {}, "draft": None, "structured_queries": {}},
        )
        compiled = BusinessDocumentSqlQueryArtifact.get_by_id("c" * 32).payload["result"]
        columns = sqlglot.parse_one(compiled["sql"], dialect="postgres").named_selects
        run = BusinessDocumentSqlQueryRun.create(
            id="run-1",
            project_id=project.id,
            tenant_id=TENANT,
            compilation_id="c" * 32,
            profile_id="profile-1",
            profile_version=1,
            kind="SQL",
            status="READY",
            columns=columns,
            rows=[[index for _ in columns] for index in range(250)],
            row_count=250,
            checks={"status": "PASS", "schema": "PASS", "bounds": "PASS", "truncated": True, "completeness": "LIMITED"},
        )
        yield SqlAssistantService(TENANT, ACTOR), session, run, tables, calls
        database.drop_tables((SqlAssistantCommand, SqlAssistantSession))


def command(service, session, kind, payload, key="command-1", revision=None):
    current = service.get(session.id)
    return service.command(
        session.id,
        {
            "client_request_id": key,
            "expected_revision": current["revision"] if revision is None else revision,
            "expected_project_version": current["project"]["state_version"],
            "type": kind,
            "payload": payload,
        },
    )


def test_view_change_preserves_sql_run_values_and_filters_all_pages(research):
    service, session, run, _, _ = research
    before = deepcopy(run.rows)
    result = service.result(session.id, run.id)
    view = default_view(result["columns"])
    view["hidden"] = [result["columns"][0]["id"]]
    view["sort"] = {"column_id": result["columns"][0]["id"], "direction": "DESC"}
    view["filters"] = [{"column_id": result["columns"][0]["id"], "operator": "gte", "value": 200}]
    command(service, session, "update_view", {"run_id": run.id, "expected_view_revision": 0, "view": view})
    updated = service.result(session.id, run.id)
    assert updated["sql_hash"] == result["sql_hash"]
    assert updated["run_id"] == run.id
    assert updated["filtered_count"] == 50
    assert updated["rows"][0]["index"] == 249
    assert BusinessDocumentSqlQueryRun.get_by_id(run.id).rows == before
    assert BusinessDocumentJob.select().count() == 0


def test_read_only_owner_can_select_and_customize_saved_result_but_not_calculate(research):
    _, session, run, _, _ = research
    service = SqlAssistantService(TENANT, ACTOR, access_role="READER")
    selected = command(service, session, "select_result", {"run_id": run.id})
    assert selected["session"]["selected_result_id"] == run.id
    with pytest.raises(PermissionDeniedError):
        command(service, session, "execute", {"selected_profile_id": "profile-1"}, key="execute")


def test_repeated_command_is_one_revision_and_changed_payload_conflicts(research):
    service, session, run, _, _ = research
    request = {
        "client_request_id": "same",
        "expected_revision": 1,
        "expected_project_version": service.get(session.id)["project"]["state_version"],
        "type": "select_result",
        "payload": {"run_id": run.id},
    }
    first = service.command(session.id, request)
    replay = service.command(session.id, request)
    assert first["session"]["revision"] == replay["session"]["revision"] == 2
    assert SqlAssistantCommand.select().count() == 1
    with pytest.raises(ConflictError, match="ключ|Ключ"):
        service.command(session.id, {**request, "payload": {"run_id": "other"}})


def test_stale_tab_cannot_overwrite_view_or_selected_branch(research):
    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    with pytest.raises(ConflictError):
        command(service, session, "select_result", {"run_id": run.id}, key="late", revision=1)
    assert service.get(session.id)["selected_result_id"] == run.id


def test_catalog_revocation_blocks_pages_and_replayed_analysis_without_cached_numbers(research, monkeypatch):
    service, session, run, _, _ = research
    result = service.result(session.id, run.id)
    column_id = result["columns"][0]["id"]
    request = {
        "client_request_id": "analysis",
        "expected_revision": 1,
        "expected_project_version": service.get(session.id)["project"]["state_version"],
        "type": "analyze",
        "payload": {"run_id": run.id, "columns": [column_id]},
    }
    service.command(session.id, request)
    assert "data" not in SqlAssistantCommand.get().effect

    async def revoked(*_args):
        raise PermissionDeniedError("Catalog access revoked")

    monkeypatch.setattr(BusinessDocumentSqlQuerySchemaService, "load_entities", revoked)
    with pytest.raises(PermissionDeniedError):
        service.result(session.id, run.id)
    with pytest.raises(PermissionDeniedError):
        service.command(session.id, request)


@pytest.mark.parametrize("tenant,actor", [("foreign", ACTOR), (TENANT, "foreign")])
def test_foreign_session_does_not_expose_results(research, tenant, actor):
    _, session, run, _, _ = research
    foreign = SqlAssistantService(tenant, actor)
    with pytest.raises(BusinessDocumentError):
        foreign.get(session.id)
    with pytest.raises(BusinessDocumentError):
        foreign.result(session.id, run.id)


def test_create_is_atomic_and_idempotent_with_one_initial_job(research):
    service, _, _, _, _ = research
    request = {"question": "Сколько заказов?", "client_request_id": "new-question"}
    first = service.create(request)
    second = service.create(request)
    assert first["id"] == second["id"]
    assert BusinessDocumentJob.select().count() == 1
    assert SqlAssistantSession.select().count() == 2
    with pytest.raises(ConflictError):
        service.create({**request, "question": "Другое содержимое"})


def test_result_expiration_does_not_hide_session_or_trigger_new_sql(research):
    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    BusinessDocumentSqlQueryRun.update(status="EXPIRED", rows=[]).where(BusinessDocumentSqlQueryRun.id == run.id).execute()
    assert service.get(session.id)["id"] == session.id
    with pytest.raises(BusinessDocumentError) as failure:
        service.result(session.id, run.id)
    assert failure.value.code == "SQL_RESULT_EXPIRED"
    assert BusinessDocumentJob.select().count() == 0


def test_manual_draft_apply_creates_new_artifact_without_replacing_selected_result(research):
    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    before = service.get(session.id)
    original = BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload
    candidate = {**deepcopy(original), "sql": original["sql"].replace("ORDER BY", "ORDER BY")}
    saved = command(service, session, "save_draft", {"query": candidate, "base_query_artifact_id": before["project"]["artifact_ids"]["query"]}, key="save")
    assert saved["session"]["draft"]["compiled"]["status"] == "READY"
    assert saved["session"]["project"]["artifact_ids"] == before["project"]["artifact_ids"]
    applied = command(service, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply")
    assert applied["session"]["project"]["artifact_ids"]["query"] != before["project"]["artifact_ids"]["query"]
    assert applied["session"]["selected_result_id"] == run.id
    assert applied["session"]["draft"] is None
    assert service.result(session.id, run.id)["query_artifact_id"] == "q" * 32
    assert BusinessDocumentJob.select().count() == 0


def test_discard_draft_preserves_view_and_selected_run(research):
    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    original = BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload
    command(service, session, "save_draft", {"query": original, "base_query_artifact_id": "q" * 32}, key="save")
    discarded = command(service, session, "discard_draft", {}, key="discard")
    assert discarded["session"]["draft"] is None
    assert discarded["session"]["selected_result_id"] == run.id
    assert discarded["session"]["project"]["artifact_ids"]["query"] == "q" * 32


def test_newly_completed_run_does_not_replace_manual_history_selection(research):
    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    values = {key: value for key, value in run.__data__.items() if key not in {"id", "create_time", "create_date", "update_time", "update_date"}}
    BusinessDocumentSqlQueryRun.create(id="late-run", **values)
    assert service.get(session.id)["selected_result_id"] == run.id


def test_research_sessions_do_not_appear_in_the_old_sql_project_registry(research):
    from api.apps.business_documents.sql_query_agents import BusinessDocumentSqlAgentService

    assert BusinessDocumentSqlAgentService.list_projects(TENANT, ACTOR) == []


def test_derived_python_result_cannot_claim_sql_formula_identity(research):
    service, session, run, _, _ = research
    values = {key: value for key, value in run.__data__.items() if key not in {"id", "create_time", "create_date", "update_time", "update_date"}}
    values.update(kind="PYTHON", source_run_id=run.id, rows=[[999 for _ in run.columns]], row_count=1)
    BusinessDocumentSqlQueryRun.create(id="derived-run", **values)
    with pytest.raises(BusinessDocumentError) as failure:
        service.result(session.id, "derived-run")
    assert failure.value.code == "SQL_SOURCE_RESULT_REQUIRED"


def test_structured_filter_trees_survive_save_apply_and_preserve_historical_result(research):
    from test.unit_test.business_documents.test_sql_assistant_query import candidate

    service, session, run, _, _ = research
    command(service, session, "select_result", {"run_id": run.id})
    query = candidate()
    saved = command(service, session, "save_draft", {"query": query, "base_query_artifact_id": "q" * 32}, key="save-tree")
    assert saved["session"]["draft"]["query"] == query
    assert "HAVING SUM" in saved["session"]["draft"]["compiled"]["sql"]
    applied = command(service, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply-tree")
    assert applied["session"]["structured_query"] == query
    assert service.get(session.id)["structured_query"] == query
    assert applied["session"]["selected_result_id"] == run.id
    assert service.result(session.id, run.id)["query_artifact_id"] == "q" * 32
    assert BusinessDocumentJob.select().count() == 0


@pytest.fixture
def approved(research, monkeypatch):
    from api.db.services.managed_resource_service import ManagedResourceService
    from test.unit_test.business_documents.test_sql_assistant_definitions import contract
    from test.unit_test.business_documents.test_sql_assistant_query import candidate

    monkeypatch.setattr(ManagedResourceService, "owner_id", lambda: "registry-owner")
    _, session, run, tables, calls = research
    admin = SqlAssistantService(TENANT, ACTOR, is_admin=True)
    payload = {
        "definition_id": None,
        "expected_version": 0,
        "title": "Сумма по клиентам",
        "description": "Сумма положительных оплаченных или отменённых заказов",
        "query": candidate(),
        "contract": contract(),
        "base_query_artifact_id": "q" * 32,
    }
    result = command(admin, session, "publish_definition", payload, key="publish-1")
    yield admin, session, run, result["effect"]["definition"], payload, tables


def test_approval_is_admin_only_and_versions_are_append_only(approved):
    admin, session, _, definition, payload, _ = approved
    ordinary = SqlAssistantService(TENANT, ACTOR)
    with pytest.raises(PermissionDeniedError):
        command(ordinary, session, "publish_definition", payload, key="not-admin")
    with pytest.raises(PermissionDeniedError):
        command(ordinary, session, "retire_definition", {"definition_id": definition["definition_id"], "expected_version": 1}, key="not-admin-retire")
    before = SqlAssistantDefinitionVersion.get_by_id(definition["version_id"]).payload
    second = {**payload, "definition_id": definition["definition_id"], "expected_version": 1, "description": "Уточнённое описание"}
    response = command(admin, session, "publish_definition", second, key="publish-2")
    assert response["effect"]["definition"]["version"] == 2
    assert SqlAssistantDefinitionVersion.get_by_id(definition["version_id"]).payload == before
    assert SqlAssistantDefinitionVersion.select().count() == 2
    with pytest.raises(ConflictError, match="изменено"):
        command(admin, session, "publish_definition", second, key="stale-publish")


def test_agreed_draft_apply_is_pinned_and_manual_sql_needs_explicit_exploration(approved):
    admin, session, run, definition, _, _ = approved
    command(admin, session, "select_result", {"run_id": run.id}, key="select")
    command(admin, session, "use_definition", {"definition_id": definition["definition_id"], "version": 1}, key="use")
    applied = command(admin, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply")
    assert applied["session"]["mode"] == "AGREED"
    assert applied["session"]["definition_status"] == "DEFINITION_VALIDATED"
    assert applied["session"]["definition_freshness"] == "CURRENT"
    assert applied["session"]["selected_result_id"] == run.id
    assert admin.result(session.id, run.id)["definition_status"] == "NOT_VALIDATED"
    original = BusinessDocumentSqlQueryArtifact.get_by_id("q" * 32).payload
    base = applied["session"]["project"]["artifact_ids"]["query"]
    with pytest.raises(BusinessDocumentError, match="структурирован"):
        command(admin, session, "save_draft", {"base_query_artifact_id": base, "query": original}, key="manual")
    command(admin, session, "start_exploration", {}, key="explore")
    command(admin, session, "save_draft", {"base_query_artifact_id": base, "query": original}, key="save-manual")
    changed = command(admin, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply-manual")
    assert changed["session"]["mode"] == "EXPLORATORY"
    assert changed["session"]["definition_status"] == "NOT_VALIDATED"


def test_superseded_or_retired_definition_blocks_shared_runtime_source(approved):
    from api.apps.business_documents.sql_query_runs import _source
    from api.db.db_models import BusinessDocumentSqlQueryProject

    admin, session, _, definition, payload, _ = approved
    command(admin, session, "use_definition", {"definition_id": definition["definition_id"], "version": 1}, key="use")
    command(admin, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply")
    project = BusinessDocumentSqlQueryProject.get_by_id(session.id)
    _source(project)
    update = {**payload, "definition_id": definition["definition_id"], "expected_version": 1, "base_query_artifact_id": project.query_artifact_id}
    command(admin, session, "publish_definition", update, key="publish-2")
    current = admin.get(session.id)
    assert current["definition_status"] == "DEFINITION_VALIDATED"
    assert current["definition_freshness"] == "SUPERSEDED"
    with pytest.raises(ConflictError) as blocked:
        _source(project)
    assert blocked.value.code == "SQL_DEFINITION_STALE"
    command(admin, session, "retire_definition", {"definition_id": definition["definition_id"], "expected_version": 2}, key="retire")
    assert admin.get(session.id)["definition_freshness"] == "RETIRED"


def test_formula_drift_is_rejected_and_changed_catalog_is_not_current(approved):
    admin, session, _, definition, _, tables = approved
    command(admin, session, "use_definition", {"definition_id": definition["definition_id"], "version": 1}, key="use")
    current = admin.get(session.id)
    query = deepcopy(current["draft"]["query"])
    query["select"][1]["kind"] = "avg"
    with pytest.raises(BusinessDocumentError):
        command(admin, session, "save_draft", {"query": query, "base_query_artifact_id": "q" * 32}, key="drift")
    tables["orders"]["version"] = 2
    assert admin.definitions(session.id)["definitions"][0]["freshness"] == "METADATA_CHANGED"
    with pytest.raises(ConflictError):
        command(admin, session, "apply_draft", {"confirmed": True, "execute": False}, key="stale-apply")


def test_mutable_values_keep_the_same_definition_and_unknown_registry_is_not_current(approved, monkeypatch):
    from api.db.services.managed_resource_service import ManagedResourceConfigurationError, ManagedResourceService

    admin, session, _, definition, _, _ = approved
    command(admin, session, "use_definition", {"definition_id": definition["definition_id"], "version": 1}, key="use")
    query = deepcopy(admin.get(session.id)["draft"]["query"])
    query["filters"][2]["parameter_value"] = "50"
    saved = command(admin, session, "save_draft", {"query": query, "base_query_artifact_id": "q" * 32}, key="save-mutable")
    assert saved["session"]["draft"]["definition_ref"]["version_id"] == definition["version_id"]
    command(admin, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply")

    def unavailable():
        raise ManagedResourceConfigurationError("unavailable")

    monkeypatch.setattr(ManagedResourceService, "owner_id", unavailable)
    current = admin.get(session.id)
    assert current["definition_status"] == "NOT_VALIDATED"
    assert current["definition_freshness"] == "UNKNOWN"


def test_api_and_worker_reject_retired_definition_before_any_physical_query(approved, monkeypatch):
    from api.apps.business_documents import sql_query_runs
    from api.db.db_models import BusinessDocumentSqlQueryProject
    from common.time_utils import current_timestamp

    admin, session, _, definition, _, _ = approved
    command(admin, session, "use_definition", {"definition_id": definition["definition_id"], "version": 1}, key="use")
    command(admin, session, "apply_draft", {"confirmed": True, "execute": False}, key="apply")
    project = BusinessDocumentSqlQueryProject.get_by_id(session.id)
    compilation, _ = sql_query_runs._source(project)
    command(admin, session, "retire_definition", {"definition_id": definition["definition_id"], "expected_version": 1}, key="retire")
    physical_calls = []
    monkeypatch.setattr(sql_query_runs, "explain_postgres", lambda *_args, **_kwargs: physical_calls.append("explain"))
    monkeypatch.setattr(sql_query_runs, "execute_postgres", lambda *_args, **_kwargs: physical_calls.append("execute"))
    with pytest.raises(ConflictError) as api:
        sql_query_runs.BusinessDocumentSqlRunService.run(
            TENANT, ACTOR, session.id, {"schema_version": "1", "expected_state_version": project.state_version, "idempotency_key": "api-stale", "selected_profile_id": None}, is_admin=True
        )
    assert api.value.code == "SQL_DEFINITION_STALE"
    assert admin.preflight(session.id)["blocker"]["code"] == "SQL_DEFINITION_STALE"
    job = BusinessDocumentJob.create(
        id="definition-job",
        tenant_id=TENANT,
        document_id=session.id,
        job_type="SQL_QUERY_EXECUTE",
        status="RUNNING",
        dedupe_key="definition-worker",
        source_state_version=project.state_version,
        available_at=current_timestamp(),
        correlation_id="definition-correlation",
        payload={"actor_id": ACTOR, "run_id": "definition-run", "selected_profile_id": None},
        lease_owner="definition-worker",
        lease_token="definition-lease",
        lease_expires_at=current_timestamp() + 60_000,
    )
    BusinessDocumentSqlQueryRun.create(
        id="definition-run", tenant_id=TENANT, project_id=session.id, compilation_id=compilation.id, profile_id="qa-profile", profile_version=1, job_id=job.id, status="QUEUED"
    )
    monkeypatch.setattr(sql_query_runs, "_current_actor_access", lambda _actor: ("AUTHOR_CREATOR", True))
    with pytest.raises(ConflictError) as worker:
        sql_query_runs.BusinessDocumentSqlRunService.execute_job(job, "definition-worker", job.lease_token)
    assert worker.value.code == "SQL_DEFINITION_STALE"
    assert physical_calls == []
