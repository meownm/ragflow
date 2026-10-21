"""Actual history HTTP handlers with controlled identity/catalog and SQLite."""

import asyncio

import pytest

from test.unit_test.api.apps.business_documents.test_sql_query_history import ACTOR, Agents, command, project as stored_project, snapshot, source as stored_source
from test.unit_test.api.apps.business_documents.test_sql_query_exports import catalog as stored_catalog
from test.unit_test.api.apps.business_documents.test_business_document_api import route_app as isolated_routes
from api.apps.business_documents.sql_query_schema import BusinessDocumentSqlQuerySchemaService as Schema
from api.db.db_models import BusinessDocumentSqlQueryArtifact as Artifact, BusinessDocumentSqlQueryProject as Project, BusinessDocumentSqlQueryRun as Run
from business_documents.application.errors import BusinessDocumentError, PermissionDeniedError


@pytest.fixture
def project():
    yield from stored_project.__wrapped__()


@pytest.fixture
def source(project, monkeypatch):
    yield from stored_source.__wrapped__(project, monkeypatch)


@pytest.fixture
def catalog(source, monkeypatch):
    return stored_catalog.__wrapped__(source, monkeypatch)


@pytest.fixture
def route_app(monkeypatch):
    yield from isolated_routes.__wrapped__(monkeypatch)


@pytest.fixture
def http(source, route_app, monkeypatch):
    project, _, _ = source
    app, module = route_app
    for table in Agents.model_tables():
        if "tenant_id" in table._meta.fields:
            table.update(tenant_id=ACTOR).where(table.tenant_id == project.tenant_id).execute()
    monkeypatch.setattr(module.current_user, "id", ACTOR)
    monkeypatch.setattr(module.current_user, "business_document_role", "AUTHOR_CREATOR", raising=False)
    database = Run._meta.database
    connection = database.connection()

    async def dispatch(function, *args, **kwargs):
        def invoke():
            database._state.set_connection(connection)
            try:
                return function(*args, **kwargs)
            finally:
                database._state.reset()

        return await asyncio.to_thread(invoke)

    monkeypatch.setattr(module, "thread_pool_exec", dispatch)
    return app.test_client(), module, project.id


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 403, 410, 422])
async def test_sql_preview_responses_disable_browser_cache(http, monkeypatch, status):
    client, module, project_id = http

    def preview(*_args):
        if status != 200:
            raise BusinessDocumentError("SQL_RESULT_EXPIRED" if status == 410 else "SQL_RESULT_DENIED", "Private result is unavailable", status)
        return {"run_id": "private-result", "columns": ["value"], "rows": [[42]]}

    monkeypatch.setattr(module.BusinessDocumentSqlRunService, "preview", staticmethod(preview))
    response = await client.get(f"/business-documents/sql-query/projects/{project_id}/runs/private-result/preview")
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_sql_cache_policy_does_not_change_other_document_responses(http):
    client, _, _ = http
    response = await client.get("/business-documents/capabilities")
    assert response.status_code == 200
    assert "Cache-Control" not in response.headers


@pytest.mark.asyncio
async def test_restore_http_replay_and_reader_denial_do_not_run_sql(http, monkeypatch):
    client, module, project_id = http
    path = f"/business-documents/sql-query/projects/{project_id}/query-history/restore"
    response = await client.post(path, json=command())
    assert response.status_code == 200, await response.get_json()
    data = (await response.get_json())["data"]
    assert data["project"]["artifact_ids"]["query"] != "q" * 32
    replay = await client.post(path, json=command())
    assert (await replay.get_json())["data"] == data
    assert Run.select().count() == 0
    monkeypatch.setattr(module.current_user, "business_document_role", "READER")
    denied = await client.post(path, json=command())
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_history_graph_http_uses_frozen_schema_with_current_catalog_permission(http, catalog, monkeypatch):
    client, module, project_id = http
    catalog["orders"].update(version=2, fqn="synthetic.qa.public.renamed_orders", technical_name="renamed_orders")
    monkeypatch.setattr(module.current_user, "business_document_role", "READER")
    path = f"/business-documents/sql-query/projects/{project_id}/graph"
    request = {"schema_version": "1", "compilation_id": "c" * 32, "query_artifact_id": "q" * 32, "depth": 1}
    response = await client.post(path, json=request)
    assert response.status_code == 200, await response.get_json()
    graph = (await response.get_json())["data"]
    assert next(node for node in graph["nodes"] if node["membership"] == "QUERY")["technical_name"] == "orders"
    assert {node["membership"] for node in graph["nodes"]} == {"QUERY", "RELATED"}

    async def denied(*_args):
        raise PermissionDeniedError("Current catalog access revoked")

    monkeypatch.setattr(Schema, "load_entities", denied)
    assert (await client.post(path, json=request)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["graph", "query-history/restore", "query-history/context"])
async def test_history_routes_reject_invalid_json_without_mutation(http, action):
    client, _, project_id = http
    path = f"/business-documents/sql-query/projects/{project_id}/{action}"
    assert (await client.post(path, json=[])).status_code == 422
    assert (await client.post(path, data="{", headers={"Content-Type": "application/json"})).status_code == 422


@pytest.mark.asyncio
async def test_full_historical_context_http_is_reader_safe_and_rechecks_unused_table(http, monkeypatch):
    client, module, project_id = http
    monkeypatch.setattr(module.current_user, "business_document_role", "READER")
    path = f"/business-documents/sql-query/projects/{project_id}/query-history/context"
    request = {"schema_version": "1", "compilation_id": "c" * 32, "query_artifact_id": "q" * 32}
    before = snapshot()
    response = await client.post(path, json=request)
    assert response.status_code == 200, await response.get_json()
    context = (await response.get_json())["data"]
    assert context["requirements"]["id"] == "r" * 32
    assert len(context["schema"]["snapshot"]["tables"]) == 8
    visible = Schema.load_entities

    async def denied(actor, command, *args):
        if "customers" in command["entity_ids"]:
            raise PermissionDeniedError("Unused accepted table was revoked")
        return await visible(actor, command, *args)

    monkeypatch.setattr(Schema, "load_entities", denied)
    assert (await client.post(path, json=request)).status_code == 403
    assert snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("identity,key", [("r" * 32, "requirements"), ("s" * 32, "schema")])
async def test_history_http_reads_accepted_artifact_without_compilation(http, monkeypatch, identity, key):
    client, module, project_id = http
    monkeypatch.setattr(module.current_user, "business_document_role", "READER")
    Artifact.delete().where(Artifact.kind.in_(("QUERY", "COMPILATION"))).execute()
    Project.update(requirements_artifact_id=None, schema_artifact_id=None, query_artifact_id=None).where(Project.id == project_id).execute()
    before = snapshot()
    response = await client.post(f"/business-documents/sql-query/projects/{project_id}/query-history/context", json={"schema_version": "1", "artifact_id": identity})
    assert response.status_code == 200, await response.get_json()
    packet = (await response.get_json())["data"]
    assert packet[key]["id"] == identity and packet["source"]["artifact_id"] == identity
    assert snapshot() == before
