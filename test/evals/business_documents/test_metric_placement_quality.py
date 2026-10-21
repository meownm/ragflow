"""Oracle and registry tests; real-model acceptance is the opt-in M06 case."""

from copy import deepcopy
import hashlib
import json
import re

import pytest
from peewee import SqliteDatabase

from test.evals.business_documents import test_live_model_quality as live
from test.evals.business_documents.metric_placement import placement_failures
from test.evals.business_documents.metric_placement import _verification_evidence_failures
from api.apps.business_documents.adapters.persistence import DOCUMENT_TABLES
from api.db.db_models import User
from tools.quality.verify_business_document_quality_report import QualityGateFailed, verify_report
from test.unit_test.tools.quality.test_business_document_quality_report import _placement_evidence, _report


CASE = next(case for case in json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))["cases"] if case["workflow"] == "metric_placement")


@pytest.mark.parametrize(
    "field,damage",
    [
        (None, "unchanged"),
        ("unexpected", "extra"),
        *[(field, damage) for field in ("source_events", "agreement_history", "agreement_decisions", "protocol", "active_review_cycle") for damage in ("missing", "changed")],
    ],
)
def test_verification_replay_requires_complete_pinned_agreement_context(field, damage):
    expected = {
        "task_type": "VERIFY_REQUIREMENT_COVERAGE",
        "source_events": [{"event_id": "answer-1", "event_type": "QuestionAnswered"}],
        "agreement_history": {"questions": [{"id": "question-1", "text": "Полный вопрос"}]},
        "agreement_decisions": {"answers": [{"event_id": "answer-1", "text": "Полный ответ"}], "proposals": []},
        "protocol": {"questions": [{"question_id": "question-1", "answer": {"source_event_id": "answer-1"}}]},
        "active_review_cycle": 7,
    }
    recorded = deepcopy(expected)
    if damage == "missing":
        recorded.pop(field)
    elif damage == "extra":
        recorded[field] = "unsupported"
    elif damage == "changed":
        paths = {
            "source_events": (0, "event_id"),
            "agreement_history": ("questions", 0, "text"),
            "agreement_decisions": ("answers", 0, "text"),
            "protocol": ("questions", 0, "answer", "source_event_id"),
        }
        if field == "active_review_cycle":
            recorded[field] += 1
        else:
            value = recorded[field]
            for key in paths[field][:-1]:
                value = value[key]
            value[paths[field][-1]] = "changed"
    failures = _verification_evidence_failures([{"input_payload": {"job_input": recorded}, "response": {"status": "PASS", "issues": []}}], expected, {}, {})
    assert ("verification_input_mismatch" in failures) is (damage != "unchanged")
    if damage == "unchanged":
        assert failures == []


def _ideal_ast():
    ast = deepcopy(CASE["seed"]["base_document"])
    next(s for s in ast["sections"] if s["id"] == "5.5")["blocks"] = [
        {"type": "paragraph", "text": " ".join(r["text"] for r in CASE["seed"]["requirements"] if r["section_id"] == "5.5")},
        {"type": "paragraph", "text": "Состав метрик и правила расчёта определены в 5.4."},
    ]
    next(s for s in ast["sections"] if s["id"] == "5.4")["blocks"] = [
        {"type": "table", "headers": ["Метрика", "Определение"], "rows": [[r["label"], r["text"].split(": ", 1)[1]] for r in CASE["seed"]["requirements"] if r["section_id"] == "5.4"]}
    ]
    return ast


def test_fixture_has_complete_clause_inventory_and_valid_template():
    from api.apps.business_documents.assets import published_template, validate_document_ast

    base = validate_document_ast(CASE["seed"]["base_document"])
    assert [s["id"] for s in base["sections"]] == [s["id"] for s in published_template()["sections"]]
    assert [(b["type"], b["text"]) for b in next(s for s in base["sections"] if s["id"] == "5.4")["blocks"]] == [("paragraph", "Требования не определены")]
    assert CASE["seed"]["question_section_id"] == "5"
    assert CASE["seed"]["proposal_section_id"] == "5.5"
    assert len(CASE["seed"]["requirements"]) == 10
    assert len({r["id"] for r in CASE["seed"]["requirements"]}) == 10
    assert not placement_failures(CASE, validate_document_ast(_ideal_ast()))


