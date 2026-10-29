"""Real Quart routes over the isolated pilot service; auth transport is controlled."""

from functools import wraps
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from quart import Blueprint, Quart, request, jsonify
from peewee import SqliteDatabase

from test.unit_test.api.apps.business_documents.test_sql_llm_logs_e1 import pilot as base_pilot, pilot_config as base_config, ACTOR, PERIOD
from api.db.db_models import BusinessDocumentJob
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture
def pilot_config():
    return base_config.__wrapped__()


@pytest.fixture
def pilot_database(tmp_path):
    return SqliteDatabase(tmp_path / "http-metadata.sqlite", check_same_thread=False)


@pytest.fixture
def route(pilot_config, pilot_database, monkeypatch):
    fixtures = base_pilot.__wrapped__(monkeypatch, pilot_config, pilot_database)
    service, _ = next(fixtures)
    api_apps = sys.modules["api.apps"]

    def authenticated(function):
        @wraps(function)
        async def invoke(*args, **kwargs):
            if request.headers.get("Authorization") != "qa":
                return jsonify({"code": 401}), 401
            return await function(*args, **kwargs)

        return invoke

    monkeypatch.setattr(api_apps, "login_required", authenticated, raising=False)
    monkeypatch.setattr(api_apps, "current_user", SimpleNamespace(id=ACTOR, is_superuser=False, business_document_role="AUTHOR_CREATOR"), raising=False)
    name = "api.apps.restful_apis.sql_llm_logs_boundary_test"
    path = Path(__file__).resolve().parents[5] / "api/apps/restful_apis/sql_assistant_api.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.manager = Blueprint("llm_logs_boundary_test", name)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    actors = []

    def factory(*args):
        actors.append(args)
        return service

    async def inline(function, *args, **kwargs):
        def invoke():
            with pilot_database.connection_context():
                return function(*args, **kwargs)

        return await asyncio.to_thread(invoke)

    monkeypatch.setattr(module, "SqlAssistantService", factory)
    monkeypatch.setattr(module, "DB", SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, "thread_pool_exec", inline)
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    try:
        yield app, service, actors
    finally:
        sys.modules.pop(name, None)
        try:
            next(fixtures)
        except StopIteration:
            pass


@pytest.mark.asyncio
async def test_count_http_creates_once_binds_server_actor_and_never_dispatches(route):
    app, _, actors = route
    client = app.test_client()
    body = {"client_request_id": "http-1", **PERIOD}
    first = await client.post("/sql-assistant/llm-log-count", json=body, headers={"Authorization": "qa"})
    again = await client.post("/sql-assistant/llm-log-count", json=body, headers={"Authorization": "qa"})
    assert first.status_code == again.status_code == 201
    assert (await first.get_json())["data"]["id"] == (await again.get_json())["data"]["id"]
    assert actors == [(ACTOR, ACTOR, False, "AUTHOR_CREATOR")] * 2
    assert first.headers["Cache-Control"] == "no-store"
    assert BusinessDocumentJob.select().count() == 0
    forged = await client.post("/sql-assistant/llm-log-count", json={**body, "actor_id": "foreign"}, headers={"Authorization": "qa"})
    assert forged.status_code == 422


@pytest.mark.asyncio
async def test_count_http_requires_decorator_and_a_typed_body(route):
    app, _, actors = route
    client = app.test_client()
    assert (await client.post("/sql-assistant/llm-log-count", json={})).status_code == 401
    assert not actors
    bad = await client.post("/sql-assistant/llm-log-count", data="invalid", headers={"Authorization": "qa", "Content-Type": "application/json"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_unknown_http_never_advises_a_resend(route, monkeypatch):
    app, service, _ = route

    def unavailable(*args):
        raise BusinessDocumentError("SQL_EXECUTION_UNKNOWN", "Unknown; do not resend", 503)

    monkeypatch.setattr(service.runtime, "call", unavailable)
    response = await app.test_client().get("/sql-assistant/sessions/s1/runs/r1/execution", headers={"Authorization": "qa"})
    assert response.status_code == 503
    data = (await response.get_json())["data"]
    assert data["recovery"] == "RECONCILE" and data["retryable"] is False
