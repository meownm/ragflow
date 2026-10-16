"""Execute embedded route bodies and the real ACL before models or streaming.

Authentication/section middleware is tested separately with a real Quart client.
These tests replace only the route decorators and external model/search I/O.
"""

import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.db.services import dialog_service
from api.db.services.knowledgebase_service import KnowledgebaseService
from common.constants import LLMType, RetCode


async def inline(function, *args, **kwargs):
    return function(*args, **kwargs)


@pytest.fixture
def embedded(monkeypatch):
    granted = {"assigned", "configured"}
    config = {"kb_ids": ["configured"]}
    monkeypatch.setattr(KnowledgebaseService, "accessible", lambda kb_id, actor: actor == "member" and kb_id in granted)
    load = Mock(return_value=[SimpleNamespace(id="assigned", tenant_id="other-owner", embd_id="embedding")])
    monkeypatch.setattr(KnowledgebaseService, "get_by_ids", load)
    search = Mock(side_effect=lambda search_id, actor: {"search_config": config} if search_id == "team-search" and actor == "member" else {})
    model = Mock(return_value=SimpleNamespace())
    metadata = AsyncMock(return_value=[])
    retrieval = AsyncMock(return_value={"chunks": []})

    async def answer(*_args, **_kwargs):
        yield {"answer": "Authorized answer"}

    ask = Mock(side_effect=answer)

    class Response:
        def __init__(self, body, **kwargs):
            self.body = body
            self.headers = SimpleNamespace(add_header=lambda *_args: None)

    namespace = {
        "thread_pool_exec": inline,
        "KnowledgebaseService": KnowledgebaseService,
        "SearchService": SimpleNamespace(get_detail=search),
        "RetCode": RetCode,
        "LLMType": LLMType,
        "LLMBundle": model,
        "apply_meta_data_filter": metadata,
        "DocMetadataService": SimpleNamespace(get_flatted_meta_by_kbs=Mock()),
        "get_model_config_from_provider_instance": model,
        "get_tenant_default_model_by_type": model,
        "settings": SimpleNamespace(retriever=SimpleNamespace(retrieval=retrieval)),
        "label_question": lambda *_args: {},
        "_resolve_reference_metadata": lambda *_args: (False, []),
        "get_json_result": lambda **kwargs: kwargs,
        "get_error_data_result": lambda **kwargs: kwargs,
        "server_error_response": lambda error: {"error": str(error)},
        "TenantService": SimpleNamespace(get_by_id=Mock(return_value=(True, SimpleNamespace(llm_id="chat")))),
        "async_ask": ask,
        "Response": Response,
        "json": json,
        "logging": logging,
        "current_user": SimpleNamespace(id="member"),
        "_full_text_weight": lambda weight: 1 - weight,
        "get_data_error_result": lambda **kwargs: kwargs,
    }
    path = Path(__file__).parents[5] / "api/apps/restful_apis/bot_api.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {"ask_about_embedded", "retrieval_test_embedded", "related_questions_embedded"}:
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    path = path.with_name("search_api.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "completion")
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return SimpleNamespace(granted=granted, config=config, load=load, search=search, model=model, metadata=metadata, retrieval=retrieval, ask=ask, namespace=namespace)


@pytest.mark.parametrize("route,key", [("ask_about_embedded", "kb_ids"), ("retrieval_test_embedded", "kb_id")])
@pytest.mark.parametrize("saved", [False, True])
async def test_unassigned_dataset_is_rejected_before_stream_model_or_metadata(embedded, route, key, saved):
    body = {"question": "q", key: ["unassigned"], "meta_data_filter": {"method": "auto"}}
    if saved:
        body.update(search_id="team-search")
        if key == "kb_ids":
            body[key] = ["assigned"]
            embedded.config["kb_ids"] = ["unassigned"]
    embedded.namespace["get_request_json"] = AsyncMock(return_value=body)
    payload, status = await embedded.namespace[route](tenant_id="member")
    assert status == 403
    assert payload["code"] == RetCode.AUTHENTICATION_ERROR
    embedded.load.assert_not_called()
    embedded.model.assert_not_called()
    embedded.metadata.assert_not_awaited()
    embedded.retrieval.assert_not_awaited()
    embedded.ask.assert_not_called()


@pytest.mark.parametrize("route,key", [("ask_about_embedded", "kb_ids"), ("retrieval_test_embedded", "kb_id"), ("related_questions_embedded", "kb_ids")])
async def test_foreign_search_is_rejected_before_reading_sources(embedded, route, key):
    embedded.namespace["get_request_json"] = AsyncMock(return_value={"question": "q", key: ["assigned"], "search_id": "foreign"})
    payload, status = await embedded.namespace[route](tenant_id="member")
    assert status == 403
    assert payload["code"] == RetCode.AUTHENTICATION_ERROR
    embedded.search.assert_called_once_with("foreign", "member")
    embedded.load.assert_not_called()
    embedded.model.assert_not_called()


async def test_retrieval_accepts_assigned_dataset_from_another_owner(embedded):
    embedded.namespace["get_request_json"] = AsyncMock(return_value={"question": "q", "kb_id": "assigned", "search_id": "team-search"})
    assert "data" in await embedded.namespace["retrieval_test_embedded"](tenant_id="member")
    embedded.load.assert_called_once_with(["assigned"])
    args = embedded.retrieval.await_args.args
    assert args[2:4] == (["other-owner"], ["assigned"])


async def test_ask_accepts_team_search_and_resolves_config_before_streaming(embedded):
    body = {"question": "q", "kb_ids": ["assigned"], "search_id": "team-search"}
    embedded.namespace["get_request_json"] = AsyncMock(return_value=body)
    response = await embedded.namespace["ask_about_embedded"](tenant_id="member")
    assert hasattr(response, "body")
    assert body["kb_ids"] == ["assigned"]
    assert embedded.config["kb_ids"] == ["configured"]
    chunks = [chunk async for chunk in response.body]
    assert any("Authorized answer" in chunk for chunk in chunks)
    assert embedded.ask.call_args.args == ("q", ["configured"], "member")


@pytest.mark.parametrize("search_id,grants,status", [("team-search", True, 200), ("foreign", True, 403), ("team-search", False, 403)])
async def test_search_completion_uses_read_permission_and_checks_grants_before_streaming(embedded, search_id, grants, status):
    if not grants:
        embedded.granted.clear()
    embedded.namespace["get_request_json"] = AsyncMock(return_value={"question": "q"})
    response = await embedded.namespace["completion"](search_id)
    if status == 403:
        payload, actual_status = response
        assert actual_status == 403
        assert payload["code"] == RetCode.AUTHENTICATION_ERROR
        embedded.ask.assert_not_called()
    else:
        chunks = [chunk async for chunk in response.body]
        assert any("Authorized answer" in chunk for chunk in chunks)
        assert embedded.ask.call_args.args == ("q", ["configured"], "member")


@pytest.mark.parametrize("configured", [False, True])
async def test_async_ask_defensively_rejects_final_selection_before_loading(embedded, configured):
    with pytest.raises(PermissionError):
        async for _event in dialog_service.async_ask(
            "q",
            ["assigned"] if configured else ["unassigned"],
            "member",
            search_config={"kb_ids": ["unassigned"]} if configured else {},
        ):
            pass
    embedded.load.assert_not_called()


@pytest.mark.parametrize("ids", [None, "assigned", [""], [1]])
def test_selection_shape_is_validated(embedded, ids):
    with pytest.raises(ValueError, match="kb_ids"):
        KnowledgebaseService.authorize_ids(ids, "member")
