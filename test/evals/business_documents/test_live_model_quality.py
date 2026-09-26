"""Opt-in data-driven real-model quality gate for business requirements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from peewee import SqliteDatabase


REPO_ROOT = Path(__file__).resolve().parents[3]
if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(REPO_ROOT / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.assets import published_template, validate_document_ast
from api.apps.business_documents.evidence import BusinessDocumentEvidence
from api.apps.business_documents.service import BusinessDocumentService
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocumentEvidenceSnapshot, BusinessDocumentJob, BusinessDocumentQuestion
from test.evals.business_documents.live_quality import ControlledFact, QualityScore, resolve_live_quality_config, score_document_quality


ASSET_ROOT = REPO_ROOT / "agent" / "business_requirements"
MODEL_GOLDEN_PATH = ASSET_ROOT / "golden_model_quality" / "v1.json"
RUBRIC = json.loads((ASSET_ROOT / "evals" / "rubric.v1.json").read_text(encoding="utf-8"))
PROMPT_NAMES = ("intake.v1.md", "draft.v1.md", "review.v1.md", "change_planner.v1.md")


@dataclass(frozen=True)
class CaseExecution:
    result: dict[str, Any]
    score: QualityScore | None
    ai_audits: tuple[dict[str, Any], ...]


class ControlledEvidenceSearch:
    def __init__(self, dataset_id: str, chunks: list[dict[str, Any]]):
        self.dataset_id = dataset_id
        self.chunks = chunks

    def search(self, actor_id, request):
        assert actor_id
        assert request["dataset_ids"] == [self.dataset_id]
        return True, {"chunks": self.chunks}


@pytest.fixture()
def database(tmp_path):
    database = SqliteDatabase(tmp_path / "live-quality.sqlite")
    tables = BusinessDocumentService.model_tables()
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)
        database.close()


def _command(projection, command_type, payload=None):
    return {
        "schema_version": "1",
        "command_id": uuid4().hex,
        "idempotency_key": uuid4().hex,
        "expected_state_version": projection["state_version"],
        "type": command_type,
        "payload": payload or {},
    }


def _complete_requested_job(worker, tenant_id, projection, command_type, payload=None):
    requested = BusinessDocumentService.execute_command(
        tenant_id,
        tenant_id,
        projection["document_id"],
        _command(projection, command_type, payload),
    )
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    for _attempt in range(job.max_attempts):
        assert worker.run_once() is True
        job = BusinessDocumentJob.get_by_id(job.id)
        if job.status in {"COMPLETED", "DEAD"}:
            break
    assert job.status == "COMPLETED", {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "attempt": job.attempt,
        "error": job.error,
    }
    return BusinessDocumentService.get_document(tenant_id, projection["document_id"], tenant_id), job


def _ai_audit(job: BusinessDocumentJob) -> dict[str, Any]:
    result = job.result if isinstance(job.result, dict) else {}
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    audit = execution.get("ai")
    assert isinstance(audit, dict), f"Job {job.id} lacks an AI execution audit"
    return audit


def _dataset_id(case: dict[str, Any]) -> str:
    return f"live-quality-{case['id'].lower().replace('_', '-')}"


def _source_ref(dataset_id: str, chunk: dict[str, Any]) -> str:
    return f"ragflow://dataset/{dataset_id}/document/{chunk['document_id']}/chunk/{chunk['chunk_id']}"


def _evidence_chunks(case: dict[str, Any], dataset_id: str) -> list[dict[str, Any]]:
    return [{"dataset_id": dataset_id, **chunk} for chunk in case["evidence_chunks"]]


def _controlled_facts(case: dict[str, Any], dataset_id: str) -> tuple[ControlledFact, ...]:
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in case["evidence_chunks"]}
    return tuple(
        ControlledFact(
            fact["fact_id"],
            tuple(fact["aliases"]),
            _source_ref(dataset_id, chunks_by_id[fact["chunk_id"]]),
            tuple(fact["expected_section_ids"]),
            tuple(fact["subject_aliases"]),
        )
        for fact in case["controlled_facts"]
    )


def _answer_for(case: dict[str, Any], question: dict[str, Any]) -> str:
    return case["answers_by_section"].get(
        question.get("target_section_id"),
        "Используй описанный сценарий и источники, не добавляя неподтвержденных фактов.",
    )


def _create_case_runtime(case: dict[str, Any], tenant_id: str):
    dataset_id = _dataset_id(case)
    dataset_ids = [dataset_id] if case["evidence_chunks"] else []
    document = BusinessDocumentService.create_document(
        tenant_id,
        tenant_id,
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": case["title"],
            "idea": case["idea"],
            "dataset_ids": dataset_ids,
        },
    )
    evidence = BusinessDocumentEvidence(
        search_adapter=ControlledEvidenceSearch(dataset_id, _evidence_chunks(case, dataset_id)),
        access_checker=lambda candidate, actor: candidate == dataset_id and actor == tenant_id,
    )
    worker = BusinessDocumentWorker(
        worker_id=f"live-quality-{case['id']}-{uuid4().hex}",
        ai=BusinessDocumentAI(),
        evidence=evidence,
        retry_base_ms=0,
    )
    return document, worker, dataset_id


def _complete_intake(case: dict[str, Any], tenant_id: str, document: dict[str, Any], worker: BusinessDocumentWorker):
    audits: list[dict[str, Any]] = []
    for _round in range(5):
        document, job = _complete_requested_job(worker, tenant_id, document, "REQUEST_INTAKE_ASSESSMENT")
        audits.append(_ai_audit(job))
        if "REQUEST_DRAFT" in document["allowed_commands"]:
            return document, audits
        open_questions = [question for question in document["protocol"]["questions"] if question["status"] == "OPEN"]
        assert open_questions, document
        for question in open_questions:
            BusinessDocumentService.execute_command(
                tenant_id,
                tenant_id,
                document["document_id"],
                _command(
                    document,
                    "ANSWER_QUESTION",
                    {
                        "question_id": question["question_id"],
                        "selected_option_id": None,
                        "custom_answer": _answer_for(case, question),
                    },
                ),
            )
            document = BusinessDocumentService.get_document(tenant_id, document["document_id"], tenant_id)
    raise AssertionError("Live model did not close intake after five assessment rounds")


def _draft_score_failures(score: QualityScore) -> list[str]:
    failures: list[str] = []
    gate = RUBRIC["live_suite_gate"]
    if score.hard_failures:
        failures.append(f"hard_failures={score.hard_failures}")
    if not score.protocol_separated:
        failures.append("protocol_not_separated")
    if not score.question_bounds_valid:
        failures.append("invalid_question_bounds")
    if score.grounded_claim_count < 2:
        failures.append(f"grounded_claim_count={score.grounded_claim_count}")
    if score.grounded_reference_precision < gate["minimum_grounded_fact_precision"]:
        failures.append(f"grounded_reference_precision={score.grounded_reference_precision}")
    if score.semantic_coverage < gate["minimum_semantic_coverage"]:
        failures.append(f"missing_fact_ids={score.missing_fact_ids}")
    if score.duplication_rate > gate["maximum_duplication_rate"]:
        failures.append(f"duplicate_content={score.duplicate_content}")
    if score.misplacement_rate > gate["maximum_misplacement_rate"]:
        failures.append(f"misplaced_fact_count={score.misplaced_fact_count}")
    if score.contradiction_rate > gate["maximum_contradiction_rate"]:
        failures.append(f"contradictions={score.contradictions}")
    if score.weighted_score < RUBRIC["pass_threshold"]:
        failures.append(f"weighted_score={score.weighted_score}")
    return failures


def _validate_draft(case: dict[str, Any], document: dict[str, Any], draft_job: BusinessDocumentJob, dataset_id: str):
    document_ast = validate_document_ast(document["current_revision"]["document_ast"])
    template = published_template()
    failures: list[str] = []
    if [section["id"] for section in document_ast["sections"]] != [section["id"] for section in template["sections"]]:
        failures.append("template_section_order_mismatch")
    conceptual = next(section for section in document_ast["sections"] if section["id"] == "4.1")
    if not any(block["type"] == "plantuml" for block in conceptual["blocks"]):
        failures.append("conceptual_plantuml_missing")
    scenario = next(section for section in document_ast["sections"] if section["id"] == "4.3")
    if not any(block["type"] in {"paragraph", "list", "table"} for block in scenario["blocks"]):
        failures.append("scenario_text_missing")
    if not any(block["type"] == "plantuml" for block in scenario["blocks"]):
        failures.append("scenario_plantuml_missing")
    body = document["current_revision"]["body_markdown"].casefold()
    for fragment in case["expected"]["required_fragments"]:
        if fragment.casefold() not in body:
            failures.append(f"required_fragment_missing:{fragment}")
    for fragment in case["expected"]["forbidden_fragments"]:
        if fragment.casefold() in body:
            failures.append(f"forbidden_fragment_present:{fragment}")
    questions = list(BusinessDocumentQuestion.select().where(BusinessDocumentQuestion.document_id == document["document_id"]))
    if not all(2 <= len(question.options) <= 4 for question in questions):
        failures.append("invalid_question_bounds")
    snapshot = BusinessDocumentEvidenceSnapshot.get(BusinessDocumentEvidenceSnapshot.job_id == draft_job.id)
    score = score_document_quality(
        document_ast,
        document["protocol"],
        template,
        RUBRIC,
        _controlled_facts(case, dataset_id),
        snapshot.snapshot,
    )
    failures.extend(_draft_score_failures(score))
    return score, failures


def _apply_review_change(
    case: dict[str, Any],
    tenant_id: str,
    document: dict[str, Any],
    worker: BusinessDocumentWorker,
    audits: list[dict[str, Any]],
):
    original_revision = document["current_revision"]
    selected_text = original_revision["section_texts"]["4.3"]
    BusinessDocumentService.execute_command(
        tenant_id,
        tenant_id,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": original_revision["revision_id"],
                "section_id": "4.3",
                "text": case["review_comment"],
                "anchor": {
                    "revision_id": original_revision["revision_id"],
                    "section_id": "4.3",
                    "selected_text": selected_text,
                    "prefix": "",
                    "suffix": "",
                    "start_offset": 0,
                    "end_offset": len(selected_text),
                },
            },
        ),
    )
    document = BusinessDocumentService.get_document(tenant_id, document["document_id"], tenant_id)
    for _round in range(6):
        document, review_job = _complete_requested_job(worker, tenant_id, document, "REQUEST_REVIEW_ASSESSMENT")
        audits.append(_ai_audit(review_job))
        for question in [item for item in document["protocol"]["questions"] if item["status"] == "OPEN"]:
            BusinessDocumentService.execute_command(
                tenant_id,
                tenant_id,
                document["document_id"],
                _command(
                    document,
                    "ANSWER_QUESTION",
                    {
                        "question_id": question["question_id"],
                        "selected_option_id": None,
                        "custom_answer": case["review_comment"],
                    },
                ),
            )
            document = BusinessDocumentService.get_document(tenant_id, document["document_id"], tenant_id)
        for proposal in [item for item in document["protocol"]["proposals"] if item["decision"] in {None, "PENDING"}]:
            BusinessDocumentService.execute_command(
                tenant_id,
                tenant_id,
                document["document_id"],
                _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal["proposal_id"], "decision": "ACCEPTED"}),
            )
            document = BusinessDocumentService.get_document(tenant_id, document["document_id"], tenant_id)
        if "APPLY_CHANGES" in document["allowed_commands"]:
            break
    else:
        raise AssertionError(
            {
                "message": "Live model did not make the confirmed review change applicable",
                "allowed_commands": document["allowed_commands"],
                "open_question_tags": [item["semantic_tag"] for item in document["protocol"]["questions"] if item["status"] == "OPEN"],
                "pending_proposals": [item["text"] for item in document["protocol"]["proposals"] if item["decision"] in {None, "PENDING"}],
                "comment_dispositions": [item["disposition"] for item in document["protocol"]["comments"]],
            }
        )
    document, change_job = _complete_requested_job(
        worker,
        tenant_id,
        document,
        "APPLY_CHANGES",
        {"base_revision_id": original_revision["revision_id"]},
    )
    audits.append(_ai_audit(change_job))
    assert document["current_revision"]["revision_number"] == original_revision["revision_number"] + 1
    assert document["current_revision"]["content_hash"] != original_revision["content_hash"]
    return document


def _case_metrics(score: QualityScore | None) -> dict[str, Any]:
    if score is None:
        return {}
    return {
        "criterion_scores": score.criterion_scores,
        "weighted_score": score.weighted_score,
        "grounded_reference_precision": score.grounded_reference_precision,
        "grounded_claim_count": score.grounded_claim_count,
        "semantic_coverage": score.semantic_coverage,
        "missing_fact_ids": score.missing_fact_ids,
        "duplicate_content_count": len(score.duplicate_content),
        "duplicate_content": score.duplicate_content,
        "duplication_rate": score.duplication_rate,
        "misplaced_fact_count": score.misplaced_fact_count,
        "misplacement_rate": score.misplacement_rate,
        "contradiction_count": len(score.contradictions),
        "contradiction_rate": score.contradiction_rate,
        "contradictions": score.contradictions,
        "hard_failures": score.hard_failures,
    }


def _run_model_case(case: dict[str, Any], tenant_id: str, audits: list[dict[str, Any]]) -> CaseExecution:
    document, worker, dataset_id = _create_case_runtime(case, tenant_id)
    workflow = case["workflow"]
    if workflow in {"intake_questions", "source_conflict"}:
        document, job = _complete_requested_job(worker, tenant_id, document, "REQUEST_INTAKE_ASSESSMENT")
        audits.append(_ai_audit(job))
        questions = [item for item in document["protocol"]["questions"] if item["status"] == "OPEN"]
        failures: list[str] = []
        question_count = case["expected"]["question_count"]
        if not question_count["minimum"] <= len(questions) <= question_count["maximum"]:
            failures.append(f"questions_count={len(questions)}")
        expected_targets = set(case["expected"]["question_target_section_ids"])
        if expected_targets and not any(question["target_section_id"] in expected_targets for question in questions):
            failures.append(f"question_targets={[question['target_section_id'] for question in questions]}")
        if document["current_revision"] is not None:
            failures.append("unexpected_revision_created")
        return CaseExecution(
            result={
                "case_id": case["id"],
                "priority": case["priority"],
                "status": "PASS" if not failures else "FAIL",
                "failures": failures,
                "metrics": {},
            },
            score=None,
            ai_audits=tuple(audits),
        )

    document, intake_audits = _complete_intake(case, tenant_id, document, worker)
    audits.extend(intake_audits)
    document, draft_job = _complete_requested_job(worker, tenant_id, document, "REQUEST_DRAFT")
    audits.append(_ai_audit(draft_job))
    if workflow == "review_change":
        document = _apply_review_change(case, tenant_id, document, worker, audits)
    score, failures = _validate_draft(case, document, draft_job, dataset_id)
    return CaseExecution(
        result={
            "case_id": case["id"],
            "priority": case["priority"],
            "status": "PASS" if not failures else "FAIL",
            "failures": failures,
            "metrics": _case_metrics(score),
        },
        score=score,
        ai_audits=tuple(audits),
    )


def _aggregate_ai_audits(audits: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not audits:
        return None
    identities = {(audit["provider"], audit["model"], audit["model_type"]) for audit in audits}
    assert len(identities) == 1, f"Live suite used inconsistent model identities: {identities}"
    first = audits[0]
    profiles = {json.dumps(audit["parameters"], sort_keys=True) for audit in audits}
    prompt_tokens = sum(audit["token_usage"]["prompt_tokens"] for audit in audits)
    completion_tokens = sum(audit["token_usage"]["completion_tokens"] for audit in audits)
    totals = [audit["token_usage"]["total_tokens"] for audit in audits]
    detailed_total = prompt_tokens + completion_tokens
    return {
        "provider": first["provider"],
        "model": first["model"],
        "model_type": first["model_type"],
        "parameter_profiles": [json.loads(profile) for profile in sorted(profiles)],
        "execution_count": len(audits),
        "duration_ms": sum(audit["duration_ms"] for audit in audits),
        "token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": detailed_total if detailed_total else sum(totals),
        },
    }


def _aggregate_metrics(scores: list[QualityScore], case_results: list[dict[str, Any]]) -> dict[str, Any]:
    criteria = {criterion["id"] for criterion in RUBRIC["criteria"]}
    criterion_scores = {criterion: sum(score.criterion_scores[criterion] for score in scores) / len(scores) if scores else 0.0 for criterion in criteria}
    p0 = [result for result in case_results if result["priority"] == "P0"]
    scored_results = [result for result in case_results if result["metrics"]]
    duplicates = [f"{result['case_id']}:{item}" for result, score in zip(scored_results, scores, strict=True) for item in score.duplicate_content]
    contradictions = [f"{result['case_id']}:{item}" for result, score in zip(scored_results, scores, strict=True) for item in score.contradictions]
    return {
        "criterion_scores": criterion_scores,
        "weighted_score": sum(score.weighted_score for score in scores) / len(scores) if scores else 0.0,
        "p0_case_pass_rate": sum(result["status"] == "PASS" for result in p0) / len(p0),
        "all_case_pass_rate": sum(result["status"] == "PASS" for result in case_results) / len(case_results),
        "grounded_reference_precision": min((score.grounded_reference_precision for score in scores), default=0.0),
        "grounded_claim_count": sum(score.grounded_claim_count for score in scores),
        "semantic_coverage": min((score.semantic_coverage for score in scores), default=0.0),
        "missing_fact_ids": sorted({fact for score in scores for fact in score.missing_fact_ids}),
        "duplicate_content_count": len(duplicates),
        "duplicate_content": duplicates,
        "duplication_rate": max((score.duplication_rate for score in scores), default=0.0),
        "misplaced_fact_count": sum(score.misplaced_fact_count for score in scores),
        "misplacement_rate": max((score.misplacement_rate for score in scores), default=0.0),
        "contradiction_count": len(contradictions),
        "contradiction_rate": max((score.contradiction_rate for score in scores), default=0.0),
        "contradictions": contradictions,
        "hard_failures": sorted({failure for score in scores for failure in score.hard_failures}),
    }


def _asset_hashes() -> dict[str, str]:
    return {name: f"sha256:{hashlib.sha256((ASSET_ROOT / 'prompts' / name).read_bytes()).hexdigest()}" for name in PROMPT_NAMES}


def _write_report(path: str, suite: dict[str, Any], case_results: list[dict[str, Any]], scores: list[QualityScore], audits: list[dict[str, Any]]):
    suite_bytes = MODEL_GOLDEN_PATH.read_bytes()
    failures = [result for result in case_results if result["status"] != "PASS"]
    report = {
        "schema_version": "2",
        "status": "FAIL" if failures else "PASS",
        "scoring_method": "deterministic_proxy",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": os.environ.get("GITHUB_SHA", "unknown"),
        "source_dirty": {"1": True, "0": False}.get(os.environ.get("RAGFLOW_QA_SOURCE_DIRTY")),
        "model_weights_digest": os.environ.get("BUSINESS_DOCUMENT_MODEL_DIGEST", ""),
        "rubric_id": RUBRIC["rubric_id"],
        "rubric_version": RUBRIC["rubric_version"],
        "template_version": published_template()["template_version"],
        "prompts": _asset_hashes(),
        "golden_suite": {
            "suite_id": suite["suite_id"],
            "suite_version": suite["suite_version"],
            "sha256": f"sha256:{hashlib.sha256(suite_bytes).hexdigest()}",
            "expected_case_ids": [case["id"] for case in suite["cases"]],
            "executed_case_ids": [result["case_id"] for result in case_results],
        },
        "ai": _aggregate_ai_audits(audits),
        "case_results": case_results,
        "metrics": _aggregate_metrics(scores, case_results),
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


@pytest.mark.p1
@pytest.mark.skipif(
    os.environ.get("BUSINESS_DOCUMENT_LIVE_LLM") != "1",
    reason="Set BUSINESS_DOCUMENT_LIVE_LLM=1 and BUSINESS_DOCUMENT_LIVE_TENANT_ID=<tenant> to run the real-model quality lane",
)
def test_live_model_golden_suite(database, monkeypatch):
    del database
    from common import settings

    if settings.FACTORY_LLM_INFOS is None:
        settings.init_settings()
    try:
        config = resolve_live_quality_config(os.environ)
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    assert config is not None
    suite = json.loads(MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        sys.modules[BusinessDocumentService.__module__],
        "ensure_dataset_access",
        lambda actor_id, dataset_ids: None,
    )
    monkeypatch.setattr(sys.modules[BusinessDocumentWorker.__module__], "related_file_search_enabled", lambda: True)
    executions: list[CaseExecution] = []
    for case in suite["cases"]:
        case_audits: list[dict[str, Any]] = []
        try:
            executions.append(_run_model_case(case, config.tenant_id, case_audits))
        except Exception as error:  # noqa: BLE001 - preserve every case result in the evidence packet
            executions.append(
                CaseExecution(
                    result={
                        "case_id": case["id"],
                        "priority": case["priority"],
                        "status": "FAIL",
                        "failures": [f"{type(error).__name__}: {error}"],
                        "metrics": {},
                    },
                    score=None,
                    ai_audits=tuple(case_audits),
                )
            )
    case_results = [execution.result for execution in executions]
    scores = [execution.score for execution in executions if execution.score is not None]
    audits = [audit for execution in executions for audit in execution.ai_audits]
    report_path = os.environ.get("BUSINESS_DOCUMENT_QUALITY_REPORT", "").strip()
    if report_path:
        _write_report(report_path, suite, case_results, scores, audits)
    failures = {result["case_id"]: result["failures"] for result in case_results if result["status"] != "PASS"}
    assert not failures, failures
