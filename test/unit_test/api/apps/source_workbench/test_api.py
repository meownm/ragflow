"""Smoke tests for the source-workspace HTTP boundary."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from quart import Blueprint, Quart, request


REPO_ROOT = Path(__file__).resolve().parents[5]
pytestmark = [pytest.mark.p1, pytest.mark.asyncio]


@pytest.fixture()
def route_app(monkeypatch):
    apps = ModuleType("api.apps")
    apps.current_user = SimpleNamespace(id="owner-a")
    apps.login_required = lambda function: function
    monkeypatch.setitem(sys.modules, "api.apps", apps)

    calls = []

    class FakeService:
        async def chat(self, owner_id, workspace_id, data):
            calls.append((owner_id, workspace_id, data))
            if data.get("question") == "invalid citation":
                raise FakeError("INVALID_CITATION", "Citation is outside the evidence", 502)
            return {"answer": "Answer [1]", "sources": [], "version": 2}

        async def process_stream(self, owner_id, workspace_id, data):
            calls.append((owner_id, workspace_id, data))

            async def events():
                yield {"event": "status", "stage": "loading", "message": "Loading", "current": 0, "total": 1}
                if data.get("prompt") == "fail":
                    raise FakeError("MODEL_FAILED", "Model failed", 502)
                yield {"event": "delta", "text": "Draft [1]"}
                yield {"event": "done", "text": "Draft [1]", "version": 2, "processed": 1, "total": 1}

            return events()

    class FakeError(Exception):
        def __init__(self, code, message, status=422):
            super().__init__(message)
            self.code = code
            self.message = message
            self.status = status

    service = ModuleType("api.source_workbench.service")
    service.SourceWorkspaceError = FakeError
    monkeypatch.setitem(sys.modules, service.__name__, service)
    adapters = ModuleType("api.source_workbench.adapters")
    adapters.build_source_workspace_service = FakeService
    monkeypatch.setitem(sys.modules, adapters.__name__, adapters)

    api_utils = ModuleType("api.utils.api_utils")
    api_utils.get_request_json = lambda: request.get_json()
    monkeypatch.setitem(sys.modules, api_utils.__name__, api_utils)

    module_name = "source_workbench_api_boundary_test"
    spec = spec_from_file_location(module_name, REPO_ROOT / "api" / "apps" / "restful_apis" / "source_workbench_api.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    module.manager = Blueprint(module_name, module_name)
    spec.loader.exec_module(module)
    app = Quart(__name__)
    app.register_blueprint(module.manager)
    return app, calls


async def test_chat_passes_authenticated_owner_and_workspace(route_app):
    app, calls = route_app
    response = await app.test_client().post(
        "/source-workspaces/workspace-1/chat",
        json={"question": "Question", "expected_version": 2},
    )
    assert response.status_code == 200
    assert (await response.get_json())["data"]["answer"] == "Answer [1]"
    assert calls == [("owner-a", "workspace-1", {"question": "Question", "expected_version": 2})]


async def test_chat_rejects_non_object_json(route_app):
    app, calls = route_app
    response = await app.test_client().post("/source-workspaces/workspace-1/chat", json=[])
    assert response.status_code == 422
    assert (await response.get_json())["data"]["error_code"] == "INVALID_INPUT"
    assert calls == []


async def test_chat_invalid_citation_uses_existing_error_envelope(route_app):
    app, _ = route_app
    response = await app.test_client().post(
        "/source-workspaces/workspace-1/chat",
        json={"question": "invalid citation", "expected_version": 2},
    )
    assert response.status_code == 502
    body = await response.get_json()
    assert body["code"] == 502
    assert body["data"]["error_code"] == "INVALID_CITATION"


async def test_process_streams_authenticated_owner_and_input(route_app):
    app, calls = route_app
    data = {"mode": "sequential", "prompt": "Revise", "expected_version": 2}
    response = await app.test_client().post("/source-workspaces/workspace-1/process", json=data)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    body = (await response.get_data()).decode()
    assert 'event: delta\ndata: {"event": "delta", "text": "Draft [1]"}' in body
    assert 'event: done\ndata: {"event": "done"' in body
    assert calls == [("owner-a", "workspace-1", data)]


async def test_process_reports_model_failure_as_terminal_stream_event(route_app):
    app, _ = route_app
    response = await app.test_client().post("/source-workspaces/workspace-1/process", json={"mode": "all", "prompt": "fail", "expected_version": 2})
    body = (await response.get_data()).decode()
    assert 'event: error\ndata: {"event": "error", "code": "MODEL_FAILED"' in body
    assert "event: done" not in body
