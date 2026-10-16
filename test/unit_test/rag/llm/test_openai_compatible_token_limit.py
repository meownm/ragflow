"""Completion limits reach compatible servers in ordinary and streaming calls."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rag.llm.chat_model import OpenAI_APIChat


@pytest.mark.p1
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("model_name", ["qwen3.8:latest", "other-model"])
@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_tokens": 65536}, {"max_tokens": 65536}),
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
