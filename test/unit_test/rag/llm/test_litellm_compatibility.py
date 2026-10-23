"""Exercise the installed LiteLLM SDK against a disposable HTTP provider."""

import asyncio
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace

import litellm
from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER
import pytest
import pytest_asyncio

from common.llm_request_context import reset_llm_request_context, set_llm_request_context
from rag.llm import chat_model

pytestmark = pytest.mark.p1


@pytest.fixture
def provider():
    state = SimpleNamespace(requests=[], status=200, tool_call=False, hold=False, received=Event(), release=Event())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            state.requests.append((self.path, {key.lower(): value for key, value in self.headers.items()}, None))
            data = json.dumps({"data": [{"id": "local-model"}]}).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append((self.path, {key.lower(): value for key, value in self.headers.items()}, body))
            state.received.set()
            if state.hold:
                state.release.wait(timeout=5)
            if state.status != 200:
                error_type = {401: "authentication_error", 429: "rate_limit_error", 503: "server_error"}[state.status]
                payload = {"error": {"message": "provider rejected request", "type": error_type}}
            elif self.path == "/api/chat":
                payload = {
                    "model": body["model"],
                    "message": {"role": "assistant", "content": "OK"},
                    "done": True,
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                }
            else:
                message = {"role": "assistant", "content": "OK"}
                if state.tool_call:
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"key":"x"}'}}],
                    }
                payload = {
                    "id": "chatcmpl-local",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if state.tool_call else "stop"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                }
            streaming = body.get("stream") and state.status == 200
            if streaming and self.path == "/api/chat":
                data = (
                    json.dumps({"model": body["model"], "message": {"role": "assistant", "content": "OK"}, "done": False})
                    + "\n"
                    + json.dumps({"model": body["model"], "message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 3, "eval_count": 2})
                    + "\n"
                ).encode()
            elif streaming:
                payload = [
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": body["model"],
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": None}],
                    },
                    {"id": "chatcmpl-local", "object": "chat.completion.chunk", "created": 1, "model": body["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": body["model"],
                        "choices": [],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                    },
                ]
                data = ("".join(f"data: {json.dumps(chunk)}\n\n" for chunk in payload) + "data: [DONE]\n\n").encode()
            else:
                data = json.dumps(payload).encode()
            try:
                self.send_response(state.status)
                self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Retry-After", "30")
                self.end_headers()
                self.wfile.write(data)
            except ConnectionError:
                if not state.hold:
                    raise

    class Server(ThreadingHTTPServer):
        daemon_threads = False

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.url = f"http://127.0.0.1:{server.server_port}"
        try:
            yield state
        finally:
            state.release.set()
            server.shutdown()
            thread.join(timeout=5)


@pytest_asyncio.fixture(autouse=True)
async def sdk_clients():
    yield
    try:
        await asyncio.wait_for(GLOBAL_LOGGING_WORKER.flush(), timeout=5)
    finally:
        await litellm.close_litellm_async_clients()


@pytest.fixture
def model_factory(monkeypatch, provider):
    # The directory conftest skips rag.llm's heavy auto-discovery. Supply only
    # the two production routing entries exercised by this transport test.
    for name in ("OpenRouter", "MiniMax", "DeepSeek", "Bedrock", "GPUStack", "Ollama"):
        if not hasattr(chat_model.SupportedLiteLLMProvider, name):
            monkeypatch.setattr(chat_model.SupportedLiteLLMProvider, name, name, raising=False)
    for name, prefix in (("OpenAI", "openai/"), ("Ollama", "ollama_chat/")):
        monkeypatch.setitem(chat_model.LITELLM_PROVIDER_PREFIX, name, prefix)
        monkeypatch.setitem(chat_model.FACTORY_DEFAULT_BASE_URL, name, provider.url + ("/v1" if name == "OpenAI" else ""))

    def create(name="OpenAI", model="gpt-4o-mini", **kwargs):
        return chat_model.LiteLLMBase("test-key", model, provider=name, max_retries=0, **kwargs)

    return create


@pytest.mark.parametrize("module,symbol", [("litellm.integrations.custom_logger", "CustomLogger"), ("litellm.caching.caching", "Cache")])
def test_sdk_import_chains(module, symbol):
    assert getattr(importlib.import_module(module), symbol)


@pytest.mark.parametrize("streaming", [False, True])
async def test_chat_transport_and_usage(model_factory, provider, streaming):
    model = model_factory()
    token = set_llm_request_context(session_id="compat-session")
    try:
        history = [{"role": "user", "content": "hello"}]
        options = {"temperature": 0, "model_type": "chat", "max_completion_tokens": 64}
        if streaming:
            chunks = [chunk async for chunk in model.async_chat_streamly("system", history, options)]
            assert "".join(chunk for chunk in chunks if isinstance(chunk, str)) == "OK"
            assert chunks[-1] == 5
        else:
            assert await model.async_chat("system", history, options) == ("OK", 5)
    finally:
        reset_llm_request_context(token)
    assert model.last_usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    path, headers, body = provider.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["authorization"] == "Bearer test-key"
    assert body["user"] == "compat-session"
    assert body["messages"][0] == {"role": "system", "content": "system"}
    assert body["max_completion_tokens"] == 64
    assert "model_type" not in body
    if streaming:
        assert body["stream_options"]["include_usage"] is True


@pytest.mark.parametrize("status,error_class", [(401, litellm.AuthenticationError), (429, litellm.RateLimitError), (503, litellm.ServiceUnavailableError)])
async def test_typed_provider_failure(model_factory, provider, status, error_class):
    provider.status = status
    model = model_factory(raise_on_error=True)
    with pytest.raises(error_class) as caught:
        await model.async_chat("", [{"role": "user", "content": "hello"}], {})
    assert caught.value.status_code == status
    assert len(provider.requests) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_ollama_native_thinking_and_context(model_factory, provider, streaming, enabled):
    model = model_factory("Ollama", "qwen3:8b")
    config = {"extra_body": {"num_ctx": 8192}, "max_completion_tokens": 64, "enable_thinking": enabled}
    if streaming:
        chunks = [chunk async for chunk in model.async_chat_streamly("", [{"role": "user", "content": "hello"}], config)]
        assert "".join(chunk for chunk in chunks if isinstance(chunk, str)) == "OK"
        assert chunks[-1] == 5
    else:
        assert await model.async_chat("", [{"role": "user", "content": "hello"}], config) == ("OK", 5)
    assert config == {"extra_body": {"num_ctx": 8192}, "max_completion_tokens": 64, "enable_thinking": enabled}
    path, headers, body = provider.requests[0]
    assert path == "/api/chat"
    assert headers["authorization"] == "Bearer test-key"
    assert body["think"] is enabled
    assert body["options"]["num_ctx"] == 8192
    assert "enable_thinking" not in body
    assert "extra_body" not in body


async def test_tool_call_serialization(model_factory, provider):
    provider.tool_call = True
    model = model_factory()
    model.tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}}}}]
    args = model._construct_completion_args([{"role": "user", "content": "lookup x"}], stream=False, tools=True)
    response = await litellm.acompletion(**args, timeout=5)
    call = response.choices[0].message.tool_calls[0]
    assert call.function.name == "lookup"
    assert json.loads(call.function.arguments) == {"key": "x"}
    assert provider.requests[0][2]["tools"] == model.tools
    assert provider.requests[0][2]["tool_choice"] == "auto"


