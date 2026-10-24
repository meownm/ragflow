"""Real SDK transports are closed before a Business Documents loop exits."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import openai
import pytest

from test.unit_test.api.apps.business_documents.test_business_document_adapter_contracts import RAGFlowLLMAdapter
from business_documents.async_runtime import run_in_worker_loop
from api.apps.business_documents.recovery import is_transient_failure
from api.db.services import llm_service, tenant_llm_service
from rag.llm.chat_model import OpenAI_APIChat


@pytest.fixture
def provider():
    state = SimpleNamespace(status=200, hold=False, received=Event(), release=Event())

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.received.set()
            if state.hold:
                state.release.wait(5)
            payload = {
                "id": "local",
                "object": "chat.completion",
                "created": 1,
                "model": "local",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            if state.status != 200:
                payload = {"error": {"message": "synthetic provider failure", "type": "server_error"}}
            streaming = body.get("stream") and state.status == 200
            if streaming:
                payload["choices"] = [{"index": 0, "delta": {"content": "{}"}, "finish_reason": "stop"}]
                data = ("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode()
            else:
                data = json.dumps(payload).encode()
            try:
                self.send_response(state.status)
                self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except ConnectionError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def adapter_models(provider, monkeypatch):
    models = []
    config = {"llm_factory": "OpenAI-API-Compatible", "llm_name": "local", "model_type": "chat", "max_tokens": 65536, "max_output_tokens": 4096, "api_base": provider.url}

    def model_instance(_config, **kwargs):
        model = OpenAI_APIChat("synthetic", "local", provider.url, **kwargs)
        models.append(model)
        return model

    from api.db.joint_services import tenant_model_service

    monkeypatch.setattr(tenant_model_service, "get_tenant_default_model_by_type", lambda *_args: config)
    monkeypatch.setattr(tenant_llm_service.TenantLLMService, "model_instance", model_instance)
    monkeypatch.setattr(tenant_llm_service.TenantLangfuseService, "filter_by_tenant", lambda **_kwargs: None)
    try:
        yield models
    finally:
        for model in models:
            model.client.close()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", ["success", "provider_error", "timeout", "cancel"])
def test_adapter_closes_real_sdk_on_its_request_loop(provider, adapter_models, streaming, mode):
    models, loops, chunks = adapter_models, [], []
    provider.status = 503 if mode == "provider_error" else 200
    provider.hold = mode in {"timeout", "cancel"}

    async def call():
        loop = asyncio.get_running_loop()
        loops.append(loop)
        task = asyncio.create_task(RAGFlowLLMAdapter().async_generate("tenant", "system", {}, chunks.append if streaming else None))
        try:
            if provider.hold:
                assert await asyncio.to_thread(provider.received.wait, 3)
                if mode == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(task, timeout=0.05)
            elif mode == "provider_error":
                with pytest.raises(openai.APIStatusError):
                    await task
            else:
                assert await task == "{}"
                assert chunks == (["{}"] if streaming else [])
        finally:
            assert models and all(model.async_client.is_closed() for model in models)
            assert not loop.is_closed()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_in_worker_loop, call()).result(timeout=10)
    assert len(models) == 1 and loops[0].is_closed()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("primary", ["503", "cancel", "timeout", "success"])
@pytest.mark.parametrize("flush_failure", ["error", "cancel"])
def test_adapter_preserves_operation_error_when_callback_flush_fails(provider, adapter_models, monkeypatch, caplog, streaming, primary, flush_failure):
    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    cleanup_error = RuntimeError("injected callback flush failure")
    operation_errors, loops = [], []
    provider.status = 503 if primary == "503" else 200
    provider.hold = primary in {"cancel", "timeout"}
    original_chat = llm_service.LLMBundle.async_chat
    original_stream = llm_service.LLMBundle.async_chat_streamly_delta

    async def tracked_chat(self, *args, **kwargs):
        try:
            return await original_chat(self, *args, **kwargs)
        except BaseException as error:
            operation_errors.append(error)
            raise

    async def tracked_stream(self, *args, **kwargs):
        try:
            async for chunk in original_stream(self, *args, **kwargs):
                yield chunk
        except BaseException as error:
            operation_errors.append(error)
            raise

    monkeypatch.setattr(llm_service.LLMBundle, "async_chat", tracked_chat)
    monkeypatch.setattr(llm_service.LLMBundle, "async_chat_streamly_delta", tracked_stream)

    async def call():
        loop = asyncio.get_running_loop()
        loops.append(loop)
        task = None
        chunks = []
        flush_calls = []

        async def failing_flush():
            flush_calls.append(True)
            if flush_failure == "error":
                raise cleanup_error
            # A new cancellation during flush must propagate, while a repeat
            # cancellation must preserve the original operation's exception.
            task.cancel("callback cleanup cancellation")
            await asyncio.sleep(0)

        monkeypatch.setattr(GLOBAL_LOGGING_WORKER, "flush", failing_flush)
        task = asyncio.create_task(RAGFlowLLMAdapter().async_generate("tenant", "system", {}, chunks.append if streaming else None))
        try:
            if provider.hold:
                assert await asyncio.to_thread(provider.received.wait, 3)
            if primary == "timeout":
                with pytest.raises(TimeoutError) as caught:
                    await asyncio.wait_for(task, timeout=0.05)
                assert caught.value.__cause__ is operation_errors[0]
            else:
                if primary == "cancel":
                    task.cancel("original operation cancellation")
                expected = asyncio.CancelledError if flush_failure == "cancel" or primary == "cancel" else openai.InternalServerError if primary == "503" else RuntimeError
                with pytest.raises(expected) as caught:
                    await task
                if primary == "cancel" or (primary == "503" and flush_failure == "error"):
                    assert caught.value is operation_errors[0]
                elif flush_failure == "error":
                    assert caught.value is cleanup_error
                else:
                    assert caught.value.args == ("callback cleanup cancellation",)
                if primary == "503" and flush_failure == "error":
                    assert is_transient_failure(caught.value)
            assert flush_calls == [True]
            if primary == "success":
                assert chunks == (["{}"] if streaming else [])
        finally:
            assert adapter_models and all(model.async_client.is_closed() for model in adapter_models)
            assert not loop.is_closed()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_in_worker_loop, call()).result(timeout=10)
    assert loops[0].is_closed()
    if primary != "success" and flush_failure == "error":
        assert "callback cleanup failed; preserving the operation error" in caplog.text


def test_async_bundle_closes_only_its_model_and_preserves_sync_cleanup():
    calls = []

    class Model:
        async def aclose(self):
            calls.append(("async", asyncio.get_running_loop().is_closed()))

        def close(self):
            calls.append(("sync", None))

    async def call():
        neighbor = llm_service.LLMBundle.__new__(llm_service.LLMBundle)
        neighbor.langfuse = Mock()
        neighbor.mdl = SimpleNamespace(close=Mock())
        bundle = llm_service.LLMBundle.__new__(llm_service.LLMBundle)
        bundle.langfuse = Mock()
        bundle.mdl = Model()
        async with bundle:
            pass
        assert bundle.langfuse is None
        neighbor.mdl.close.assert_not_called()
        await neighbor.aclose()
        neighbor.mdl.close.assert_called_once()
        assert neighbor.langfuse is None

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_in_worker_loop, call()).result(timeout=5)
    assert calls == [("async", False), ("sync", None)]


def _bundle_for_model(model):
    bundle = llm_service.LLMBundle.__new__(llm_service.LLMBundle)
    bundle.mdl, bundle.langfuse = model, None
    return bundle


@pytest.mark.parametrize("primary", ["cancel", "503", "success"])
def test_cleanup_failure_preserves_operation_error(provider, monkeypatch, caplog, primary):
    provider.status = 503 if primary == "503" else 200
    cleanup_error = RuntimeError("injected cleanup failure")

    async def call():
        model = OpenAI_APIChat("synthetic", "local", provider.url, max_retries=0, raise_on_error=True)
        bundle = _bundle_for_model(model)
        original_close = model.async_client._client._transport.aclose
        operation_error = None

        async def failing_close():
            await original_close()
            raise cleanup_error

        monkeypatch.setattr(model.async_client._client._transport, "aclose", failing_close)
        try:
            with pytest.raises(asyncio.CancelledError if primary == "cancel" else openai.InternalServerError if primary == "503" else RuntimeError) as caught:
                async with bundle:
                    try:
                        await model.async_client.chat.completions.create(model="local", messages=[{"role": "user", "content": "x"}])
                        if primary == "cancel":
                            raise asyncio.CancelledError("operation cancelled")
                    except BaseException as error:
                        operation_error = error
                        raise
            assert caught.value is (cleanup_error if primary == "success" else operation_error)
            if primary == "503":
                assert is_transient_failure(caught.value)
            assert bundle.langfuse is None
        finally:
            model.client.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_in_worker_loop, call()).result(timeout=10)
    if primary != "success":
        assert "preserving the operation error" in caplog.text


@pytest.mark.parametrize("operation_cancelled", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_repeated_cancellation_joins_real_tcp_close(provider, monkeypatch, caplog, operation_cancelled, cleanup_fails):
    loops = []

    async def call():
        loop = asyncio.get_running_loop()
        loops.append(loop)
        model = OpenAI_APIChat("synthetic", "local", provider.url, max_retries=0, raise_on_error=True)
        bundle = _bundle_for_model(model)
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        operation_error = asyncio.CancelledError("operation cancelled")
        original_close = None
        network_stream = None

        async def operation():
            nonlocal original_close, network_stream
            async with bundle:
                await model.async_client.chat.completions.create(model="local", messages=[{"role": "user", "content": "x"}])
                connection = model.async_client._client._transport._pool.connections[0]
                network_stream = connection._connection._network_stream
                original_close = network_stream.aclose

                async def held_close():
                    entered.set()
                    await release.wait()
                    await original_close()
                    assert network_stream._stream._transport.is_closing()
                    closed.append(not loop.is_closed())
                    if cleanup_fails:
                        raise RuntimeError("injected cleanup failure after TCP close")

                monkeypatch.setattr(network_stream, "aclose", held_close)
                if operation_cancelled:
                    raise operation_error

        task = asyncio.create_task(operation())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            for message in ("first cleanup cancellation", "second cleanup cancellation"):
                task.cancel(message)
                await asyncio.sleep(0)
                assert not task.done() and not closed
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            if operation_cancelled:
                assert caught.value is operation_error
            else:
                assert caught.value.args == ("first cleanup cancellation",)
            assert closed == [True]
            assert not loop.is_closed()
            await bundle.aclose()
            assert closed == [True]
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if original_close is not None:
                await original_close()
            model.client.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(run_in_worker_loop, call()).result(timeout=10)
    assert loops[0].is_closed()
    if cleanup_fails:
        assert "cleanup failed after cancellation" in caplog.text
