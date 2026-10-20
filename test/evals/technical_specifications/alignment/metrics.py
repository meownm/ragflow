"""Corpus-grounded semantic measures, separate from execution and qualification."""

from api.technical_specifications.alignment import validate_report
from api.technical_specifications.domain import SpecificationError
from test.evals.technical_specifications.alignment.corpus import CASES, evidence_matches, inputs, is_conformant

BASE_COUNTERS = ("expected_fragments", "scored_fragments", "unscored_fragments", "undetermined_fragments", "contradiction_tp", "contradiction_fp", "contradiction_fn", "contradiction_tn")
FACTS = (
    "coverage_correct",
    "consistency_correct",
    "binary_expected",
    "binary_scored",
    "expected_contradictions",
    "positive_expected",
    "positive_scored",
    "false_blocks",
    "unsupported_expected",
    "unsupported_scored",
    "unsupported_found",
    "joint_expected",
    "joint_scored",
    "joint_found",
    "evidence_expected",
    "evidence_scored",
    "evidence_correct",
)
COUNTERS = BASE_COUNTERS + FACTS

MEASURES = (
    "coverage_accuracy",
    "consistency_accuracy",
    "contradiction_precision",
    "contradiction_recall",
    "contradiction_f1",
    "false_block_rate",
    "unsupported_rule_recall",
    "joint_contradiction_recall",
    "undetermined_rate",
    "evidence_clause_accuracy",
)


def _fraction(numerator, denominator, complete):
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if complete and denominator else None}


def semantic_metrics(report):
    """No denominator is selected by the model or reduced by a failed request.

    A completed UNDETERMINED does not detect a golden contradiction. Transport
    failures and malformed reports remain unscored, and suppress full rates.
    """
    expected = report.get("expected_case_ids")
    items = report.get("cases")
    lookup = {case[0]: case for case in CASES}
    if (
        not isinstance(expected, list)
        or not expected
        or any(not isinstance(key, str) or key not in lookup for key in expected)
        or len(set(expected)) != len(expected)
        or not isinstance(items, list)
        or any(not isinstance(item, dict) for item in items)
        or [item.get("case_id") for item in items] != expected[: len(items)]
    ):
        return None
    results = {item["case_id"]: item.get("result", {}) for item in items}
    counts = dict.fromkeys(COUNTERS, 0)
    coverage_correct = consistency_correct = binary_expected = binary_scored = expected_contradictions = 0
    positive_expected = positive_scored = false_blocks = unsupported_expected = unsupported_scored = unsupported_found = 0
    joint_expected = joint_scored = joint_found = 0
    evidence_expected = evidence_scored = evidence_correct = 0
    for key in expected:
        case = lookup[key]
        options = case[5] if len(case) > 5 else {}
        rows = options.get("rows", [(case[3], case[4])])
        counts["expected_fragments"] += len(rows)
        evidence_expected += len(rows) if options.get("row_quotes") else 0
        binary_expected += sum(label in ("CONSISTENT", "CONTRADICTED") for _, label in rows)
        expected_contradictions += sum(label == "CONTRADICTED" for _, label in rows)
        positive = is_conformant(case)
        positive_expected += positive
        unsupported = key == "BTZ-23"
        unsupported_expected += unsupported
        joint = options.get("joint") == "CONTRADICTED"
        joint_expected += joint
        result = results.get(key)
        if not isinstance(result, dict) or result.get("execution_status") != "COMPLETE" or result.get("report_status") != "COMPLETE":
            continue
        document, basis = inputs(case)
        try:
            checked = validate_report(result.get("report"), document, basis)
        except SpecificationError:
            continue
        actual = checked["report"]
        counts["scored_fragments"] += len(rows)
        positive_scored += positive
        false_blocks += positive and checked["quality_status"] != "PASS"
        unsupported_scored += unsupported
        unsupported_found += unsupported and any(row["kind"] == "UNSUPPORTED_BUSINESS_RULE" for row in actual["additional_rules"])
        joint_scored += joint
        joint_found += joint and actual["joint"]["consistency"] == "CONTRADICTED"
        for index, (row, (coverage, consistency)) in enumerate(zip(actual["requirements"], rows, strict=True)):
            matched = evidence_matches(case, index, row)
            if matched is not None:
                evidence_scored += 1
                evidence_correct += matched
            coverage_correct += row["coverage"] == coverage
            consistency_correct += row["consistency"] == consistency
            counts["undetermined_fragments"] += "UNDETERMINED" in (row["coverage"], row["consistency"])
            if consistency not in ("CONSISTENT", "CONTRADICTED"):
                continue
            binary_scored += 1
            gold = consistency == "CONTRADICTED"
            predicted = row["consistency"] == "CONTRADICTED"
            counts["contradiction_" + ("tp" if gold and predicted else "fn" if gold else "fp" if predicted else "tn")] += 1
    counts["unscored_fragments"] = counts["expected_fragments"] - counts["scored_fragments"]
    counts.update(
        coverage_correct=coverage_correct,
        consistency_correct=consistency_correct,
        binary_expected=binary_expected,
        binary_scored=binary_scored,
        expected_contradictions=expected_contradictions,
        positive_expected=positive_expected,
        positive_scored=positive_scored,
        false_blocks=false_blocks,
        unsupported_expected=unsupported_expected,
        unsupported_scored=unsupported_scored,
        unsupported_found=unsupported_found,
        joint_expected=joint_expected,
        joint_scored=joint_scored,
        joint_found=joint_found,
        evidence_expected=evidence_expected,
        evidence_scored=evidence_scored,
        evidence_correct=evidence_correct,
    )
    return {**counts, "measures": _derived_measures(counts)}


