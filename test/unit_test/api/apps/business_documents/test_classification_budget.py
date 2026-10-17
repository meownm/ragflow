"""Complete classification responses fit dense and maximum-size evidence."""

import json
import math
from types import SimpleNamespace

import pytest

from test.unit_test.api.apps.business_documents import test_business_document_ai_cancellation as model_fixtures
from api.apps.business_documents.ai import BusinessDocumentAI, RAGFlowLLMAdapter
from api.apps.business_documents.assets import prompt_descriptor
from api.apps.business_documents.evidence import MAX_CHUNK_CHARS, MAX_CHUNKS, MAX_TOTAL_CHARS
from api.apps.business_documents.recovery import AIRecovery
from business_documents.application.errors import ValidationError
from common.token_utils import encoder

model_boundary = model_fixtures.model_boundary


def _classification(chunks):
    class Capture:
        def __init__(self):
            self.entries = []

        def generate(self, tenant, system, payload, **kwargs):
            self.entries.append((system, payload))
            return {"source_data_ids": [int(identifier) for identifier in payload["fragments"]], "model_instruction_ids": []}

    job = SimpleNamespace(id="bounded-source", job_type="GENERATE_DRAFT", tenant_id="disposable-tenant", payload={"prompt": prompt_descriptor("GENERATE_DRAFT"), "idea": "Preserve all source data"})
    assert len(chunks) <= MAX_CHUNKS and sum(map(len, chunks)) <= MAX_TOTAL_CHARS
    assert all(len(content) <= MAX_CHUNK_CHARS for content in chunks)
    evidence = {"chunks": [{"source_ref": f"ragflow://dataset/d/document/doc/chunk/{i}", "content_hash": str(i), "content": content} for i, content in enumerate(chunks)]}
    capture = Capture()
    ai = BusinessDocumentAI(capture)
    selected = ai._prepare_draft_evidence(job, ai._prompt(job, evidence), AIRecovery())
    assert ["".join(chunk["content_fragments"]) for chunk in selected.input_payload["evidence"]["chunks"]] == chunks
    return capture.entries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        ["A. " * 1000],
        ["A. " * 1333 + "X"],
        ["A. " * 666 + "XX"] * 12,
        [". " * 1000] * 12,
        [". " * 2000],
        ["A large source paragraph " * 80] * 12,
    ],
)
async def test_complete_dense_and_large_evidence_has_output_margin(model_boundary, chunks):
    config, _, _, calls = model_boundary
    config["max_tokens"] = 262144
    batches = _classification(chunks)
    assert len(batches) <= 4  # Leaves generation and repair within six calls.
    for index, (system, payload) in enumerate(batches):
        await RAGFlowLLMAdapter().async_generate("disposable-tenant", system, payload)
        identifiers = [int(identifier) for identifier in payload["fragments"]]
        for split in [0, len(identifiers) // 2, len(identifiers)]:
            response = json.dumps({"source_data_ids": identifiers[:split], "model_instruction_ids": identifiers[split:]}, indent=2)
            # A byte-granularity tokenizer must also fit, not just cl100k.
            required = len(response.encode("utf-8"))
            output_budget = calls["parameters"][index]["max_completion_tokens"]
            assert output_budget >= required + math.floor(required / 4)
            assert output_budget <= 65536
    assert calls["chat"] == len(batches)


@pytest.mark.asyncio
async def test_full_input_output_and_reserves_must_fit_before_provider_call(model_boundary):
    config, _, _, calls = model_boundary
    config["max_tokens"] = 262144
    [(system, payload)] = _classification(["A. " * 1000])
    adapter = RAGFlowLLMAdapter()
    await adapter.async_generate("disposable-tenant", system, payload)
    required = calls["parameters"][0]["max_completion_tokens"]
    serialized = json.dumps(payload, ensure_ascii=False)
    input_tokens = max(len(encoder.encode(system)) + len(encoder.encode(serialized)), len(system.encode("utf-8")) + len(serialized.encode("utf-8")))
    exact_window = input_tokens + max(256, math.ceil(input_tokens / 20)) + required
    config["max_tokens"] = exact_window
    await adapter.async_generate("disposable-tenant", system, payload)
    config["max_tokens"] = exact_window - 1
    with pytest.raises(ValidationError) as caught:
        await adapter.async_generate("disposable-tenant", system, payload)
    assert caught.value.code == "MODEL_CONTEXT_TOO_LARGE"
    assert caught.value.details["required_response_tokens"] == required
    assert caught.value.details["input_tokens"] == input_tokens
    assert calls["chat"] == 2 and adapter.consume_execution_audit() is None


@pytest.mark.asyncio
async def test_format_repair_context_is_counted_without_truncation(model_boundary):
    config, _, _, calls = model_boundary
    [(system, payload)] = _classification(["A. " * 1000])
    config["max_tokens"] = 65536
    await RAGFlowLLMAdapter().async_generate("disposable-tenant", system, payload)
    payload["format_repair"] = {"previous_response": " complete previous response" * 6000}
    before = json.dumps(payload, ensure_ascii=False)
    with pytest.raises(ValidationError) as caught:
        await RAGFlowLLMAdapter().async_generate("disposable-tenant", system, payload)
    assert caught.value.code == "MODEL_CONTEXT_TOO_LARGE" and calls["chat"] == 1
    assert json.dumps(payload, ensure_ascii=False) == before
