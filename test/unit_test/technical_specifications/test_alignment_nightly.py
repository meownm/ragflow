"""Nightly orchestration keeps failures, debt and immutable series boundaries."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test.evals.technical_specifications.alignment import nightly


def stage(kind):
    identifiers = nightly.experiment_case_ids(kind)
    result = {"expected_case_ids": identifiers, "cases": [], "budget": {"spent_tokens": 10, "reserved_tokens": 0}}
    for identifier in identifiers:
        item = {"case_id": identifier, "status": "PASS", "result": {"audit": {"model": "synthetic", "model_digest": "a" * 64, "profile_sha256": "b" * 64, "request_id": uuid4().hex}}}
        if identifier == "ADMISSION-CONTEXT-LOW":
            item.update(inference_calls=0, evidence_kind="DETERMINISTIC", result={})
        result["cases"].append(item)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "failure", "unknown", "debt", "identity", "duplicate", "exception"])
async def test_nightly_collects_in_order_and_stops_before_new_series_on_unsafe_state(monkeypatch, mode):
    calls, snapshots = [], []
    duplicate = uuid4().hex

    async def run_experiment(_ai, _actor, kind, *, checkpoint):
        calls.append(kind)
        value = stage(kind)
        if mode == "failure" and kind == "REASONING":
            value["cases"][-1]["status"] = "FAIL"
            value["cases"][-1]["result"].update(execution_status="COMPLETE", report_status="INCOMPLETE", quality_status="FAIL")
        if mode == "unknown":
            value["cases"][-1]["status"] = "INCOMPLETE"
        if mode == "debt":
            value["budget"]["reserved_tokens"] = 100
        if mode == "identity" and kind == "ADMISSION":
            value["cases"][-1]["result"]["audit"]["model_digest"] = "c" * 64
        if mode == "duplicate":
            value["cases"][0]["result"]["audit"]["request_id"] = duplicate
            value["cases"][1]["result"]["audit"]["request_id"] = duplicate
        checkpoint(value)
        if mode == "exception":
            raise TimeoutError
        return value

    async def full(_ai, _actor, *, checkpoint, expected_identity):
        calls.append("FULL")
        assert expected_identity == ("a" * 64, "b" * 64)
        value = {"cases": [], "quality_status": "PASS", "evidence_status": "VERIFIED", "status": "PASS", "qualifies": True}
        checkpoint(value)
        return value

    monkeypatch.setattr(nightly, "run_experiment", run_experiment)
    monkeypatch.setattr(nightly, "run_evaluation", full)
    result = await nightly.run_nightly(SimpleNamespace(), "synthetic", checkpoint=snapshots.append)
    assert result["status"] == ("FAIL" if mode == "failure" else "INCOMPLETE")
    assert not result["qualifies"] and result["evidence_status"] == "INCOMPLETE"
    assert all(not item["qualifies"] for item in snapshots)
    if mode in {"complete", "failure"}:
        assert calls == [*nightly.ORDER, "FULL"]
    else:
        assert "FULL" not in calls and len(calls) == (2 if mode == "identity" else 1)
        assert result["reason_code"]
    if mode == "debt":
        assert result["nightly_experiments"]["REPEATS"]["budget"]["reserved_tokens"] == 100
        assert nightly.experiment_summary(result)[0]["total_tokens"] is None
    # Saving subsequent stages cannot mutate the retained checkpoint.
    assert snapshots[0]["nightly_experiments"]["REPEATS"]["cases"] == []


def test_missing_or_forged_experiment_summary_never_qualifies():
    value = {"nightly_experiments": {kind: stage(kind) for kind in nightly.ORDER}}
    assert nightly.nightly_status(value) == "INCOMPLETE"
    duplicate = deepcopy(value)
    duplicate["nightly_experiments"]["REPEATS"]["cases"][1]["case_id"] = "foreign"
    summary = nightly.experiment_summary(duplicate)[0]
    assert summary["executed_cases"] == 0 and summary["quality_status"] == "UNKNOWN"
    assert nightly.nightly_status({}) == "INCOMPLETE"
    assert all(item["quality_status"] == "UNKNOWN" for item in nightly.experiment_summary(value))
