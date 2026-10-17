"""Preserve real provider metadata across SDK errors and stale model catalogs."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace

import httpx
import litellm
import pytest

from test.unit_test.api.apps.business_documents.test_business_document_ai_cancellation import model_boundary as adapter_model_boundary, RAGFlowLLMAdapter
from api.apps.business_documents.recovery import is_transient_failure, retry_delay_ms
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture
def model_boundary(monkeypatch):
    return adapter_model_boundary.__wrapped__(monkeypatch)


@pytest.fixture
def rejected_ollama():
    state = SimpleNamespace(status=503, retry_after="6", requests=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append((self.path, body))
            data = json.dumps({"error": "QA provider failure"}).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Retry-After", state.retry_after)
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.url = f"http://127.0.0.1:{server.server_port}"
        try:
            yield state
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status,transient", [(400, False), (401, False), (403, False), (408, True), (429, True), (503, True)])
async def test_real_ollama_sdk_preserves_http_policy(rejected_ollama, stream, status, transient):
    rejected_ollama.status = status
    try:
        with pytest.raises(Exception) as caught:
            result = await litellm.acompletion(model="ollama_chat/qa-model", api_base=rejected_ollama.url, messages=[{"role": "user", "content": "hello"}], stream=stream, max_retries=0, timeout=5)
            if stream:
                async for _chunk in result:
                    pass
        assert is_transient_failure(caught.value) is transient
        assert retry_delay_ms(caught.value, 5000) == 6000
        assert len(rejected_ollama.requests) == 1
    finally:
        await litellm.close_litellm_async_clients()


@pytest.mark.asyncio
async def test_real_sdk_http_date_retry_after(rejected_ollama):
    rejected_ollama.retry_after = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=35), usegmt=True)
    try:
        with pytest.raises(Exception) as caught:
            await litellm.acompletion(model="ollama_chat/qa-model", api_base=rejected_ollama.url, messages=[{"role": "user", "content": "hello"}], max_retries=0, timeout=5)
        assert 33000 <= retry_delay_ms(caught.value, 5000) <= 35000
    finally:
        await litellm.close_litellm_async_clients()


def test_sdk_error_headers_and_cyclic_chain_terminate():
    error = RuntimeError("SDK wrapper")
    error.status_code = 429
    error.headers = {"retry-after": "30"}
    error.__context__ = error
    assert is_transient_failure(error)
    assert retry_delay_ms(error, 5000) == 30000


def test_explicit_cause_preserves_auth_failure_over_wrapper_status():
    response = httpx.Response(401, request=httpx.Request("POST", "https://provider.invalid"))
    transport = httpx.HTTPStatusError("Authentication rejected", request=response.request, response=response)
    wrapper = RuntimeError("SDK error")
    wrapper.status_code = 500
    wrapper.__cause__ = transport
    wrapper.__context__ = TimeoutError()
    assert is_transient_failure(wrapper) is False


def test_programming_error_does_not_become_retryable_from_its_context():
    response = httpx.Response(503, request=httpx.Request("POST", "https://provider.invalid"))
    transport = httpx.HTTPStatusError("Provider failed", request=response.request, response=response)
    bug = ValueError("Invalid application state")
    bug.__context__ = transport
    assert is_transient_failure(bug) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("window", [65536, 262144])
async def test_default_ollama_discovers_context_without_changing_saved_config(model_boundary, monkeypatch, stream, window):
    config, _, _, calls = model_boundary
    config.update(api_base="http://provider/v1/", api_key="tenant-secret", max_tokens=4096)
    lookups = []

    async def show(_client, url, **kwargs):
        assert url == "http://provider/api/show"
        assert kwargs["headers"] == {"Authorization": "Bearer tenant-secret"}
        assert kwargs["json"] == {"model": "local-chat"}
        lookups.append(url)
        return httpx.Response(200, json={"details": {"family": "qwen"}, "model_info": {"unrelated.context_length": 4096, "qwen.context_length": window}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    adapter = RAGFlowLLMAdapter()
    for task in ("PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"):
        payload = {"job_input": {"task_type": task, "text": "confirmed requirement " * 3000}}
        await adapter.async_generate("tenant", "system", payload, (lambda _chunk: None) if stream else None)
    assert len(lookups) == len(calls["bundle"]) == 2
    assert all(call[0][1]["max_tokens"] == window for call in calls["bundle"])
    assert all(parameters["extra_body"] == {"num_ctx": window} for parameters in calls["parameters"])
    assert config["max_tokens"] == 4096 and config["llm_name"] == "local-chat"


@pytest.mark.asyncio
async def test_default_ollama_refreshes_context_for_next_request(model_boundary, monkeypatch):
    config, _, _, calls = model_boundary
    config.update(api_base="http://provider", max_tokens=4096)
    windows = iter((65536, 131072))

    async def show(_client, url, **_kwargs):
        return httpx.Response(200, json={"model_info": {"general.context_length": next(windows)}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    adapter = RAGFlowLLMAdapter()
    for _ in range(2):
        await adapter.async_generate("tenant", "system", {"job_input": {"task_type": "PLAN_CHANGES"}})
    assert [call[0][1]["max_tokens"] for call in calls["bundle"]] == [65536, 131072]
    assert config["max_tokens"] == 4096


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, [], {}, {"model_info": []}, {"model_info": {"qwen.context_length": True}}, {"model_info": {"qwen.context_length": 0}}])
async def test_default_ollama_cannot_invent_context(model_boundary, monkeypatch, metadata):
    config, _, _, calls = model_boundary
    config.update(api_base="http://provider", max_tokens=4096)

    async def show(_client, url, **_kwargs):
        return httpx.Response(200, json=metadata, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    with pytest.raises(BusinessDocumentError) as caught:
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {})
    assert caught.value.code == "MODEL_CONTEXT_UNAVAILABLE" and calls["bundle"] == []


@pytest.mark.asyncio
async def test_metadata_failure_preserves_http_retry_after(model_boundary, monkeypatch):
    config, _, _, calls = model_boundary
    config.update(api_base="http://provider", max_tokens=4096)

    async def show(_client, url, **_kwargs):
        return httpx.Response(503, headers={"Retry-After": "12"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {})
    assert is_transient_failure(caught.value) and retry_delay_ms(caught.value, 5000) == 12000
    assert calls["bundle"] == []
