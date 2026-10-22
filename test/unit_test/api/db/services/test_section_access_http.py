"""Production login_required in Quart, without application bootstrap or shared data."""

import ast
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from functools import wraps
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ParamSpec, TypeVar

import pytest
from quart import Blueprint, Quart, current_app, jsonify, request
from quart_auth import Unauthorized

from api.db.services import access_group_service as policy
from common.constants import RetCode


@pytest.mark.parametrize("group_sections", [None, set(), {"chat"}, {"business_documents"}, {"technical_specifications"}])
@pytest.mark.parametrize("actor", ["anonymous", "reader", "admin"])
@pytest.mark.parametrize(
    "blueprint,path,section",
    [
        ("dataset_api", "/api/v1/datasets", "dataset"),
        ("chat_api", "/api/v1/chat/completions", "chat"),
        ("technical_specifications_api", "/api/v1/source-workspaces/<workspace>/technical-specifications", "technical_specifications"),
        ("business_document_api", "/api/v1/business-documents", "business_documents"),
        ("backward_compat", "/api/v1/datasets/<dataset>/run_raptor", "dataset"),
        ("source_workbench_api", "/api/v1/source-workspaces", "business_documents"),
        ("source_workbench_api", "/api/v1/source-workspaces/<workspace>/search", "search"),
    ],
)
@pytest.mark.asyncio
async def test_http_denies_before_scenario_and_managed_resource_checks(monkeypatch, group_sections, actor, blueprint, path, section):
    source = Path(__file__).resolve().parents[5] / "api/apps/__init__.py"
    names = {"login_required", "_normalize_auth_types"}
    functions = [node for node in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    user = None if actor == "anonymous" else SimpleNamespace(id=actor, is_superuser=actor == "admin")
    namespace = {
        "logging": logging,
        "os": os,
        "time": time,
        "wraps": wraps,
        "Callable": Callable,
        "Awaitable": Awaitable,
        "Iterable": Iterable,
        "P": ParamSpec("P"),
        "T": TypeVar("T"),
        "request": request,
        "current_app": current_app,
        "QuartAuthUnauthorized": Unauthorized,
        "RetCode": RetCode,
        "AUTH_JWT": "JWT",
        "AUTH_API": "API",
        "AUTH_BETA": "BETA",
        "DEFAULT_AUTH_TYPES": ("JWT", "API"),
        "_load_user": lambda _auth: user,
        "get_json_result": lambda **data: jsonify(data),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    monkeypatch.setattr(policy, "get_visible_sections", lambda: ["dataset"])
    monkeypatch.setattr(policy.AccessGroupService, "effective_policy", staticmethod(lambda _id: None if group_sections is None else {"sections": group_sections, "dataset_ids": set()}))
    reached = []
    managed = ModuleType("api.db.services.managed_resource_service")
    managed.ManagedResourceService = SimpleNamespace(request_allowed=lambda *_args: reached.append("managed") or True)
    monkeypatch.setitem(sys.modules, managed.__name__, managed)
    app = Quart(__name__)
    routes = Blueprint(blueprint, __name__)

    @routes.route(path, methods=["POST"])
    @namespace["login_required"]
    async def scenario(**_kwargs):
        reached.append("scenario")
        return jsonify(code=0)

    app.register_blueprint(routes)

    @app.errorhandler(Unauthorized)
    def unauthorized(_error):
        return jsonify(code=401), 401

    response = await app.test_client().post(path.replace("<workspace>", "w").replace("<dataset>", "d"), json={})
    allowed_sections = {"dataset"} if group_sections is None else group_sections
    required = {"search", "business_documents", "technical_specifications"} if blueprint == "source_workbench_api" and path == "/api/v1/source-workspaces" else {section}
    expected = 401 if user is None else 200 if actor == "admin" or required.intersection(allowed_sections) else 403
    assert response.status_code == expected
    assert reached == (["managed", "scenario"] if expected == 200 else [])
    if expected != 200:
        assert (await response.get_json())["code"] != 0
