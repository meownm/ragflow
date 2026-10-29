"""Actual Quart transport; service auth/state behavior has separate tests."""

import pytest

from business_documents.application.errors import BusinessDocumentError, PermissionDeniedError
from test.unit_test.api.apps.business_documents.test_business_document_api import ACTOR, route_app as shared_route_app


@pytest.fixture
def route_app(monkeypatch):
    yield from shared_route_app.__wrapped__(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,discard", [("cancel", False), ("discard", True)])
async def test_stop_and_discard_are_distinct_transport_commands(route_app, monkeypatch, operation, discard):
    app, module = route_app
    calls = []
    monkeypatch.setattr(module.BusinessDocumentSqlRunService, "cancel_result", staticmethod(lambda *args, **kwargs: calls.append((args, kwargs)) or {"rows_status": "READY"}))
    body = {"schema_version": "1", "expected_state_version": 2, "idempotency_key": "stop", "run_id": "r1"}
    response = await app.test_client().post(f"/business-documents/sql-query/projects/p1/runs/r1/{operation}", json=body)
    assert response.status_code == 200
    assert calls[0][0][:4] == (ACTOR, ACTOR, "p1", body)
    assert calls[0][1] == {"discard": discard}


@pytest.mark.asyncio
@pytest.mark.parametrize("action,method,status", [("update_project", "patch", 200), ("copy_project", "post", 201)])
async def test_registry_commands_bind_actor_and_keep_versioned_payload(route_app, monkeypatch, action, method, status):
    app, module = route_app
    calls = []
    monkeypatch.setattr(module, "_is_admin", lambda: False)
    monkeypatch.setattr(module, "_access_role", lambda: "AUTHOR_CREATOR")
    monkeypatch.setattr(module.BusinessDocumentSqlAgentService, action, staticmethod(lambda *args: calls.append(args) or {"id": "project-1"}))
    body = {"schema_version": "1", "expected_state_version": 7, "idempotency_key": "registry-1", "title": "Name"}
    url = "/business-documents/sql-query/projects/project-1" + ("/copy" if action == "copy_project" else "")
    response = await getattr(app.test_client(), method)(url, json=body)
    assert response.status_code == status
    assert calls == [(ACTOR, ACTOR, "project-1", body, False, "AUTHOR_CREATOR")]


@pytest.mark.asyncio
async def test_registry_search_uses_authenticated_scope_and_strict_archive_boolean(route_app, monkeypatch):
    app, module = route_app
    calls = []
    monkeypatch.setattr(module, "_is_admin", lambda: False)
    monkeypatch.setattr(module, "_access_role", lambda: "AUTHOR_CREATOR")
    monkeypatch.setattr(module.BusinessDocumentSqlAgentService, "list_projects", staticmethod(lambda *args: calls.append(args) or []))
    response = await app.test_client().get("/business-documents/sql-query/projects?search=50%25&archived=true")
    assert response.status_code == 200
    assert calls == [(ACTOR, ACTOR, False, "AUTHOR_CREATOR", "50%", True)]
    assert (await app.test_client().get("/business-documents/sql-query/projects?archived=1")).status_code == 422
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix,method", [("", "patch"), ("/copy", "post")])
async def test_registry_commands_reject_malformed_json(route_app, suffix, method):
    app, _ = route_app
    response = await getattr(app.test_client(), method)("/business-documents/sql-query/projects/project-1" + suffix, data="invalid", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_connection_put_uses_authenticated_project_scope_and_versioned_body(route_app, monkeypatch):
    app, module = route_app
    calls = []

    def configure(*args):
        calls.append(args)
        return {"id": "project-1", "execution_profile_id": "profile-1", "state_version": 8}

    monkeypatch.setattr(module, "_is_admin", lambda: False)
    monkeypatch.setattr(module, "_access_role", lambda: "AUTHOR_CREATOR")
    monkeypatch.setattr(module.BusinessDocumentSqlRunService, "configure_connection", staticmethod(configure))
    body = {"schema_version": "1", "expected_state_version": 7, "idempotency_key": "source-1", "selected_profile_id": "profile-1"}
    response = await app.test_client().put("/business-documents/sql-query/projects/project-1/connection", json=body)
    assert response.status_code == 200
    assert (await response.get_json())["data"]["execution_profile_id"] == "profile-1"
    assert calls == [(ACTOR, ACTOR, "project-1", body, False, "AUTHOR_CREATOR")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 409, 503])
async def test_connection_transport_preserves_authorization_conflicts_and_unavailability(route_app, monkeypatch, status):
    app, module = route_app

    def fail(*args):
        raise BusinessDocumentError("SQL_CONNECTION_FAILURE", "Unavailable", status)

    monkeypatch.setattr(module.BusinessDocumentSqlRunService, "configure_connection", staticmethod(fail))
    response = await app.test_client().put("/business-documents/sql-query/projects/project-1/connection", json={})
    assert response.status_code == status
    assert (await response.get_json())["data"]["error_code"] == "SQL_CONNECTION_FAILURE"


@pytest.mark.asyncio
async def test_connection_transport_rejects_malformed_json(route_app):
    app, _ = route_app
    response = await app.test_client().put("/business-documents/sql-query/projects/project-1/connection", data="invalid", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_connection_probe_uses_authenticated_scope_and_explicit_automatic_choice(route_app, monkeypatch):
    app, module = route_app
    calls = []
    monkeypatch.setattr(module, "_is_admin", lambda: False)
    monkeypatch.setattr(module, "_access_role", lambda: "AUTHOR_CREATOR")
    monkeypatch.setattr(module.BusinessDocumentSqlRunService, "check_connection", staticmethod(lambda *args: calls.append(args) or {"status": "AVAILABLE", "query_checked": False}))
    payload = {"schema_version": "1", "selected_profile_id": None}
    response = await app.test_client().post("/business-documents/sql-query/projects/project-1/connection/check", json=payload)
    assert response.status_code == 200 and (await response.get_json())["data"]["query_checked"] is False
    assert calls == [(ACTOR, ACTOR, "project-1", payload, False, "AUTHOR_CREATOR")]


@pytest.mark.asyncio
async def test_connection_probe_rejects_malformed_json(route_app):
    app, _ = route_app
    response = await app.test_client().post("/business-documents/sql-query/projects/project-1/connection/check", data="invalid", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostic,code,status",
    [
        ("password authentication failed", "SQL_CONNECTION_AUTH_FAILED", 503),
        ("SSL error: certificate verify failed", "SQL_CONNECTION_TLS_FAILED", 503),
        ("Connection refused", "SQL_CONNECTION_NETWORK_FAILED", 503),
        ("timeout expired", "SQL_CONNECTION_TIMEOUT", 504),
    ],
)
async def test_probe_http_error_keeps_family_without_driver_secrets(route_app, monkeypatch, diagnostic, code, status):
    import psycopg2
    from api.apps.business_documents.sql_query_postgres import _connection_failure

    app, module = route_app

    def probe(*args):
        error = psycopg2.OperationalError(f"{diagnostic}; user=private-user password=do-not-export host=qa.invalid")
        raise _connection_failure(error) from error

    monkeypatch.setattr(module.BusinessDocumentSqlRunService, "check_connection", staticmethod(probe))
    response = await app.test_client().post("/business-documents/sql-query/projects/project-1/connection/check", json={"schema_version": "1", "selected_profile_id": None})
    body = await response.get_data(as_text=True)
    assert response.status_code == status and (await response.get_json())["data"]["error_code"] == code
    assert all(value not in body for value in ("private-user", "do-not-export", "qa.invalid"))


@pytest.mark.asyncio
async def test_sql_graph_route_uses_session_actor_and_passes_closed_body_to_owner(route_app, monkeypatch):
    app, module = route_app
    calls = []

    async def graph(actor, payload, admin, role):
        calls.append((actor, payload, admin, role))
        return {"nodes": [], "edges": [], "schema_version": "1"}

    monkeypatch.setattr(module, "_is_admin", lambda: False)
    monkeypatch.setattr(module, "_access_role", lambda: "READER")
    monkeypatch.setattr(module.BusinessDocumentSqlQuerySchemaService, "graph", staticmethod(graph))
    payload = {"schema_version": "1", "entity_ids": ["orders"], "sql": "SELECT o.id FROM public.orders o"}
    response = await app.test_client().post("/business-documents/sql-query/schema/graph", json=payload)
    assert response.status_code == 200
    assert (await response.get_json())["data"]["nodes"] == []
    assert calls == [(ACTOR, payload, False, "READER")]


@pytest.mark.asyncio
async def test_view_get_and_put_use_session_tenant_actor_and_separate_version(route_app, monkeypatch):
    app, module = route_app
    calls = []

    def read(tenant, actor, project, admin, role):
        calls.append(("read", tenant, actor, project, admin, role))
        return {"version": 0, "view": None}

    def save(tenant, actor, project, payload, admin, role):
        calls.append(("save", tenant, actor, project, payload, admin, role))
        return {"version": 1, "view": payload["view"]}

    monkeypatch.setattr(module, "_is_admin", lambda: False)
    monkeypatch.setattr(module, "_access_role", lambda: "READER")
    monkeypatch.setattr(module.BusinessDocumentSqlWorkspaceService, "view", staticmethod(read))
    monkeypatch.setattr(module.BusinessDocumentSqlWorkspaceService, "save_view", staticmethod(save))
    client = app.test_client()
    assert (await client.get("/business-documents/sql-query/projects/project-1/view")).status_code == 200
    payload = {"schema_version": "1", "expected_version": 0, "view": {"mode": "query"}}
    response = await client.put("/business-documents/sql-query/projects/project-1/view", json=payload)
    assert response.status_code == 200
    assert (await response.get_json())["data"]["version"] == 1
    assert calls == [("read", ACTOR, ACTOR, "project-1", False, "READER"), ("save", ACTOR, ACTOR, "project-1", payload, False, "READER")]


@pytest.mark.asyncio
@pytest.mark.parametrize("target,status", [("graph", 403), ("graph", 503), ("view", 403), ("view", 409)])
async def test_transport_preserves_domain_denials_and_conflicts(route_app, monkeypatch, target, status):
    app, module = route_app
    monkeypatch.setattr(module, "_access_role", lambda: "READER")
    error = PermissionDeniedError("Not permitted") if status == 403 else BusinessDocumentError("SQL_FAILURE", "Unavailable or conflict", status)
    if target == "graph":

        async def reject(*args):
            raise error

        monkeypatch.setattr(module.BusinessDocumentSqlQuerySchemaService, "graph", staticmethod(reject))
        response = await app.test_client().post("/business-documents/sql-query/schema/graph", json={"schema_version": "1", "entity_ids": ["orders"]})
    else:

        def reject(*args):
            raise error

        monkeypatch.setattr(module.BusinessDocumentSqlWorkspaceService, "save_view", staticmethod(reject))
        response = await app.test_client().put("/business-documents/sql-query/projects/project-1/view", json={"schema_version": "1"})
    assert response.status_code == status
    assert (await response.get_json())["data"]["error_code"] == error.code
