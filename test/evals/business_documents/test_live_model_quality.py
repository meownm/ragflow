"""Opt-in data-driven real-model quality gate for business requirements."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from peewee import SqliteDatabase


REPO_ROOT = Path(__file__).resolve().parents[3]
if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(REPO_ROOT / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.ai import BusinessDocumentAI, RAGFlowLLMAdapter
from api.apps.business_documents.assets import published_template, validate_document_ast
from api.apps.business_documents.evidence import BusinessDocumentEvidence
from api.apps.business_documents.runtime import document_queries
from api.apps.business_documents.runtime import document_commands
from api.apps.business_documents.runtime import document_creation
from api.apps.business_documents.adapters.persistence import DOCUMENT_TABLES
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocumentEvidenceSnapshot, BusinessDocumentJob, BusinessDocumentQuestion
from test.evals.business_documents.live_quality import ControlledFact, QualityScore, resolve_live_quality_config, score_document_quality
from test.evals.business_documents.metric_placement import run_metric_placement


ASSET_ROOT = REPO_ROOT / "agent" / "business_requirements"
MODEL_GOLDEN_PATH = ASSET_ROOT / "golden_model_quality" / "v2.json"
RUBRIC = json.loads((ASSET_ROOT / "evals" / "rubric.v1.json").read_text(encoding="utf-8"))
PROMPT_NAMES = ("intake.v1.md", "draft.v1.md", "draft_evidence.v1.md", "review.v1.md", "change_planner.v1.md", "requirement_coverage.v1.md")


@dataclass(frozen=True)
class CaseExecution:
    result: dict[str, Any]
    score: QualityScore | None
    ai_audits: tuple[dict[str, Any], ...]


def _append_evidence(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"recorded_at": datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False) + "\n")


def _record_model_calls(monkeypatch, calls: list[dict[str, Any]], journal_path: Path | None = None) -> None:
    """Retain synthetic inputs and outputs without changing the live adapter."""
    generate = RAGFlowLLMAdapter.async_generate

    async def recorded(self, tenant_id, system_prompt, input_payload, on_chunk=None):
        request = {"system_prompt": system_prompt, "input_payload": deepcopy(input_payload)}
        call = {
            **request,
            "request_sha256": hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        }
        calls.append(call)
        call_id = len(calls)

        def record(event, **detail):
            if journal_path is not None:
                _append_evidence(journal_path, {"event": event, "call_id": call_id, **detail})

        record("request", **call)
        callback = on_chunk
        if journal_path is not None and on_chunk is not None:

            def callback(chunk):
                record("chunk", text=chunk)
                on_chunk(chunk)

        try:
            response = await generate(self, tenant_id, system_prompt, input_payload, callback)
        except BaseException as error:
            call["error"] = {"type": type(error).__name__, "message": str(error)}
            record("error", error=call["error"], traceback=traceback.format_exc())
            raise
        call["response"] = response
        record("response", text=response, sha256=hashlib.sha256(response.encode("utf-8")).hexdigest())
        return response

    monkeypatch.setattr(RAGFlowLLMAdapter, "async_generate", recorded)


def _case_job_evidence(previous_ids: set[str]) -> list[dict[str, Any]]:
    return [
        {"job_id": job.id, "job_type": job.job_type, "status": job.status, "attempt": job.attempt, "error": job.error, "result": job.result}
        for job in BusinessDocumentJob.select().order_by(BusinessDocumentJob.create_time, BusinessDocumentJob.id)
        if job.id not in previous_ids
    ]


def _execution_error(error: Exception) -> dict[str, Any]:
    detail = error.args[0] if error.args and isinstance(error.args[0], dict) else {}
    job_error = detail.get("error")
    code = getattr(error, "code", None) or (job_error.get("code") if isinstance(job_error, dict) else None)
    return {
        "error_type": type(error).__name__,
        "job_code": code if isinstance(code, str) else None,
        **({"check_id": detail["check_id"]} if isinstance(detail.get("check_id"), str) else {}),
    }


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
    database = SqliteDatabase(tmp_path / "live-quality.sqlite", timeout=30, pragmas={"journal_mode": "wal"})
    tables = DOCUMENT_TABLES
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False), database.connection_context():
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)


def test_live_database_allows_checkpoint_while_reader_is_open(database):
    database.execute_sql("CREATE TABLE checkpoint_probe (value INTEGER)")
    database.execute_sql("INSERT INTO checkpoint_probe VALUES (0)")

    def checkpoint():
        with database.connection_context():
            database.execute_sql("UPDATE checkpoint_probe SET value = 1")

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.atomic():
                assert database.execute_sql("SELECT value FROM checkpoint_probe").fetchone() == (0,)
                pool.submit(checkpoint).result(timeout=5)
        assert database.execute_sql("SELECT value FROM checkpoint_probe").fetchone() == (1,)
    finally:
        database.execute_sql("DROP TABLE checkpoint_probe")


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
    requested = document_commands.execute(
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
    if job.status != "COMPLETED":
        raise AssertionError(
            {
                "check_id": "job_completed",
                "job_id": job.id,
                "job_type": job.job_type,
                "status": job.status,
                "attempt": job.attempt,
                "error": job.error,
            }
        )
    return document_queries.get_document(projection["document_id"], tenant_id), job


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
    document = document_creation.execute(
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
        ai=BusinessDocumentAI(adapter=RAGFlowLLMAdapter(model_name_override=os.environ.get("BUSINESS_DOCUMENT_QUALITY_MODEL") or None)),
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
            document_commands.execute(
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
            document = document_queries.get_document(document["document_id"], tenant_id)
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
    assert selected_text.strip(), "Generated draft has an empty review target section 4.3"
    document_commands.execute(
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
    document = document_queries.get_document(document["document_id"], tenant_id)
    for _round in range(6):
        document, review_job = _complete_requested_job(worker, tenant_id, document, "REQUEST_REVIEW_ASSESSMENT")
        audits.append(_ai_audit(review_job))
        if "APPLY_CHANGES" in document["allowed_commands"]:
            break
        for question in [item for item in document["protocol"]["questions"] if item["status"] == "OPEN"]:
            document_commands.execute(
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
            document = document_queries.get_document(document["document_id"], tenant_id)
        for proposal in [item for item in document["protocol"]["proposals"] if item["decision"] in {None, "PENDING"}]:
            document_commands.execute(
                tenant_id,
                tenant_id,
                document["document_id"],
                _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal["proposal_id"], "decision": "ACCEPTED"}),
            )
            document = document_queries.get_document(document["document_id"], tenant_id)
        if "APPLY_CHANGES" in document["allowed_commands"]:
            break
    else:
        raise AssertionError(
            {
                "message": "Live model did not make the confirmed review change applicable",
                "check_id": "review_change_applicable",
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
    if document["current_revision"]["revision_number"] != original_revision["revision_number"] + 1:
        raise AssertionError({"check_id": "review_revision_created"})
    if document["current_revision"]["content_hash"] == original_revision["content_hash"]:
        raise AssertionError({"check_id": "review_content_changed"})
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
    if case["workflow"] == "metric_placement":
        result, case_audits = run_metric_placement(case, tenant_id, RAGFlowLLMAdapter(model_name_override=os.environ.get("BUSINESS_DOCUMENT_QUALITY_MODEL") or None))
        result["evidence"] = {key: result.pop(key) for key in list(result) if key not in {"case_id", "priority", "status", "failures", "metrics"}}
        audits.extend(case_audits)
        return CaseExecution(result=result, score=None, ai_audits=tuple(audits))
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
    audits = [
        step
        for audit in audits
        for step in [audit, *audit.get("recovery", []), *(audit["verification"] if isinstance(audit.get("verification"), list) else [audit["verification"]] if audit.get("verification") else [])]
    ]
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
        "p0_case_pass_rate": sum(result["status"] == "PASS" for result in p0) / len(p0) if p0 else None,
        "all_case_pass_rate": sum(result["status"] == "PASS" for result in case_results) / len(case_results) if case_results else None,
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


def _write_json(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def _write_report(
    path: str,
    suite: dict[str, Any],
    case_results: list[dict[str, Any]],
    scores: list[QualityScore],
    audits: list[dict[str, Any]],
    *,
    active_case_id: str | None = None,
    diagnostic_case_id: str | None = None,
):
    suite_bytes = MODEL_GOLDEN_PATH.read_bytes()
    failures = [result for result in case_results if result["status"] != "PASS"]
    complete = active_case_id is None and diagnostic_case_id is None and len(case_results) == len(suite["cases"])
    interrupted = any(result["status"] == "INCOMPLETE" for result in case_results)
    report = {
        "schema_version": "2",
        "status": "INCOMPLETE" if not complete or interrupted else "FAIL" if failures else "PASS",
        "active_case_id": active_case_id,
        "diagnostic_case_id": diagnostic_case_id,
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
        "metrics": _aggregate_metrics(scores, case_results) if case_results else None,
    }
    _write_json(path, report)


def test_partial_live_report_identifies_unfinished_case(tmp_path):
    suite = json.loads(MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "quality.json"
    _write_report(str(path), suite, [], [], [], active_case_id=suite["cases"][0]["id"])
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "INCOMPLETE"
    assert report["active_case_id"] == suite["cases"][0]["id"]
    assert report["golden_suite"]["executed_case_ids"] == []
    assert report["metrics"] is None


def test_execution_error_keeps_full_report_incomplete(tmp_path):
    suite = json.loads(MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    results = [{"case_id": case["id"], "priority": case["priority"], "status": "INCOMPLETE" if index == 0 else "PASS", "failures": [], "metrics": {}} for index, case in enumerate(suite["cases"])]
    path = tmp_path / "quality.json"
    _write_report(str(path), suite, results, [], [])
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "INCOMPLETE"


@pytest.mark.parametrize(
    "error,expected",
    [
        (AssertionError({"error": {"code": "REQUIREMENT_COVERAGE_FAILED", "message": "private document"}}), {"error_type": "AssertionError", "job_code": "REQUIREMENT_COVERAGE_FAILED"}),
        (AssertionError({"error": "private exception"}), {"error_type": "AssertionError", "job_code": None}),
        (TimeoutError("private endpoint"), {"error_type": "TimeoutError", "job_code": None}),
    ],
)
def test_case_execution_error_preserves_codes_without_error_text(error, expected):
    assert _execution_error(error) == expected


def test_failed_live_job_preserves_diagnostic_through_pytest_assertion_rewriting(monkeypatch):
    job = SimpleNamespace(id="failed-job", job_type="PLAN_CHANGES", max_attempts=1, status="DEAD", attempt=1, error={"code": "REQUIREMENT_COVERAGE_FAILED", "message": "private content"})
    monkeypatch.setattr(document_commands, "execute", lambda *_: {"job_id": job.id})
    monkeypatch.setattr(BusinessDocumentJob, "get_by_id", lambda *_: job)
    document = {"document_id": "document", "state_version": 1}
    with pytest.raises(AssertionError) as caught:
        _complete_requested_job(SimpleNamespace(run_once=lambda: True), "tenant", document, "APPLY_CHANGES")
    assert _execution_error(caught.value) == {"error_type": "AssertionError", "job_code": "REQUIREMENT_COVERAGE_FAILED", "check_id": "job_completed"}


@pytest.mark.asyncio
async def test_model_call_evidence_preserves_inputs_response_and_stream_callback(monkeypatch):
    calls = []
    payload = {"job_input": {"task_type": "GENERATE_DRAFT"}, "evidence": {"chunks": [{"content": "Подтвержденный факт"}]}}

    def callback(_chunk):
        pass

    async def generate(self, tenant_id, system_prompt, input_payload, on_chunk=None):
        assert tenant_id == "synthetic-tenant" and system_prompt == "trusted prompt"
        assert input_payload is payload and on_chunk is callback
        input_payload["job_input"]["changed_after_call"] = True
        return '{"draft": {}}'

    monkeypatch.setattr(RAGFlowLLMAdapter, "async_generate", generate)
    _record_model_calls(monkeypatch, calls)
    adapter = RAGFlowLLMAdapter()
    result = await adapter.async_generate("synthetic-tenant", "trusted prompt", payload, callback)
    assert result == calls[0]["response"] == '{"draft": {}}'
    assert "changed_after_call" not in calls[0]["input_payload"]["job_input"]
    request = {key: calls[0][key] for key in ("system_prompt", "input_payload")}
    assert calls[0]["request_sha256"] == hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@pytest.mark.asyncio
async def test_model_call_evidence_does_not_retry_or_hide_provider_errors(monkeypatch):
    calls = []
    error = TimeoutError("synthetic timeout")

    async def generate(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(RAGFlowLLMAdapter, "async_generate", generate)
    _record_model_calls(monkeypatch, calls)
    with pytest.raises(TimeoutError) as caught:
        await RAGFlowLLMAdapter().async_generate("synthetic-tenant", "prompt", {})
    assert caught.value is error
    assert len(calls) == 1 and calls[0]["error"] == {"type": "TimeoutError", "message": "synthetic timeout"}
    assert "response" not in calls[0]


@pytest.mark.parametrize("failure", [TimeoutError("provider timeout"), asyncio.CancelledError("cancelled")])
@pytest.mark.asyncio
async def test_model_journal_retains_request_and_partial_stream_on_failure(monkeypatch, tmp_path, failure):
    journal = tmp_path / "calls.jsonl"
    chunks = []

    async def generate(self, tenant_id, system_prompt, input_payload, on_chunk=None):
        assert json.loads(journal.read_text(encoding="utf-8"))["event"] == "request"
        on_chunk("частичный ответ")
        assert len(journal.read_text(encoding="utf-8").splitlines()) == 2
        raise failure

    monkeypatch.setattr(RAGFlowLLMAdapter, "async_generate", generate)
    calls = []
    _record_model_calls(monkeypatch, calls, journal)
    with pytest.raises(type(failure)) as caught:
        await RAGFlowLLMAdapter().async_generate("synthetic", "full prompt", {"data": "input"}, chunks.append)
    assert caught.value is failure and chunks == ["частичный ответ"]
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [item["event"] for item in events] == ["request", "chunk", "error"]
    assert events[0]["system_prompt"] == "full prompt"
    assert events[-1]["error"]["type"] in events[-1]["traceback"]


@pytest.mark.asyncio
async def test_model_journal_keeps_complete_untruncated_response(monkeypatch, tmp_path):
    response = "полный ответ\n" * 10000

    async def generate(*_args, **_kwargs):
        return response

    monkeypatch.setattr(RAGFlowLLMAdapter, "async_generate", generate)
    journal = tmp_path / "calls.jsonl"
    _record_model_calls(monkeypatch, [], journal)
    assert await RAGFlowLLMAdapter().async_generate("synthetic", "prompt", {}) == response
    final = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
    assert final["text"] == response
    assert final["sha256"] == hashlib.sha256(response.encode()).hexdigest()


def test_review_journey_applies_ready_change_before_creating_more_feedback(monkeypatch):
    revision = {"revision_id": "revision-1", "revision_number": 1, "content_hash": "before", "section_texts": {"4.3": "Existing scenario"}}
    document = {"document_id": "synthetic-document", "state_version": 1, "current_revision": revision}
    assessed = {
        **document,
        "allowed_commands": ["APPLY_CHANGES"],
        "protocol": {
            "questions": [{"question_id": "open-question", "status": "OPEN"}],
            "proposals": [{"proposal_id": "undecided-proposal", "decision": "PENDING"}],
        },
    }
    commands = []
    jobs = []

    def execute(_tenant, _actor, _document, command):
        commands.append(command["type"])
        assert command["type"] == "ADD_COMMENT", "Ready partial changes must not be invalidated by new feedback"

    def complete(_worker, _tenant, _document, command_type, payload=None):
        jobs.append(command_type)
        if command_type == "REQUEST_REVIEW_ASSESSMENT":
            return assessed, SimpleNamespace(result={"execution": {"ai": {"task": command_type}}})
        assert command_type == "APPLY_CHANGES" and payload == {"base_revision_id": revision["revision_id"]}
        return {**assessed, "current_revision": {**revision, "revision_number": 2, "content_hash": "after"}}, SimpleNamespace(result={"execution": {"ai": {"task": command_type}}})

    monkeypatch.setattr(document_commands, "execute", execute)
    monkeypatch.setattr(document_queries, "get_document", lambda *_: assessed)
    monkeypatch.setattr(sys.modules[__name__], "_complete_requested_job", complete)
    result = _apply_review_change({"review_comment": "Confirmed business change"}, "synthetic-tenant", document, object(), [])
    assert commands == ["ADD_COMMENT"]
    assert jobs == ["REQUEST_REVIEW_ASSESSMENT", "APPLY_CHANGES"]
    assert result["protocol"] == assessed["protocol"]


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
    diagnostic_case_id = os.environ.get("BUSINESS_DOCUMENT_QUALITY_CASE_ID", "").strip() or None
    if diagnostic_case_id and diagnostic_case_id not in {case["id"] for case in suite["cases"]}:
        pytest.fail("Unknown BUSINESS_DOCUMENT_QUALITY_CASE_ID", pytrace=False)
    cases = [case for case in suite["cases"] if diagnostic_case_id is None or case["id"] == diagnostic_case_id]
    monkeypatch.setattr(
        document_creation,
        "_datasets",
        SimpleNamespace(ensure_dataset_access=lambda *_: None, ensure_dataset_embedding_compatibility=document_creation._datasets.ensure_dataset_embedding_compatibility),
    )
    monkeypatch.setattr(sys.modules[BusinessDocumentWorker.__module__], "related_file_search_enabled", lambda: True)
    executions: list[CaseExecution] = []
    model_calls: list[dict[str, Any]] = []
    case_exchanges: list[dict[str, Any]] = []
    report_path = os.environ.get("BUSINESS_DOCUMENT_QUALITY_REPORT", "").strip()
    _record_model_calls(monkeypatch, model_calls, Path(report_path).with_suffix(".calls.jsonl") if report_path else None)

    def persist(active_case_id: str | None = None) -> None:
        if not report_path:
            return
        _write_report(
            report_path,
            suite,
            [execution.result for execution in executions],
            [execution.score for execution in executions if execution.score is not None],
            [audit for execution in executions for audit in execution.ai_audits],
            active_case_id=active_case_id,
            diagnostic_case_id=diagnostic_case_id,
        )

    persist()
    for case in cases:
        persist(case["id"])
        started = time.monotonic()
        case_audits: list[dict[str, Any]] = []
        previous_job_ids = {job.id for job in BusinessDocumentJob.select(BusinessDocumentJob.id)}
        first_call = len(model_calls)
        if report_path:
            _append_evidence(Path(report_path).with_suffix(".calls.jsonl"), {"event": "case_start", "case_id": case["id"], "first_call_id": first_call + 1})
        try:
            executions.append(_run_model_case(case, config.tenant_id, case_audits))
        except Exception as error:  # noqa: BLE001 - preserve the unfinished case without attributing it to model quality
            if report_path:
                _append_evidence(
                    Path(report_path).with_suffix(".errors.jsonl"), {"case_id": case["id"], "error": str(error), "traceback": traceback.format_exc(), "jobs": _case_job_evidence(previous_job_ids)}
                )
            executions.append(
                CaseExecution(
                    result={
                        "case_id": case["id"],
                        "priority": case["priority"],
                        "status": "INCOMPLETE",
                        "failures": [f"{type(error).__name__}: {error}"],
                        "execution_error": _execution_error(error),
                        "metrics": {},
                    },
                    score=None,
                    ai_audits=tuple(case_audits),
                )
            )
        if report_path:
            _append_evidence(Path(report_path).with_suffix(".calls.jsonl"), {"event": "case_end", "case_id": case["id"], "status": executions[-1].result["status"], "last_call_id": len(model_calls)})
            case_exchanges.append({"case_id": case["id"], "adapter_calls": model_calls[first_call:], "jobs": _case_job_evidence(previous_job_ids)})
            _write_json(Path(report_path).with_suffix(".exchanges.json"), {"schema_version": "1", "cases": case_exchanges})
        executions[-1].result["duration_ms"] = round((time.monotonic() - started) * 1000)
        persist()
    case_results = [execution.result for execution in executions]
    persist()
    failures = {result["case_id"]: result["failures"] for result in case_results if result["status"] != "PASS"}
    assert not failures, failures
