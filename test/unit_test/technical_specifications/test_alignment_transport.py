"""Real loopback HTTP protocol with controlled inference; not live model evidence."""

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from api.technical_specifications.alignment import candidate_identity, source_snapshot
from api.technical_specifications.alignment_ai import AlignmentAI, ollama_request
from api.technical_specifications.alignment_budget import AlignmentBudget
from api.technical_specifications.domain import empty_sections


@pytest.fixture
async def transport():
    state = {"requests": [], "digest": "weights-1", "done": True, "reason": "stop", "mode": "normal", "started": asyncio.Event(), "second_started": asyncio.Event(), "release": asyncio.Event()}

    async def tags(request):
        if state.get("http_endpoint") == "tags":
            return web.Response(status=state["http_status"], text="private provider detail")
        return web.json_response({"models": [{"name": "qa", "digest": state["digest"]}]})

    async def chat(request):
        payload = await request.json()
        state["requests"].append((payload, dict(request.headers)))
        state["started"].set()
        if len(state["requests"]) == 2:
            state["second_started"].set()
        rejected = state.get("rejected_request_id")
        if state.get("http_endpoint") == "chat" and (rejected is None or rejected == request.headers.get("x-ollama-proxy-request-id")):
            if "rejection_release" in state:
                await state["rejection_release"].wait()
            return web.Response(status=state["http_status"], text=state.get("error_body", "private provider detail"))
        if state["mode"] == "wait":
            await state["release"].wait()
        incoming = json.loads(payload["messages"][1]["content"])
        refs = [{"citation_id": next(item["id"] for item in incoming["citations"] if item["quote"] == "Оператор отменяет заявку.")}]
        rows = [{"id": row["id"], "coverage": "IMPLEMENTED", "consistency": "CONSISTENT", "references": refs, "reason": ""} for row in incoming["basis"]["inventory"]]
        if state["mode"] == "missing":
            rows = []
        if state["mode"] == "unknown_citation":
            rows[0]["references"] = [{"citation_id": "C999999"}]
        if state["mode"] == "literal_citation":
            rows[0]["references"] = [{"section": "4", "quote": "Оператор отменяет заявку."}]
        if state["mode"] == "oversized_reason":
            rows[0]["reason"] = "я" * 257
        report = {
            "candidate_sha256": incoming["candidate_sha256"],
            "basis_sha256": incoming["basis_sha256"],
            "requirements": rows,
            "additional_rules": [],
            "joint": {"checked_ids": [row["id"] for row in incoming["basis"]["inventory"]], "consistency": "CONSISTENT", "reason": ""},
        }
        if state["mode"] == "wrong_basis_digest":
            report["basis_sha256"] += report["basis_sha256"][20:34]
        if state["mode"] == "missing_basis_digest":
            del report["basis_sha256"]
        content = json.dumps(report, ensure_ascii=False)
        if state["mode"] == "duplicate":
            content = '{"requirements":[],"requirements":[]}'
        if state["mode"] == "drift":
            state["digest"] = "weights-2"
        response = {"model": "qa", "done": state["done"], "done_reason": state["reason"], "message": {"role": "assistant", "content": content}, "prompt_eval_count": 100, "eval_count": 90}
        if payload.get("think") is True:
            response["message"]["thinking"] = "controlled reasoning"
        if state["mode"] == "no_usage":
            del response["eval_count"]
        if state["mode"] == "wrong_model":
            response["model"] = "foreign"
        if state["mode"] == "truncated":
            response["prompt_truncated"] = True
        if state["mode"] == "usage_overrun":
            response["eval_count"] = payload["options"]["num_predict"] + 1
        return web.json_response(response)

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    model = SimpleNamespace(
        context_tokens=32000, output_tokens=16000, count_tokens=lambda value: len(value) // 3 + 1, model_config={"llm_factory": "Ollama", "api_base": f"http://127.0.0.1:{port}", "llm_name": "qa"}
    )

    async def opened(actor):
        assert actor == "qa"
        return model

    document = {"id": "tz", "revision": 1, "sections": {**empty_sections(), "4": "Оператор отменяет заявку."}}
    basis = source_snapshot([{"document_id": "bt", "revision_id": "r1", "agreement_event_id": "event", "content_hash": "hash", "body_markdown": "Оператор отменяет заявку."}])
    yield AlignmentAI(opened), state, document, basis, model
    state["release"].set()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_wire_limits_full_payload_and_completion_metadata(transport):
    ai, state, document, basis, _ = transport
    result = await ai.check("qa", document, basis)
    assert result["quality_status"] == "PASS" and result["report_status"] == "COMPLETE"
    audit = result["audit"]
    request, headers = state["requests"][0]
    assert request["options"]["num_ctx"] == 32000 and request["options"]["num_predict"] == audit["output_limit"]
    incoming = json.loads(request["messages"][1]["content"])
    assert request["format"] == incoming["response_schema"] and request["stream"] is False
    assert all(request["format"]["properties"][key]["enum"] == [incoming[key]] for key in ("candidate_sha256", "basis_sha256"))
    assert request["truncate"] is False and request["shift"] is False
    assert headers["x-ollama-proxy-bypass-dedup"] == "1"
    assert json.loads(request["messages"][1]["content"])["candidate_sha256"] == candidate_identity(document)
    assert audit["finish_reason"] == "stop" and audit["model_digest"] == "weights-1"


