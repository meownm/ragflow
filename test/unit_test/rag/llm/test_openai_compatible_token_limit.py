"""Completion limits reach compatible servers in ordinary and streaming calls."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rag.llm.chat_model import OpenAI_APIChat


@pytest.mark.p1
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_tokens": 65536}, {"max_tokens": 65536}),
        ({"max_completion_tokens": 8192}, {"max_completion_tokens": 8192}),
        ({"max_tokens": 65536, "max_completion_tokens": 8192}, {"max_completion_tokens": 8192}),
    ],
)
def test_completion_limit_reaches_compatible_transport(streaming, limits, expected):
    requests = []

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="{}"), finish_reason="stop")])

    async def create(**kwargs):
        requests.append(kwargs)
        if kwargs.get("stream"):
            return chunks()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")], usage=None)

    model = OpenAI_APIChat.__new__(OpenAI_APIChat)
    model.model_name = "qwen3.8:latest"
    model.max_retries = 0
    model.async_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def call():
        options = {**limits, "temperature": 0, "model_type": "chat"}
        if streaming:
            return [chunk async for chunk in model.async_chat_streamly("Return JSON", [{"role": "user", "content": "x"}], options)]
        return await model.async_chat("Return JSON", [{"role": "user", "content": "x"}], options)

    # Keep the transport's private loop separate from pytest-asyncio's policy loop.
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(asyncio.run, call()).result()
    assert result[0] == "{}"
    assert len(requests) == 1
    request = requests[0]
    assert {k: request[k] for k in ["max_tokens", "max_completion_tokens"] if k in request} == expected
    assert "model_type" not in request
