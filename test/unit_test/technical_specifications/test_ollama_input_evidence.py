"""Controlled loopback receipts; separate from real provider acceptance."""

import asyncio
import json
from copy import deepcopy

import aiohttp
import pytest
from aiohttp import web

from api.technical_specifications.alignment import fingerprint
from api.technical_specifications.alignment_ai import _context_rejection
from api.technical_specifications.alignment_budget import AlignmentBudget
from api.technical_specifications.ollama_input_evidence import attest_input


@pytest.fixture
async def evidence_server():
    state = {"damage": None, "requests": [], "renders": 0, "parallel": False, "small_started": 0, "small_finished": 0, "loaded_context": 2048}
    both_started = asyncio.Event()
    request = {
        "model": "qa",
        "messages": [{"role": "system", "content": "System rules.\n"}, {"role": "user", "content": '{"source":"all original clauses"}'}],
        "format": {"type": "object"},
        "stream": False,
        "truncate": False,
        "shift": False,
        "options": {"num_ctx": 32000, "num_predict": 16000, "temperature": 0},
    }
    native = {"prompt_eval_count": 5000, "eval_count": 90, "message": {"thinking": "controlled private reasoning"}}

    async def version(_request):
        return web.json_response({"version": "unknown" if state["damage"] == "version" else "0.32.14"})

    async def loaded(_request):
        entry = {"name": "qa", "digest": "b" * 64 if state["damage"] == "digest" else "a" * 64, "context_length": 8192 if state["damage"] == "loaded" else state["loaded_context"]}
        return web.json_response({"models": None if state["damage"] == "models" else [entry]})

    async def chat(incoming):
        body = await incoming.json()
        state["requests"].append((body, dict(incoming.headers)))
        if state["parallel"]:
            if body["options"]["num_ctx"] == 2048:
                state["small_started"] += 1
                if state["small_started"] == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), 0.3)
                state["small_finished"] += 1
            else:
                assert state["small_finished"] == 2
        if state["damage"] == "transport" and body.get("_debug_render_only"):
            return web.Response(status=503, text="unavailable")
        if state["damage"] == "timeout":
            await asyncio.sleep(0.2)
        if body["messages"] == []:
            state["loaded_context"] = body["options"]["num_ctx"]
            return web.json_response({"model": body["model"], "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop" if state["damage"] == "load" else "load"})
        if body.get("_debug_render_only"):
            state["renders"] += 1
            if state["damage"] == "ignored_render":
                return web.json_response({"done": True, "eval_count": 1})
            system, user = body["messages"][:2]
            rendered = system["content"].strip() + "\n" + user["content"] + "\n" + body["messages"][-1].get("thinking", "")
            if state["damage"] == "source":
                rendered = "omitted original input"
            if state["damage"] == "order":
                rendered = user["content"] + "\n" + system["content"].strip()
            if state["damage"] == "small_render" and body["options"]["num_ctx"] == 2048:
                rendered += "changed template"
            if state["damage"] == "restored_render" and state["renders"] == 2:
                rendered += "drift"
            return web.json_response({"_debug_info": {"rendered_template": rendered}})
        error = {"type": "exceed_context_size_error", "code": 400, "n_prompt_tokens": 5001 if state["damage"] == "count" else 5000, "n_ctx": 2048}
        if state["damage"] == "boolean":
            error["n_prompt_tokens"] = True
        if state["damage"] == "untyped":
            return web.json_response({"error": "private provider detail"}, status=400)
        if state["damage"] == "generated":
            return web.json_response({"done": True, "eval_count": 1})
        return web.json_response({"error": error}, status=400)

    app = web.Application()
    app.router.add_get("/api/version", version)
    app.router.add_get("/api/ps", loaded)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    yield state, request, native, base
    await runner.cleanup()


async def observed(server, *, seconds=10):
    _state, request, native, base = server
    budget = AlignmentBudget(seconds=seconds)
    budget.claim(request["options"]["num_ctx"])
    async with aiohttp.ClientSession() as session:
        evidence = await attest_input(session, base, {}, request, native, budget, _context_rejection, expected_digest="a" * 64)
    assert budget.spent_calls == 1 and budget.reserved_tokens == 32000 and budget.spent_tokens == 0
    return evidence


@pytest.mark.asyncio
async def test_exact_paired_receipts_preserve_source_thinking_and_limits(evidence_server):
    state, request, native, _ = evidence_server
    before = deepcopy((request, native))
    evidence = await observed(evidence_server)
    assert evidence["status"] == "VERIFIED" and evidence["exact_prompt_tokens"] == native["prompt_eval_count"]
    assert evidence["parent_wire_sha256"] == fingerprint(request)
    assert evidence["source_content_sha256"] == fingerprint(request["messages"])
    assert evidence["loaded_context"] == 32000 and evidence["model_digest"] == "a" * 64
    assert len(state["requests"]) == 4
    identifiers = set()
    for body, headers in state["requests"]:
        if body["messages"]:
            assert body["messages"][:2] == request["messages"]
            assert body["messages"][-1]["thinking"] == native["message"]["thinking"]
        assert body["format"] == request["format"] and body["truncate"] is False and body["shift"] is False
        assert body["options"]["num_predict"] == 1
        identifiers.add(headers["x-ollama-proxy-request-id"])
    assert len(identifiers) == 4 and native["message"]["thinking"] not in json.dumps(evidence)
    assert (request, native) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["version", "source", "order", "small_render", "restored_render", "count", "boolean", "untyped", "generated", "ignored_render", "load", "loaded", "digest", "models"]
)
async def test_unproved_or_mismatched_evidence_never_verifies(evidence_server, damage):
    evidence_server[0]["damage"] = damage
    evidence = await observed(evidence_server)
    assert evidence["status"] == "INCOMPLETE" and evidence["reason_code"].startswith("ATTESTATION_")
    if damage in {"boolean", "untyped", "generated", "ignored_render", "load"}:
        assert evidence["usage_unknown"] is True
    assert "private provider detail" not in json.dumps(evidence)


@pytest.mark.asyncio
async def test_attestation_timeout_retains_unknown_usage_and_never_retries_inference(evidence_server):
    evidence_server[0]["damage"] = "timeout"
    evidence = await observed(evidence_server, seconds=0.03)
    assert evidence["status"] == "INCOMPLETE" and evidence["usage_unknown"] is True
    assert len(evidence_server[0]["requests"]) == 2


@pytest.mark.asyncio
async def test_small_probes_overlap_and_restore_waits_for_both(evidence_server):
    state = evidence_server[0]
    state["parallel"] = True
    evidence = await observed(evidence_server)
    assert evidence["status"] == "VERIFIED" and state["small_finished"] == 2


@pytest.mark.asyncio
async def test_failed_peer_is_awaited_and_unknown_usage_is_retained(evidence_server):
    state = evidence_server[0]
    state.update(parallel=True, damage="transport")
    evidence = await observed(evidence_server)
    assert evidence["status"] == "INCOMPLETE" and evidence["usage_unknown"] is True
    assert state["small_finished"] == 2 and len(state["requests"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,output", [(100, 20), (31000, 90), (True, 90), (5000, None)])
async def test_no_probe_without_a_conservative_existing_reservation(evidence_server, prompt, output):
    state, _request, native, _base = evidence_server
    native.update(prompt_eval_count=prompt, eval_count=output)
    evidence = await observed(evidence_server)
    assert evidence["status"] == "INCOMPLETE" and not state["requests"]