@pytest.mark.parametrize("section_id", [s["id"] for s in CASE["seed"]["base_document"]["sections"]])
def test_m06_cannot_start_with_any_template_section_missing(section_id):
    from api.apps.business_documents.assets import contract_schema, published_template, prompt_text
    from test.evals.business_documents.metric_placement import evidence_failures

    evidence = _placement_evidence(CASE)
    call = evidence["model_calls"][0]
    document = call["input_payload"]["job_input"]["current_revision"]["document_ast"]
    document["sections"] = [s for s in document["sections"] if s["id"] != section_id]
    call["input_sha256"] = hashlib.sha256(json.dumps(call["input_payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    failures = evidence_failures(
        CASE, evidence, {name: prompt_text(name) for name in ("change_planner", "requirement_coverage")}, published_template(), contract_schema("document_draft_v2"), contract_schema("change_plan")
    )
    assert "source_input_mismatch" in failures


def test_active_suite_preserves_original_cases_except_m04_author_answer_and_registers_admin_diagnostic(monkeypatch):
    monkeypatch.syspath_prepend(str(live.REPO_ROOT / "admin/server"))
    from admin.server.document_quality_runs import _known_case_ids

    baseline = json.loads((live.ASSET_ROOT / "golden_model_quality/v1.json").read_text(encoding="utf-8"))
    active = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    original_cases = deepcopy(active["cases"][:-1])
    m04 = next(case for case in original_cases if case["id"] == "M04_prompt_injection_remains_evidence")
    original_m04 = next(case for case in baseline["cases"] if case["id"] == m04["id"])
    assert m04["answers_by_section"]["4"] != original_m04["answers_by_section"]["4"]
    m04["answers_by_section"]["4"] = original_m04["answers_by_section"]["4"]
    assert original_cases == baseline["cases"]
    assert CASE["id"] in _known_case_ids()


def test_injection_fixture_answers_limit_and_sla_without_removing_attack_or_facts():
    suite = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    case = next(case for case in suite["cases"] if case["id"].startswith("M04_"))
    assert "одной активной заявки на сотрудника" in live._answer_for(case, {"target_section_id": "4"})
    answer = live._answer_for(case, {"target_section_id": "5.5"})
    assert all(value in answer for value in ("99,5%", "3 секунды", "equipment_request_submitted", "equipment_request_error_total"))
    assert len(case["controlled_facts"]) == 5
    assert case["evidence_chunks"] and case["expected"]


def test_unapplied_model_reply_reports_format_failure_before_confirmation(monkeypatch):
    class Unapplied(FixedTransport):
        def generate(self, tenant, system, payload, **kwargs):
            super().generate(tenant, system, payload, **kwargs)
            return "Please clarify the requested change."

    database = SqliteDatabase(":memory:")
    with database.bind_ctx((*DOCUMENT_TABLES, User), bind_refs=False, bind_backrefs=False), database.connection_context():
        database.create_tables((*DOCUMENT_TABLES, User))
        monkeypatch.setattr(live, "RAGFlowLLMAdapter", lambda **_: Unapplied())
        execution = live._run_model_case(CASE, "metric-placement-quality", [])
        assert execution.result["status"] == "FAIL"
        assert execution.result["failures"] == ["job:CHANGE_FORMAT_NOT_APPLIED"]
        assert "final_ast" not in execution.result["evidence"]
    assert len(execution.result["evidence"]["model_calls"]) == 2


@pytest.mark.parametrize("requirement", CASE["seed"]["requirements"], ids=lambda r: r["id"])
def test_every_requirement_is_required(requirement):
    ast = _ideal_ast()
    section = next(s for s in ast["sections"] if s["id"] == requirement["section_id"])
    if requirement.get("label"):
        section["blocks"][0]["rows"] = [row for row in section["blocks"][0]["rows"] if row[0] != requirement["label"]]
    else:
        section["blocks"][0]["text"] = section["blocks"][0]["text"].replace(requirement["text"], "")
    assert placement_failures(CASE, ast)


@pytest.mark.parametrize("requirement,check", [(r, c) for r in CASE["seed"]["requirements"] for c in r["checks"]], ids=lambda x: x["id"])
def test_each_bound_role_negation_and_ratio_clause_is_required(requirement, check):
    ast = _ideal_ast()
    section = next(s for s in ast["sections"] if s["id"] == requirement["section_id"])
    if requirement.get("label"):
        row = next(row for row in section["blocks"][0]["rows"] if row[0] == requirement["label"])
        value = row[1].casefold().replace("ё", "е")
        changed, count = re.subn(check["pattern"], "[утрачено]", value)
        row[1] = changed
    else:
        value = section["blocks"][0]["text"].casefold().replace("ё", "е")
        changed, count = re.subn(check["pattern"], "[утрачено]", value)
        section["blocks"][0]["text"] = changed
    assert count, (requirement["id"], check["id"])
    assert f"requirement:{requirement['id']}:{check['id']}" in placement_failures(CASE, ast)


@pytest.mark.parametrize("duplicate", [False, True])
def test_metrics_cannot_be_moved_or_duplicated_in_monitoring(duplicate):
    ast = _ideal_ast()
    report = next(s for s in ast["sections"] if s["id"] == "5.4")
    next(s for s in ast["sections"] if s["id"] == "5.5")["blocks"].extend(deepcopy(report["blocks"]))
    if not duplicate:
        report["blocks"] = []
    assert placement_failures(CASE, ast)


class FixedTransport:
    """Test the runner only; it is never selected by the live gate."""

    def __init__(self, failures=()):
        self.audit = None
        self.failures = list(failures)
        self.timeouts = []

    def generate(self, tenant, system, payload, *, timeout_seconds=None):
        job = payload["job_input"]
        self.timeouts.append(timeout_seconds)
        self.audit = {
            "provider": "Fixture",
            "model": "unit",
            "model_type": "chat",
            "duration_ms": 1,
            "parameters": {"temperature": 0, "top_p": 0.1, "max_tokens": 65536 if job["task_type"] == "PLAN_CHANGES" else 4096},
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        if job["task_type"] != "PLAN_CHANGES":
            if self.failures:
                failure = self.failures.pop(0)
                if failure == "transport":
                    raise TimeoutError("Verifier temporarily unavailable")
                return {"status": "FAIL", "issues": []}
            return {"status": "PASS", "issues": []}
        sections = {s["id"]: s for s in job["current_revision"]["document_ast"]["sections"]}
        ideal = {s["id"]: s for s in _ideal_ast()["sections"]}
        answer = job["protocol"]["questions"][0]["answer"]["source_event_id"]
        proposal = job["protocol"]["proposals"][0]["decision_event_id"]
        return {
            "schema_version": "1",
            "base_revision_id": job["current_revision"]["revision_id"],
            "source_state_version": job["state_version"],
            "acknowledged_no_change_event_ids": [],
            "operations": [
                {
                    "operation_id": "report",
                    "type": "REPLACE_BLOCK",
                    "section_id": "5.4",
                    "block_id": sections["5.4"]["blocks"][0]["id"],
                    "source_event_ids": [answer],
                    "block": deepcopy(ideal["5.4"]["blocks"][0]),
                },
                *[
                    {"operation_id": f"monitor-{index}", "type": "REPLACE_BLOCK", "section_id": "5.5", "block_id": original["id"], "source_event_ids": [proposal], "block": deepcopy(block)}
                    for index, (original, block) in enumerate(zip(sections["5.5"]["blocks"], ideal["5.5"]["blocks"], strict=True))
                ],
            ],
        }

    def consume_execution_audit(self):
        audit, self.audit = self.audit, None
        return audit


@pytest.fixture()
def case_execution(monkeypatch, request):
    database = SqliteDatabase(":memory:")
    with database.bind_ctx((*DOCUMENT_TABLES, User), bind_refs=False, bind_backrefs=False), database.connection_context():
        database.create_tables((*DOCUMENT_TABLES, User))
        adapter = FixedTransport(getattr(request, "param", ()))
        monkeypatch.setattr(live, "RAGFlowLLMAdapter", lambda **_: adapter)
        execution = live._run_model_case(CASE, "metric-placement-quality", [])
        assert execution.result["status"] == "PASS", execution.result["failures"]
        assert all(timeout is not None and 0 < timeout <= 900 for timeout in adapter.timeouts)
        yield execution


def test_registered_workflow_records_actual_prompt_and_model_exchanges(case_execution, tmp_path):
    evidence = case_execution.result["evidence"]
    inputs = evidence["model_calls"][0]["input_payload"]["job_input"]
    assert not any(question["status"] == "OPEN" for question in inputs["protocol"]["questions"])
    assert set(inputs["active_change_input_event_ids"]) == set(evidence["expected_sources"]["5"] + evidence["expected_sources"]["5.5"])
    assert set(inputs["answered_section_ids"]) == {*CASE["seed"]["undefined_section_ids"], "5"}
    assert [c["task_type"] for c in evidence["model_calls"]] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert "Согласование размещения и оснований" in evidence["model_calls"][0]["system_prompt"]
    assert evidence["model_calls"][1]["system_prompt"] == (live.ASSET_ROOT / "prompts/requirement_coverage.v1.md").read_text(encoding="utf-8")
    assert not evidence["model_calls"][0]["input_payload"]["job_input"]["protocol"]["comments"]
    suite = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "diagnostic.json"
    live._write_report(str(path), suite, [case_execution.result], [], list(case_execution.ai_audits), diagnostic_case_id=CASE["id"])
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "INCOMPLETE"
    assert report["case_results"][0]["status"] == "PASS"
    assert "requirement_coverage.v1.md" in report["prompts"]


def test_insert_block_proof_preserves_requirements_and_replay(tmp_path):
    report = _report()
    result = next(item for item in report["case_results"] if item["case_id"] == CASE["id"])
    result["evidence"] = _placement_evidence(CASE, insert_monitor=True)
    path = tmp_path / "insert-proof.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert verify_report(path, live.REPO_ROOT, "candidate-sha")["status"] == "PASS"
    next(s for s in result["evidence"]["final_ast"]["sections"] if s["id"] == "5.5")["blocks"][0]["text"] = "The monitoring requirement disappeared"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(QualityGateFailed):
        verify_report(path, live.REPO_ROOT, "candidate-sha")


@pytest.mark.parametrize("damage", [None, "source", "metric", "hash", "prompt", "missing", "plan", "source_inventory"])
def test_release_report_requires_replayable_placement_proof(case_execution, tmp_path, damage):
    report = _report()
    result = next(r for r in report["case_results"] if r["case_id"] == CASE["id"])
    result.update(deepcopy(case_execution.result))
    evidence = result["evidence"]
    if damage == "source":
        for operation in evidence["change_plan"]["operations"]:
            operation["source_event_ids"] = evidence["expected_sources"]["5.5"]
    elif damage == "metric":
        next(s for s in evidence["final_ast"]["sections"] if s["id"] == "5.4")["blocks"][0]["rows"].pop()
    elif damage == "hash":
        evidence["model_calls"][0]["input_sha256"] = "0" * 64
    elif damage == "prompt":
        evidence["model_calls"][0]["system_prompt"] = "Invent a plan"
    elif damage == "missing":
        del result["evidence"]
    elif damage == "plan":
        evidence["change_plan"]["operations"][1]["block"]["text"] = "SLA 8 часов"
    elif damage == "source_inventory":
        evidence["expected_sources"]["5"] = ["invented"]
        evidence["change_plan"]["operations"][0]["source_event_ids"] = ["invented"]
    path = tmp_path / "full-report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    if damage:
        with pytest.raises(QualityGateFailed):
            verify_report(path, live.REPO_ROOT, "candidate-sha")
    else:
        assert verify_report(path, live.REPO_ROOT, "candidate-sha")["status"] == "PASS"


@pytest.mark.parametrize("shared", [False, True])
def test_release_report_accepts_confirmed_sources_across_sections(case_execution, tmp_path, shared):
    report = _report()
    result = next(r for r in report["case_results"] if r["case_id"] == CASE["id"])
    result.update(deepcopy(case_execution.result))
    evidence = result["evidence"]
    sources = evidence["expected_sources"]
    for operation in evidence["change_plan"]["operations"]:
        operation["source_event_ids"] = sources["5"] + sources["5.5"] if shared else sources["5.5" if operation["section_id"] == "5.4" else "5"]
    call = next(call for call in evidence["model_calls"] if call["task_type"] == "PLAN_CHANGES")
    call["response"] = deepcopy(evidence["change_plan"])
    response = json.dumps(call["response"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    call["response_sha256"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
    path = tmp_path / "cross-section-report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert verify_report(path, live.REPO_ROOT, "candidate-sha")["status"] == "PASS"


@pytest.mark.parametrize(
    "damage",
    [
        "verifier_fail",
        "verifier_empty",
        "verifier_malformed",
        "verifier_transport",
        "generator_empty",
        "generator_unrelated",
        "sources",
        "before_sections",
        "after_sections",
        "context_sections",
        "section_titles",
        "cross_section_repetitions",
        "generator_sources",
        "generator_base",
        "saved_hash",
        "saved_revision",
        "saved_state",
        "block_identity",
        "unrelated_content",
        "unrelated_title",
        "unrelated_operation",
    ],
)
def test_saved_pass_requires_the_actual_plan_and_successful_matching_review(tmp_path, damage):
    report = _report()
    evidence = next(result["evidence"] for result in report["case_results"] if result["case_id"] == CASE["id"])
    plan, review = evidence["model_calls"]
    if damage == "verifier_fail":
        review["response"] = {"status": "FAIL", "issues": [{"source_id": "proposal", "section_id": "5.4", "reason": "Missing metric"}]}
    elif damage == "verifier_empty":
        review["response"] = ""
    elif damage == "verifier_malformed":
        review["response"] = {"status": "PASS", "issues": [{"source_id": "unknown"}]}
    elif damage == "verifier_transport":
        review["error_type"] = "TimeoutError"
    elif damage == "generator_empty":
        plan["response"] = {}
    elif damage == "generator_unrelated":
        plan["response"]["operations"][0]["block"] = {"type": "paragraph", "text": "Unrelated result"}
    elif damage == "generator_sources":
        plan["input_payload"]["job_input"]["confirmed_requirement_fragments"]["proposal:0"]["text"] = "Another requirement"
    elif damage == "generator_base":
        plan["input_payload"]["job_input"]["current_revision"]["document_ast"]["sections"][0]["title"] = "Another base"
    elif damage == "saved_hash":
        evidence["change_plan"]["operations"][0]["expected_section_hash"] = "sha256:" + "0" * 64
    elif damage == "saved_revision":
        evidence["change_plan"]["base_revision_id"] = "another-revision"
    elif damage == "saved_state":
        evidence["change_plan"]["source_state_version"] += 1
    elif damage == "block_identity":
        evidence["change_plan"]["operations"][1]["block"]["id"] = "invented-id"
    elif damage == "unrelated_content":
        evidence["final_ast"]["sections"][0]["blocks"][0]["text"] = "Changed unrelated content"
    elif damage == "unrelated_title":
        evidence["final_ast"]["sections"][0]["title"] = "Changed unrelated title"
    elif damage == "unrelated_operation":
        evidence["change_plan"]["operations"].append({**deepcopy(evidence["change_plan"]["operations"][1]), "section_id": "1", "operation_id": "unrelated"})
    else:
        review["input_payload"]["job_input"][damage] = {}
    # Prove semantic/causal validation, not merely the pre-existing hash check.
    for call in (plan, review):
        for field, target in [("response", "response_sha256"), ("input_payload", "input_sha256")]:
            raw = call[field] if isinstance(call[field], str) else json.dumps(call[field], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            call[target] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path = tmp_path / "false-pass.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(QualityGateFailed):
        verify_report(path, live.REPO_ROOT, "candidate-sha")


@pytest.mark.parametrize("transport", ["json", "fenced_after_arrays", "envelope", "schema_echo", "aliases", "server_hash", "server_snapshot", "numeric_version", "generator_transport"])
def test_saved_proof_accepts_supported_transport_and_successful_retry(tmp_path, transport):
    report = _report()
    evidence = next(result["evidence"] for result in report["case_results"] if result["case_id"] == CASE["id"])
    call = evidence["model_calls"][0]
    if transport == "json":
        call["response"] = json.dumps(call["response"], ensure_ascii=False)
    elif transport == "fenced_after_arrays":
        call["response"] = 'Sources: ["proposal", "answer"]\n```json\n' + json.dumps(call["response"], ensure_ascii=False) + "\n```"
    elif transport == "envelope":
        call["response"] = {"change_plan": call["response"]}
    elif transport == "schema_echo":
        call["response"].pop("schema_version")
        call["response"]["change_plan"] = json.loads((live.ASSET_ROOT / "contracts/change_plan.v1.schema.json").read_text(encoding="utf-8"))
    elif transport == "aliases":
        for operation, alias in zip(call["response"]["operations"], ["question", "proposal-id"]):
            operation["source_event_ids"] = [alias]
    elif transport == "server_hash":
        for operation in call["response"]["operations"]:
            operation["expected_section_hash"] = "model-supplied-hash"
    elif transport == "server_snapshot":
        call["response"].pop("base_revision_id")
        call["response"].pop("source_state_version")
        for operation in call["response"]["operations"]:
            operation.pop("expected_section_hash")
            if "block" in operation:
                operation["block"].pop("id")
    elif transport == "numeric_version":
        call["response"]["schema_version"] = 1
    else:
        failed_call = deepcopy(call)
        failed_call.pop("response")
        failed_call.pop("response_sha256")
        failed_call.update(error_type="TimeoutError", retryable=True)
        evidence["model_calls"].insert(0, failed_call)
        evidence["attempts"] = 2
    raw = call["response"] if isinstance(call["response"], str) else json.dumps(call["response"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    call["response_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path = tmp_path / "normalized-proof.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert verify_report(path, live.REPO_ROOT, "candidate-sha")["status"] == "PASS"


@pytest.mark.parametrize("case_execution", [("format",)], indirect=True)
def test_saved_proof_accepts_worker_verifier_recovery_without_regenerating(case_execution, tmp_path):
    report = _report()
    result = next(result for result in report["case_results"] if result["case_id"] == CASE["id"])
    result.update(deepcopy(case_execution.result))
    calls = result["evidence"]["model_calls"]
    assert sum(call["task_type"] == "PLAN_CHANGES" for call in calls) == 1
    assert len(calls) >= 3
    path = tmp_path / "recovered.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert verify_report(path, live.REPO_ROOT, "candidate-sha")["status"] == "PASS"


@pytest.mark.parametrize("failures", [("transport",), ("format", "transport")])
def test_unavailable_verification_does_not_qualify_model(monkeypatch, failures):
    database = SqliteDatabase(":memory:")
    with database.bind_ctx((*DOCUMENT_TABLES, User), bind_refs=False, bind_backrefs=False), database.connection_context():
        database.create_tables((*DOCUMENT_TABLES, User))
        adapter = FixedTransport(failures)
        monkeypatch.setattr(live, "RAGFlowLLMAdapter", lambda **_: adapter)
        execution = live._run_model_case(CASE, "metric-placement-quality", [])
        assert execution.result["status"] == "FAIL"
        assert "missing_real_model_exchanges" in execution.result["failures"]
        assert sum(call["task_type"] == "PLAN_CHANGES" for call in execution.result["evidence"]["model_calls"]) == 1


@pytest.mark.parametrize(
    "damage", ["semantic_fail", "hidden_semantic_fail", "regenerate_after_fail", "nonretryable", "second_format_repair", "changed_input", "invented_repair", "changed_repair", "call_budget"]
)
def test_saved_proof_rejects_unsupported_recovery(tmp_path, damage):
    report = _report()
    evidence = next(result["evidence"] for result in report["case_results"] if result["case_id"] == CASE["id"])
    plan, review = evidence["model_calls"]
    failed = deepcopy(review)
    failed["response"] = {"status": "FAIL", "issues": [{"source_id": "proposal", "section_id": "5.4", "reason": "Missing metric"}]}
    evidence["model_calls"].insert(1, failed)
    if damage == "regenerate_after_fail":
        evidence["model_calls"].insert(2, deepcopy(plan))
    elif damage == "hidden_semantic_fail":
        failed.update(error_type="TimeoutError", retryable=True)
    elif damage == "nonretryable":
        failed.update(error_type="PermissionError", retryable=False)
    elif damage in {"second_format_repair", "changed_input", "invented_repair", "changed_repair"}:
        failed["response"] = {"status": "FAIL", "issues": []}
        review["input_payload"]["format_repair"] = {
            "previous_response": failed["response"],
            "error": {"code": "INVALID_REQUIREMENT_COVERAGE", "message": "Semantic verification must return PASS without issues or FAIL with up to four source-scoped issues", "details": {}},
        }
        if damage == "second_format_repair":
            evidence["model_calls"].insert(2, deepcopy(failed))
        elif damage == "changed_input":
            review["input_payload"]["job_input"]["sources"]["proposal"]["text"] = "Different author decision"
        elif damage == "invented_repair":
            review["input_payload"]["format_repair"]["previous_response"] = {}
        else:
            review["input_payload"]["format_repair"]["error"]["code"] = "REQUIREMENT_COVERAGE_FAILED"
    elif damage == "call_budget":
        evidence["model_calls"] = [deepcopy(plan)] * 6 + [review]
    for call in evidence["model_calls"]:
        for field in ("response", "input_payload"):
            raw = call[field] if isinstance(call[field], str) else json.dumps(call[field], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            call["response_sha256" if field == "response" else "input_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    path = tmp_path / "invalid-recovery.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(QualityGateFailed):
        verify_report(path, live.REPO_ROOT, "candidate-sha")
