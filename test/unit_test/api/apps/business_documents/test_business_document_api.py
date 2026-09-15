#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from quart import Blueprint, Quart


REPO_ROOT = Path(__file__).resolve().parents[5]
if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(REPO_ROOT / "api" / "apps")]
    sys.modules["api.apps"] = api_apps
else:
    api_apps = sys.modules["api.apps"]


ACTOR = "route-owner"


@pytest.fixture()
def route_app(monkeypatch):
    monkeypatch.setattr(api_apps, "current_user", SimpleNamespace(id=ACTOR, is_superuser=False), raising=False)
    monkeypatch.setattr(api_apps, "login_required", lambda function: function, raising=False)
    module_name = "api.apps.restful_apis.business_document_api_boundary_test"
    spec = spec_from_file_location(module_name, REPO_ROOT / "api" / "apps" / "restful_apis" / "business_document_api.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    module.manager = Blueprint("business_document_api_boundary_test", module_name)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    yield app, module
    sys.modules.pop(module_name, None)


@pytest.mark.p0
@pytest.mark.asyncio
async def test_json_routes_reject_non_object_and_malformed_json(route_app):
    app, _module = route_app
    client = app.test_client()
    cases = (
        ("post", "/business-documents", "INVALID_DOCUMENT"),
        ("post", "/business-documents/sql-query/schema/resolve", "INVALID_SQL_SCHEMA_REQUEST"),
        ("post", "/business-documents/sql-query/schema/entities", "INVALID_SQL_SCHEMA_ENTITY_REQUEST"),
        ("post", "/business-documents/sql-query/plan", "INVALID_SQL_QUERY_PLAN_REQUEST"),
        ("post", "/business-documents/sql-query/compile", "INVALID_SQL_QUERY_SPECIFICATION"),
        ("post", "/business-documents/sql-query/projects", "INVALID_SQL_AGENT_PROJECT"),
        ("post", "/business-documents/sql-query/projects/project-1/agent-jobs", "INVALID_SQL_AGENT_REQUEST"),
        (
            "post",
            "/business-documents/sql-query/projects/project-1/proposals/proposal-1/decision",
            "INVALID_SQL_AGENT_DECISION",
        ),
        ("post", "/business-documents/sql-query/execution-profiles", "INVALID_SQL_EXECUTION_PROFILE"),
        ("post", "/business-documents/sql-query/catalog-bindings", "INVALID_SQL_CATALOG_BINDING"),
        ("post", "/business-documents/sql-query/execution-binding/resolve", "INVALID_SQL_EXECUTION_BINDING_REQUEST"),
        ("post", "/business-documents/doc-1/commands", "INVALID_COMMAND_REQUEST"),
        ("put", "/business-documents/doc-1/owner", "INVALID_DOCUMENT_ASSIGNMENT"),
    )
    for method, path, error_code in cases:
        request = getattr(client, method)
        response = await request(path, json=[])
        body = await response.get_json()
        assert response.status_code == 422
        assert body["code"] == 422
        assert body["data"] == {"error_code": error_code, "details": {}}

    for method, path, error_code in cases:
        request = getattr(client, method)
        malformed = await request(path, data="{", headers={"Content-Type": "application/json"})
        malformed_body = await malformed.get_json()
        assert malformed.status_code == 422
        assert malformed_body["data"]["error_code"] == error_code

        empty = await request(path)
        empty_body = await empty.get_json()
        assert empty.status_code == 422
        assert empty_body["data"]["error_code"] == error_code


@pytest.mark.p0
@pytest.mark.asyncio
async def test_routes_pass_tenant_and_owner_in_service_contract_order(route_app, monkeypatch):
    app, module = route_app
    calls = []

    def create_document(tenant_id, actor_id, data, is_admin, access_role):
        calls.append(("create", tenant_id, actor_id, data, is_admin, access_role))
        return {"document_id": "doc-1"}

    def execute_command(tenant_id, actor_id, document_id, data, is_admin, access_role):
        calls.append(("command", tenant_id, actor_id, document_id, data, is_admin, access_role))
        return {"accepted": True, "document_id": document_id}

    def get_document(tenant_id, document_id, actor_id, is_admin, access_role):
        calls.append(("get", tenant_id, document_id, actor_id, is_admin, access_role))
        return {
            "document_id": document_id,
            "owner_id": actor_id,
            "current_revision": {"revision_id": "revision-1", "section_texts": {"5.5": "Метрика"}},
        }

    monkeypatch.setattr(module.BusinessDocumentService, "create_document", staticmethod(create_document))
    monkeypatch.setattr(module.BusinessDocumentService, "execute_command", staticmethod(execute_command))
    monkeypatch.setattr(module.BusinessDocumentService, "get_document", staticmethod(get_document))
    client = app.test_client()
    create_payload = {"schema_version": "1", "document_type": "business_requirements", "title": "T", "idea": "I"}
    command_payload = {
        "schema_version": "1",
        "command_id": "command-1",
        "idempotency_key": "key-1",
        "expected_state_version": 1,
        "type": "REQUEST_INTAKE_ASSESSMENT",
        "payload": {},
    }

    assert (await client.post("/business-documents", json=create_payload)).status_code == 201
    assert (await client.post("/business-documents/doc-1/commands", json=command_payload)).status_code == 200
    get_response = await client.get("/business-documents/doc-1")
    assert get_response.status_code == 200
    assert (await get_response.get_json())["data"]["current_revision"]["section_texts"] == {"5.5": "Метрика"}
    assert calls == [
        ("create", ACTOR, ACTOR, create_payload, False, "AUTHOR_CREATOR"),
        ("command", ACTOR, ACTOR, "doc-1", command_payload, False, "AUTHOR_CREATOR"),
        ("get", ACTOR, "doc-1", ACTOR, False, "AUTHOR_CREATOR"),
    ]


@pytest.mark.p0
@pytest.mark.asyncio
async def test_delete_route_passes_admin_role_to_service(route_app, monkeypatch):
    app, module = route_app
    module.current_user.is_superuser = True
    calls = []

    def delete_document(actor_id, document_id, is_admin, access_role):
        calls.append((actor_id, document_id, is_admin, access_role))
        return {"document_id": document_id, "deleted": True}

    monkeypatch.setattr(module.BusinessDocumentService, "delete_document", staticmethod(delete_document))
    response = await app.test_client().delete("/business-documents/doc-1")

    assert response.status_code == 200
    assert (await response.get_json())["data"] == {"document_id": "doc-1", "deleted": True}
    assert calls == [(ACTOR, "doc-1", True, "AUTHOR_CREATOR")]


@pytest.mark.p0
@pytest.mark.asyncio
async def test_access_filter_and_assignment_routes_pass_role_and_scope(route_app, monkeypatch):
    app, module = route_app
    module.current_user.business_document_role = "EXTENDED_MODERATOR"
    calls = []

    def list_documents(tenant_id, actor_id, page, page_size, is_admin, access_role, scope):
        calls.append(("list", tenant_id, actor_id, page, page_size, is_admin, access_role, scope))
        return {"items": [], "scope": scope}

    def list_access_users(actor_id, is_admin, access_role):
        calls.append(("users", actor_id, is_admin, access_role))
        return {"items": []}

    def assign_document(actor_id, document_id, data, is_admin, access_role):
        calls.append(("assign", actor_id, document_id, data, is_admin, access_role))
        return {"document_id": document_id, "owner_id": data["owner_id"]}

    monkeypatch.setattr(module.BusinessDocumentService, "list_documents", staticmethod(list_documents))
    monkeypatch.setattr(module.BusinessDocumentService, "list_access_users", staticmethod(list_access_users))
    monkeypatch.setattr(module, "assign_business_document", assign_document)
    client = app.test_client()

    assert (await client.get("/business-documents?page=2&page_size=5&scope=mine")).status_code == 200
    assert (await client.get("/business-documents/access/users")).status_code == 200
    assignment = {"owner_id": "author-2", "expected_state_version": 7}
    response = await client.put("/business-documents/doc-1/owner", json=assignment)

    assert response.status_code == 200
    assert calls == [
        ("list", ACTOR, ACTOR, 2, 5, False, "EXTENDED_MODERATOR", "mine"),
        ("users", ACTOR, False, "EXTENDED_MODERATOR"),
        ("assign", ACTOR, "doc-1", assignment, False, "EXTENDED_MODERATOR"),
    ]


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "status", "details"),
    [
        ("DOCUMENT_PERMISSION_DENIED", 403, {}),
        ("USER_NOT_FOUND", 404, {}),
        ("DOCUMENT_NOT_FOUND", 404, {}),
        ("STATE_VERSION_CONFLICT", 409, {"expected": 7, "actual": 8}),
        ("OPERATION_IN_PROGRESS", 409, {}),
        ("INVALID_DOCUMENT_ASSIGNMENT", 422, {}),
    ],
)
async def test_assignment_route_preserves_application_error_envelope(route_app, monkeypatch, error_code, status, details):
    app, module = route_app

    def assign_document(*_args, **_kwargs):
        raise module.BusinessDocumentError(error_code, "assignment failed", status, details)

    monkeypatch.setattr(module, "assign_business_document", assign_document)
    response = await app.test_client().put(
        "/business-documents/doc-1/owner",
        json={"owner_id": "author-2", "expected_state_version": 7},
    )

    assert response.status_code == status
    assert await response.get_json() == {
        "code": status,
        "message": "assignment failed",
        "data": {"error_code": error_code, "details": details},
    }


