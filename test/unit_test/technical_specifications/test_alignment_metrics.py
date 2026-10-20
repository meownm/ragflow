"""Independent confusion-matrix examples and fixed-denominator regressions."""

from copy import deepcopy

import pytest

from api.technical_specifications.alignment import report_input, validate_report
from test.evals.technical_specifications.alignment.corpus import CASES, inputs
from test.evals.technical_specifications.alignment.evaluate import public_summary
from test.evals.technical_specifications.alignment.metrics import public_metrics, semantic_metrics


def case_result(key, *, consistency=None, unsupported=False, joint=None):
    case = next(case for case in CASES if case[0] == key)
    document, basis = inputs(case)
    options = case[5] if len(case) > 5 else {}
    expected = options.get("rows", [(case[3], case[4])])
    refs = [{"section": "5", "quote": document["sections"]["5"][:80]}]
    report = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": [
            {
                "id": row["id"],
                "coverage": labels[0],
                "consistency": consistency or labels[1],
                "references": [{"section": "5", "quote": options["row_quotes"][index]}] if options.get("row_quotes") else refs,
                "reason": "Synthetic semantic answer",
            }
            for index, (row, labels) in enumerate(zip(basis["inventory"], expected, strict=True))
        ],
        "additional_rules": [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": refs, "reason": "Commission has no basis"}] if unsupported else [],
        "joint": {"checked_ids": [row["id"] for row in basis["inventory"]], "consistency": joint or options.get("joint", "CONSISTENT"), "reason": "Synthetic joint answer"},
    }
    return {"case_id": key, "status": "PASS", "errors": [], "result": validate_report(report, document, basis)}


def suite(keys, items):
    return {"status": "INCOMPLETE", "expected_case_ids": keys, "cases": items}


def test_independent_binary_confusion_and_false_block_measure():
    keys = ["BTZ-01", "BTZ-02", "BTZ-03", "BTZ-19"]
    report = suite(keys, [case_result(keys[0], consistency="CONTRADICTED"), case_result(keys[1]), case_result(keys[2], consistency="CONSISTENT"), case_result(keys[3])])
    metrics = semantic_metrics(report)
    assert {key: metrics["contradiction_" + key] for key in ("tp", "fp", "fn", "tn")} == dict.fromkeys(("tp", "fp", "fn", "tn"), 1)
    for key in ("contradiction_precision", "contradiction_recall", "contradiction_f1", "false_block_rate", "consistency_accuracy"):
        assert metrics["measures"][key]["value"] == 0.5
    assert metrics["measures"]["coverage_accuracy"] == {"numerator": 4, "denominator": 4, "value": 1}
    assert metrics["measures"]["false_block_rate"]["denominator"] == 2
    assert public_summary(report)["alignment_quality"] == metrics


def test_unexecuted_case_keeps_fragment_denominator_and_suppresses_full_rates():
    report = suite(["BTZ-01", "BTZ-02"], [case_result("BTZ-01")])
    value = semantic_metrics(report)
    assert value["expected_fragments"] == 2 and value["scored_fragments"] == 1 and value["unscored_fragments"] == 1
    assert value["contradiction_fn"] == 0  # runtime absence is not a semantic prediction
    assert value["measures"]["coverage_accuracy"] == {"numerator": 1, "denominator": 2, "value": None}
    assert value["measures"]["contradiction_recall"] == {"numerator": 0, "denominator": 1, "value": None}
    assert public_summary(report)["alignment_metrics"]["model_cases_unexecuted"] == 1


def test_completed_undetermined_is_a_missed_contradiction_and_has_its_own_rate():
    value = semantic_metrics(suite(["BTZ-02"], [case_result("BTZ-02", consistency="UNDETERMINED")]))
    assert value["unscored_fragments"] == 0 and value["undetermined_fragments"] == 1
    assert value["contradiction_fn"] == 1
    assert value["measures"]["contradiction_recall"]["value"] == 0
    assert value["measures"]["undetermined_rate"]["value"] == 1


