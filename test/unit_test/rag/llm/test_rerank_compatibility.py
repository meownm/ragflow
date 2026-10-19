"""Rerank URL, SDK decoding and provider verification contracts."""

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import cohere
import httpx
import numpy as np
import pytest
import requests

from rag.llm import rerank_model as models


ADAPTERS = [models.JinaRerank, models.OpenAI_APIRerank, models.NewAPIRerank, models.CoHereRerank]


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(status=200, calls=[], payload={"results": [{"index": 1, "relevance_score": 0.8}, {"index": 0, "relevance_score": 0.2}]})

    def respond(request):
        state.calls.append(request)
        return httpx.Response(state.status, json=state.payload)

    def post(url, *, headers, json, timeout):
        assert timeout == 30
        request = httpx.Request("POST", url, headers=headers, json=json)
        response = respond(request)
        result = requests.Response()
        result.status_code = response.status_code
        result._content = response.content
        return result

    real_client = cohere.Client
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        monkeypatch.setattr(cohere, "Client", lambda **kwargs: real_client(httpx_client=http_client, **kwargs))
        monkeypatch.setattr(models.requests, "post", post)
        yield state


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(
    "endpoint_url",
    [
        "http://reranker:8013/v1",
        "http://reranker:8013/v1/",
        "http://host.docker.internal:8013/v1/rerank",
        "http://reranker:8013/proxy/v2/rerank/",
    ],
)
def test_urls_preserve_prefix_and_ignore_rerank_in_hostname(adapter, endpoint_url, transport):
    model = adapter("test-key", "model-r", endpoint_url)
    rank, _ = model.similarity("question", ["first", "second"])
    path = httpx.URL(endpoint_url).path.rstrip("/")
    expected_path = path if path.endswith("/rerank") else path + "/rerank"
    request = transport.calls[0]
    assert request.url.path == expected_path
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["documents"] == ["first", "second"]
    assert body["top_n"] == 2
    assert np.allclose(rank, [0.2, 0.8])


@pytest.mark.parametrize("adapter", [models.JinaRerank, models.OpenAI_APIRerank, models.NewAPIRerank])
def test_endpoint_preserves_query_parameters(adapter):
    model = adapter("key", "model-r", "http://reranker:8013/prefix/v1/?route=rerank")
    assert model.base_url == "http://reranker:8013/prefix/v1/rerank?route=rerank"


def test_jina_reports_server_token_usage(transport):
    transport.payload["usage"] = {"total_documents": 2, "total_tokens": 17}
    _, count = models.JinaRerank("key", "model-r", "http://reranker/v1").similarity("q", ["a", "b"])
    assert count == 17


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(
    "payload",
    [
        {"results": []},
        {"results": [{"index": 0, "relevance_score": 0.2}]},
        {"results": [{"index": 0, "relevance_score": 0.2}, {"index": 0, "relevance_score": 0.8}]},
        {"results": [{"index": -1, "relevance_score": 0.2}, {"index": 1, "relevance_score": 0.8}]},
        {"results": [{"index": 0, "relevance_score": 0.2}, {"index": 2, "relevance_score": 0.8}]},
        {"error": "upstream failed"},
    ],
)
def test_malformed_responses_cannot_be_accepted_as_zero_scores(adapter, payload, transport):
    transport.payload = payload
    with pytest.raises((ValueError, KeyError, TypeError)):
        adapter("key", "model-r", "http://reranker/v1").similarity("q", ["a", "b"])


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_http_errors_are_propagated(adapter, transport):
    transport.status = 503
    with pytest.raises(Exception):
        adapter("key", "model-r", "http://reranker/v1").similarity("q", ["a", "b"])


@pytest.fixture
def provider_service(monkeypatch):
    """Load the service with DB ports isolated; do not bootstrap shared stores."""
    modules = {
        "api.db.joint_services.tenant_model_service": ["get_model_config_from_provider_instance", "delete_models_by_instance_ids", "delete_instances_by_provider_ids"],
        "api.db.services.tenant_model_provider_service": ["TenantModelProviderService"],
        "api.db.services.tenant_model_instance_service": ["TenantModelInstanceService"],
        "api.db.services.tenant_model_service": ["TenantModelService"],
    }
    for name, symbols in modules.items():
        stub = ModuleType(name)
        for symbol in symbols:
            setattr(stub, symbol, MagicMock())
        monkeypatch.setitem(sys.modules, name, stub)
    # The LLM unit lane deliberately imports individual adapters without the
    # heavyweight provider registry. Only the rerank port is used by this test.
    registry = ModuleType("rag.llm")
    for symbol in ["ChatModel", "EmbeddingModel", "ModelMeta", "OcrModel", "RerankModel", "TTSModel"]:
        setattr(registry, symbol, {})
    monkeypatch.setitem(sys.modules, "rag.llm", registry)
    path = Path(__file__).resolve().parents[4] / "api/apps/services/provider_api_service.py"
    spec = importlib.util.spec_from_file_location("isolated_rerank_provider_service", path)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    service.TenantModelProviderService.get_by_id.return_value = (False, None)
    monkeypatch.setattr(service, "FACTORY_LLM_INFOS", [{"name": "Jina", "llm": []}])
    monkeypatch.setattr(service, "RerankModel", {"Jina": models.JinaRerank})
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"total_tokens": 0}, {"total_tokens": 17}])
async def test_provider_verification_accepts_valid_scores_without_billing_usage(provider_service, transport, usage):
    transport.payload = {"results": [{"index": 0, "relevance_score": 0.0}]}
    if usage is not None:
        transport.payload["usage"] = usage
    success, message = await provider_service.verify_api_key("Jina", "key", "http://reranker/v1", model_info=[{"model_name": "model-r", "model_type": ["rerank"]}])
    assert success, message


@pytest.mark.asyncio
async def test_provider_verification_rejects_empty_results(provider_service, transport):
    transport.payload = {"results": [], "usage": {"total_tokens": 17}}
    success, _ = await provider_service.verify_api_key("Jina", "key", "http://reranker/v1", model_info=[{"model_name": "model-r", "model_type": ["rerank"]}])
    assert success is False


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["Jina", "Cohere"])
@pytest.mark.parametrize("custom_model", [None, "model-r"])
async def test_provider_checks_explicit_model_before_catalog(provider_service, transport, monkeypatch, provider, custom_model):
    monkeypatch.setattr(provider_service, "FACTORY_LLM_INFOS", [{"name": provider, "llm": [{"llm_name": "catalog-model", "model_type": "rerank"}]}])
    monkeypatch.setattr(provider_service, "RerankModel", {provider: models.JinaRerank if provider == "Jina" else models.CoHereRerank})
    transport.payload = {"results": [{"index": 0, "relevance_score": 0.5}]}
    info = [{"model_name": custom_model, "model_type": ["rerank"]}] if custom_model else None
    success, message = await provider_service.verify_api_key(provider, "key", "http://reranker/v1", model_info=info)
    assert success, message
    assert json.loads(transport.calls[0].content)["model"] == (custom_model or "catalog-model")
