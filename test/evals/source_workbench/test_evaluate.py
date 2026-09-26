"""Check scoring and fail-closed inputs without pretending to run live retrieval."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from test.evals.source_workbench import evaluate
from test.evals.source_workbench.evaluate import GOLDEN, collect_live, score_case, score_suite, validate_bindings, validate_gold


@pytest.fixture()
def gold():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture()
def bindings(gold):
    return {
        "disposable": True,
        "workspace_id": "isolated-workspace",
        "workspace_title": "eval-source-workbench-example",
        "dataset_id": "isolated-dataset",
        "document_ids": {alias: f"indexed-{alias}" for alias in gold["documents"]},
    }


def test_gold_has_independent_positive_and_no_answer_labels(gold):
    validate_gold(gold)
    assert len(gold["documents"]) == 4
    assert len([case for case in gold["cases"] if case["relevant"]]) == 4
    assert len([case for case in gold["cases"] if not case["relevant"]]) == 2


def test_scores_returned_document_order_and_no_answer_leak(gold, bindings):
    observations = {
        "p1_acknowledgement": [{"document_id": "indexed-vacation"}, {"document_id": "indexed-incident"}],
        "vacation_notice": [{"document_id": "indexed-vacation"}],
        "travel_receipts": [{"document_id": "indexed-travel"}, {"document_id": "indexed-travel"}],
        "equipment_return": [{"document_id": "indexed-equipment"}],
        "unsupported_fine": [{"document_id": "indexed-equipment"}],
        "unsupported_taxi": [],
    }
    report = score_suite(gold, bindings, observations)
    assert report["metrics"] == {
        "mean_reciprocal_rank_at_5": 0.875,
        "mean_recall_at_5": 1.0,
        "no_answer_result_rate": 0.5,
    }
    assert report["status"] == "fail"
    assert report["cases"][4] == {"id": "unsupported_fine", "kind": "no_answer", "returned": 1, "passed": False}


def test_missing_observation_or_disposable_binding_cannot_pass(gold, bindings):
    observations = {case["id"]: [] for case in gold["cases"]}
    observations.pop("travel_receipts")
    with pytest.raises(ValueError, match="actual search observation"):
        score_suite(gold, bindings, observations)
    invalid = deepcopy(bindings)
    invalid["disposable"] = False
    with pytest.raises(ValueError, match="disposable"):
        validate_bindings(gold, invalid)


def test_unranked_document_cannot_earn_relevance_credit(bindings):
    case = {"id": "positive", "relevant": ["incident"]}
    candidates = [{"document_id": f"unrelated-{index}"} for index in range(5)] + [{"document_id": "indexed-incident"}]
    result = score_case(case, candidates, bindings["document_ids"])
    assert result["reciprocal_rank_at_5"] == 0
    assert result["recall_at_5"] == 0


def test_label_drift_is_rejected(gold):
    invalid = deepcopy(gold)
    invalid["cases"][0]["relevant"] = ["missing-alias"]
    with pytest.raises(ValueError, match="relevance labels"):
        validate_gold(invalid)


def test_live_runner_rejects_non_loopback_and_missing_credentials_before_http(gold, bindings):
    with pytest.raises(ValueError, match="loopback"):
        collect_live("https://shared.example.org", gold, bindings, "Bearer test", "")
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        collect_live("http://127.0.0.1:9380", gold, bindings, "", "")


def test_live_runner_rejects_a_workspace_bound_to_another_dataset(monkeypatch, gold, bindings):
    monkeypatch.setattr(evaluate, "_read_json", lambda *args, **kwargs: {"title": bindings["workspace_title"], "dataset_ids": ["other-dataset"]})
    with pytest.raises(ValueError, match="dedicated eval dataset"):
        collect_live("http://127.0.0.1:9380", gold, bindings, "Bearer test", "")
