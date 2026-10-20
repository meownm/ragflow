"""Operational unknowns must not turn into model-quality failures or successes."""

from types import SimpleNamespace

import pytest

from test.evals.technical_specifications.alignment import evaluate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,status,quality",
    [
        ({"execution_status": "INCOMPLETE", "report_status": "INCOMPLETE", "quality_status": "UNKNOWN", "reason_code": "MODEL_TRANSPORT_ERROR"}, "INCOMPLETE", "UNKNOWN"),
        ({"execution_status": "COMPLETE", "report_status": "INCOMPLETE", "quality_status": "FAIL", "reason_code": "ALIGNMENT_REPORT_INVALID"}, "FAIL", "FAIL"),
        ({"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "evidence_status": "INCOMPLETE", "oracle_errors": []}, "INCOMPLETE", "PASS"),
        ({"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "FAIL", "evidence_status": "VERIFIED", "oracle_errors": []}, "PASS", "PASS"),
        ({"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "evidence_status": "VERIFIED", "oracle_errors": ["MISSED_CONTRADICTION"]}, "FAIL", "FAIL"),
    ],
)
async def test_execution_semantics_and_qualification_are_separate(monkeypatch, result, status, quality):
    result["audit"] = {"model_digest": "synthetic", "profile_sha256": "synthetic"}
    monkeypatch.setattr(evaluate, "CASES", [("test",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda _case, value: value.get("oracle_errors", [value.get("reason_code")]))

    async def check(*args, **kwargs):
        return result

    report = await evaluate.run_cases(SimpleNamespace(check=check), "synthetic")
    assert report["status"] == status and report["quality_status"] == quality
    assert report["qualifies"] is (status == "PASS")


@pytest.mark.asyncio
async def test_model_or_profile_switch_inside_full_run_invalidates_qualification(monkeypatch):
    monkeypatch.setattr(evaluate, "CASES", [("first",), ("second",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda *_: [])
    index = 0

    async def check(*args, **kwargs):
        nonlocal index
        index += 1
        return {
            "execution_status": "COMPLETE",
            "report_status": "COMPLETE",
            "quality_status": "PASS",
            "evidence_status": "VERIFIED",
            "audit": {"model_digest": f"weights-{index}", "profile_sha256": "same"},
        }

    report = await evaluate.run_cases(SimpleNamespace(check=check), "synthetic")
    assert report["quality_status"] == "PASS"
    assert report["evidence_status"] == report["status"] == "INCOMPLETE"
    assert not report["qualifies"]


@pytest.mark.asyncio
async def test_expected_identity_mismatch_stops_paid_probes_and_keeps_response(monkeypatch):
    from test.evals.technical_specifications.alignment import real_limits

    monkeypatch.setattr(evaluate, "CASES", [("first",), ("second",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda *_: [])
    calls = []

    async def check(*args, **kwargs):
        calls.append("natural")
        return {"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "evidence_status": "VERIFIED", "audit": {"model_digest": "changed", "profile_sha256": "profile"}}

    async def forbidden(*args, **kwargs):
        raise AssertionError("No paid probes after identity change")

    monkeypatch.setattr(real_limits, "run", forbidden)
    report = await evaluate.run_evaluation(SimpleNamespace(check=check), "synthetic", expected_identity=("expected", "profile"))
    assert calls == ["natural"] and len(report["cases"]) == 1
    assert report["cases"][0]["result"]["audit"]["model_digest"] == "changed"
    assert report["real_limit_probes"]["reason_code"] == "MODEL_IDENTITY_CHANGED"
    assert not report["qualifies"]


def test_unknown_usage_is_not_reported_as_zero():
    report = {"status": "INCOMPLETE", "expected_case_ids": ["one"], "cases": [{"case_id": "one", "status": "INCOMPLETE", "errors": ["TIMEOUT"], "result": {"audit": {}}}]}
    summary = evaluate.public_summary(report)
    assert summary["duration_ms"] is None and summary["total_tokens"] is None
    assert summary["p0_case_pass_rate"] is None
    assert summary["alignment_metrics"]["model_cases_unknown"] == 1


@pytest.mark.parametrize("reserved,total", [(0, 120), (20, None)])
def test_public_totals_cover_real_probes_and_retain_unknown_usage(reserved, total):
    report = {
        "status": "INCOMPLETE",
        "expected_case_ids": ["one"],
        "cases": [{"case_id": "one", "status": "INCOMPLETE", "errors": [], "result": {"audit": {"prompt_tokens": 10, "completion_tokens": 10, "duration_ms": 10}}}],
        "budget": {"spent_tokens": 120, "reserved_tokens": reserved, "elapsed_ms": 2000},
    }
    summary = evaluate.public_summary(report)
    assert summary["total_tokens"] == total and summary["duration_ms"] == 2000


def test_long_semantic_cases_cover_distinct_positions_and_require_relevant_quotes():
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score

    selected = [case for case in CASES if case[0] in {"BTZ-53", "BTZ-54", "BTZ-55"}]
    assert len(selected) == 3
    for index, case in enumerate(selected):
        document, basis = inputs(case)
        assert len(document["sections"]["5"]) > 40_000 and len(basis["inventory"]) == 3
        oracle = case[5]
        rows = [{"coverage": labels[0], "consistency": labels[1], "references": [{"quote": oracle["row_quotes"][position]}]} for position, labels in enumerate(oracle["rows"])]
        result = {"report_status": "COMPLETE", "quality_status": "FAIL", "report": {"requirements": rows, "joint": {"consistency": "CONTRADICTED"}, "additional_rules": []}}
        assert score(case, result) == []
        position = document["sections"]["5"].index(oracle["row_quotes"][index]) / len(document["sections"]["5"])
        assert abs(position - index / 2) < 0.01
        rows[index]["references"] = [{"quote": "адаптер передаёт идентификатор трассы"}]
        assert f"row:{index + 1}:EVIDENCE_CLAUSE_MISSING" in score(case, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["MODEL_FINISH_UNKNOWN", "MODEL_INPUT_TRUNCATED", "MODEL_IDENTITY_MISMATCH", "ALIGNMENT_REPORT_INVALID"])
async def test_unknown_backend_stops_semantics_and_real_probes_independent_of_error_label(monkeypatch, reason):
    from test.evals.technical_specifications.alignment import real_limits

    monkeypatch.setattr(evaluate, "CASES", [("first",), ("second",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda *_: [reason])
    calls = []

    async def check(*args, budget, **kwargs):
        calls.append(1)
        budget.claim(30)
        return {
            "execution_status": "INCOMPLETE",
            "report_status": "INCOMPLETE",
            "quality_status": "UNKNOWN",
            "reason_code": reason,
            "audit": {"finish_reason": None, "backend_completion_known": False},
        }

    async def no_probes(*args, **kwargs):
        raise AssertionError("Unknown backend must prevent new paid probes")

    monkeypatch.setattr(real_limits, "run", no_probes)
    report = await evaluate.run_evaluation(SimpleNamespace(check=check), "synthetic")
    assert calls == [1] and report["status"] == "INCOMPLETE"
    assert len(report["cases"]) == 1 and report["budget"]["reserved_tokens"] == 30
    assert report["real_limit_probes"]["reason_code"] == "BACKEND_USAGE_UNKNOWN_AFTER_INTERRUPTED_CASE"
