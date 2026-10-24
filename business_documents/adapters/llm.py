"""Tenant-model integration shared by document and SQL scenarios.

Importing this module does not bootstrap the HTTP application or open clients.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import math
import time
from typing import Any, Callable

from business_documents.application.errors import ValidationError
from business_documents.async_runtime import run_in_worker_loop

MODEL_TIMEOUT_SECONDS = 1800


async def _drain_litellm_callbacks() -> None:
    """Let LiteLLM callbacks finish before the adapter's private loop closes.

    LiteLLM 1.82.5 schedules callback coroutines on a global worker.  This
    adapter is synchronous and creates a short-lived event loop, so returning
    immediately can abandon callbacks bound to this loop.  Wait for queued and
    already-running callbacks, but never stop or clear the process-global worker:
    those operations could cancel telemetry from a concurrent LLM request.
    """

    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    # Give the worker a turn to dequeue callbacks created by the completion.
    await asyncio.sleep(0)
    try:
        # With no configured remote callback this normally completes within a
        # single event-loop turn.  Do not let best-effort provider telemetry
        # consume the durable job's own model timeout.
        await asyncio.wait_for(GLOBAL_LOGGING_WORKER.flush(), timeout=1.0)
        loop = asyncio.get_running_loop()
        running = tuple(task for task in getattr(GLOBAL_LOGGING_WORKER, "_running_tasks", ()) if not task.done() and task.get_loop() is loop)
        if running:
            _, pending = await asyncio.wait(running, timeout=1.0)
            if pending:
                raise TimeoutError
    except TimeoutError:
        logging.warning("Timed out waiting for LiteLLM callbacks before closing the adapter event loop")


def classification_response_tokens(identifiers: list[int]) -> int:
    # ASCII bytes bound byte-based tokenizers, including Qwen's single-digit
    # tokens. Include readable whitespace and both partition layouts.
    required = max(
        len(json.dumps({"source_data_ids": source, "model_instruction_ids": instructions}, indent=2).encode("utf-8"))
        for source, instructions in ((identifiers, []), ([], identifiers), (identifiers[:1], identifiers[1:]))
    )
    return required + max(256, math.ceil(required / 4))


class RAGFlowLLMAdapter:
    """Thin injectable adapter over the tenant's configured default chat model."""

    def __init__(self, model_name_override: str | None = None, *, max_output_tokens: int | None = None):
        if max_output_tokens is not None and (type(max_output_tokens) is not int or max_output_tokens <= 0):
            raise ValueError("max_output_tokens must be a positive integer")
        self._execution_audit: dict[str, Any] | None = None
        self._model_name_override = model_name_override
        self._max_output_tokens = max_output_tokens

    def consume_execution_audit(self) -> dict[str, Any] | None:
        audit = self._execution_audit
        self._execution_audit = None
        return deepcopy(audit)

    async def async_generate(
        self,
        tenant_id: str,
        system_prompt: str,
        input_payload: dict[str, Any],
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type
        from api.db.services.llm_service import LLMBundle
        from common.constants import LLMType

        self._execution_audit = None
        model_config = get_tenant_default_model_by_type(tenant_id, LLMType.CHAT)
        if self._model_name_override is not None and model_config.get("llm_factory") != "Ollama":
            raise ValueError("Quality model override requires Ollama")
        if model_config.get("llm_factory") == "Ollama" and (model_config.get("api_base") or self._model_name_override is not None):
            import httpx

            base = str(model_config.get("api_base") or "").rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            name = self._model_name_override or model_config["llm_name"]
            api_key = model_config.get("api_key")
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            # Resolve each request so a replaced model or changed context cannot
            # retain a stale catalog limit in this long-lived worker adapter.
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{base}/api/show", headers=headers, json={"model": name})
                response.raise_for_status()
                try:
                    metadata = response.json()
                except ValueError as error:
                    raise ValidationError("MODEL_CONTEXT_UNAVAILABLE", "The selected Ollama model does not publish its context window") from error
            from common.ollama import native_ollama_config

            resolved = native_ollama_config({**model_config, "llm_name": name}, metadata)
            if resolved is None:
                raise ValidationError("MODEL_CONTEXT_UNAVAILABLE", "The selected Ollama model does not publish its context window")
            model_config = resolved
        from common.token_utils import encoder

        context_window = model_config.get("max_tokens")
        local_ollama = False
        # Ollama's compatible endpoint cannot set num_ctx per completion. Read
        # the loaded model's window instead of treating the catalog default as
        # its runtime context. Other compatible servers use their configured
        # window and need not implement Ollama's metadata endpoint.
        if model_config.get("llm_factory") == "OpenAI-API-Compatible" and model_config.get("api_base"):
            import httpx

            base = model_config["api_base"].rstrip("/")
            if base.endswith("/v1"):
                base = base[:-3]
            api_key = model_config.get("api_key")
            metadata = None
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    response = await client.get(f"{base}/api/ps", headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
                    response.raise_for_status()
                    metadata = response.json()
                except (httpx.HTTPError, ValueError):
                    # Optional discovery must not require unrelated compatible
                    # providers to expose an Ollama administration endpoint.
                    pass
                if isinstance(metadata, dict) and isinstance(metadata.get("models"), list):
                    name = str(model_config.get("llm_name") or model_config.get("model_name") or "").split("___")[0]
                    loaded = next((item for item in metadata["models"] if isinstance(item, dict) and name in {item.get("name"), item.get("model")}), None)
                    if loaded is not None:
                        context_window = loaded.get("context_length")
                        local_ollama = type(context_window) is int and context_window > 0
                    else:
                        from common.ollama import native_ollama_config

                        try:
                            response = await client.post(f"{base}/api/show", headers={"Authorization": f"Bearer {api_key}"} if api_key else {}, json={"model": name})
                            response.raise_for_status()
                            resolved = native_ollama_config(model_config, response.json())
                        except (httpx.HTTPError, ValueError):
                            resolved = None
                        if resolved is not None:
                            model_config = resolved
                            context_window = resolved["max_tokens"]
                            local_ollama = True
        if type(context_window) is not int or context_window <= 0:
            raise ValidationError("MODEL_CONTEXT_UNAVAILABLE", "Configure the selected model's context window")
        from common.model_limits import output_token_limit

        try:
            output_limit = output_token_limit(model_config, local_ollama=local_ollama)
        except ValueError as error:
            raise ValidationError("MODEL_OUTPUT_LIMIT_UNAVAILABLE", str(error)) from error
        if self._max_output_tokens is not None:
            output_limit = min(output_limit, self._max_output_tokens) if output_limit is not None else self._max_output_tokens
        content = json.dumps(input_payload, ensure_ascii=False)
        task_type = input_payload.get("job_input", {}).get("task_type")
        input_tokens = len(encoder.encode(system_prompt, disallowed_special=())) + len(encoder.encode(content, disallowed_special=()))
        if task_type == "CLASSIFY_DRAFT_EVIDENCE":
            input_tokens = max(input_tokens, len(system_prompt.encode("utf-8")) + len(content.encode("utf-8")))
        input_reserve = max(256, math.ceil(input_tokens / 20)) if task_type == "CLASSIFY_DRAFT_EVIDENCE" else 256
        available = context_window - input_tokens - input_reserve
        if output_limit is not None:
            available = min(available, output_limit)
        required_response_tokens = classification_response_tokens([int(identifier) for identifier in input_payload["fragments"]]) if task_type == "CLASSIFY_DRAFT_EVIDENCE" else 1
        if task_type == "VERIFY_REQUIREMENT_COVERAGE":
            # Smallest valid coverage report; a one-token completion can never
            # satisfy the contract and must fail before opening the provider.
            required_response_tokens = 8
        if available < required_response_tokens:
            code = "REQUIREMENT_CONTEXT_TOO_LARGE" if task_type == "VERIFY_REQUIREMENT_COVERAGE" else "MODEL_CONTEXT_TOO_LARGE"
            raise ValidationError(
                code,
                "The complete input and response budget do not fit the selected model context",
                {
                    "input_tokens": input_tokens,
                    "input_reserve": input_reserve,
                    "required_response_tokens": required_response_tokens,
                    "context_window": context_window,
                    "output_limit": output_limit,
                },
            )
        limit_parameter = "max_tokens" if model_config.get("llm_factory") == "OpenAI-API-Compatible" else "max_completion_tokens"
        generation_parameters = {
            "temperature": 0,
            "top_p": 0.1,
            limit_parameter: available,
        }
        if model_config.get("llm_factory") == "Ollama":
            generation_parameters["extra_body"] = {"num_ctx": context_window}
        elif (
            model_config.get("llm_factory") == "OpenAI-API-Compatible"
            and str(model_config.get("llm_name") or model_config.get("model_name") or "").lower().split("/")[-1].startswith("qwen3")
            and task_type in {"PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE", "CLASSIFY_DRAFT_EVIDENCE"}
        ):
            generation_parameters["reasoning_effort"] = "none"
        provider = " ".join(str(model_config.get("llm_factory") or "unknown").split())[:128]
        model = " ".join(str(model_config.get("llm_name") or model_config.get("model_name") or "unknown").split())[:256]
        model_type = " ".join(str(model_config.get("model_type") or LLMType.CHAT.value).split())[:64]
        started_at = time.perf_counter()
        self._execution_audit = None

        # The durable business-document queue owns retries and exposes each
        # failure to the user.  Provider-internal retries can otherwise keep a
        # single visible attempt inside repeated five-minute HTTP calls.
        try:
            from opentelemetry import trace

            span_context = trace.get_tracer("ragflow.business_documents").start_as_current_span(
                "business_documents.ai.generate",
                attributes={
                    "business_document.task_type": str(task_type or "unknown")[:64],
                    "gen_ai.system": provider,
                    "gen_ai.request.model": model,
                },
            )
        except ImportError:
            from contextlib import nullcontext

            span_context = nullcontext(None)

        with span_context as span:
            async with LLMBundle(tenant_id, model_config, lang="Russian", max_retries=0, raise_on_error=True, timeout=MODEL_TIMEOUT_SECONDS) as bundle:
                operation_error = None
                try:
                    history = [{"role": "user", "content": content}]
                    if on_chunk is None:
                        result = await bundle.async_chat(system_prompt, history, generation_parameters)
                    else:
                        chunks: list[str] = []
                        try:
                            async for chunk in bundle.async_chat_streamly_delta(system_prompt, history, generation_parameters):
                                chunks.append(chunk)
                                on_chunk(chunk)
                            result = "".join(chunks)
                        except RuntimeError as error:
                            if chunks or "does not implement" not in str(error):
                                raise
                            result = await bundle.async_chat(system_prompt, history, generation_parameters)
                    raw_usage = getattr(getattr(bundle, "mdl", None), "last_usage", None) or {}
                    token_usage = {
                        "prompt_tokens": max(0, int(raw_usage.get("prompt_tokens", 0) or 0)),
                        "completion_tokens": max(0, int(raw_usage.get("completion_tokens", 0) or 0)),
                        "total_tokens": max(0, int(raw_usage.get("total_tokens", 0) or 0)),
                    }
                    duration_ms = max(0.0, (time.perf_counter() - started_at) * 1000)
                    if not math.isfinite(duration_ms):
                        duration_ms = 0.0
                    self._execution_audit = {
                        "provider": provider,
                        "model": model,
                        "model_type": model_type,
                        "parameters": generation_parameters,
                        "duration_ms": round(duration_ms, 3),
                        "token_usage": token_usage,
                    }
                    if span is not None:
                        span.set_attribute("gen_ai.usage.input_tokens", token_usage["prompt_tokens"])
                        span.set_attribute("gen_ai.usage.output_tokens", token_usage["completion_tokens"])
                        span.set_attribute("gen_ai.usage.total_tokens", token_usage["total_tokens"])
                    logging.info(
                        "business_document.ai.complete task_type=%s provider=%s model=%s duration_ms=%.3f prompt_tokens=%d completion_tokens=%d total_tokens=%d",
                        task_type,
                        provider,
                        model,
                        duration_ms,
                        token_usage["prompt_tokens"],
                        token_usage["completion_tokens"],
                        token_usage["total_tokens"],
                    )
                    return result
                except BaseException as error:
                    operation_error = error
                    raise
                finally:
                    try:
                        await _drain_litellm_callbacks()
                    except asyncio.CancelledError:
                        if not isinstance(operation_error, asyncio.CancelledError):
                            raise
                    except Exception:
                        if operation_error is None:
                            raise
                        logging.warning("LiteLLM callback cleanup failed; preserving the operation error", exc_info=True)

    def generate(self, tenant_id: str, system_prompt: str, input_payload: dict[str, Any], *, timeout_seconds: float | None = None) -> str:
        return run_in_worker_loop(asyncio.wait_for(self.async_generate(tenant_id, system_prompt, input_payload), timeout=timeout_seconds))

    def generate_stream(self, tenant_id: str, system_prompt: str, input_payload: dict[str, Any], on_chunk: Callable[[str], None], *, timeout_seconds: float | None = None) -> str:
        return run_in_worker_loop(asyncio.wait_for(self.async_generate(tenant_id, system_prompt, input_payload, on_chunk), timeout=timeout_seconds))
