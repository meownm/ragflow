"""Mindmap authorization before retrieval, with disposable service doubles.

The route adapter bodies are compiled from source to check both error mappings
without initializing the application. Authentication decorators are outside this
unit boundary; dataset and search checks execute the real generation service.
"""

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api.db.services import dialog_service as service
from common.constants import RetCode


@pytest.fixture
def mindmap(monkeypatch):
    granted = {"assigned", "configured"}
    config = {"kb_ids": ["configured"]}
    accessible = Mock(side_effect=lambda kb_id, actor_id: actor_id == "reader" and kb_id in granted)
    search_detail = Mock(side_effect=lambda search_id, actor_id: {"tenant_id": "owner", "search_config": config} if search_id == "own-search" and actor_id == "reader" else {})
    load = Mock(side_effect=lambda ids: [SimpleNamespace(id=kb_id, tenant_id="data-owner", embd_id="embedding") for kb_id in ids])
    retrieval = AsyncMock(return_value={"chunks": [{"content_with_weight": "Synthetic authorized source"}]})
    model_config = Mock(return_value={"model": "synthetic"})
    models = Mock(return_value=SimpleNamespace())

    class Extractor:
        def __init__(self, _model):
            pass

        async def __call__(self, contents):
            assert contents == ["Synthetic authorized source"]
            return SimpleNamespace(output={"nodes": ["Synthetic result"]})

    monkeypatch.setattr(service.KnowledgebaseService, "accessible", accessible)
    monkeypatch.setattr(service.KnowledgebaseService, "get_by_ids", load)
    monkeypatch.setattr(service.SearchService, "get_detail", search_detail)
    monkeypatch.setattr(service, "LLMBundle", models)
    monkeypatch.setattr(service, "get_model_config_from_provider_instance", model_config)
    monkeypatch.setattr(service, "get_tenant_default_model_by_type", model_config)
    monkeypatch.setattr(service, "MindMapExtractor", Extractor)
    monkeypatch.setattr(service, "label_question", lambda *_args: {})
    monkeypatch.setattr(service.settings, "retriever", SimpleNamespace(retrieval=retrieval), raising=False)
    return SimpleNamespace(
        granted=granted,
        config=config,
        accessible=accessible,
        search_detail=search_detail,
        load=load,
        retrieval=retrieval,
        models=models,
        model_config=model_config,
    )


@pytest.mark.parametrize("requested,search_id", [(["unassigned"], ""), (["assigned", "unassigned"], ""), (["assigned"], "own-search")])
async def test_mindmap_rejects_requested_or_saved_unassigned_dataset_before_loading(mindmap, requested, search_id):
    mindmap.granted.discard("configured")
    with pytest.raises(PermissionError, match="datasets"):
        await service.gen_mindmap("question", requested, "reader", search_id)
    mindmap.load.assert_not_called()
    mindmap.models.assert_not_called()
    mindmap.retrieval.assert_not_awaited()


async def test_mindmap_rejects_foreign_search_before_reading_its_config(mindmap):
    with pytest.raises(PermissionError, match="search"):
        await service.gen_mindmap("question", ["assigned"], "reader", "foreign-search")
    mindmap.search_detail.assert_called_once_with("foreign-search", "reader")
    mindmap.load.assert_not_called()
    mindmap.retrieval.assert_not_awaited()


async def test_mindmap_rejects_search_removed_after_authorization(mindmap):
    mindmap.search_detail.side_effect = None
    mindmap.search_detail.return_value = {}
    with pytest.raises(PermissionError, match="search"):
        await service.gen_mindmap("question", ["assigned"], "reader", "own-search")
    mindmap.load.assert_not_called()


async def test_mindmap_unions_saved_and_requested_datasets_without_mutating_them(mindmap):
    requested = ["assigned", "configured", "assigned"]
    original_config = deepcopy(mindmap.config)
    result = await service.gen_mindmap("question", requested, "reader", "own-search")
    assert result == {"nodes": ["Synthetic result"]}
    mindmap.load.assert_called_once_with(["configured", "assigned"])
    assert mindmap.retrieval.await_args.kwargs["kb_ids"] == ["configured", "assigned"]
    assert mindmap.retrieval.await_args.kwargs["tenant_ids"] == ["data-owner"]
    assert mindmap.accessible.call_args_list[0].args == ("configured", "reader")
    assert mindmap.accessible.call_args_list[1].args == ("assigned", "reader")
    assert mindmap.model_config.call_args.args[0] == "reader"
    assert mindmap.config == original_config
    assert requested == ["assigned", "configured", "assigned"]


async def test_mindmap_rechecks_revoked_grants_on_each_request(mindmap):
    await service.gen_mindmap("question", ["assigned"], "reader")
    mindmap.granted.clear()
    with pytest.raises(PermissionError):
        await service.gen_mindmap("question", ["assigned"], "reader")
    assert mindmap.retrieval.await_count == 1
    mindmap.load.assert_called_once_with(["assigned"])


@pytest.mark.parametrize("ids", ["assigned", None, [None], [""], [123]])
async def test_mindmap_rejects_invalid_dataset_ids(mindmap, ids):
    with pytest.raises(ValueError, match="kb_ids"):
        await service.gen_mindmap("question", ids, "reader")
    mindmap.load.assert_not_called()
    mindmap.retrieval.assert_not_awaited()


async def test_mindmap_empty_selection_does_not_load_models(mindmap):
    mindmap.load.return_value = []
    mindmap.load.side_effect = None
    assert await service.gen_mindmap("question", [], "reader") == {"error": "No KB selected"}
    mindmap.models.assert_not_called()


@pytest.mark.parametrize("adapter", ["chat_api", "bot_api"])
@pytest.mark.parametrize(
    "body,status",
    [
        ({"kb_ids": ["unassigned"]}, 403),
        ({"kb_ids": ["assigned"], "search_id": "foreign-search"}, 403),
        ({"kb_ids": "assigned"}, 400),
        ({"kb_ids": ["assigned"], "search_id": None}, 400),
        ({"kb_ids": ["assigned"]}, 200),
    ],
)
async def test_mindmap_adapter_statuses_use_authenticated_actor(mindmap, adapter, body, status):
    path = Path(__file__).parents[5] / "api" / "apps" / "restful_apis" / f"{adapter}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "mindmap")
    function.decorator_list = []
    namespace = {
        "current_user": SimpleNamespace(id="reader"),
        "get_request_json": AsyncMock(return_value={"question": "question", **body}),
        "gen_mindmap": service.gen_mindmap,
        "RetCode": RetCode,
        "get_json_result": lambda **kwargs: kwargs,
        "get_data_error_result": lambda **kwargs: kwargs,
        "get_error_data_result": lambda **kwargs: kwargs,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    args = {"tenant_id": "reader"} if adapter == "bot_api" else {}
    response = await namespace["mindmap"](**args)
    if status == 200:
        assert response == {"data": {"nodes": ["Synthetic result"]}}
    else:
        payload, actual_status = response
        assert actual_status == status
        if status == 403:
            assert payload["code"] == RetCode.AUTHENTICATION_ERROR
        mindmap.load.assert_not_called()
        mindmap.retrieval.assert_not_awaited()