@pytest.mark.parametrize("flag", ["truncate", "shift"])
def test_model_plan_cannot_enable_context_loss(flag):
    request = ollama_request("qa", "system", {}, {"context_limit": 32000, "output_limit": 1000, flag: True})
    assert request[flag] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "nested", "code", "boolean", "not-overflow", "other-context", "text-only", "duplicate", "oversized", "wrong-status"])
async def test_typed_context_rejection_is_distinct_from_an_arbitrary_bad_request(transport, damage):
    ai, state, document, basis, _ = transport
    error = {"type": "exceed_context_size_error", "code": 400, "n_prompt_tokens": 32001, "n_ctx": 32000, "message": "private provider detail"}
    if damage == "code":
        error["code"] = 500
    elif damage == "boolean":
        error["n_prompt_tokens"] = True
    elif damage == "not-overflow":
        error["n_prompt_tokens"] = 32000
    elif damage == "other-context":
        error["n_ctx"] = 31000
    body = json.dumps({"error": json.dumps({"error": error}) if damage == "nested" else error})
    if damage == "text-only":
        body = json.dumps({"error": "request exceeds context; private provider detail"})
    elif damage == "duplicate":
        body = '{"error":{},"error":' + json.dumps(error) + "}"
    elif damage == "oversized":
        body += " " * 65536
    state.update(http_endpoint="chat", http_status=500 if damage == "wrong-status" else 400, error_body=body)
    budget = AlignmentBudget()
    result = await ai.check("qa", document, basis, budget=budget)
    valid = damage in (None, "nested")
    assert result["reason_code"] == ("MODEL_CONTEXT_REJECTED" if valid else "MODEL_TRANSPORT_ERROR")
    assert result["quality_status"] == "UNKNOWN" and result["report_status"] == "INCOMPLETE" and not result["qualifies"]
    assert not result["audit"]["backend_completion_known"] and budget.reserved_tokens > 0 and budget.spent_tokens == 0
    assert len(state["requests"]) == 1 and "private provider detail" not in str(result)
    if valid:
        assert result["audit"]["provider_http_status"] == 400
        assert result["audit"]["rejected_prompt_tokens"] == 32001 and result["audit"]["rejected_context_limit"] == 32000
        assert result["audit"]["prompt_tokens"] is result["audit"]["completion_tokens"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", [False, True])
async def test_async_reservation_must_commit_before_inference(transport, rejected):
    ai, state, document, basis, _ = transport
    budget = AlignmentBudget()

    async def reserved(audit):
        assert audit["request_id"] and budget.reserved_tokens > 0 and not state["requests"]
        await asyncio.sleep(0)
        assert not state["requests"]
        if rejected:
            raise RuntimeError("Synthetic reservation persistence failure")

    if rejected:
        with pytest.raises(RuntimeError):
            await ai.check("qa", document, basis, budget=budget, on_reserved=reserved)
        assert not state["requests"] and budget.reserved_tokens > 0
    else:
        await ai.check("qa", document, basis, budget=budget, on_reserved=reserved)
        assert len(state["requests"]) == 1 and budget.reserved_tokens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "duplicate", "unknown_citation", "literal_citation", "oversized_reason", "wrong_basis_digest", "missing_basis_digest"])
async def test_complete_transport_but_bad_report_is_model_contract_failure(transport, mode):
    ai, state, document, basis, _ = transport
    state["mode"] = mode
    result = await ai.check("qa", document, basis)
    assert result["execution_status"] == "COMPLETE" and result["quality_status"] == "FAIL"
    assert not result["qualifies"] and result["reason_code"] == "ALIGNMENT_REPORT_INVALID"
    assert len(state["requests"]) == 1


@pytest.mark.asyncio
async def test_schema_rejection_never_retries_with_free_json(transport):
    ai, state, document, basis, _ = transport
    state.update(http_endpoint="chat", http_status=400, error_body='{"error":"unsupported schema"}')
    budget = AlignmentBudget()
    result = await ai.check("qa", document, basis, budget=budget)
    assert not result["qualifies"] and result["reason_code"] == "MODEL_TRANSPORT_ERROR"
    assert len(state["requests"]) == 1 and isinstance(state["requests"][0][0]["format"], dict)
    assert budget.reserved_tokens > 0