async def test_sync_multimodal_completion(model_factory, provider):
    model = model_factory()
    content = [
        {"type": "text", "text": "Describe this image"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
    ]
    args = model._construct_completion_args([{"role": "user", "content": content}], stream=False, tools=False)

    def completion():
        # The sync SDK also runs async service telemetry on its worker thread.
        # Give that thread an owned loop, including shutdown of its callbacks.
        with asyncio.Runner():
            return litellm.completion(**args, timeout=5)

    response = await asyncio.to_thread(completion)
    assert response.choices[0].message.content == "OK"
    assert response.usage.total_tokens == 5
    assert provider.requests[0][2]["messages"][0]["content"] == content


@pytest.mark.parametrize("status", [200, 401, 503])
async def test_model_metadata_aiohttp_transport(provider, status):
    from rag.llm.model_meta import Base

    provider.status = status
    model = Base("test-key", provider.url)
    result = await model._get_raw_model_list()
    assert result == ({"data": [{"id": "local-model"}]} if status == 200 else None)
    path, headers, _body = provider.requests[0]
    assert path == "/v1/models"
    assert headers["authorization"] == "Bearer test-key"


async def test_cancellation_reaches_sdk_transport(model_factory, provider):
    provider.hold = True
    model = model_factory()
    task = asyncio.create_task(model.async_chat("", [{"role": "user", "content": "hello"}], {}))
    try:
        assert await asyncio.to_thread(provider.received.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    finally:
        provider.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(provider.requests) == 1


def test_completion_preserves_borrowed_transport_resource(model_factory):
    class BorrowedClient:
        def __deepcopy__(self, _memo):
            raise TypeError("A borrowed transport cannot be copied")

    client = BorrowedClient()
    model = model_factory()
    arguments = model._construct_completion_args([{"role": "user", "content": "synthetic control"}], stream=False, tools=False, http_client=client)
    assert arguments["http_client"] is client