def _derived_measures(counts):
    complete = counts["unscored_fragments"] == 0
    binary_complete = counts["binary_scored"] == counts["binary_expected"]
    tp, fp, fn = (counts["contradiction_" + name] for name in ("tp", "fp", "fn"))
    measures = {
        "coverage_accuracy": _fraction(counts["coverage_correct"], counts["expected_fragments"], complete),
        "consistency_accuracy": _fraction(counts["consistency_correct"], counts["expected_fragments"], complete),
        "contradiction_precision": _fraction(tp, tp + fp, binary_complete),
        "contradiction_recall": _fraction(tp, counts["expected_contradictions"], binary_complete),
        "contradiction_f1": _fraction(2 * tp, 2 * tp + fp + fn, binary_complete),
        "false_block_rate": _fraction(counts["false_blocks"], counts["positive_expected"], counts["positive_scored"] == counts["positive_expected"]),
        "unsupported_rule_recall": _fraction(counts["unsupported_found"], counts["unsupported_expected"], counts["unsupported_scored"] == counts["unsupported_expected"]),
        "joint_contradiction_recall": _fraction(counts["joint_found"], counts["joint_expected"], counts["joint_scored"] == counts["joint_expected"]),
        "undetermined_rate": _fraction(counts["undetermined_fragments"], counts["expected_fragments"], complete),
        "evidence_clause_accuracy": _fraction(counts["evidence_correct"], counts["evidence_expected"], counts["evidence_scored"] == counts["evidence_expected"]),
    }
    return measures


def public_metrics(value):
    """Bind every public fraction to bounded facts and report completeness."""
    if not isinstance(value, dict) or any(type(value.get(key)) is not int or not 0 <= value[key] <= len(CASES) * 128 for key in COUNTERS):
        return None
    scored, expected = value["scored_fragments"], value["expected_fragments"]
    if (
        expected < 1
        or scored + value["unscored_fragments"] != expected
        or any(value[key] > scored for key in ("undetermined_fragments", "coverage_correct", "consistency_correct", "binary_scored", "evidence_scored"))
        or any(value[key] > expected for key in ("binary_expected", "evidence_expected"))
        or sum(value["contradiction_" + key] for key in ("tp", "fp", "fn", "tn")) != value["binary_scored"]
        or value["binary_scored"] > value["binary_expected"]
        or not value["contradiction_tp"] + value["contradiction_fn"] <= value["expected_contradictions"] <= value["binary_expected"]
        or value["expected_contradictions"] - value["contradiction_tp"] - value["contradiction_fn"] > value["binary_expected"] - value["binary_scored"]
    ):
        return None
    for prefix, found in (("positive", "false_blocks"), ("unsupported", "unsupported_found"), ("joint", "joint_found"), ("evidence", "evidence_correct")):
        if not value[found] <= value[prefix + "_scored"] <= value[prefix + "_expected"] <= expected:
            return None
    measures = value.get("measures")
    if not isinstance(measures, dict):
        return None
    derived = _derived_measures(value)
    for key, wanted in derived.items():
        measure = measures.get(key)
        if (
            not isinstance(measure, dict)
            or any(type(measure.get(name)) is not int or measure[name] != wanted[name] for name in ("numerator", "denominator"))
            or (measure.get("value") is not None and type(measure["value"]) not in (int, float))
            or measure.get("value") != wanted["value"]
        ):
            return None
    return {**{key: value[key] for key in COUNTERS}, "measures": derived}
