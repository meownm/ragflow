"""The LLM adapter closes resources and never hides partial stream failures."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from business_documents.adapters import llm as llm_module

from test.unit_test.api.apps.business_documents.test_business_document_adapter_contracts import RAGFlowLLMAdapter
from api.apps.business_documents import ai as ai_module
from api.apps.business_documents.ai_prompt import build_prompt
from api.apps.business_documents.assets import prompt_descriptor
from api.db.db_models import BusinessDocumentJob
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def model_boundary(monkeypatch):
    config = {"llm_factory": "Ollama", "llm_name": "local-chat", "model_type": "chat", "max_tokens": 131072}
    calls = {"bundle": [], "chat": 0, "closed": 0, "drained": 0, "parameters": []}
    lookup = ModuleType("api.db.joint_services.tenant_model_service")
    lookup.get_tenant_default_model_by_type = Mock(return_value=config)
    monkeypatch.setitem(sys.modules, lookup.__name__, lookup)
    models = ModuleType("api.db.services.llm_service")

    class Bundle:
        mode = "normal"

        def __init__(self, *args, **kwargs):
            calls["bundle"].append((args, kwargs))
            self.mdl = SimpleNamespace(last_usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            calls["closed"] += 1

        async def async_chat(self, *_args):
            calls["parameters"].append(_args[2])
            calls["chat"] += 1
            return "fallback-result"

        async def async_chat_streamly_delta(self, *_args):
            calls["parameters"].append(_args[2])
            if self.mode == "unsupported":
                raise RuntimeError("Provider does not implement streaming")
            if self.mode == "error_before_output":
                raise RuntimeError("Provider transport failed")
            yield "first"
            if self.mode == "error_after_output":
                raise RuntimeError("Provider does not implement remaining output")
            if self.mode == "cancel":
                raise asyncio.CancelledError()
            yield "second"

    models.LLMBundle = Bundle
    monkeypatch.setitem(sys.modules, models.__name__, models)

    async def drain():
        calls["drained"] += 1

    monkeypatch.setattr(llm_module, "_drain_litellm_callbacks", drain)
    return config, lookup, Bundle, calls


@pytest.mark.asyncio
async def test_stream_success_delivers_each_chunk_and_single_consumable_audit(model_boundary):
    _, _, _, calls = model_boundary
    adapter = RAGFlowLLMAdapter()
    chunks = []
    result = await adapter.async_generate("disposable-tenant", "system", {"job_input": {"task_type": "GENERATE_DRAFT"}}, chunks.append)
    assert result == "firstsecond" and chunks == ["first", "second"]
    assert calls["chat"] == 0 and calls["closed"] == calls["drained"] == 1
    audit = adapter.consume_execution_audit()
    assert audit["provider"] == "Ollama" and audit["model"] == "local-chat"
    assert audit["token_usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert audit["parameters"]["max_completion_tokens"] > 65536
    assert adapter.consume_execution_audit() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("task", ["GENERATE_DRAFT", "GENERATE_EVA_CHANGE", "ASSESS_REVIEW", "VERIFY_REQUIREMENT_COVERAGE"])
async def test_large_model_response_is_returned_complete_with_auditable_budget(model_boundary, monkeypatch, stream, task):
    from common.token_utils import encoder
    from business_documents.domain.execution_audit import validate_ai_audit

    _, _, bundle, calls = model_boundary
    response = json.dumps({"text": "complete requirement " * 12000})
    response_tokens = len(encoder.encode(response))
    assert response_tokens > 8192

    async def chat(_self, _system, _messages, parameters):
        calls["parameters"].append(parameters)
        return response

    async def chunks(_self, _system, _messages, parameters):
        calls["parameters"].append(parameters)
        for offset in range(0, len(response), 8192):
            yield response[offset : offset + 8192]

    monkeypatch.setattr(bundle, "async_chat", chat)
    monkeypatch.setattr(bundle, "async_chat_streamly_delta", chunks)
    received = []
    adapter = RAGFlowLLMAdapter()
    assert await adapter.async_generate("tenant", "system", {"job_input": {"task_type": task}}, received.append if stream else None) == response
    if stream:
        assert "".join(received) == response
    audit = validate_ai_audit(adapter.consume_execution_audit())
    assert audit["parameters"]["max_completion_tokens"] > max(65536, response_tokens)
    assert calls["closed"] == calls["drained"] == 1


@pytest.mark.asyncio
async def test_stream_fallback_is_allowed_only_before_first_chunk(model_boundary):
    _, _, bundle, calls = model_boundary
    bundle.mode = "unsupported"
    adapter = RAGFlowLLMAdapter()
    chunks = []
    result = await adapter.async_generate("tenant", "system", {}, chunks.append)
    assert result == "fallback-result" and chunks == []
    assert calls["chat"] == 1 and calls["closed"] == calls["drained"] == 1
    assert adapter.consume_execution_audit() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, expected_chunks", [("error_before_output", []), ("error_after_output", ["first"]), ("cancel", ["first"])])
async def test_stream_failure_or_cancellation_is_propagated_without_retry(model_boundary, mode, expected_chunks):
    _, _, bundle, calls = model_boundary
    bundle.mode = mode
    adapter = RAGFlowLLMAdapter()
    chunks = []
    with pytest.raises(asyncio.CancelledError if mode == "cancel" else RuntimeError):
        await adapter.async_generate("tenant", "system", {}, chunks.append)
    assert chunks == expected_chunks and calls["chat"] == 0
    assert calls["closed"] == calls["drained"] == 1
    assert adapter.consume_execution_audit() is None


@pytest.mark.asyncio
async def test_missing_tenant_model_never_creates_provider_bundle(model_boundary):
    _, lookup, _, calls = model_boundary
    lookup.get_tenant_default_model_by_type.side_effect = ValueError("No tenant chat model configured")
    with pytest.raises(ValueError, match="No tenant chat model"):
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {})
    assert calls["bundle"] == [] and calls["closed"] == calls["drained"] == 0


@pytest.mark.asyncio
async def test_quality_override_rejects_other_provider_and_keeps_configuration(model_boundary):
    config, _, _, calls = model_boundary
    config["llm_factory"] = "OpenAI"
    with pytest.raises(ValueError, match="requires Ollama"):
        await RAGFlowLLMAdapter("another-local-model").async_generate("tenant", "system", {})
    assert config["llm_name"] == "local-chat" and calls["bundle"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [65536, 131072, 262144])
async def test_quality_override_is_local_to_requested_attempt(model_boundary, monkeypatch, window):
    config, _, _, calls = model_boundary
    import httpx

    config.update(api_base="http://provider/v1/", api_key="tenant-secret")
    lookups = []

    async def show(_client, url, **kwargs):
        assert url == "http://provider/api/show" and kwargs["headers"] == {"Authorization": "Bearer tenant-secret"}
        assert kwargs["json"] == {"model": "qualified-local-model"}
        lookups.append(url)
        return httpx.Response(200, json={"model_info": {"qwen.context_length": window}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    adapter = RAGFlowLLMAdapter("qualified-local-model")
    for task in ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]:
        await adapter.async_generate("tenant", "system", {"job_input": {"task_type": task}})
    assert calls["bundle"][0][0][1]["llm_name"] == "qualified-local-model"
    assert calls["bundle"][0][0][1]["max_tokens"] == window
    assert config["llm_name"] == "local-chat" and config["max_tokens"] == 131072
    assert len(lookups) == len(calls["bundle"]) == 2
    assert all(parameters["extra_body"] == {"num_ctx": window} for parameters in calls["parameters"])


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {"model_info": None}, {"model_info": {"qwen.context_length": True}}])
async def test_override_requires_the_selected_models_window(model_boundary, monkeypatch, metadata):
    import httpx

    _, _, _, calls = model_boundary

    async def show(_client, url, **kwargs):
        return httpx.Response(200, json=metadata, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    with pytest.raises(BusinessDocumentError) as caught:
        await RAGFlowLLMAdapter("unknown-window").async_generate("tenant", "system", {})
    assert caught.value.code == "MODEL_CONTEXT_UNAVAILABLE" and calls["bundle"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [65536, 131072, 262144])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("task", ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE", "GENERATE_DRAFT", "GENERATE_EVA_CHANGE", "ASSESS_REVIEW"])
async def test_context_budget_and_native_window_agree(model_boundary, window, stream, task):
    from common.token_utils import encoder
    from business_documents.domain.execution_audit import validate_ai_audit

    config, _, _, calls = model_boundary
    config["max_tokens"] = window
    # A complete large input, not a token-count stub. More than 64k at 128k/256k.
    payload = {"job_input": {"task_type": task, "text": " complete requirement" * (window // 4)}}
    snapshot = json.dumps(payload, ensure_ascii=False)
    available = window - len(encoder.encode("system")) - len(encoder.encode(snapshot)) - 256
    adapter = RAGFlowLLMAdapter()
    await adapter.async_generate("tenant", "system", payload, (lambda _: None) if stream else None)
    assert calls["parameters"] == [{"temperature": 0, "top_p": 0.1, "max_completion_tokens": available, "extra_body": {"num_ctx": window}}]
    assert len(calls["bundle"]) == 1
    assert json.dumps(payload, ensure_ascii=False) == snapshot
    assert validate_ai_audit(adapter.consume_execution_audit())["parameters"] == calls["parameters"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [65536, 131072, 262144])
async def test_complete_change_prompt_fits_budget_without_truncating_history(model_boundary, monkeypatch, window):
    from common.token_utils import encoder
    from test.unit_test.api.apps.business_documents.test_business_document_ai_export_boundaries import _coverage_job

    config, _, bundle, calls = model_boundary
    config["max_tokens"] = window
    job = _coverage_job()
    job.payload["source_events"].append({"event_id": "historical", "event_type": "ChangesApplied", "payload": {"text": " complete history" * 40000}})
    snapshot = json.dumps(job.payload, ensure_ascii=False)
    prompt = build_prompt(job.job_type, job.payload)
    serialized = json.dumps(prompt.input_payload, ensure_ascii=False)
    used = len(encoder.encode(prompt.system)) + len(encoder.encode(serialized)) + 256
    assert 65536 < used < 131072

    async def chat(_self, system, messages, _parameters):
        assert system == prompt.system
        assert messages == [{"role": "user", "content": serialized}]
        return "result"

    monkeypatch.setattr(bundle, "async_chat", chat)
    adapter = RAGFlowLLMAdapter()
    if window == 65536:
        with pytest.raises(BusinessDocumentError) as caught:
            await adapter.async_generate("tenant", prompt.system, prompt.input_payload)
        assert caught.value.code == "MODEL_CONTEXT_TOO_LARGE" and not calls["bundle"]
    else:
        assert await adapter.async_generate("tenant", prompt.system, prompt.input_payload) == "result"
        audit = adapter.consume_execution_audit()
        assert audit["parameters"]["max_completion_tokens"] == window - used
        assert audit["parameters"]["extra_body"] == {"num_ctx": window}
        assert len(calls["bundle"]) == calls["closed"] == calls["drained"] == 1
    assert json.dumps(job.payload, ensure_ascii=False) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [65536, 131072, 262144])
@pytest.mark.parametrize(
    "task,remaining,code",
    [
        ("PLAN_CHANGES", 0, "MODEL_CONTEXT_TOO_LARGE"),
        ("VERIFY_REQUIREMENT_COVERAGE", 0, "REQUIREMENT_CONTEXT_TOO_LARGE"),
        ("VERIFY_REQUIREMENT_COVERAGE", 1, "REQUIREMENT_CONTEXT_TOO_LARGE"),
        ("VERIFY_REQUIREMENT_COVERAGE", 7, "REQUIREMENT_CONTEXT_TOO_LARGE"),
        ("VERIFY_REQUIREMENT_COVERAGE", 8, None),
        ("VERIFY_REQUIREMENT_COVERAGE", 2047, None),
        ("VERIFY_REQUIREMENT_COVERAGE", 2048, None),
    ],
)
async def test_budget_boundaries_stop_before_provider(model_boundary, monkeypatch, window, task, remaining, code):
    from common import token_utils

    config, _, _, calls = model_boundary
    config["max_tokens"] = window
    encoded = []

    def encode(text, **kwargs):
        assert kwargs == {"disallowed_special": ()}
        encoded.append(text)
        return range(1 if text == "system" else window - 257 - remaining)

    monkeypatch.setattr(token_utils, "encoder", SimpleNamespace(encode=encode))
    adapter = RAGFlowLLMAdapter()
    if code:
        with pytest.raises(BusinessDocumentError) as caught:
            await adapter.async_generate("tenant", "system", {"job_input": {"task_type": task}})
        assert caught.value.code == code
        assert calls["bundle"] == [] and adapter.consume_execution_audit() is None
    else:
        await adapter.async_generate("tenant", "system", {"job_input": {"task_type": task}})
        assert calls["parameters"][0]["max_completion_tokens"] == remaining
    assert len(encoded) == 2  # Each message is counted once, no repeated budget pass.


@pytest.mark.asyncio
async def test_budget_counts_special_token_looking_document_text_as_data(model_boundary):
    _, _, _, calls = model_boundary
    await RAGFlowLLMAdapter().async_generate("tenant", "system", {"job_input": {"task_type": "PLAN_CHANGES", "text": "Preserve <|endoftext|> literally"}})
    assert len(calls["bundle"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [65536, 131072, 262144])
@pytest.mark.parametrize("stream", [False, True])
async def test_compatible_ollama_uses_loaded_window_and_qwen_content_budget(model_boundary, monkeypatch, window, stream):
    import httpx
    from common.token_utils import encoder
    from business_documents.domain.execution_audit import validate_ai_audit

    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI-API-Compatible", llm_name="qwen3.8:latest", api_base="http://provider/v1/", api_key="tenant-secret", max_tokens=8192)
    lookups = []

    async def ps(_client, url, **kwargs):
        assert url == "http://provider/api/ps" and kwargs["headers"] == {"Authorization": "Bearer tenant-secret"}
        lookups.append(url)
        return httpx.Response(200, json={"models": [{"name": "unrelated", "context_length": 4096}, {"name": "qwen3.8:latest", "context_length": window}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", ps)
    adapter = RAGFlowLLMAdapter()
    for task in ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]:
        payload = {"job_input": {"task_type": task, "text": " full requirement" * 12000}}
        available = window - len(encoder.encode("system")) - len(encoder.encode(json.dumps(payload, ensure_ascii=False))) - 256
        await adapter.async_generate("tenant", "system", payload, (lambda _: None) if stream else None)
        expected = {"temperature": 0, "top_p": 0.1, "max_tokens": available, "reasoning_effort": "none"}
        assert calls["parameters"][-1] == expected
        assert validate_ai_audit(adapter.consume_execution_audit())["parameters"] == expected
    assert len(lookups) == len(calls["bundle"]) == 2
    assert config["max_tokens"] == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{"models": [{"name": "qwen3", "context_length": None}]}, {"models": [{"name": "qwen3", "context_length": True}]}])
async def test_compatible_ollama_cannot_invent_a_loaded_model_window(model_boundary, monkeypatch, metadata):
    import httpx

    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI-API-Compatible", llm_name="qwen3", api_base="http://provider/v1")

    async def ps(_client, url, **kwargs):
        return httpx.Response(200, json=metadata, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", ps)
    with pytest.raises(BusinessDocumentError) as caught:
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {})
    assert caught.value.code == "MODEL_CONTEXT_UNAVAILABLE" and calls["bundle"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status,metadata", [(401, None), (404, None), (405, None), (503, None), (200, {"models": []}), (200, {"models": [{"name": "another-model", "context_length": 4096}]})])
async def test_non_ollama_compatible_keeps_its_configured_window(model_boundary, monkeypatch, status, metadata):
    import httpx
    from common.token_utils import encoder

    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI-API-Compatible", llm_name="other-model", api_base="http://provider/v1", max_output_tokens=131072)

    async def ps(_client, url, **kwargs):
        return httpx.Response(status, json=metadata, request=httpx.Request("GET", url))

    async def show(_client, url, **kwargs):
        return httpx.Response(404, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", ps)
    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    payload = {"job_input": {"task_type": "PLAN_CHANGES"}}
    await RAGFlowLLMAdapter().async_generate("tenant", "system", payload)
    available = config["max_tokens"] - len(encoder.encode("system")) - len(encoder.encode(json.dumps(payload))) - 256
    assert calls["parameters"] == [{"temperature": 0, "top_p": 0.1, "max_tokens": available}]


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded", [[], [{"name": "other-model", "context_length": 4096}]])
@pytest.mark.parametrize("stream", [False, True])
async def test_unloaded_compatible_ollama_uses_native_explicit_context(model_boundary, monkeypatch, loaded, stream):
    import httpx
    from common.token_utils import encoder

    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI-API-Compatible", llm_name="qwen3.8:latest", api_base="http://provider/v1/", api_key="tenant-secret", max_tokens=8192)
    original = dict(config)
    requests = []

    async def ps(_client, url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"models": loaded}, request=httpx.Request("GET", url))

    async def show(_client, url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"details": {"family": "qwen35"}, "model_info": {"qwen35.context_length": 262144}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", ps)
    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    payload = {"job_input": {"task_type": "PLAN_CHANGES", "text": " full requirement" * 12000}}
    adapter = RAGFlowLLMAdapter()
    await adapter.async_generate("tenant", "system", payload, (lambda _: None) if stream else None)
    available = 262144 - len(encoder.encode("system")) - len(encoder.encode(json.dumps(payload))) - 256
    assert calls["parameters"] == [{"temperature": 0, "top_p": 0.1, "max_completion_tokens": available, "extra_body": {"num_ctx": 262144}}]
    resolved = calls["bundle"][0][0][1]
    assert resolved == {**original, "llm_factory": "Ollama", "api_base": "http://provider", "max_tokens": 262144}
    assert requests == [
        ("http://provider/api/ps", {"headers": {"Authorization": "Bearer tenant-secret"}}),
        ("http://provider/api/show", {"headers": {"Authorization": "Bearer tenant-secret"}, "json": {"model": "qwen3.8:latest"}}),
    ]
    assert config == original
    assert adapter.consume_execution_audit()["provider"] == "Ollama"


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {"model_info": None}, {"model_info": {"qwen.context_length": True}}, {"model_info": {"qwen.context_length": -1}}])
async def test_unloaded_compatible_unknown_metadata_never_guesses_output(model_boundary, monkeypatch, metadata):
    import httpx

    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI-API-Compatible", llm_name="unknown", api_base="http://provider/v1")

    async def ps(_client, url, **kwargs):
        return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

    async def show(_client, url, **kwargs):
        return httpx.Response(200, json=metadata, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", ps)
    monkeypatch.setattr(httpx.AsyncClient, "post", show)
    with pytest.raises(BusinessDocumentError) as caught:
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {})
    assert caught.value.code == "MODEL_OUTPUT_LIMIT_UNAVAILABLE"
    assert calls["bundle"] == []


@pytest.mark.parametrize("response, code", [(None, "EMPTY_AI_RESPONSE")])
def test_ai_worker_rejects_empty_and_non_object_model_response(response, code):
    adapter = SimpleNamespace(generate=Mock(return_value=response))
    job = BusinessDocumentJob(tenant_id="disposable-tenant", job_type="ASSESS_INTAKE", attempt=1, payload={"prompt": prompt_descriptor("ASSESS_INTAKE")})
    with pytest.raises(BusinessDocumentError) as caught:
        ai_module.BusinessDocumentAI(adapter).process(job)
    assert caught.value.code == code
    assert adapter.generate.call_count == 2
    assert adapter.generate.call_args.args[2]["format_repair"]["error"]["code"] == code


def test_non_object_intake_response_is_retained_as_an_unanswered_free_text_question():
    adapter = SimpleNamespace(generate=Mock(return_value="[]"))
    job = BusinessDocumentJob(tenant_id="disposable-tenant", job_type="ASSESS_INTAKE", attempt=1, payload={"prompt": prompt_descriptor("ASSESS_INTAKE")})
    output = ai_module.BusinessDocumentAI(adapter).process(job)
    assert output["outcome"] == "NEEDS_INPUT" and output["questions"][0]["text"] == "[]"
    assert output["questions"][0]["allow_custom_answer"] and output["warnings"]
    assert adapter.generate.call_count == 1


def test_job_with_changed_prompt_is_rejected_before_calling_model():
    adapter = SimpleNamespace(generate=Mock())
    job = BusinessDocumentJob(tenant_id="disposable-tenant", job_type="ASSESS_INTAKE", attempt=1, payload={"prompt": {**prompt_descriptor("ASSESS_INTAKE"), "content_hash": "tampered-prompt"}})
    with pytest.raises(BusinessDocumentError) as caught:
        ai_module.BusinessDocumentAI(adapter).process(job)
    assert caught.value.code == "PROMPT_ASSET_CHANGED"
    adapter.generate.assert_not_called()


def test_export_job_is_rejected_by_model_worker_before_any_provider_call():
    adapter = SimpleNamespace(generate=Mock())
    job = BusinessDocumentJob(tenant_id="disposable-tenant", job_type="GENERATE_EXPORT", payload={})
    with pytest.raises(BusinessDocumentError) as caught:
        ai_module.BusinessDocumentAI(adapter).process(job)
    assert caught.value.code == "INVALID_AI_JOB"
    adapter.generate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["GENERATE_DRAFT", "ASSESS_INTAKE", "PLAN_SQL_QUERY", "ANALYZE_SQL_REQUIREMENTS", "VERIFY_REQUIREMENT_COVERAGE"])
@pytest.mark.parametrize("stream", [False, True])
async def test_openai_output_ceiling_is_independent_of_context(model_boundary, task, stream):
    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI", llm_name="gpt-4.1", max_tokens=1047576)
    await RAGFlowLLMAdapter().async_generate("tenant", "system", {"job_input": {"task_type": task}}, (lambda _: None) if stream else None)
    assert calls["parameters"][0]["max_completion_tokens"] == 32768
    assert config["max_tokens"] == 1047576


@pytest.mark.asyncio
async def test_unknown_provider_output_limit_stops_before_bundle(model_boundary):
    config, _, _, calls = model_boundary
    config.update(llm_factory="OpenAI", llm_name="unregistered-future-model", max_tokens=1000000)
    with pytest.raises(BusinessDocumentError) as error:
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {})
    assert error.value.code == "MODEL_OUTPUT_LIMIT_UNAVAILABLE"
    assert calls["bundle"] == []


@pytest.mark.asyncio
async def test_coverage_output_ceiling_must_fit_minimal_report(model_boundary):
    config, _, _, calls = model_boundary
    config["max_output_tokens"] = 7
    with pytest.raises(BusinessDocumentError) as error:
        await RAGFlowLLMAdapter().async_generate("tenant", "system", {"job_input": {"task_type": "VERIFY_REQUIREMENT_COVERAGE"}})
    assert error.value.code == "REQUIREMENT_CONTEXT_TOO_LARGE"
    assert error.value.details["required_response_tokens"] == 8
    assert error.value.details["output_limit"] == 7
    assert calls["bundle"] == []