@pytest.mark.parametrize("index", [2, 3, 5])
def test_generic_real_limit_probes_keep_their_exact_protocol(index):
    from test.evals.technical_specifications.alignment.real_limits import probe_spec

    spec = probe_spec(index)
    wire = ollama_request("qa", spec["system"], spec["payload"], spec["plan"])
    assert wire == {
        "model": "qa",
        "stream": False,
        "format": "json",
        "truncate": False,
        "shift": False,
        "messages": [{"role": "system", "content": spec["system"]}, {"role": "user", "content": json.dumps(spec["payload"], ensure_ascii=False)}],
        "options": {"temperature": 0, "num_ctx": spec["plan"]["context_limit"], "num_predict": spec["plan"]["output_limit"]},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["drift", "no_usage", "length", "unknown_finish"])
async def test_unverified_or_truncated_execution_never_qualifies(transport, mode):
    ai, state, document, basis, _ = transport
    if mode == "length":
        state["reason"] = "length"
    elif mode == "unknown_finish":
        state["reason"] = None
    else:
        state["mode"] = mode
    result = await ai.check("qa", document, basis)
    assert not result["qualifies"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [("wrong_model", "MODEL_IDENTITY_MISMATCH"), ("truncated", "MODEL_INPUT_TRUNCATED"), ("usage_overrun", "MODEL_LIMITS_VIOLATED")])
async def test_observed_provider_fault_has_its_own_reason_not_generic_incomplete(transport, mode, code):
    ai, state, document, basis, _ = transport
    state["mode"] = mode
    result = await ai.check("qa", document, basis)
    assert result["quality_status"] == "UNKNOWN" and result["reason_code"] == code
    assert not result["qualifies"] and len(state["requests"]) == 1


@pytest.mark.asyncio
async def test_insufficient_output_precedes_any_inference(transport):
    ai, state, document, basis, model = transport
    model.output_tokens = 1
    result = await ai.check("qa", document, basis)
    assert result["reason_code"] == "ALIGNMENT_CONTEXT_EXCEEDED" and not state["requests"]


@pytest.mark.asyncio
async def test_total_deadline_includes_wait_for_provider(transport):
    ai, state, document, basis, _ = transport
    state["mode"] = "wait"
    result = await ai.check("qa", document, basis, budget=AlignmentBudget(seconds=0.05))
    assert result["reason_code"] == "ALIGNMENT_DEADLINE" and result["quality_status"] == "UNKNOWN"
    assert len(result["audit"]["request_id"]) == 32
    # Closing the client does not claim that backend inference has stopped.
    assert not state["release"].is_set()
    state["release"].set()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["tags", "chat"])
