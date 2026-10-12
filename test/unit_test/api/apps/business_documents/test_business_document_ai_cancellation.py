"""The LLM adapter closes resources and never hides partial stream failures."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_adapter_contracts import ai_module, RAGFlowLLMAdapter
from api.apps.business_documents.assets import prompt_descriptor
from api.db.db_models import BusinessDocumentJob
from business_documents.application.errors import BusinessDocumentError


@pytest.fixture()
def model_boundary(monkeypatch):
    config = {"llm_factory": "Ollama", "llm_name": "local-chat", "model_type": "chat"}
    calls = {"bundle": [], "chat": 0, "closed": 0, "drained": 0}
    lookup = ModuleType("api.db.joint_services.tenant_model_service")
    lookup.get_tenant_default_model_by_type = Mock(return_value=config)
    monkeypatch.setitem(sys.modules, lookup.__name__, lookup)
    models = ModuleType("api.db.services.llm_service")

    class Bundle:
        mode = "normal"

        def __init__(self, *args, **kwargs):
            calls["bundle"].append((args, kwargs))
            self.mdl = SimpleNamespace(last_usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls["closed"] += 1

        async def async_chat(self, *_args):
            calls["chat"] += 1
            return "fallback-result"

        async def async_chat_streamly_delta(self, *_args):
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

    monkeypatch.setattr(ai_module, "_drain_litellm_callbacks", drain)
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
    assert audit["parameters"]["max_completion_tokens"] == 8192
    assert adapter.consume_execution_audit() is None


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
async def test_quality_override_is_local_to_requested_attempt(model_boundary):
    config, _, _, calls = model_boundary
    await RAGFlowLLMAdapter("qualified-local-model").async_generate("tenant", "system", {})
    assert calls["bundle"][0][0][1]["llm_name"] == "qualified-local-model"
    assert calls["bundle"][0][0][1]["max_tokens"] == 4096
    assert config["llm_name"] == "local-chat" and "max_tokens" not in config


@pytest.mark.parametrize("response, code", [(None, "EMPTY_AI_RESPONSE"), ("[]", "INVALID_AI_JSON")])
def test_ai_worker_rejects_empty_and_non_object_model_response(response, code):
    adapter = SimpleNamespace(generate=Mock(return_value=response))
    job = BusinessDocumentJob(tenant_id="disposable-tenant", job_type="ASSESS_INTAKE", attempt=1, payload={"prompt": prompt_descriptor("ASSESS_INTAKE")})
    with pytest.raises(BusinessDocumentError) as caught:
        ai_module.BusinessDocumentAI(adapter).process(job)
    assert caught.value.code == code
    adapter.generate.assert_called_once()


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
