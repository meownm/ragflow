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
    prompt = (ROOT / "agent/business_requirements/prompts/draft.v1.md").read_bytes()
    scores = {criterion["id"]: 4.0 for criterion in rubric["criteria"]}
    return {
        "schema_version": "1",
        "status": "PASS",
        "scoring_method": "deterministic_proxy",
        "generated_at": "2026-09-20T12:00:00+00:00",
        "source_revision": "candidate-sha",
        "rubric_id": rubric["rubric_id"],
        "rubric_version": rubric["rubric_version"],
        "template_version": template["template_version"],
        "prompt": f"sha256:{hashlib.sha256(prompt).hexdigest()}",
        "ai": {
            "provider": "Ollama",
            "model": "qualified-model",
            "model_type": "chat",
            "parameters": {"temperature": 0, "top_p": 0.1, "max_completion_tokens": 8192},
            "duration_ms": 1250.5,
            "token_usage": {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        },
        "metrics": {
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


@pytest.mark.parametrize("mutation", ["revision", "prompt", "score", "grounding", "coverage", "duplication", "misplacement", "contradiction", "hard_failure", "ai_audit"])
def test_quality_report_verifier_fails_closed_on_stale_or_failed_evidence(tmp_path, mutation):
    report = _report()
    expected_revision = "candidate-sha"
    if mutation == "revision":
        report["source_revision"] = "other-sha"
    elif mutation == "prompt":
        report["prompt"] = "sha256:" + "0" * 64
    elif mutation == "score":
        report["metrics"]["criterion_scores"] = {key: 0.0 for key in report["metrics"]["criterion_scores"]}
        report["metrics"]["weighted_score"] = 0.0
    elif mutation == "grounding":
        report["metrics"]["grounded_reference_precision"] = 0.5
    elif mutation == "coverage":
        report["metrics"]["semantic_coverage"] = 0.8
        report["metrics"]["missing_fact_ids"] = ["latency"]
    elif mutation == "duplication":
        report["metrics"]["duplicate_content_count"] = 1
        report["metrics"]["duplicate_content"] = ["blocks:1~3.3"]
        report["metrics"]["duplication_rate"] = 0.2
    elif mutation == "misplacement":
        report["metrics"]["misplaced_fact_count"] = 1
        report["metrics"]["misplacement_rate"] = 0.2
    elif mutation == "contradiction":
        report["metrics"]["contradiction_count"] = 1
        report["metrics"]["contradiction_rate"] = 0.2
        report["metrics"]["contradictions"] = ["availability:98%"]
    elif mutation == "hard_failure":
        report["metrics"]["hard_failures"] = ["EVIDENCE_INSTRUCTION_EXECUTED"]
    else:
        report["ai"]["token_usage"]["total_tokens"] = 999

    expected_error = ValueError if mutation == "ai_audit" else QualityGateFailed
    with pytest.raises(expected_error):
        verify_report(_write(tmp_path, report), ROOT, expected_revision)
