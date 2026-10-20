"""Synthetic inference checks for full replay, admission and thinking experiments."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from aiohttp import web

from api.technical_specifications.alignment import fingerprint, report_input
from api.technical_specifications.alignment_ai import AlignmentAI, ollama_request
from test.evals.technical_specifications.alignment import evaluate, experiments
from test.evals.technical_specifications.alignment.corpus import CASES, inputs
from test.unit_test.technical_specifications.test_alignment_metrics import case_result


def test_known_contract_failure_counts_as_failure_without_claiming_answer_variability():
    valid = case_result("BTZ-01")
    attempts = [
        {**valid, "natural_case_id": "BTZ-01"},
        {"natural_case_id": "BTZ-01", "status": "FAIL", "result": {"execution_status": "COMPLETE", "report_status": "INCOMPLETE", "quality_status": "FAIL"}},
    ]
    row = experiments.repeat_summary(attempts, ["BTZ-01"], 2)[0]
    assert row["executed_attempts"] == 2 and row["failed"] == 1 and row["unexecuted"] == 0
    assert row["failure_rate"] == 0.5
    assert row["label_variants"] is None and row["answer_variants"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["vary", "unknown", "profile", "duplicate"])
async def test_replay_keeps_every_attempt_and_stops_on_unknown_or_changed_identity(monkeypatch, mode):
    selected = [case for case in CASES if case[0] == "BTZ-01"]
    monkeypatch.setattr(evaluate, "CASES", selected)
    monkeypatch.setattr(experiments, "CASES", selected)
    calls = []

    async def check(_actor, _document, _basis, *, budget, on_reserved):
        calls.append(budget)
        claim = budget.claim(30)
        audit = {"model": "synthetic", "model_digest": "f" * 64, "profile_sha256": "e" * 64, "request_id": "a" * 32 if mode == "duplicate" else uuid4().hex, "backend_completion_known": True}
        on_reserved(audit)
        if mode == "unknown" and len(calls) == 1:
            return {"execution_status": "INCOMPLETE", "report_status": "INCOMPLETE", "quality_status": "UNKNOWN", "audit": {**audit, "backend_completion_known": False}}
        budget.settle(10, 10, claim_id=claim)
        if mode == "profile" and len(calls) == 2:
            audit["model_digest"] = "c" * 64
        result = case_result("BTZ-01", consistency="CONTRADICTED" if mode == "vary" and len(calls) == 2 else None)["result"]
        return {**result, "audit": audit}

    report = await experiments.run_experiment(SimpleNamespace(check=check), "synthetic", "REPEATS")
    assert len({id(value) for value in calls}) == 1
    assert report["scope"] == "EXPERIMENT" and not report["qualifies"]
    assert report["expected_case_ids"] == ["REPEAT-1/BTZ-01", "REPEAT-2/BTZ-01"]
    row = report["repeat_summary"][0]
    assert row["expected_attempts"] == 2 and row["executed_attempts"] == len(calls)
    if mode == "vary":
        assert report["quality_status"] == "FAIL"
        assert row["failure_rate"] == 0.5 and row["label_variants"] == 2
        assert report["budget"]["spent_tokens"] == 40
    else:
        assert row["failure_rate"] is None and row["label_variants"] is None
        assert report["reason_code"]
    if mode == "unknown":
        assert len(calls) == 1 and row["unexecuted"] == 1 and row["unknown"] == 1
        assert report["budget"]["reserved_tokens"] == 30


def _audit(config, system, payload, plan, request_id):
    model = config["llm_name"]
    request = ollama_request(model, system, payload, plan)
    return {
        "provider": "Ollama",
        "model": model,
        "response_model": model,
        "model_digest": "f" * 64,
        "model_digest_after": "f" * 64,
        "request_id": request_id,
        "generation_parameters": request["options"],
        "wire_sha256": fingerprint(request),
        "prompt_tokens": 10,
        "completion_tokens": 8 if plan["output_limit"] == 8 else 100,
        "finish_reason": "length" if plan["output_limit"] == 8 else "stop",
        "evidence_complete": False,
        "input_integrity": "NOT_ATTESTED",
        "thinking_requested": plan.get("think"),
        "thinking_observed": "think" in plan,
    }


@pytest.mark.asyncio
async def test_admission_runs_complete_natural_input_at_exact_and_above_boundary_but_rejects_below(monkeypatch):
    calls = []
    model = SimpleNamespace(context_tokens=32000, output_tokens=16000, count_tokens=lambda text: len(text) // 4 + 1, model_config={"llm_factory": "Ollama", "llm_name": "qa", "api_base": "unused"})

    async def opened(_actor):
        return model

    async def inference(config, system, payload, plan, budget, *, request_id):
        calls.append((payload, plan))
        result = case_result("BTZ-02")["result"]["report"]
        for row in result["requirements"]:
            row["references"] = [{"citation_id": payload["citations"][0]["id"]}]
        return json.dumps(result), _audit(config, system, payload, plan, request_id)

    monkeypatch.setattr("api.technical_specifications.alignment_ai._ollama", inference)
    report = await experiments.run_experiment(AlignmentAI(opened), "synthetic", "ADMISSION")
    boundary = report["application_context_boundary"]
    assert [plan["context_limit"] for _, plan in calls] == [boundary, boundary + 1]
    case = next(case for case in CASES if case[0] == "BTZ-02")
    document, basis = inputs(case)
    assert all(payload == report_input(document, basis) for payload, _ in calls)
    assert [item["status"] for item in report["cases"]] == ["PASS"] * 3
    assert report["cases"][0]["result"]["reason_code"] == "ALIGNMENT_CONTEXT_EXCEEDED"
    assert report["budget"]["calls"] == 2 and report["budget"]["reserved_tokens"] == 0
    assert report["backend_token_boundary"] == "UNVERIFIED" and report["evidence_status"] == "INCOMPLETE" and not report["qualifies"]


@pytest.fixture
async def thinking_profile():
    state = {"values": [True], "capabilities": ["thinking"]}

    async def show(_request):
        return web.json_response({"thinking": {"values": state["values"]}, "capabilities": state["capabilities"]})

    app = web.Application()
    app.router.add_post("/api/show", show)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    model = SimpleNamespace(context_tokens=32000, model_config={"llm_factory": "Ollama", "llm_name": "qa", "api_base": f"http://127.0.0.1:{port}"})

    async def opened(_actor):
        return model

    yield state, AlignmentAI(opened)
    await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "unknown-usage", "timeout", "missing-thinking", "digest", "denied", "named", "truncated-low", "truncated-sufficient", "missing-thinking-sufficient"])
async def test_thinking_experiment_checks_small_and_sufficient_outputs_without_inventing_accounting(monkeypatch, thinking_profile, mode):
    state, ai = thinking_profile
    if mode == "denied":
        state["values"] = [False]  # Explicit model controls override a generic capability.
    if mode == "named":
        state["values"] = ["high"]
    calls = []

    async def inference(config, system, payload, plan, budget, *, request_id, on_request):
        calls.append(plan)
        audit = _audit(config, system, payload, plan, request_id)
        on_request(audit)
        if mode == "timeout":
            raise TimeoutError
        if mode == "unknown-usage":
            audit["completion_tokens"] = None
        if mode == "missing-thinking":
            audit["thinking_observed"] = False
        if mode == "missing-thinking-sufficient" and len(calls) == 2:
            audit["thinking_observed"] = False
        if mode == "truncated-low" or mode == "truncated-sufficient" and len(calls) == 2:
            audit["input_integrity"] = "TRUNCATED"
        if mode == "digest" and len(calls) == 2:
            audit["model_digest_after"] = "c" * 64
        result = case_result("BTZ-28")["result"]["report"]
        for row in result["requirements"]:
            row["references"] = [{"citation_id": payload["citations"][0]["id"]}]
        return json.dumps(result), audit

    monkeypatch.setattr(experiments, "_ollama", inference)
    report = await experiments.run_experiment(ai, "synthetic", "REASONING")
    assert report["evidence_status"] == "INCOMPLETE" and not report["qualifies"]
    if mode == "denied":
        assert not calls and report["reason_code"] == "THINKING_CAPABILITY_UNAVAILABLE"
        return
    assert report["reasoning_token_accounting"] == "NOT_ATTESTED"
    assert all(plan["think"] == ("high" if mode == "named" else True) for plan in calls)
    if mode in ("timeout", "unknown-usage"):
        assert len(calls) == 1 and report["budget"]["reserved_tokens"] > 0
    elif mode != "truncated-low":
        assert [plan["output_limit"] for plan in calls] == [8, 8192]
    else:
        assert len(calls) == 1 and report["budget"]["reserved_tokens"] == 0
    if mode in ("complete", "named"):
        assert [item["status"] for item in report["cases"]] == ["PASS", "PASS"]
        assert report["quality_status"] == "PASS" and report["budget"]["spent_tokens"] == 128
    if mode in ("digest", "missing-thinking", "missing-thinking-sufficient", "truncated-low", "truncated-sufficient"):
        assert report["quality_status"] == "UNKNOWN"
        assert experiments.experiment_case_status("REASONING", report["cases"][-1]) == "INCOMPLETE"
        from test.evals.technical_specifications.alignment.nightly import experiment_summary

        assert experiment_summary({"nightly_experiments": {"REASONING": report}})[2]["quality_status"] == "UNKNOWN"
    if mode.startswith("truncated"):
        assert report["cases"][-1]["errors"] == ["MODEL_INPUT_TRUNCATED"]


@pytest.mark.asyncio
async def test_unknown_experiment_is_rejected_before_provider_access():
    with pytest.raises(ValueError, match="Unknown"):
        await experiments.run_experiment(None, None, "OTHER")


@pytest.mark.parametrize("pending", [False, True])
def test_interrupted_host_report_keeps_expected_denominator_and_latest_settled_or_reserved_ledger(pending):
    from test.evals.technical_specifications.alignment.run_live import _recover

    first = "REPEAT-1/BTZ-01"
    expected = [first, "REPEAT-2/BTZ-01"]
    budget = {"calls": 2 if pending else 1, "spent_tokens": 20, "reserved_tokens": 30 if pending else 0}
    events = [
        ("ALIGNMENT_ACTIVE", {"active_case_id": expected[1] if pending else first, "active_audit": {"request_id": "a" * 32}, "budget": {"spent_tokens": 0, "reserved_tokens": 30}}),
        ("ALIGNMENT_CASE", {"case_id": first, "status": "FAIL"}),
        ("ALIGNMENT_LEDGER", budget),
    ]
    report = _recover([prefix + "=" + json.dumps(value) for prefix, value in events], expected, "REPEATS", 124)
    assert report["expected_case_ids"] == expected and report["budget"] == budget
    assert report["quality_status"] == "FAIL" and report["evidence_status"] == "INCOMPLETE" and not report["qualifies"]
    assert ("active_audit" in report) is pending
