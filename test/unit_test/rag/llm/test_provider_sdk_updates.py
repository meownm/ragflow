"""Provider boundaries that must survive security-driven SDK upgrades."""

import json

import httpx
import numpy as np
import pytest
from mistralai import Mistral
from zai import ZhipuAiClient

from rag.llm.chat_model import MistralChat
from rag.llm.embedding_model import MistralEmbed, ZhipuEmbed


@pytest.mark.parametrize("stream", [False, True])
def test_mistral_chat_uses_real_sdk_transport(stream):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.path == "/v1/chat/completions"
        if stream:
            body = {
                "id": "local",
                "created": 1,
                "model": "mistral-small-latest",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}],
            }
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n")
        return httpx.Response(
            200,
            json={
                "id": "local",
                "created": 1,
                "model": "mistral-small-latest",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport, Mistral(api_key="test-key", client=transport) as sdk:
        model = MistralChat.__new__(MistralChat)
        model.model_name = "mistral-small-latest"
        model.client = sdk
        history = [{"role": "user", "content": "hello"}]
        if stream:
            assert list(model.chat_streamly("", history, {"temperature": 0})) == ["OK", 1]
        else:
            assert model._chat(history, {"temperature": 0}) == ("OK", 3)
    assert requests[0]["messages"] == history
    assert requests[0]["temperature"] == 0


@pytest.mark.parametrize("query", [False, True])
def test_mistral_embeddings_preserve_shape_and_usage(query):
    def respond(request):
        body = json.loads(request.content)
        assert body["input"] == ["hello"]
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "id": "local",
                "object": "list",
                "model": "mistral-embed",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport, Mistral(api_key="test-key", client=transport) as sdk:
        model = MistralEmbed.__new__(MistralEmbed)
        model.model_name = "mistral-embed"
        model.client = sdk
        vectors, usage = model.encode_queries("hello") if query else model.encode(["hello"])
    assert np.allclose(vectors, [0.1, 0.2] if query else [[0.1, 0.2]])
    assert usage == 2


def test_zhipu_embeddings_keep_chinese_endpoint_and_authorization():
    def respond(request):
        assert request.url.host == "open.bigmodel.cn"
        assert request.url.path == "/api/paas/v4/embeddings"
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content)["input"] == ["hello"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "embedding-2",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        sdk = ZhipuAiClient(api_key="test-key", http_client=transport)
        model = ZhipuEmbed.__new__(ZhipuEmbed)
        model.client = sdk
        model.model_name = "embedding-2"
        vector, usage = model.encode_queries("hello")
    assert np.allclose(vector, [0.1, 0.2])
    assert usage == 2
