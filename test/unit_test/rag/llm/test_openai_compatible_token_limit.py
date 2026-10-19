"""Completion limits reach compatible servers in ordinary and streaming calls."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rag.llm.chat_model import LiteLLMBase, OpenAI_APIChat
from rag.llm import chat_model


@pytest.mark.parametrize("override,expected", [(None, 600), (1800, 1800)])
def test_model_request_timeout_reaches_both_sdk_clients(monkeypatch, override, expected):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "600")
    for name in ("OpenRouter", "MiniMax"):
        if not hasattr(chat_model.SupportedLiteLLMProvider, name):
            monkeypatch.setattr(chat_model.SupportedLiteLLMProvider, name, name, raising=False)
    options = {} if override is None else {"timeout": override}
    model = OpenAI_APIChat("synthetic-key", "local", "http://127.0.0.1:1/v1", **options)
    try:
        assert model.client.timeout == model.async_client.timeout == expected
    finally:
        model.client.close()
        asyncio.run(model.async_client.close())
    native = LiteLLMBase("synthetic-key", "local", provider="Ollama", **options)
    assert native.timeout == expected


@pytest.mark.p1
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("model_name", ["qwen3.8:latest", "other-model"])
@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_tokens": 65536}, {"max_tokens": 65536}),
        ({"max_tokens": 220000}, {"max_tokens": 220000}),
        ({"max_completion_tokens": 8192}, {"max_completion_tokens": 8192}),
        ({"max_tokens": 65536, "max_completion_tokens": 8192}, {"max_completion_tokens": 8192}),
    ],
)
def test_completion_limit_reaches_compatible_transport(streaming, model_name, limits, expected):
    requests = []

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="{}"), finish_reason="stop")])

    async def create(**kwargs):
        requests.append(kwargs)
        if kwargs.get("stream"):
            return chunks()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")], usage=None)

    model = OpenAI_APIChat.__new__(OpenAI_APIChat)
    model.model_name = model_name
    model.max_retries = 0
    model.async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def call():
        options = {**limits, "temperature": 0, "model_type": "chat", "reasoning_effort": "none"}
        if streaming:
            return [chunk async for chunk in model.async_chat_streamly("Return JSON", [{"role": "user", "content": "x"}], options)]
        return await model.async_chat("Return JSON", [{"role": "user", "content": "x"}], options)

    def run_call():
        with asyncio.Runner() as runner:
            return runner.run(call())

    # Runner also closes the private loop when nest_asyncio patches asyncio.run.
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(run_call).result()
    assert result[0] == "{}"
    assert len(requests) == 1
    request = requests[0]
    assert {k: request[k] for k in ["max_tokens", "max_completion_tokens"] if k in request} == expected
    assert "model_type" not in request
    if model_name.startswith("qwen3"):
        assert request["reasoning_effort"] == "none"
    else:
        assert "reasoning_effort" not in request
