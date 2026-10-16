"""Document jobs retain typed provider failures without changing chat defaults."""

import httpx
import pytest

from rag.llm.chat_model import Base, LiteLLMBase, LLMErrorCode


@pytest.mark.asyncio
@pytest.mark.parametrize("model_class", [Base, LiteLLMBase])
@pytest.mark.parametrize("status", [401, 429, 503])
async def test_opt_in_preserves_original_failure_and_retry_headers(model_class, status):
    model = object.__new__(model_class)
    model.raise_on_error = True
    response = httpx.Response(status, headers={"retry-after": "30"}, request=httpx.Request("POST", "https://provider.invalid"))
    error = httpx.HTTPStatusError("provider failure", request=response.request, response=response)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await model._exceptions_async(error, 0)
    assert caught.value is error
    assert caught.value.response.headers["retry-after"] == "30"


@pytest.mark.asyncio
@pytest.mark.parametrize("model_class", [Base, LiteLLMBase])
async def test_normal_chat_still_returns_its_existing_error_envelope(model_class):
    model = object.__new__(model_class)
    model.max_retries = 0
    model._classify_error = lambda _error: LLMErrorCode.ERROR_AUTHENTICATION
    result = await model._exceptions_async(ValueError("invalid credentials"), 0)
    assert result == "**ERROR**: MAX_RETRIES_EXCEEDED - invalid credentials"


@pytest.mark.parametrize("enabled", [False, True])
def test_queue_owned_retry_disables_sdk_retries_only_when_opted_in(monkeypatch, enabled):
    from rag.llm import chat_model

    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(chat_model, "OpenAI", Client)
    monkeypatch.setattr(chat_model, "AsyncOpenAI", Client)
    Base("key", "model", "https://provider.invalid", max_retries=0, raise_on_error=enabled)
    assert len(calls) == 2
    for options in calls:
        if enabled:
            assert options["max_retries"] == 0
        else:
            assert "max_retries" not in options