@pytest.mark.p0
@pytest.mark.asyncio
async def test_catalog_route_returns_the_service_projection(route_app, monkeypatch):
    app, module = route_app
    catalog = {
        "items": [
            {
                "id": "L2-01.01.04.01.01",
                "title": "Разрешённый документ",
                "capability_level": "L5",
            }
        ],
        "total": 1,
    }
    monkeypatch.setattr(module.BusinessDocumentService, "list_catalog", staticmethod(lambda: catalog))

    response = await app.test_client().get("/business-documents/catalog")

    assert response.status_code == 200
    assert (await response.get_json())["data"] == catalog


@pytest.mark.p0
@pytest.mark.asyncio
async def test_capabilities_route_projects_role_without_listing_documents(route_app):
    app, module = route_app
    module.current_user.business_document_role = "AUTHOR_EDITOR"

    response = await app.test_client().get("/business-documents/capabilities")

    assert response.status_code == 200
    assert (await response.get_json())["data"] == {
        "access_role": "AUTHOR_EDITOR",
        "capabilities": {
            "read": True,
            "create": False,
            "edit_own": True,
            "edit_all": False,
            "delete": False,
            "assign": False,
        },
    }


@pytest.mark.p0
@pytest.mark.asyncio
async def test_sql_schema_route_passes_tenant_actor_role_and_payload(route_app, monkeypatch):
    app, module = route_app
    module.current_user.business_document_role = "MODERATOR_CREATOR"
    calls = []

    async def resolve(tenant_id, actor_id, payload, is_admin, access_role):
        calls.append(("resolve", tenant_id, actor_id, payload, is_admin, access_role))
        return {"schema_version": "1", "status": "READY", "resolutions": [], "llm": {"status": "SKIPPED"}}

    async def load_entities(actor_id, payload, is_admin, access_role):
        calls.append(("entities", actor_id, payload, is_admin, access_role))
        return {"schema_version": "1", "status": "READY", "entities": []}

    def compile_query(actor_id, payload, is_admin, access_role):
        calls.append(("compile", actor_id, payload, is_admin, access_role))
        return {"schema_version": "1", "status": "NEEDS_CLARIFICATION", "sql": None}

    async def plan_query(tenant_id, actor_id, payload, is_admin, access_role):
        calls.append(("plan", tenant_id, actor_id, payload, is_admin, access_role))
        return {"schema_version": "1", "status": "FALLBACK", "proposal": None}

    monkeypatch.setattr(module.BusinessDocumentSqlQuerySchemaService, "resolve", staticmethod(resolve))
    monkeypatch.setattr(module.BusinessDocumentSqlQuerySchemaService, "load_entities", staticmethod(load_entities))
    monkeypatch.setattr(module.BusinessDocumentSqlQueryPlanningService, "plan", staticmethod(plan_query))
    monkeypatch.setattr(module.BusinessDocumentSqlQueryService, "compile", staticmethod(compile_query))
    payload = {"terms": ["Заказ"], "requirements": "Нужны заказы", "locale": "ru"}
    entity_payload = {"entity_ids": ["orders"], "locale": "ru"}
    compile_payload = {"schema_version": "1"}
    plan_payload = {"schema_version": "1", "locale": "ru"}

    response = await app.test_client().post("/business-documents/sql-query/schema/resolve", json=payload)
    entity_response = await app.test_client().post("/business-documents/sql-query/schema/entities", json=entity_payload)
    plan_response = await app.test_client().post("/business-documents/sql-query/plan", json=plan_payload)
    compile_response = await app.test_client().post("/business-documents/sql-query/compile", json=compile_payload)

    assert response.status_code == 200
    assert entity_response.status_code == 200
    assert plan_response.status_code == 200
    assert compile_response.status_code == 200
    assert (await response.get_json())["data"]["status"] == "READY"
    assert calls == [
        ("resolve", ACTOR, ACTOR, payload, False, "MODERATOR_CREATOR"),
        ("entities", ACTOR, entity_payload, False, "MODERATOR_CREATOR"),
        ("plan", ACTOR, ACTOR, plan_payload, False, "MODERATOR_CREATOR"),
        ("compile", ACTOR, compile_payload, False, "MODERATOR_CREATOR"),
    ]


