from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.quality.verify_business_document_quality_report import QualityGateFailed, verify_report


ROOT = Path(__file__).parents[4]


def test_quality_report_cli_runs_as_module_without_pythonpath(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-B", "-m", "tools.quality.verify_business_document_quality_report", "--report", str(path), "--expected-revision", "candidate-sha"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout)["status"] == "PASS"


def _placement_evidence(case, *, insert_monitor=False):
    """Synthetic verifier input only; the live gate never imports this factory."""
    from business_documents.domain.block_diff import with_block_ids
    from business_documents.domain.assessment import without_answered_gap_warnings
    from business_documents.domain.content import apply_change_plan, bind_change_plan_snapshot, render_section_text
    from business_documents.domain.requirement_coverage import agreement_decisions, changed_section_repetitions

    def digest(value):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    ast = json.loads(json.dumps(case["seed"]["base_document"]))
    monitoring = next(s for s in ast["sections"] if s["id"] == "5.5")
    monitoring["blocks"] = [{"type": "paragraph", "text": " ".join(r["text"] for r in case["seed"]["requirements"] if r["section_id"] == "5.5")}]
    next(s for s in ast["sections"] if s["id"] == "5.4")["blocks"] = [
        {"type": "table", "headers": ["Метрика", "Определение"], "rows": [[r["label"], r["text"].split(": ", 1)[1]] for r in case["seed"]["requirements"] if r["section_id"] == "5.4"]}
    ]
    identified_base = with_block_ids(case["seed"]["base_document"])
    base = {s["id"]: s for s in identified_base["sections"]}
    report_section = next(s for s in ast["sections"] if s["id"] == "5.4")
    plan = {
        "schema_version": "1",
        "base_revision_id": "fixture",
        "source_state_version": 1,
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {"operation_id": "report", "type": "REPLACE_BLOCK", "section_id": "5.4", "block_id": base["5.4"]["blocks"][0]["id"], "source_event_ids": ["answer"], "block": report_section["blocks"][0]},
            {"operation_id": "monitor", "type": "REPLACE_BLOCK", "section_id": "5.5", "block_id": base["5.5"]["blocks"][0]["id"], "source_event_ids": ["proposal"], "block": monitoring["blocks"][0]},
            {"operation_id": "remove-metrics", "type": "DELETE_BLOCK", "section_id": "5.5", "block_id": base["5.5"]["blocks"][1]["id"], "source_event_ids": ["answer"]},
        ],
    }
    sources = {
        "answer": {"text": next(option["label"] for option in case["seed"]["options"] if option["option_id"] == case["seed"]["selected_option_id"]), "context": case["seed"]["question"]},
        "proposal": {"text": "Мониторинг включает:\n" + "\n".join(requirement["text"] for requirement in case["seed"]["requirements"])},
    }
    if insert_monitor:
        operation = plan["operations"][1]
        operation["type"] = "INSERT_BLOCK"
        operation["after_block_id"] = None
        removed = operation.pop("block_id")
        plan["operations"].append({"operation_id": "remove-monitor", "type": "DELETE_BLOCK", "section_id": "5.5", "block_id": removed, "source_event_ids": ["proposal"]})
    template = json.loads((ROOT / "agent/business_requirements/templates/business_requirements.v1.json").read_text(encoding="utf-8"))
    plan = bind_change_plan_snapshot(identified_base, plan, base_revision_id="fixture", source_state_version=1)
    schema = json.loads((ROOT / "agent/business_requirements/contracts/document_draft.v2.schema.json").read_text(encoding="utf-8"))
    ast = apply_change_plan(identified_base, plan, template, schema)
    ast = without_answered_gap_warnings(ast, frozenset(case["seed"]["undefined_section_ids"]))
    titles = {section["id"]: section["title"] for section in template["sections"]} | {section["id"]: section["title"] for section in ast["sections"]}
    changed = {"5.4", "5.5"}
    calls = []
    for task, name in [("PLAN_CHANGES", "change_planner"), ("VERIFY_REQUIREMENT_COVERAGE", "requirement_coverage")]:
        system = (ROOT / f"agent/business_requirements/prompts/{name}.v1.md").read_text(encoding="utf-8")
        payload = {"job_input": {"task_type": task}}
        if task == "PLAN_CHANGES":
            payload["job_input"].update(
                current_revision={"revision_id": "fixture", "document_ast": identified_base},
                state_version=1,
                answered_section_ids=[*case["seed"]["undefined_section_ids"], "5"],
                confirmed_requirement_fragments={
                    f"{source_id}:{index}": {"source_id": source_id, "text": text.strip(), **({"context": source["context"]} if "context" in source else {})}
                    for source_id, source in sources.items()
                    for index, text in enumerate(re.split(r"(?<=[.!?;])\s+|\n+", source["text"]))
                    if text.strip()
                },
                active_change_input_event_ids=["answer", "proposal"],
                protocol={
                    "questions": [{"question_id": "question", "target_section_id": "5", "answer": {"source_event_id": "answer", "selected_option_id": case["seed"]["selected_option_id"]}}],
                    "proposals": [{"proposal_id": "proposal-id", "target_section_id": "5.5", "decision": "ACCEPTED", "decision_event_id": "proposal"}],
                    "comments": [],
                },
            )
        else:
            payload["job_input"].update(
                sources=sources,
                before_sections={key: render_section_text(base[key]) for key in ("5.4", "5.5")},
                after_sections={s["id"]: render_section_text(s) for s in ast["sections"] if s["id"] in changed},
                context_sections={s["id"]: render_section_text(s) for s in ast["sections"] if s["id"] not in changed},
                section_titles=titles,
                cross_section_repetitions=[list(pair) for pair in changed_section_repetitions(case["seed"]["base_document"], ast)],
                source_events=[],
                agreement_history={},
                agreement_decisions=agreement_decisions(calls[0]["input_payload"]["job_input"]),
                protocol=json.loads(json.dumps(calls[0]["input_payload"]["job_input"]["protocol"])),
                active_review_cycle=None,
            )
        response = json.loads(json.dumps(plan)) if task == "PLAN_CHANGES" else {"status": "PASS", "issues": []}
        calls.append(
            {
                "task_type": task,
                "system_prompt": system,
                "system_sha256": digest(system),
                "input_payload": payload,
                "input_sha256": digest(encoded(payload)),
                "response": response,
                "response_sha256": digest(encoded(response)),
            }
        )
    return {
        "fixture_sha256": digest(encoded(case)),
        "attempts": 1,
        "model_calls": calls,
        "expected_sources": {"5": ["answer"], "5.5": ["proposal"]},
        "final_ast": ast,
        "requirement_ids": [r["id"] for r in case["seed"]["requirements"]],
        "change_plan": plan,
    }


def _report() -> dict:
    rubric = json.loads((ROOT / "agent/business_requirements/evals/rubric.v1.json").read_text(encoding="utf-8"))
    template = json.loads((ROOT / "agent/business_requirements/templates/business_requirements.v1.json").read_text(encoding="utf-8"))
    golden_path = ROOT / "agent/business_requirements/golden_model_quality/v2.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    prompt_names = ("intake.v1.md", "draft.v1.md", "draft_evidence.v1.md", "review.v1.md", "change_planner.v1.md", "requirement_coverage.v1.md")
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
        "prompts": {name: f"sha256:{hashlib.sha256((ROOT / 'agent/business_requirements/prompts' / name).read_bytes()).hexdigest()}" for name in prompt_names},
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
                **({"evidence": _placement_evidence(case)} if case["workflow"] == "metric_placement" else {}),
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


def test_quality_report_verifier_accepts_runtime_progress_fields(tmp_path):
    report = _report()
    report.update({"active_case_id": None, "diagnostic_case_id": None, "source_dirty": True, "model_weights_digest": ""})
    for case in report["case_results"]:
        case["duration_ms"] = 1200

    assert verify_report(_write(tmp_path, report), ROOT, "candidate-sha")["status"] == "PASS"


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
