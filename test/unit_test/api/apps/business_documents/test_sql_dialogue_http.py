"""Real Quart serialization and authentication for dialogue options/errors."""
# Imported pytest fixtures are intentionally requested by registered name.
# ruff: noqa: F811

from functools import wraps
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from quart import Blueprint, Quart, request, jsonify
from peewee import SqliteDatabase

from test.unit_test.api.apps.business_documents.test_sql_assistant_service import ACTOR, command, research, source  # noqa: F401
from test.unit_test.api.apps.business_documents import test_sql_query_revisions as accepted
from business_documents.application.errors import BusinessDocumentError, PermissionDeniedError, ConflictError, ValidationError
from api.db.db_models import BusinessDocumentSqlQueryProject


@pytest.fixture
def project(tmp_path, monkeypatch):
    database = SqliteDatabase(tmp_path / "dialogue-http.sqlite", check_same_thread=False)
    monkeypatch.setattr(accepted, "SqliteDatabase", lambda *_args, **_kwargs: database)
    yield from accepted.project.__wrapped__()


@pytest.fixture
def route(research, monkeypatch):
    service, session, run, tables, _ = research
    api_apps = sys.modules["api.apps"]

    def authenticated(function):
        @wraps(function)
        async def invoke(*args, **kwargs):
            if request.headers.get("Authorization") != "qa":
                return jsonify({"code": 401}), 401
            return await function(*args, **kwargs)

        return invoke

    monkeypatch.setattr(api_apps, "login_required", authenticated, raising=False)
    monkeypatch.setattr(api_apps, "current_user", SimpleNamespace(id=ACTOR), raising=False)
    name = "api.apps.restful_apis.sql_dialogue_boundary_test"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[5] / "api/apps/restful_apis/sql_assistant_api.py")
    module = importlib.util.module_from_spec(spec)
    module.manager = Blueprint("sql_dialogue_boundary_test", name)
    sys.modules[name] = module
    spec.loader.exec_module(module)

    async def inline(function):
        def invoke():
            with session._meta.database.connection_context():
                return function()

        return await asyncio.to_thread(invoke)

    monkeypatch.setattr(module, "_service", lambda: service)
    monkeypatch.setattr(module, "DB", SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, "thread_pool_exec", inline)
    monkeypatch.setattr(module, "wake_business_document_worker", lambda: None)
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    try:
        yield app.test_client(), service, session, tables
    finally:
        sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_options_http_requires_auth_and_returns_typed_current_objects(route):
    client, _, session, _ = route
    path = f"/sql-assistant/sessions/{session.id}/dialogue-options"
    assert (await client.get(path)).status_code == 401
    response = await client.get(path, headers={"Authorization": "qa"})
    assert response.status_code == 200
    data = (await response.get_json())["data"]
    assert data["references"][0]["kind"] == "TABLE"
    assert response.headers["Cache-Control"] == "no-store"
    bad = await client.get(path + "?q=" + "x" * 10001, headers={"Authorization": "qa"})
    assert bad.status_code == 422
    assert (await bad.get_json())["data"]["recovery"] == "EDIT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,recovery",
    [
        (PermissionDeniedError("Denied"), "DENIED"),
        (ConflictError("SQL_DIALOGUE_CONTEXT_CHANGED", "Changed"), "REFRESH"),
        (BusinessDocumentError("SQL_RESULT_ANSWER_UNAVAILABLE", "Unavailable", 503), "RETRY_LATER"),
        (ValidationError("SQL_METRIC_SELECTION_REQUIRED", "Choose", {"metrics": [{"id": "allowed"}]}), "RESELECT"),
    ],
)
async def test_errors_have_actionable_transport_contract(route, monkeypatch, error, recovery):
    client, service, session, _ = route

    def fail(*_args):
        raise error

    monkeypatch.setattr(service, "dialogue_options", fail)
    response = await client.get(f"/sql-assistant/sessions/{session.id}/dialogue-options", headers={"Authorization": "qa"})
    assert response.status_code == error.status
    data = (await response.get_json())["data"]
    assert data["recovery"] == recovery
    assert data["retryable"] == (recovery == "RETRY_LATER")
    if recovery == "RESELECT":
        assert data["metrics"] == [{"id": "allowed"}]


@pytest.mark.asyncio
async def test_initial_mention_is_resolved_by_id_and_replayed_once(route):
    client, _, _, tables = route
    entity = next(iter(tables.values()))
    body = {"question": "Исследуй выбранную таблицу", "client_request_id": "new-mention", "references": [{"kind": "TABLE", "id": entity["id"]}]}
    first = await client.post("/sql-assistant/sessions", json=body, headers={"Authorization": "qa"})
    again = await client.post("/sql-assistant/sessions", json=body, headers={"Authorization": "qa"})
    assert first.status_code == again.status_code == 201
    saved_id = (await first.get_json())["data"]["id"]
    assert saved_id == (await again.get_json())["data"]["id"]
    assert entity["fqn"] in BusinessDocumentSqlQueryProject.get_by_id(saved_id).source_request
    bad = await client.post("/sql-assistant/sessions", json={**body, "client_request_id": "bad", "references": [{**body["references"][0], "title": "forged"}]}, headers={"Authorization": "qa"})
    assert bad.status_code == 422
