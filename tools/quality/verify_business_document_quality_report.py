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
    expected = {"provider", "model", "model_type", "parameters", "duration_ms", "token_usage"}
    if not isinstance(ai, dict) or set(ai) != expected:
        raise ValueError("Report lacks the complete executed-model audit")
    if not all(isinstance(ai.get(key), str) and ai[key] for key in ("provider", "model", "model_type")):
        raise ValueError("Report lacks the executed model identity")
    parameters = ai["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {"temperature", "top_p", "max_completion_tokens"}:
        raise ValueError("Report has invalid generation parameters")
    if parameters["temperature"] != 0 or parameters["top_p"] != 0.1 or parameters["max_completion_tokens"] not in {4096, 8192}:
        raise ValueError("Report generation parameters do not match the controlled profile")
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


def verify_report(report_path: Path, root: Path, expected_revision: str | None = None) -> dict[str, Any]:
    report = _json(report_path)
    asset_root = root / "agent" / "business_requirements"
    rubric = _json(asset_root / "evals" / "rubric.v1.json")
    template = _json(asset_root / "templates" / "business_requirements.v1.json")
    prompt_bytes = (asset_root / "prompts" / "draft.v1.md").read_bytes()
    prompt_hash = f"sha256:{hashlib.sha256(prompt_bytes).hexdigest()}"

    required = {
        "schema_version",
        "status",
        "scoring_method",
        "generated_at",
        "source_revision",
        "rubric_id",
        "rubric_version",
        "template_version",
        "prompt",
        "ai",
        "metrics",
    }
    if set(report) != required or report["schema_version"] != "1":
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
    if report["template_version"] != template["template_version"] or report["prompt"] != prompt_hash:
        raise QualityGateFailed("Report assets do not match the current template and draft prompt")

    ai = _verify_ai_audit(report["ai"])
    metrics = report["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != {
        "criterion_scores",
        "weighted_score",
        "grounded_reference_precision",
        "grounded_claim_count",
        "hard_failures",
    }:
        raise ValueError("Invalid quality metrics")
    weights = {item["id"]: float(item["weight"]) for item in rubric["criteria"]}
    scores = metrics["criterion_scores"]
    if not isinstance(scores, dict) or set(scores) != set(weights):
        raise ValueError("Criterion scores do not match the published rubric")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 4 for value in scores.values()):
        raise ValueError("Criterion score is outside the published range")
    recomputed = sum(float(scores[key]) * weights[key] for key in weights)
    weighted_score = metrics["weighted_score"]
    if isinstance(weighted_score, bool) or not isinstance(weighted_score, (int, float)) or not math.isclose(weighted_score, recomputed, abs_tol=1e-9):
        raise ValueError("Weighted score is inconsistent")
    if weighted_score < rubric["pass_threshold"]:
        raise QualityGateFailed("Weighted quality score is below the release threshold")
    if metrics["hard_failures"]:
        raise QualityGateFailed("Live model report contains a hard failure")
    grounded_precision = metrics["grounded_reference_precision"]
    if isinstance(grounded_precision, bool) or not isinstance(grounded_precision, (int, float)):
        raise ValueError("Grounded fact precision is invalid")
    if grounded_precision < rubric["live_suite_gate"]["minimum_grounded_fact_precision"]:
        raise QualityGateFailed("Grounded fact precision is below the release threshold")

    return {
        "status": "PASS",
        "model": ai["model"],
        "provider": ai["provider"],
        "weighted_score": weighted_score,
        "grounded_reference_precision": grounded_precision,
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