@pytest.mark.p0
@pytest.mark.asyncio
async def test_sql_agent_project_routes_are_thin_and_wake_worker_after_enqueue(route_app, monkeypatch):
    app, module = route_app
    module.current_user.business_document_role = "MODERATOR_CREATOR"
    calls = []

    def create_project(tenant_id, actor_id, payload, is_admin, access_role):
        calls.append(("create", tenant_id, actor_id, payload, is_admin, access_role))
        return {"id": "project-1"}

    def list_projects(tenant_id, actor_id, is_admin, access_role):
        calls.append(("list", tenant_id, actor_id, is_admin, access_role))
        return [{"id": "project-1"}]

    def get_project(tenant_id, actor_id, project_id, is_admin, access_role):
        calls.append(("get", tenant_id, actor_id, project_id, is_admin, access_role))
        return {"id": project_id}

    def request_agent(tenant_id, actor_id, project_id, payload, is_admin, access_role):
        calls.append(("request", tenant_id, actor_id, project_id, payload, is_admin, access_role))
        return {"id": project_id, "operation_state": "RUNNING"}

    def decide(tenant_id, actor_id, project_id, proposal_id, payload, is_admin, access_role):
        calls.append(("decide", tenant_id, actor_id, project_id, proposal_id, payload, is_admin, access_role))
        return {"id": project_id, "operation_state": "IDLE"}

    service = module.BusinessDocumentSqlAgentService
    monkeypatch.setattr(service, "create_project", staticmethod(create_project))
    monkeypatch.setattr(service, "list_projects", staticmethod(list_projects))
    monkeypatch.setattr(service, "get_project", staticmethod(get_project))
    monkeypatch.setattr(service, "request_agent", staticmethod(request_agent))
    monkeypatch.setattr(service, "decide_proposal", staticmethod(decide))
    monkeypatch.setattr(module, "wake_business_document_worker", lambda: calls.append(("wake",)))
    client = app.test_client()
    project_payload = {"schema_version": "1", "title": "T", "source_request": "R", "locale": "ru"}
    agent_payload = {
        "schema_version": "1",
        "expected_state_version": 1,
        "idempotency_key": "run-1",
        "kind": "REQUIREMENTS",
        "payload": {},
    }
    decision_payload = {
        "schema_version": "1",
        "expected_state_version": 2,
        "idempotency_key": "accept-1",
        "decision": "ACCEPT",
        "artifact_payload": None,
    }

    assert (await client.post("/business-documents/sql-query/projects", json=project_payload)).status_code == 201
    assert (await client.get("/business-documents/sql-query/projects")).status_code == 200
    assert (await client.get("/business-documents/sql-query/projects/project-1")).status_code == 200
    assert (await client.post("/business-documents/sql-query/projects/project-1/agent-jobs", json=agent_payload)).status_code == 202
    assert (
        await client.post(
            "/business-documents/sql-query/projects/project-1/proposals/proposal-1/decision",
            json=decision_payload,
        )
    ).status_code == 200
    assert calls == [
        ("create", ACTOR, ACTOR, project_payload, False, "MODERATOR_CREATOR"),
        ("list", ACTOR, ACTOR, False, "MODERATOR_CREATOR"),
        ("get", ACTOR, ACTOR, "project-1", False, "MODERATOR_CREATOR"),
        ("request", ACTOR, ACTOR, "project-1", agent_payload, False, "MODERATOR_CREATOR"),
        ("wake",),
        ("decide", ACTOR, ACTOR, "project-1", "proposal-1", decision_payload, False, "MODERATOR_CREATOR"),
    ]


