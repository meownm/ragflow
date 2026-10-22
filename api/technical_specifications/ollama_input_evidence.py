"""Paired native render/rejection evidence for the tested Ollama protocol.

No generation is requested by render probes. A context challenge must be a typed
pre-inference rejection; any unexpected generation retains the caller's token
reservation. Thinking is kept private and never copied into the returned audit.
"""

import asyncio
import json
from hashlib import sha256
from uuid import uuid4

import aiohttp

from .alignment import MAX_REPORT_BYTES, fingerprint, strict_json
from .domain import SpecificationError

PROTOCOL = "OLLAMA_RENDER_REJECTION_V2"
CONTEXT = 2048


async def attest_input(session, base, headers, request, native, budget, rejection, *, expected_digest):
    """Match complete effective input to the backend's exact token count.

    Ollama 0.32.14 renders structured thinking a second time with the assistant
    prefill. The same prefill rendered in both windows must be byte-identical;
    its typed overflow count must equal the completed inference's prompt count.
    No absence of an error or a caller-supplied VERIFIED flag is evidence.
    """
    evidence = {"protocol": PROTOCOL, "parent_wire_sha256": fingerprint(request), "status": "INCOMPLETE", "steps": []}
    prompt, output = native.get("prompt_eval_count"), native.get("eval_count")
    context = request["options"]["num_ctx"]
    if type(prompt) is not int or type(output) is not int or prompt <= CONTEXT or 3 * prompt + output + 2 > context:
        return {**evidence, "reason_code": "ATTESTATION_RESERVATION_INSUFFICIENT"}
    if not isinstance(expected_digest, str) or len(expected_digest) != 64 or any(char not in "0123456789abcdef" for char in expected_digest):
        return {**evidence, "reason_code": "ATTESTATION_MODEL_IDENTITY_UNAVAILABLE"}

    async def call(kind, path, body=None):
        identifier = uuid4().hex
        step = {"kind": kind, "request_id": identifier}
        if body is not None:
            step["wire_sha256"] = fingerprint(body)
        evidence["steps"].append(step)
        method = session.get if body is None else session.post
        options = {} if body is None else {"json": body}
        async with method(base + path, headers={**headers, "x-ollama-proxy-request-id": identifier}, **options) as response:
            step["http"] = response.status
            parts, size = [], 0
            async for part in response.content.iter_chunked(16384):
                size += len(part)
                if size > MAX_REPORT_BYTES:
                    raise ValueError("ATTESTATION_RESPONSE_TOO_LARGE")
                parts.append(part)
            raw = b"".join(parts)
            step["response_sha256"] = sha256(raw).hexdigest()
            data = strict_json(raw.decode())
        return step, data

    def rendered(step, data):
        info = data.get("_debug_info")
        value = info.get("rendered_template") if isinstance(info, dict) else None
        if step["http"] != 200 or not isinstance(value, str) or not value:
            evidence["usage_unknown"] = True
            raise ValueError("ATTESTATION_RENDER_UNAVAILABLE")
        step["render_sha256"] = sha256(value.encode()).hexdigest()
        step["render_bytes"] = len(value.encode())
        return value

    try:
        async with asyncio.timeout(budget.remaining()):
            step, version = await call("version", "/api/version")
            if step["http"] != 200 or version.get("version") != "0.32.14":
                return {**evidence, "reason_code": "ATTESTATION_PROTOCOL_UNSUPPORTED"}
            evidence["version"] = version["version"]
            messages = request["messages"]
            if [item.get("role") for item in messages] != ["system", "user"]:
                return {**evidence, "reason_code": "ATTESTATION_MESSAGES_UNSUPPORTED"}
            thinking = native.get("message", {}).get("thinking")
            if isinstance(thinking, str) and thinking:
                messages = [*messages, {"role": "assistant", "thinking": thinking}]
            prefill = {**request, "messages": messages, "options": {**request["options"], "num_predict": 1}}
            full = {**prefill, "_debug_render_only": True}
            small = {**prefill, "options": {**prefill["options"], "num_ctx": CONTEXT}}
            # Both probes use the same complete prefill and small context.
            # Await both before restoring; a failed peer must not outlive proof.
            probes = await asyncio.gather(
                call("small_render", "/api/chat", {**small, "_debug_render_only": True}),
                call("exact_rejection", "/api/chat", small),
                return_exceptions=True,
            )
            for probe in probes:
                if isinstance(probe, BaseException):
                    evidence["usage_unknown"] = True
                    raise probe
            step, data = probes[0]
            original = rendered(step, data)
            # The native renderer trims the system prompt's outer whitespace.
            # The serialized user input must occur exactly, without normalization.
            system, user = (request["messages"][0]["content"].strip(), request["messages"][1]["content"])
            if not system or not user or system not in original or user not in original or original.index(system) >= original.index(user):
                raise ValueError("ATTESTATION_SOURCE_CONTENT_MISMATCH")
            evidence["source_content_sha256"] = fingerprint(request["messages"])
            evidence["system_outer_whitespace_removed"] = len(request["messages"][0]["content"]) - len(system)
            step, data = probes[1]
            counted = rejection(json.dumps(data).encode(), CONTEXT) if step["http"] == 400 else None
            if counted is None:
                evidence["usage_unknown"] = True
                raise ValueError("ATTESTATION_REJECTION_UNPROVEN")
            step.update(counted)
            # Compare the full-window render, then explicitly load that window.
            # A rendered template alone does not prove a runner remains loaded.
            step, data = await call("restored_render", "/api/chat", full)
            if rendered(step, data) != original:
                raise ValueError("ATTESTATION_RENDER_DRIFT")
            step, data = await call("load_context", "/api/chat", {**request, "messages": [], "options": {**request["options"], "num_predict": 1}})
            message = data.get("message")
            if (
                step["http"] != 200
                or data.get("done") is not True
                or data.get("done_reason") != "load"
                or data.get("model") != request["model"]
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
                or message.get("content") != ""
                or message.get("thinking") not in (None, "")
                or message.get("tool_calls") not in (None, [])
                or any(key in data and (type(data[key]) is not int or data[key] != 0) for key in ("prompt_eval_count", "eval_count"))
            ):
                evidence["usage_unknown"] = True
                raise ValueError("ATTESTATION_CONTEXT_LOAD_UNPROVEN")
            step, loaded = await call("loaded_context", "/api/ps")
            models = loaded.get("models")
            entry = next((item for item in models if isinstance(item, dict) and request["model"] in {item.get("name"), item.get("model")}), {}) if isinstance(models, list) else {}
            if step["http"] != 200 or entry.get("context_length") != context or entry.get("digest") != expected_digest:
                raise ValueError("ATTESTATION_LOADED_CONTEXT_UNVERIFIED")
            evidence.update(exact_prompt_tokens=counted["rejected_prompt_tokens"], loaded_context=entry["context_length"], model_digest=entry["digest"])
            if counted["rejected_prompt_tokens"] != prompt:
                raise ValueError("ATTESTATION_PROMPT_TOKEN_MISMATCH")
            evidence.update(status="VERIFIED", reason_code=None)
    except (ValueError, UnicodeError, SpecificationError) as error:
        evidence["reason_code"] = str(error) if isinstance(error, ValueError) and str(error).startswith("ATTESTATION_") else "ATTESTATION_RESPONSE_INVALID"
    except (TimeoutError, aiohttp.ClientError):
        evidence.update(reason_code="ATTESTATION_TRANSPORT_INCOMPLETE", usage_unknown=True)
    return evidence
