"""Controller verification with synthetic inference, not real-model evidence."""

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

from api.technical_specifications.alignment import fingerprint
from api.technical_specifications.alignment_ai import ollama_request
from test.evals.technical_specifications.alignment import real_limits


def _native_audit(config, system, payload, plan, request_id):
    model = config["llm_name"].split("___")[0]
    wire = ollama_request(model, system, payload, plan)
    return {
        "provider": "Ollama",
        "model": model,
        "response_model": model,
        "model_digest": "f" * 64,
        "model_digest_after": "f" * 64,
        "request_id": request_id,
        "wire_sha256": fingerprint(wire),
        "generation_parameters": wire["options"],
        "finish_reason": "length" if plan["output_limit"] == 8 else "stop",
        "backend_completion_known": True,
        "prompt_tokens": 100,
        "completion_tokens": plan["output_limit"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["timeout", "unknown-finish"])
async def test_unknown_backend_timeout_stops_calls_without_fake_ledger_debit(monkeypatch, completion):
    calls = []

    async def inference(*args, **kwargs):
        calls.append(1)
        if completion == "unknown-finish":
            return "{}", {"finish_reason": None, "prompt_tokens": 5, "completion_tokens": 5}
        raise TimeoutError

    async def open_model(_):
        return SimpleNamespace(count_tokens=lambda text: 10, model_config={"llm_factory": "Ollama", "api_base": "http://127.0.0.1:1", "llm_name": "qa"})

    monkeypatch.setattr(real_limits, "_ollama", inference)
    report = await real_limits.run(open_model, "synthetic")
    assert len(calls) == 1
    assert report["status"] == "INCOMPLETE" and len(report["cases"]) == 8
    assert report["budget"]["calls"] == 1
    assert report["budget"]["reserved_tokens"] == real_limits.probe_spec(2)["plan"]["context_limit"]
    assert len(report["cases"][1]["request_id"]) == 32
    assert report["cases"][2]["reason_code"] == "BACKEND_USAGE_UNKNOWN"
    assert report["cases"][4]["reason_code"] == "BACKEND_USAGE_UNKNOWN"


@pytest.mark.asyncio
async def test_cancel_does_not_promote_client_close_to_backend_stop(monkeypatch):
    state = {"calls": 0, "request_id": None, "client_closed": False}

    async def loaded(_):
        return web.json_response({"models": [{"name": "qa", "context_length": 2048, "digest": "f" * 64}]})

    async def observed(request):
        if state["request_id"] != request.match_info["id"]:
            raise web.HTTPNotFound
        return web.json_response({"status": "processing", "upstream_started_at": "2026-10-20T00:00:00Z"})

    app = web.Application()
    app.router.add_get("/api/ps", loaded)
    app.router.add_get("/_proxy/admin/transactions/{id}", observed)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async def inference(config, system, payload, plan, budget, *, request_id=None, on_request=None):
        audit = _native_audit(config, system, payload, plan, request_id)
        if on_request:
            on_request({**audit, "model_digest_after": None, "backend_completion_known": False, "finish_reason": None})
        state["calls"] += 1
        if state["calls"] == 3:
            state["request_id"] = request_id
            try:
                await asyncio.Event().wait()
            finally:
                state["client_closed"] = True
        return "{}", audit

    monkeypatch.setattr(real_limits, "_ollama", inference)

    async def open_model(_):
        return SimpleNamespace(count_tokens=lambda text: len(text) // 4 + 1, model_config={"llm_factory": "Ollama", "api_base": f"http://127.0.0.1:{port}", "llm_name": "qa"})

    snapshots = []
    try:
        report = await real_limits.run(open_model, "synthetic", checkpoint=lambda probes, ledger: snapshots.append(dict(ledger)))
    finally:
        await runner.cleanup()
    assert report["status"] == "INCOMPLETE" and len(report["cases"]) == 8
    assert report["cases"][1]["status"] == "PASS"  # expected output pressure
    assert report["cases"][4]["reason_code"] == "BACKEND_STOP_UNVERIFIED"
    assert report["cases"][4]["upstream_request_started"] and state["client_closed"]
    assert report["cases"][6]["status"] == "INCOMPLETE"
    assert report["budget"]["calls"] == 3 and report["budget"]["reserved_tokens"] > 0
    assert any(item["calls"] == 3 and item["reserved_tokens"] > 0 for item in snapshots)
    assert all(item["evidence_kind"] != "NATURAL_MODEL" for item in report["cases"])


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["output-overrun", "premature-length", "foreign-digest", "wrong-wire", "ignored-options"])
async def test_output_probe_checks_actual_contract_before_accepting_length(monkeypatch, damage):
    calls = []

    async def inference(config, system, payload, plan, budget, *, request_id=None, on_request=None):
        calls.append(request_id)
        audit = _native_audit(config, system, payload, plan, request_id)
        if damage == "output-overrun":
            audit["completion_tokens"] = plan["output_limit"] + 1
        elif damage == "premature-length":
            audit["completion_tokens"] = plan["output_limit"] - 1
        elif damage == "foreign-digest":
            audit["model_digest_after"] = "c" * 64
        elif damage == "wrong-wire":
            audit["wire_sha256"] = "0" * 64
        else:
            audit["generation_parameters"]["num_predict"] += 1
        return "{}", audit

    async def open_model(_):
        return SimpleNamespace(count_tokens=lambda _: 100, model_config={"llm_factory": "Ollama", "api_base": "http://127.0.0.1:1", "llm_name": "qa"})

    monkeypatch.setattr(real_limits, "_ollama", inference)
    report = await real_limits.run(open_model, "synthetic")
    assert len(calls) == 1
    assert report["cases"][1]["status"] != "PASS"
    assert report["cases"][1]["reason_code"] == "REAL_PROBE_AUDIT_INVALID"
    assert report["budget"]["calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "context", "wire", "zero-usage", "finish"])
async def test_native_context_rejection_is_observed_without_erasing_usage_debt(monkeypatch, damage):
    calls = []

    async def loaded(_):
        return web.json_response({"models": [{"name": "qa", "context_length": 2048, "digest": "f" * 64}]})

    app = web.Application()
    app.router.add_get("/api/ps", loaded)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async def inference(config, system, payload, plan, budget, *, request_id=None, on_request=None):
        calls.append(request_id)
        audit = _native_audit(config, system, payload, plan, request_id)
        if len(calls) == 2:
            audit.update(
                provider_http_status=400,
                input_integrity="REJECTED",
                input_tokenization="SERVER_REJECTION_COUNT_ONLY",
                finish_reason="input_rejected",
                backend_completion_known=False,
                evidence_complete=False,
                prompt_tokens=None,
                completion_tokens=None,
                rejected_prompt_tokens=4096,
                rejected_context_limit=2048,
            )
            if damage == "context":
                audit["rejected_context_limit"] = 1024
            elif damage == "wire":
                audit["wire_sha256"] = "0" * 64
            elif damage == "zero-usage":
                audit.update(prompt_tokens=0, completion_tokens=0)
            elif damage == "finish":
                audit["finish_reason"] = "stop"
        return "{}", audit

    async def open_model(_):
        return SimpleNamespace(count_tokens=lambda _: 100, model_config={"llm_factory": "Ollama", "api_base": f"http://127.0.0.1:{port}", "llm_name": "qa"})

    monkeypatch.setattr(real_limits, "_ollama", inference)
    try:
        report = await real_limits.run(open_model, "synthetic")
    finally:
        await runner.cleanup()
    assert len(calls) == report["budget"]["calls"] == 2
    assert report["status"] == "INCOMPLETE" and len(report["cases"]) == 8
    observed = report["cases"][2]
    assert observed["reason_code"] == ("REAL_PROBE_AUDIT_INVALID" if damage else "CONTEXT_REJECTION_OBSERVED_USAGE_UNVERIFIED")
    if damage is None:
        assert observed["guard_rejection_observed"] and observed["qualification_rejected"]
        assert observed["audit"]["prompt_tokens"] is None
    assert report["budget"]["spent_tokens"] == 108 and report["budget"]["reserved_tokens"] == 2048
    assert report["cases"][4]["reason_code"] == "BACKEND_USAGE_UNKNOWN"