@pytest.mark.p0
@pytest.mark.asyncio
async def test_sql_execution_registry_routes_keep_admin_and_author_contracts_separate(route_app, monkeypatch):
    app, module = route_app
    module.current_user.is_superuser = True
    module.current_user.business_document_role = "MODERATOR_CREATOR"
    calls = []

    def list_connectors(actor_id, is_admin):
        calls.append(("connectors", actor_id, is_admin))
        return {"schema_version": "1", "items": []}

    def list_profiles(actor_id, is_admin):
        calls.append(("profiles", actor_id, is_admin))
        return {"schema_version": "1", "items": []}

    def create_profile(actor_id, payload, is_admin):
        calls.append(("profile.create", actor_id, payload, is_admin))
        return {"id": "profile-1", "version": 1}

    def update_profile(actor_id, profile_id, payload, is_admin):
        calls.append(("profile.update", actor_id, profile_id, payload, is_admin))
        return {"id": profile_id, "version": 2}

    def list_bindings(actor_id, is_admin):
        calls.append(("bindings", actor_id, is_admin))
        return {"schema_version": "1", "items": []}

    def create_binding(actor_id, payload, is_admin):
        calls.append(("binding.create", actor_id, payload, is_admin))
        return {"id": "binding-1", "version": 1}

    def update_binding(actor_id, binding_id, payload, is_admin):
        calls.append(("binding.update", actor_id, binding_id, payload, is_admin))
        return {"id": binding_id, "version": 2}

    def resolve_binding(actor_id, payload, is_admin, access_role):
        calls.append(("binding.resolve", actor_id, payload, is_admin, access_role))
        return {"schema_version": "1", "status": "BOUND", "selection": {"profile": {"id": "profile-1"}}}

    service = module.BusinessDocumentSqlExecutionRegistryService
    monkeypatch.setattr(service, "list_connectors", staticmethod(list_connectors))
    monkeypatch.setattr(service, "list_profiles", staticmethod(list_profiles))
    monkeypatch.setattr(service, "create_profile", staticmethod(create_profile))
    monkeypatch.setattr(service, "update_profile", staticmethod(update_profile))
    monkeypatch.setattr(service, "list_bindings", staticmethod(list_bindings))
    monkeypatch.setattr(service, "create_binding", staticmethod(create_binding))
    monkeypatch.setattr(service, "update_binding", staticmethod(update_binding))
    monkeypatch.setattr(service, "resolve", staticmethod(resolve_binding))
    client = app.test_client()
    profile_payload = {"schema_version": "1", "name": "Warehouse RO"}
    binding_payload = {"schema_version": "1", "catalog_schema": "dwh"}
    resolve_payload = {"schema_version": "1", "schema_snapshot": {"status": "READY"}}

    assert (await client.get("/business-documents/sql-query/execution-connectors")).status_code == 200
    assert (await client.get("/business-documents/sql-query/execution-profiles")).status_code == 200
    assert (await client.post("/business-documents/sql-query/execution-profiles", json=profile_payload)).status_code == 201
    assert (await client.put("/business-documents/sql-query/execution-profiles/profile-1", json=profile_payload)).status_code == 200
    assert (await client.get("/business-documents/sql-query/catalog-bindings")).status_code == 200
    assert (await client.post("/business-documents/sql-query/catalog-bindings", json=binding_payload)).status_code == 201
    assert (await client.put("/business-documents/sql-query/catalog-bindings/binding-1", json=binding_payload)).status_code == 200
    assert (await client.post("/business-documents/sql-query/execution-binding/resolve", json=resolve_payload)).status_code == 200

    assert calls == [
        ("connectors", ACTOR, True),
        ("profiles", ACTOR, True),
        ("profile.create", ACTOR, profile_payload, True),
        ("profile.update", ACTOR, "profile-1", profile_payload, True),
        ("bindings", ACTOR, True),
        ("binding.create", ACTOR, binding_payload, True),
        ("binding.update", ACTOR, "binding-1", binding_payload, True),
        ("binding.resolve", ACTOR, resolve_payload, True, "MODERATOR_CREATOR"),
    ]
