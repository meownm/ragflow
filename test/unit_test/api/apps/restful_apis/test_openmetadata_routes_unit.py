#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


class _Manager:
    def route(self, *_args, **_kwargs):
        return lambda function: function


class _Args(dict):
    pass


async def _thread_pool_exec(function, *args, **kwargs):
    return function(*args, **kwargs)


def _module(name, **values):
    module = ModuleType(name)
    for key, value in values.items():
        setattr(module, key, value)
    return module


def _load_route(monkeypatch, *, user, payload=None, args=None, catalog_access=True):
    class OMError(RuntimeError):
        pass

    class OMConfigurationError(OMError):
        pass

    class OMAuthenticationError(OMError):
        pass

    class OMPermissionError(OMError):
        pass

    class OMNotFoundError(OMError):
        pass

    class OMConflictError(OMError):
        pass

    request = SimpleNamespace(args=_Args(args or {}))
    monkeypatch.setitem(sys.modules, "quart", _module("quart", request=request))
    monkeypatch.setitem(
        sys.modules,
        "api.apps",
        _module("api.apps", current_user=user, login_required=lambda function: function),
    )
    monkeypatch.setitem(
        sys.modules,
        "api.apps.services.openmetadata_copilot_service",
        _module(
            "api.apps.services.openmetadata_copilot_service",
            OpenMetadataCopilotService=object,
            OpenMetadataError=OMError,
            OpenMetadataConfigurationError=OMConfigurationError,
            OpenMetadataAuthenticationError=OMAuthenticationError,
            OpenMetadataPermissionError=OMPermissionError,
            OpenMetadataNotFoundError=OMNotFoundError,
            OpenMetadataConflictError=OMConflictError,
        ),
    )

    async def get_request_json():
        return {} if payload is None else payload

    monkeypatch.setitem(
        sys.modules,
        "api.utils.api_utils",
        _module(
            "api.utils.api_utils",
            get_json_result=lambda code=0, message="success", data=None: {
                "code": code,
                "message": message,
                "data": data,
            },
            get_request_json=get_request_json,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "common.constants",
        _module(
            "common.constants",
            RetCode=SimpleNamespace(
                ARGUMENT_ERROR=101,
                AUTHENTICATION_ERROR=109,
                PERMISSION_ERROR=108,
                NOT_FOUND=404,
                CONFLICT=409,
                CONNECTION_ERROR=105,
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "common.misc_utils",
        _module("common.misc_utils", thread_pool_exec=_thread_pool_exec),
    )

    class _KnowledgebaseService:
        @classmethod
        def accessible(cls, dataset_id, user_id):
            return catalog_access

    monkeypatch.setitem(
        sys.modules,
        "api.db.services.knowledgebase_service",
        _module(
            "api.db.services.knowledgebase_service",
            KnowledgebaseService=_KnowledgebaseService,
        ),
    )
    repo_root = Path(__file__).resolve().parents[5]
    path = repo_root / "api" / "apps" / "restful_apis" / "openmetadata_api.py"
    spec = importlib.util.spec_from_file_location("test_openmetadata_api_module", path)
    module = importlib.util.module_from_spec(spec)
    module.manager = _Manager()
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, OMPermissionError


class _FakeService:
    def __init__(self, permission_error):
        self.config = SimpleNamespace(max_results=25, dataset_id="openmetadata-dataset")
        self.catalog = SimpleNamespace(run=self.run, classify=self.classify)
        self.discovery = SimpleNamespace(search=self.search)
        self.impact_quality = SimpleNamespace(impact=self.impact)
        self.starter_questions = SimpleNamespace(generate=lambda **_kwargs: {"questions": []})
        self.governance = SimpleNamespace(preview=self.preview, confirm=self.confirm)
        self.permission_error = permission_error
        self.calls = []
        self.visible_entity = {"id": "entity", "name": "orders", "fqn": "svc.db.schema.orders"}

    def status(self, *, force=False, user_id=""):
        return {"connected": True, "write_enabled": True, "refreshed": force, "user_id": user_id}

    def run(self, question, **kwargs):
        self.calls.append(("query", question, kwargs))
        return {"answer": "ok"}

    @staticmethod
    def classify(question):
        return "impact" if "depend" in question.casefold() else "discovery"

    def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        return {"entities": []}

    def impact(self, query, **kwargs):
        self.calls.append(("impact", query, kwargs))
        return {"entity": {"id": "x"}}

    def get_visible_entity(self, entity_id, **kwargs):
        self.calls.append(("visible_entity", entity_id, kwargs))
        return self.visible_entity if entity_id == self.visible_entity["id"] else None

    def preview(self, **kwargs):
        self.calls.append(("preview", kwargs))
        return {"confirmation_token": "signed"}

    def confirm(self, **kwargs):
        self.calls.append(("confirm", kwargs))
        return {"applied": True}


def test_status_exposes_governance_only_to_superuser(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
        args={"refresh": "true"},
    )
    module._SERVICE = _FakeService(permission_error)

    result = asyncio.run(module.status())

    assert result["code"] == 0
    assert result["data"]["refreshed"] is True
    assert result["data"]["user_id"] == "reader"
    assert result["data"]["governance_allowed"] is False


def test_catalog_routes_deny_user_without_dataset_access(monkeypatch):
    route_cases = [
        ("status", {}, {}),
        ("starter_questions", {}, {}),
        ("provision_agents", {}, {}),
        ("query_catalog", {"question": "orders"}, {}),
        ("search_entities", {}, {"q": "orders"}),
        ("entity_relationships", {}, {}),
        ("governance_preview", {"entity_id": "entity", "changes": {"description": "new"}}, {}),
        ("governance_confirm", {"confirmation_token": "signed"}, {}),
    ]

    for route_name, payload, args in route_cases:
        module, permission_error = _load_route(
            monkeypatch,
            user=SimpleNamespace(id="other-tenant", is_superuser=True),
            payload=payload,
            args=args,
            catalog_access=False,
        )
        service = _FakeService(permission_error)
        module._SERVICE = service

        route = getattr(module, route_name)
        result = asyncio.run(route("entity") if route_name == "entity_relationships" else route())

        assert result["code"] == 108
        assert "Dataset" in result["message"]
        assert service.calls == []


def test_query_passes_user_scope_filters_and_depth(monkeypatch):
    context = [{"question": "tables", "entity_ids": ["entity"]}]
    action = {"type": "impact", "entity_id": "entity"}
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader-a", is_superuser=False),
        payload={
            "question": "orders",
            "filters": {"domain": "Finance"},
            "depth": 3,
            "context": context,
            "selected_entity_id": "entity",
            "action": action,
            "locale": "en",
        },
    )
    service = _FakeService(permission_error)
    module._SERVICE = service

    result = asyncio.run(module.query_catalog())

    assert result["data"] == {"answer": "ok"}
    assert service.calls == [
        (
            "query",
            "orders",
            {
                "user_id": "reader-a",
                "filters": {"domain": "Finance"},
                "depth": 3,
                "context": context,
                "selected_entity_id": "entity",
                "action": action,
                "locale": "en",
            },
        )
    ]


def test_impact_query_skips_dataset_retrieval(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
        payload={"question": "What depends on users?", "locale": "en"},
    )
    service = _FakeService(permission_error)
    module._SERVICE = service

    async def unexpected_dataset_search(*_args, **_kwargs):
        raise AssertionError("impact questions must not invoke Dataset retrieval")

    monkeypatch.setattr(module, "_dataset_hits", unexpected_dataset_search)

    result = asyncio.run(module.query_catalog())

    assert result["data"] == {"answer": "ok"}
    assert service.calls[0][0] == "query"


def test_query_rejects_invalid_filters(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
        payload={"question": "orders", "filters": []},
    )
    module._SERVICE = _FakeService(permission_error)

    result = asyncio.run(module.query_catalog())

    assert result["code"] == 101
    assert "filters" in result["message"]


def test_query_rejects_invalid_context(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
        payload={"question": "orders", "context": {}},
    )
    module._SERVICE = _FakeService(permission_error)

    result = asyncio.run(module.query_catalog())

    assert result["code"] == 101
    assert "context" in result["message"]


def test_entity_search_passes_pagination_sort_and_description_filter(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
        args={
            "q": "orders",
            "limit": "10",
            "offset": "20",
            "sort": "updated_at",
            "has_description": "false",
            "locale": "en",
        },
    )
    service = _FakeService(permission_error)
    module._SERVICE = service

    async def unexpected_dataset_search(*_args, **_kwargs):
        raise AssertionError("catalog browsing must not invoke Dataset retrieval")

    monkeypatch.setattr(module, "_dataset_hits", unexpected_dataset_search)

    result = asyncio.run(module.search_entities())

    assert result["data"] == {"entities": []}
    assert service.calls == [
        (
            "search",
            "orders",
            {
                "filters": {"has_description": False},
                "limit": 10,
                "offset": 20,
                "sort": "updated_at",
                "user_id": "reader",
                "locale": "en",
            },
        )
    ]


def test_entity_relationships_rejects_entity_outside_user_scope(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
    )
    service = _FakeService(permission_error)
    service.visible_entity = {"id": "other", "name": "other"}
    module._SERVICE = service

    result = asyncio.run(module.entity_relationships("entity"))

    assert result["code"] == 404
    assert "недоступна" in result["message"]
    assert service.calls == [
        ("visible_entity", "entity", {"user_id": "reader"}),
    ]


def test_governance_preview_is_denied_before_service_call(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="reader", is_superuser=False),
        payload={"entity_id": "entity", "changes": {"description": "new"}},
    )
    service = _FakeService(permission_error)
    module._SERVICE = service

    result = asyncio.run(module.governance_preview())

    assert result["code"] == 108
    assert service.calls == []


def test_governance_preview_and_confirm_for_superuser(monkeypatch):
    module, permission_error = _load_route(
        monkeypatch,
        user=SimpleNamespace(id="admin", is_superuser=True),
        payload={"entity_id": "entity", "changes": {"description": "new"}},
    )
    service = _FakeService(permission_error)
    module._SERVICE = service
    preview = asyncio.run(module.governance_preview())

    assert preview["data"]["confirmation_token"] == "signed"

    async def confirm_payload():
        return {"confirmation_token": "signed"}

    module.get_request_json = confirm_payload
    confirmed = asyncio.run(module.governance_confirm())

    assert confirmed["data"]["applied"] is True
    assert [call[0] for call in service.calls] == ["preview", "confirm"]
