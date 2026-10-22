"""Real anonymous auth functions and TZ routes, without application bootstrap."""

import ast
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from functools import wraps
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import ParamSpec, TypeVar

import pytest
from quart import Blueprint, Quart, current_app, g, has_app_context, jsonify, request, session
from quart_auth import Unauthorized
from werkzeug.local import LocalProxy

from common.constants import RetCode
from common.observability import get_log_context, new_id

ROOT = Path(__file__).resolve().parents[3]
BASE = "/source-workspaces/workspace/technical-specifications"
ENDPOINTS = [
    ("GET", ""),
    ("POST", ""),
    ("GET", "/doc"),
    ("PUT", "/doc"),
    ("GET", "/doc/draft"),
    ("PUT", "/doc/draft"),
    ("GET", "/doc/revisions"),
    ("GET", "/doc/revisions/1"),
    ("GET", "/eva-pages"),
    ("POST", "/import-eva"),
    ("GET", "/import-eva/operation"),
    ("POST", "/import-eva/cancel"),
    ("GET", "/business-sources"),
    ("GET", "/doc/alignment"),
    ("POST", "/doc/alignment"),
    *[("POST", "/doc/" + action) for action in ["chat", "comments", "apply", "restore", "export", "pull", "resolve-eva", "publish-eva", "connect-eva", "approve"]],
]


def _install_application_error_handlers(app):
    namespace = {"logging": logging, "RetCode": RetCode, "has_app_context": has_app_context, "jsonify": jsonify, "get_log_context": get_log_context, "new_id": new_id}
    for relative, names in [
        ("api/utils/api_utils.py", {"_safe_jsonify", "get_json_result", "server_error_response"}),
        ("api/apps/__init__.py", {"unauthorized_quart_auth"}),
    ]:
        path = ROOT / relative
        functions = [node for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        assert {node.name for node in functions} == names
        for node in functions:
            node.decorator_list = []
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    app.errorhandler(Exception)(namespace["server_error_response"])
    app.errorhandler(Unauthorized)(namespace["unauthorized_quart_auth"])


@pytest.fixture
def anonymous_client(monkeypatch):
    # Execute the production functions themselves. Importing api.apps would start
    # the whole server and initialize shared service settings during collection.
    path = ROOT / "api/apps/__init__.py"
    names = {"_normalize_auth_types", "_load_user_from_session", "_load_user", "login_required"}
    functions = [node for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    namespace = {
        "logging": logging,
        "os": os,
        "time": time,
        "wraps": wraps,
        "Callable": Callable,
        "Iterable": Iterable,
        "Awaitable": Awaitable,
        "P": ParamSpec("P"),
        "T": TypeVar("T"),
        "request": request,
        "g": g,
        "session": session,
        "current_app": current_app,
        "QuartAuthUnauthorized": Unauthorized,
        "AUTH_JWT": "JWT",
        "AUTH_API": "API",
        "AUTH_BETA": "BETA",
        "DEFAULT_AUTH_TYPES": ("JWT", "API"),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    apps = ModuleType("api.apps")
    apps.current_user = LocalProxy(namespace["_load_user"])
    apps.login_required = namespace["login_required"]
    monkeypatch.setitem(sys.modules, "api.apps", apps)
    runtime = ModuleType("api.technical_specifications.runtime")

    def forbidden_construction():
        raise AssertionError("Anonymous request must stop before constructing the scenario or accessing storage/model/Eva")

    runtime.build_specifications = forbidden_construction
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    utilities = ModuleType("api.utils.api_utils")
    utilities.get_request_json = lambda: request.get_json()
    monkeypatch.setitem(sys.modules, utilities.__name__, utilities)
    route_path = ROOT / "api/apps/restful_apis/technical_specifications_api.py"
    spec = spec_from_file_location("specification_anonymous_routes", route_path)
    module = module_from_spec(spec)
    module.manager = Blueprint("specification_anonymous_routes", __name__)
    spec.loader.exec_module(module)
    app = Quart(__name__)
    app.secret_key = "synthetic-anonymous-boundary"
    app.register_blueprint(module.manager)
    _install_application_error_handlers(app)
    return app.test_client()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "Bearer "], ids=["no-credentials", "empty-bearer"])
@pytest.mark.parametrize("method,path", ENDPOINTS)
async def test_anonymous_routes_reject_before_parsing_or_creating_services(anonymous_client, method, path, authorization):
    headers = {"Authorization": authorization} if authorization is not None else {}
    response = await anonymous_client.open(BASE + path, method=method, headers=headers, json=[])
    assert response.status_code == 401
    error = await response.get_json()
    assert error["code"] == 401 and error["message"] and error["data"] is None