@pytest.mark.parametrize("status", [429, 503, 401, 500])
async def test_provider_rejection_closes_session_without_retry_or_invented_usage(transport, monkeypatch, endpoint, status):
    ai, state, document, basis, _ = transport
    state.update(http_endpoint=endpoint, http_status=status)
    actual = aiohttp.ClientSession
    sessions = []

    def session(*args, **kwargs):
        created = actual(*args, **kwargs)
        sessions.append(created)
        return created

    monkeypatch.setattr(aiohttp, "ClientSession", session)
    budget = AlignmentBudget()
    result = await ai.check("qa", document, basis, budget=budget)
    assert result["reason_code"] == ("MODEL_PROVIDER_OVERLOADED" if status in {429, 503} else "MODEL_TRANSPORT_ERROR")
    assert result["audit"]["provider_http_status"] == status
    assert result["execution_status"] == result["report_status"] == "INCOMPLETE"
    assert result["quality_status"] == "UNKNOWN" and not result["qualifies"]
    assert "private provider detail" not in str(result)
    assert budget.spent_calls == 1 and budget.spent_tokens == 0 and budget.reserved_tokens > 0
    assert len(state["requests"]) == int(endpoint == "chat")
    assert len(sessions) == 1 and sessions[0].closed
    # An independent later attempt can run; the failed response left no open session.
    state.pop("http_endpoint")
    recovered = await ai.check("qa", document, basis)
    assert recovered["report_status"] == "COMPLETE"
    assert len(state["requests"]) == int(endpoint == "chat") + 1
    assert len(sessions) == 2 and all(item.closed for item in sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_provider_overload_persists_one_attempt_and_replay_never_dispatches_again(transport, store, status):
    from api.db.db_models import TechnicalSpecificationAlignmentObservation as Observation
    from api.technical_specifications.alignment_application import AlignmentApplication
    from test.unit_test.technical_specifications import test_alignment_application as scenarios

    ai, state, _document, _basis, model = transport

    async def opened(_actor):
        return model

    ai.open_model = opened
    state.update(http_endpoint="chat", http_status=status)
    document = store.create("author", "workspace", "Synthetic TZ", empty_sections())
    service = AlignmentApplication(store, scenarios.Sources(), ai, scenarios.run)
    request = scenarios.request(document)
    result = await service.check("author", "workspace", document["id"], request)
    check = result["checks"][0]
    assert check["status"] == "FINISHED" and not check["qualifies"]
    assert check["result"]["reason_code"] == "MODEL_PROVIDER_OVERLOADED"
    assert check["result"]["quality_status"] == "UNKNOWN"
    observed = Observation.get()
    assert observed.error_code == "MODEL_PROVIDER_OVERLOADED" and observed.reserved_tokens > 0
    assert observed.calls == 1 and not observed.usage_known and observed.report_complete is False
    repeated = await service.check("author", "workspace", document["id"], request)
    assert repeated["checks"] == result["checks"]
    assert len(state["requests"]) == Observation.select().count() == 1


@pytest.mark.asyncio
async def test_concurrent_attempt_overload_cannot_spend_or_release_another_budget(transport):
    ai, state, document, basis, _ = transport
    state.update(http_endpoint="chat", http_status=503, mode="wait", rejection_release=asyncio.Event())
    budgets = [AlignmentBudget(), AlignmentBudget()]

    def first_reserved(audit):
        state["rejected_request_id"] = audit["request_id"]

    first = asyncio.create_task(ai.check("qa", document, basis, budget=budgets[0], on_reserved=first_reserved))
    second = None
    try:
        await asyncio.wait_for(state["started"].wait(), 2)
        second = asyncio.create_task(ai.check("qa", {**document, "id": "tz-second"}, basis, budget=budgets[1]))
        await asyncio.wait_for(state["second_started"].wait(), 2)
        before = budgets[1].snapshot()
        assert before["calls"] == 1 and before["reserved_tokens"] > 0
        state["rejection_release"].set()
        failed = await asyncio.wait_for(first, 2)
        assert failed["reason_code"] == "MODEL_PROVIDER_OVERLOADED" and budgets[0].reserved_tokens > 0
        assert not second.done()
        assert budgets[1].reserved_tokens == before["reserved_tokens"] and budgets[1].spent_tokens == 0
        state["release"].set()
        completed = await asyncio.wait_for(second, 2)
        assert completed["report_status"] == "COMPLETE"
        assert completed["audit"]["request_id"] != failed["audit"]["request_id"]
        assert budgets[1].spent_tokens == 190 and budgets[1].reserved_tokens == 0
        assert budgets[0].spent_tokens == 0 and budgets[0].reserved_tokens > 0
    finally:
        state["rejection_release"].set()
        state["release"].set()
        first.cancel()
        tasks = [first]
        if second:
            second.cancel()
            tasks.append(second)
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_paid_reservation_and_request_identity_are_recorded_before_dispatch(transport):
    ai, state, document, basis, _model = transport
    snapshots = []
    budget = AlignmentBudget()

    def reserved(audit):
        assert not state["requests"]
        assert budget.reserved_tokens == audit["context_limit"]
        snapshots.append({"audit": dict(audit), "budget": budget.snapshot()})

    result = await ai.check("qa", document, basis, budget=budget, on_reserved=reserved)
    assert len(snapshots) == 1 and snapshots[0]["budget"]["calls"] == 1
    assert snapshots[0]["budget"]["reserved_tokens"] > 0 and snapshots[0]["budget"]["spent_tokens"] == 0
    assert snapshots[0]["audit"]["request_id"] == result["audit"]["request_id"]


@pytest.mark.asyncio
async def test_total_budget_rejects_unreserved_context_before_any_inference(transport):
    ai, state, document, basis, model = transport
    result = await ai.check("qa", document, basis, budget=AlignmentBudget(token_limit=model.context_tokens - 1))
    assert result["reason_code"] == "ALIGNMENT_TOKEN_LIMIT" and not state["requests"]
    assert result["audit"]["spent_calls"] == 0 and result["audit"]["reserved_tokens"] == 0


@pytest.mark.asyncio
async def test_native_thinking_wire_observation_does_not_invent_reasoning_token_counts(transport):
    from api.technical_specifications.alignment import report_input
    from api.technical_specifications.alignment_ai import SYSTEM, _ollama

    _ai, state, document, basis, model = transport
    _raw, audit = await _ollama(model.model_config, SYSTEM, report_input(document, basis), {"context_limit": 32000, "output_limit": 1000, "think": True}, AlignmentBudget())
    assert state["requests"][0][0]["think"] is True
    assert audit["thinking_requested"] is True and audit["thinking_observed"] is True
    assert audit["thinking_characters"] == len("controlled reasoning")
    assert audit["reasoning_token_accounting"] == "NOT_ATTESTED" and "controlled reasoning" not in str(audit)
