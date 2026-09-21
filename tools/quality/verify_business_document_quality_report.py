"""Verify a live Business Documents model-qualification report fail closed."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any


class QualityGateFailed(ValueError):
    pass


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _verify_ai_audit(ai: Any) -> dict[str, Any]:
    expected = {"provider", "model", "model_type", "parameter_profiles", "execution_count", "duration_ms", "token_usage"}
    if not isinstance(ai, dict) or set(ai) != expected:
        raise ValueError("Report lacks the complete executed-model audit")
    if not all(isinstance(ai.get(key), str) and ai[key] for key in ("provider", "model", "model_type")):
        raise ValueError("Report lacks the executed model identity")
    profiles = ai["parameter_profiles"]
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("Report has no generation parameter profiles")
    for parameters in profiles:
        if not isinstance(parameters, dict) or set(parameters) != {"temperature", "top_p", "max_completion_tokens"}:
            raise ValueError("Report has invalid generation parameters")
        if parameters["temperature"] != 0 or parameters["top_p"] != 0.1 or parameters["max_completion_tokens"] not in {4096, 8192}:
            raise ValueError("Report generation parameters do not match the controlled profile")
    execution_count = ai["execution_count"]
    if isinstance(execution_count, bool) or not isinstance(execution_count, int) or execution_count < 1:
        raise ValueError("Report has invalid model execution count")
    duration_ms = ai["duration_ms"]
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)) or not math.isfinite(duration_ms) or duration_ms < 0:
        raise ValueError("Report has invalid model duration")
    usage = ai["token_usage"]
    usage_keys = {"prompt_tokens", "completion_tokens", "total_tokens"}
    if not isinstance(usage, dict) or set(usage) != usage_keys:
        raise ValueError("Report has invalid token usage")
    if any(isinstance(usage[key], bool) or not isinstance(usage[key], int) or usage[key] < 0 for key in usage_keys):
        raise ValueError("Report has invalid token usage")
    if usage["prompt_tokens"] + usage["completion_tokens"] not in {0, usage["total_tokens"]}:
        raise ValueError("Report token usage is inconsistent")
    return ai


def _verify_case_metrics(metrics: Any, weights: dict[str, float], rubric: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "criterion_scores",
        "weighted_score",
        "grounded_reference_precision",
        "grounded_claim_count",
        "semantic_coverage",
        "missing_fact_ids",
        "duplicate_content_count",
        "duplicate_content",
        "duplication_rate",
        "misplaced_fact_count",
        "misplacement_rate",
        "contradiction_count",
        "contradiction_rate",
        "contradictions",
        "hard_failures",
    }
    if not isinstance(metrics, dict) or set(metrics) != expected:
        raise ValueError("Scored live golden case has invalid metrics")
    scores = metrics["criterion_scores"]
    if not isinstance(scores, dict) or set(scores) != set(weights):
        raise ValueError("Case criterion scores do not match the published rubric")
    numeric_rates = (
        "weighted_score",
        "grounded_reference_precision",
        "semantic_coverage",
        "duplication_rate",
        "misplacement_rate",
        "contradiction_rate",
    )
    if any(
        isinstance(metrics[name], bool)
        or not isinstance(metrics[name], (int, float))
        or not math.isfinite(metrics[name])
        for name in numeric_rates
    ):
        raise ValueError("Case metrics contain an invalid numeric value")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 4 for value in scores.values()):
        raise ValueError("Case criterion score is outside the published range")
    for name in ("grounded_reference_precision", "semantic_coverage", "duplication_rate", "misplacement_rate", "contradiction_rate"):
        if not 0 <= metrics[name] <= 1:
            raise ValueError(f"Case {name} is outside the published range")
    for count_name in ("grounded_claim_count", "duplicate_content_count", "misplaced_fact_count", "contradiction_count"):
        count = metrics[count_name]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"Case {count_name} is invalid")
    for list_name in ("missing_fact_ids", "duplicate_content", "contradictions", "hard_failures"):
        values = metrics[list_name]
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"Case {list_name} is invalid")
    if len(metrics["duplicate_content"]) != metrics["duplicate_content_count"]:
        raise ValueError("Case duplicate content count is inconsistent")
    if len(metrics["contradictions"]) != metrics["contradiction_count"]:
        raise ValueError("Case contradiction count is inconsistent")
    for count_name, rate_name in (
        ("duplicate_content_count", "duplication_rate"),
        ("misplaced_fact_count", "misplacement_rate"),
        ("contradiction_count", "contradiction_rate"),
    ):
        if (metrics[count_name] == 0) != (metrics[rate_name] == 0):
            raise ValueError(f"Case {count_name} and {rate_name} are inconsistent")
    recomputed = sum(float(scores[key]) * weights[key] for key in weights)
    if not math.isclose(metrics["weighted_score"], recomputed, abs_tol=1e-9):
        raise ValueError("Case weighted score is inconsistent")
    gate = rubric["live_suite_gate"]
    if (
        metrics["hard_failures"]
        or metrics["weighted_score"] < rubric["pass_threshold"]
        or metrics["grounded_reference_precision"] < gate["minimum_grounded_fact_precision"]
        or metrics["missing_fact_ids"]
        or metrics["semantic_coverage"] < gate["minimum_semantic_coverage"]
        or metrics["duplication_rate"] > gate["maximum_duplication_rate"]
        or metrics["misplacement_rate"] > gate["maximum_misplacement_rate"]
        or metrics["contradiction_rate"] > gate["maximum_contradiction_rate"]
    ):
        raise QualityGateFailed("Scored live golden case violates the published quality gate")
    return metrics


def verify_report(report_path: Path, root: Path, expected_revision: str | None = None) -> dict[str, Any]:
    report = _json(report_path)
    asset_root = root / "agent" / "business_requirements"
    rubric = _json(asset_root / "evals" / "rubric.v1.json")
    weights = {item["id"]: float(item["weight"]) for item in rubric["criteria"]}
    template = _json(asset_root / "templates" / "business_requirements.v1.json")
    golden_path = asset_root / "golden_model_quality" / "v1.json"
    golden = _json(golden_path)
    prompt_names = ("intake.v1.md", "draft.v1.md", "review.v1.md", "change_planner.v1.md")
    prompt_hashes = {
        name: f"sha256:{hashlib.sha256((asset_root / 'prompts' / name).read_bytes()).hexdigest()}"
        for name in prompt_names
    }

    required = {
        "schema_version",
        "status",
        "scoring_method",
        "generated_at",
        "source_revision",
        "rubric_id",
        "rubric_version",
        "template_version",
        "prompts",
        "golden_suite",
        "ai",
        "case_results",
        "metrics",
    }
    if set(report) != required or report["schema_version"] != "2":
        raise ValueError("Invalid report envelope")
    if report["status"] != "PASS":
        raise QualityGateFailed("Live model report is not PASS")
    if report["scoring_method"] != "deterministic_proxy":
        raise ValueError("Unsupported scoring method")
    generated_at = datetime.fromisoformat(report["generated_at"])
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must include a timezone")
    if expected_revision is not None and report["source_revision"] != expected_revision:
        raise QualityGateFailed("Report source revision does not match the candidate")
    if (report["rubric_id"], report["rubric_version"]) != (rubric["rubric_id"], rubric["rubric_version"]):
        raise QualityGateFailed("Report rubric does not match the published rubric")
    if report["template_version"] != template["template_version"] or report["prompts"] != prompt_hashes:
        raise QualityGateFailed("Report assets do not match the current template and prompts")

    expected_case_ids = [case["id"] for case in golden["cases"]]
    golden_suite = report["golden_suite"]
    expected_golden = {
        "suite_id": golden["suite_id"],
        "suite_version": golden["suite_version"],
        "sha256": f"sha256:{hashlib.sha256(golden_path.read_bytes()).hexdigest()}",
        "expected_case_ids": expected_case_ids,
        "executed_case_ids": expected_case_ids,
    }
    if golden_suite != expected_golden:
        raise QualityGateFailed("Report golden suite is stale or incomplete")

    case_results = report["case_results"]
    if not isinstance(case_results, list) or len(case_results) != len(expected_case_ids):
        raise ValueError("Invalid live golden case results")
    scored_cases: list[tuple[str, dict[str, Any]]] = []
    for expected_case, result in zip(golden["cases"], case_results, strict=True):
        if not isinstance(result, dict) or set(result) != {"case_id", "priority", "status", "failures", "metrics"}:
            raise ValueError("Invalid live golden case result")
        if result["case_id"] != expected_case["id"] or result["priority"] != expected_case["priority"]:
            raise QualityGateFailed("Live golden case identity does not match the suite")
        if result["status"] != "PASS" or result["failures"] != []:
            raise QualityGateFailed(f"Live golden case failed: {result['case_id']}")
        if expected_case["workflow"] in {"draft_quality", "review_change"}:
            scored_cases.append((result["case_id"], _verify_case_metrics(result["metrics"], weights, rubric)))
        elif result["metrics"] != {}:
            raise ValueError("Unscored live golden case contains unexpected metrics")

    ai = _verify_ai_audit(report["ai"])
    metrics = report["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != {
        "criterion_scores",
        "weighted_score",
        "p0_case_pass_rate",
        "all_case_pass_rate",
        "grounded_reference_precision",
        "grounded_claim_count",
        "semantic_coverage",
        "missing_fact_ids",
        "duplicate_content_count",
        "duplicate_content",
        "duplication_rate",
        "misplaced_fact_count",
        "misplacement_rate",
        "contradiction_count",
        "contradiction_rate",
        "contradictions",
        "hard_failures",
    }:
        raise ValueError("Invalid quality metrics")
    scores = metrics["criterion_scores"]
    if not isinstance(scores, dict) or set(scores) != set(weights):
        raise ValueError("Criterion scores do not match the published rubric")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 4 for value in scores.values()):
        raise ValueError("Criterion score is outside the published range")
    recomputed = sum(float(scores[key]) * weights[key] for key in weights)
    weighted_score = metrics["weighted_score"]
    if isinstance(weighted_score, bool) or not isinstance(weighted_score, (int, float)) or not math.isclose(weighted_score, recomputed, abs_tol=1e-9):
        raise ValueError("Weighted score is inconsistent")
    if not scored_cases:
        raise QualityGateFailed("Live golden suite contains no scored draft cases")
    expected_scores = {
        criterion: sum(case_metrics["criterion_scores"][criterion] for _, case_metrics in scored_cases) / len(scored_cases)
        for criterion in weights
    }
    expected_weighted = sum(case_metrics["weighted_score"] for _, case_metrics in scored_cases) / len(scored_cases)
    if any(not math.isclose(scores[key], expected_scores[key], abs_tol=1e-9) for key in weights) or not math.isclose(
        weighted_score, expected_weighted, abs_tol=1e-9
    ):
        raise ValueError("Aggregate scores do not match case-level evidence")
    if weighted_score < rubric["pass_threshold"]:
        raise QualityGateFailed("Weighted quality score is below the release threshold")
    for metric_name, threshold_name in (("p0_case_pass_rate", "p0_case_pass_rate"), ("all_case_pass_rate", "all_case_pass_rate")):
        rate = metrics[metric_name]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 <= rate <= 1:
            raise ValueError(f"{metric_name} is invalid")
        if rate < rubric["live_suite_gate"][threshold_name]:
            raise QualityGateFailed(f"{metric_name} is below the release threshold")
    expected_p0_rate = sum(result["status"] == "PASS" for result in case_results if result["priority"] == "P0") / sum(
        result["priority"] == "P0" for result in case_results
    )
    expected_all_rate = sum(result["status"] == "PASS" for result in case_results) / len(case_results)
    if not math.isclose(metrics["p0_case_pass_rate"], expected_p0_rate) or not math.isclose(
        metrics["all_case_pass_rate"], expected_all_rate
    ):
        raise ValueError("Aggregate case pass rate does not match case-level evidence")
    if metrics["hard_failures"]:
        raise QualityGateFailed("Live model report contains a hard failure")
    grounded_precision = metrics["grounded_reference_precision"]
    if isinstance(grounded_precision, bool) or not isinstance(grounded_precision, (int, float)):
        raise ValueError("Grounded fact precision is invalid")
    if grounded_precision < rubric["live_suite_gate"]["minimum_grounded_fact_precision"]:
        raise QualityGateFailed("Grounded fact precision is below the release threshold")
    semantic_coverage = metrics["semantic_coverage"]
    missing_fact_ids = metrics["missing_fact_ids"]
    if isinstance(semantic_coverage, bool) or not isinstance(semantic_coverage, (int, float)) or not math.isfinite(semantic_coverage) or not 0 <= semantic_coverage <= 1:
        raise ValueError("Semantic coverage is invalid")
    if not isinstance(missing_fact_ids, list) or any(not isinstance(fact_id, str) or not fact_id for fact_id in missing_fact_ids):
        raise ValueError("Missing fact identifiers are invalid")
    if missing_fact_ids or semantic_coverage < rubric["live_suite_gate"]["minimum_semantic_coverage"]:
        raise QualityGateFailed("Controlled semantic facts are missing")
    for metric_name, count_name, gate_name, message in (
        ("duplication_rate", "duplicate_content_count", "maximum_duplication_rate", "Content duplication exceeds the release threshold"),
        ("misplacement_rate", "misplaced_fact_count", "maximum_misplacement_rate", "Misplaced facts exceed the release threshold"),
        ("contradiction_rate", "contradiction_count", "maximum_contradiction_rate", "Contradictory facts exceed the release threshold"),
    ):
        rate = metrics[metric_name]
        count = metrics[count_name]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 <= rate <= 1:
            raise ValueError(f"{metric_name} is invalid")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"{count_name} is invalid")
        if (count == 0) != (rate == 0):
            raise ValueError(f"{count_name} and {metric_name} are inconsistent")
        if rate > rubric["live_suite_gate"][gate_name]:
            raise QualityGateFailed(message)
    contradictions = metrics["contradictions"]
    if not isinstance(contradictions, list) or any(not isinstance(item, str) or not item for item in contradictions):
        raise ValueError("Contradiction details are invalid")
    if len(contradictions) != metrics["contradiction_count"]:
        raise ValueError("Contradiction count and details are inconsistent")
    duplicate_content = metrics["duplicate_content"]
    if not isinstance(duplicate_content, list) or any(not isinstance(item, str) or not item for item in duplicate_content):
        raise ValueError("Duplicate content details are invalid")
    if len(duplicate_content) != metrics["duplicate_content_count"]:
        raise ValueError("Duplicate content count and details are inconsistent")

    expected_duplicates = [
        f"{case_id}:{item}" for case_id, case_metrics in scored_cases for item in case_metrics["duplicate_content"]
    ]
    expected_contradictions = [
        f"{case_id}:{item}" for case_id, case_metrics in scored_cases for item in case_metrics["contradictions"]
    ]
    expected_aggregate = {
        "grounded_reference_precision": min(case_metrics["grounded_reference_precision"] for _, case_metrics in scored_cases),
        "grounded_claim_count": sum(case_metrics["grounded_claim_count"] for _, case_metrics in scored_cases),
        "semantic_coverage": min(case_metrics["semantic_coverage"] for _, case_metrics in scored_cases),
        "missing_fact_ids": sorted({item for _, case_metrics in scored_cases for item in case_metrics["missing_fact_ids"]}),
        "duplicate_content_count": len(expected_duplicates),
        "duplicate_content": expected_duplicates,
        "duplication_rate": max(case_metrics["duplication_rate"] for _, case_metrics in scored_cases),
        "misplaced_fact_count": sum(case_metrics["misplaced_fact_count"] for _, case_metrics in scored_cases),
        "misplacement_rate": max(case_metrics["misplacement_rate"] for _, case_metrics in scored_cases),
        "contradiction_count": len(expected_contradictions),
        "contradiction_rate": max(case_metrics["contradiction_rate"] for _, case_metrics in scored_cases),
        "contradictions": expected_contradictions,
        "hard_failures": sorted({item for _, case_metrics in scored_cases for item in case_metrics["hard_failures"]}),
    }
    for name, expected_value in expected_aggregate.items():
        actual_value = metrics[name]
        if isinstance(expected_value, float):
            if not math.isclose(actual_value, expected_value, abs_tol=1e-9):
                raise ValueError(f"Aggregate {name} does not match case-level evidence")
        elif actual_value != expected_value:
            raise ValueError(f"Aggregate {name} does not match case-level evidence")

    return {
        "status": "PASS",
        "model": ai["model"],
        "provider": ai["provider"],
        "weighted_score": weighted_score,
        "grounded_reference_precision": grounded_precision,
        "semantic_coverage": semantic_coverage,
        "duplication_rate": metrics["duplication_rate"],
        "misplacement_rate": metrics["misplacement_rate"],
        "contradiction_rate": metrics["contradiction_rate"],
        "p0_case_pass_rate": metrics["p0_case_pass_rate"],
        "all_case_pass_rate": metrics["all_case_pass_rate"],
        "case_count": len(case_results),
        "source_revision": report["source_revision"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--expected-revision")
    args = parser.parse_args()
    try:
        print(json.dumps(verify_report(args.report, args.root, args.expected_revision), ensure_ascii=False, sort_keys=True))
        return 0
    except QualityGateFailed as error:
        print(f"FAIL: {error}")
        return 1
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(f"INCOMPLETE: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
