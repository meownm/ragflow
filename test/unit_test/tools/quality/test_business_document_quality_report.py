from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.quality.verify_business_document_quality_report import QualityGateFailed, verify_report


ROOT = Path(__file__).parents[4]


def _report() -> dict:
    rubric = json.loads((ROOT / "agent/business_requirements/evals/rubric.v1.json").read_text(encoding="utf-8"))
    template = json.loads((ROOT / "agent/business_requirements/templates/business_requirements.v1.json").read_text(encoding="utf-8"))
    golden_path = ROOT / "agent/business_requirements/golden_model_quality/v1.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    prompt_names = ("intake.v1.md", "draft.v1.md", "review.v1.md", "change_planner.v1.md")
    scores = {criterion["id"]: 4.0 for criterion in rubric["criteria"]}
    scored_metrics = {
        "criterion_scores": scores,
        "weighted_score": 4.0,
        "grounded_reference_precision": 1.0,
        "grounded_claim_count": 5,
        "semantic_coverage": 1.0,
        "missing_fact_ids": [],
        "duplicate_content_count": 0,
        "duplicate_content": [],
        "duplication_rate": 0.0,
        "misplaced_fact_count": 0,
        "misplacement_rate": 0.0,
        "contradiction_count": 0,
        "contradiction_rate": 0.0,
        "contradictions": [],
        "hard_failures": [],
    }
    return {
        "schema_version": "2",
        "status": "PASS",
        "scoring_method": "deterministic_proxy",
        "generated_at": "2026-09-20T12:00:00+00:00",
        "source_revision": "candidate-sha",
        "rubric_id": rubric["rubric_id"],
        "rubric_version": rubric["rubric_version"],
        "template_version": template["template_version"],
        "prompts": {
            name: f"sha256:{hashlib.sha256((ROOT / 'agent/business_requirements/prompts' / name).read_bytes()).hexdigest()}"
            for name in prompt_names
        },
        "golden_suite": {
            "suite_id": golden["suite_id"],
            "suite_version": golden["suite_version"],
            "sha256": f"sha256:{hashlib.sha256(golden_path.read_bytes()).hexdigest()}",
            "expected_case_ids": [case["id"] for case in golden["cases"]],
            "executed_case_ids": [case["id"] for case in golden["cases"]],
        },
        "ai": {
            "provider": "Ollama",
            "model": "qualified-model",
            "model_type": "chat",
            "parameter_profiles": [{"temperature": 0, "top_p": 0.1, "max_completion_tokens": 8192}],
            "execution_count": 5,
            "duration_ms": 1250.5,
            "token_usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        },
        "case_results": [
            {
                "case_id": case["id"],
                "priority": case["priority"],
                "status": "PASS",
                "failures": [],
                "metrics": json.loads(json.dumps(scored_metrics)) if case["workflow"] in {"draft_quality", "review_change"} else {},
            }
            for case in golden["cases"]
        ],
        "metrics": {
            "criterion_scores": scores,
            "weighted_score": 4.0,
            "p0_case_pass_rate": 1.0,
            "all_case_pass_rate": 1.0,
            "grounded_reference_precision": 1.0,
            "grounded_claim_count": 15,
            "semantic_coverage": 1.0,
            "missing_fact_ids": [],
            "duplicate_content_count": 0,
            "duplicate_content": [],
            "duplication_rate": 0.0,
            "misplaced_fact_count": 0,
            "misplacement_rate": 0.0,
            "contradiction_count": 0,
            "contradiction_rate": 0.0,
            "contradictions": [],
            "hard_failures": [],
        },
    }


def _write(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "quality-report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_quality_report_verifier_accepts_exact_current_assets(tmp_path):
    result = verify_report(_write(tmp_path, _report()), ROOT, "candidate-sha")

    assert result["status"] == "PASS"
    assert result["model"] == "qualified-model"


@pytest.mark.parametrize(
    "mutation",
    [
        "revision",
        "prompt",
        "golden",
        "case",
        "case_score",
        "score",
        "grounding",
        "coverage",
        "duplication",
        "misplacement",
        "contradiction",
        "hard_failure",
        "ai_audit",
    ],
)
def test_quality_report_verifier_fails_closed_on_stale_or_failed_evidence(tmp_path, mutation):
    report = _report()
    expected_revision = "candidate-sha"
    if mutation == "revision":
        report["source_revision"] = "other-sha"
    elif mutation == "prompt":
        report["prompts"]["draft.v1.md"] = "sha256:" + "0" * 64
    elif mutation == "golden":
        report["golden_suite"]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "case":
        report["case_results"][0]["status"] = "FAIL"
        report["case_results"][0]["failures"] = ["quality regression"]
    elif mutation == "case_score":
        report["case_results"][1]["metrics"]["criterion_scores"]["template_fidelity"] = 3.9
        report["case_results"][1]["metrics"]["weighted_score"] = 3.985
    elif mutation == "score":
        report["metrics"]["criterion_scores"] = {key: 0.0 for key in report["metrics"]["criterion_scores"]}
        report["metrics"]["weighted_score"] = 0.0
        for result in report["case_results"]:
            if result["metrics"]:
                result["metrics"]["criterion_scores"] = {key: 0.0 for key in result["metrics"]["criterion_scores"]}
                result["metrics"]["weighted_score"] = 0.0
    elif mutation == "grounding":
        report["metrics"]["grounded_reference_precision"] = 0.5
        for result in report["case_results"]:
            if result["metrics"]:
                result["metrics"]["grounded_reference_precision"] = 0.5
    elif mutation == "coverage":
        report["metrics"]["semantic_coverage"] = 0.8
        report["metrics"]["missing_fact_ids"] = ["latency"]
        report["case_results"][1]["metrics"]["semantic_coverage"] = 0.8
        report["case_results"][1]["metrics"]["missing_fact_ids"] = ["latency"]
    elif mutation == "duplication":
        report["metrics"]["duplicate_content_count"] = 1
        report["metrics"]["duplicate_content"] = [f"{report['case_results'][1]['case_id']}:blocks:1~3.3"]
        report["metrics"]["duplication_rate"] = 0.2
        report["case_results"][1]["metrics"]["duplicate_content_count"] = 1
        report["case_results"][1]["metrics"]["duplicate_content"] = ["blocks:1~3.3"]
        report["case_results"][1]["metrics"]["duplication_rate"] = 0.2
    elif mutation == "misplacement":
        report["metrics"]["misplaced_fact_count"] = 1
        report["metrics"]["misplacement_rate"] = 0.2
        report["case_results"][1]["metrics"]["misplaced_fact_count"] = 1
        report["case_results"][1]["metrics"]["misplacement_rate"] = 0.2
    elif mutation == "contradiction":
        report["metrics"]["contradiction_count"] = 1
        report["metrics"]["contradiction_rate"] = 0.2
        report["metrics"]["contradictions"] = [f"{report['case_results'][1]['case_id']}:availability:98%"]
        report["case_results"][1]["metrics"]["contradiction_count"] = 1
        report["case_results"][1]["metrics"]["contradiction_rate"] = 0.2
        report["case_results"][1]["metrics"]["contradictions"] = ["availability:98%"]
    elif mutation == "hard_failure":
        report["metrics"]["hard_failures"] = ["EVIDENCE_INSTRUCTION_EXECUTED"]
        report["case_results"][1]["metrics"]["hard_failures"] = ["EVIDENCE_INSTRUCTION_EXECUTED"]
    else:
        report["ai"]["token_usage"]["total_tokens"] = 999

    expected_error = ValueError if mutation in {"ai_audit", "case_score"} else QualityGateFailed
    with pytest.raises(expected_error):
        verify_report(_write(tmp_path, report), ROOT, expected_revision)