@pytest.mark.parametrize("failure", ["transport", "invalid-report", "wrong-snapshot"])
def test_incomplete_or_invalid_report_cannot_contribute_semantic_predictions(failure):
    item = case_result("BTZ-02")
    if failure == "transport":
        item["result"] = {"execution_status": "INCOMPLETE", "report_status": "INCOMPLETE", "quality_status": "UNKNOWN"}
    elif failure == "invalid-report":
        item["result"]["report"]["requirements"] = []
    else:
        item["result"]["report"]["candidate_sha256"] = "foreign"
    value = semantic_metrics(suite(["BTZ-02"], [item]))
    assert value["scored_fragments"] == 0 and value["unscored_fragments"] == 1
    assert value["contradiction_fn"] == 0 and value["measures"]["contradiction_recall"]["value"] is None


def test_empty_detection_denominators_are_na_and_not_perfect_scores():
    value = semantic_metrics(suite(["BTZ-01"], [case_result("BTZ-01")]))
    for key in ("contradiction_precision", "contradiction_recall", "contradiction_f1", "unsupported_rule_recall", "joint_contradiction_recall", "evidence_clause_accuracy"):
        assert value["measures"][key] == {"numerator": 0, "denominator": 0, "value": None}


def test_label_accuracy_does_not_mask_an_irrelevant_but_literal_citation():
    item = case_result("BTZ-53")
    item["result"]["report"]["requirements"][1]["references"] = [{"section": "5", "quote": "адаптер передаёт идентификатор трассы"}]
    value = semantic_metrics(suite(["BTZ-53"], [item]))
    assert value["measures"]["consistency_accuracy"]["value"] == 1
    assert value["measures"]["evidence_clause_accuracy"] == {"numerator": 2, "denominator": 3, "value": 2 / 3}


@pytest.mark.parametrize("found", [False, True])
def test_unsupported_and_joint_recall_use_explicit_golden_cases(found):
    value = semantic_metrics(suite(["BTZ-23", "BTZ-28"], [case_result("BTZ-23", unsupported=found), case_result("BTZ-28", joint="CONTRADICTED" if found else "CONSISTENT")]))
    for key in ("unsupported_rule_recall", "joint_contradiction_recall"):
        assert value["measures"][key] == {"numerator": int(found), "denominator": 1, "value": int(found)}


@pytest.mark.parametrize("keys,items", [([], []), (["foreign"], []), (["BTZ-01", "BTZ-01"], []), (["BTZ-01"], [{"case_id": "foreign"}])])
def test_foreign_empty_or_duplicate_inventory_has_no_quality_estimate(keys, items):
    assert semantic_metrics(suite(keys, items)) is None


def test_public_projection_contains_only_counts_and_verified_fractions():
    value = semantic_metrics(suite(["BTZ-01"], [case_result("BTZ-01")]))
    value["private"] = "private document"
    value["measures"]["private"] = "private answer"
    public = public_metrics(value)
    assert "private" not in str(public)
    for path, invalid in [("scored_fragments", True), ("unscored_fragments", 1), ("contradiction_tp", 10)]:
        changed = deepcopy(value)
        changed[path] = invalid
        assert public_metrics(changed) is None
    for invalid in (float("nan"), float("inf"), 0.5):
        changed = deepcopy(value)
        changed["measures"]["coverage_accuracy"]["value"] = invalid
        assert public_metrics(changed) is None


@pytest.mark.parametrize(
    "replacement",
    [
        {"numerator": 1, "denominator": 2, "value": 0.5},
        {"numerator": 1, "denominator": 1, "value": 1},
        {"numerator": 2, "denominator": 2, "value": None},
    ],
)
def test_public_projection_rejects_partial_rate_and_reduced_or_invented_denominators(replacement):
    value = semantic_metrics(suite(["BTZ-01", "BTZ-02"], [case_result("BTZ-01")]))
    assert public_metrics(value) == value
    value["measures"]["coverage_accuracy"] = replacement
    assert public_metrics(value) is None


@pytest.mark.parametrize("counter,invalid", [("binary_scored", 0), ("expected_contradictions", 0), ("positive_scored", 2), ("evidence_correct", 1), ("expected_fragments", 10**1000)])
def test_public_projection_rejects_inconsistent_numeric_facts(counter, invalid):
    value = semantic_metrics(suite(["BTZ-01", "BTZ-02"], [case_result("BTZ-01"), case_result("BTZ-02")]))
    value[counter] = invalid
    assert public_metrics(value) is None
