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
            return {"answer": "Answer [1]", "sources": [], "version": 2}

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
