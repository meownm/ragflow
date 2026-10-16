"""Oracle and registry tests; real-model acceptance is the opt-in M06 case."""

from copy import deepcopy
import json
import re

import pytest
from peewee import SqliteDatabase

from test.evals.business_documents import test_live_model_quality as live
from test.evals.business_documents.metric_placement import placement_failures
from api.apps.business_documents.adapters.persistence import DOCUMENT_TABLES
from api.db.db_models import User
from business_documents.domain.content import section_hash
from tools.quality.verify_business_document_quality_report import QualityGateFailed, verify_report
from test.unit_test.tools.quality.test_business_document_quality_report import _report


CASE = next(case for case in json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))["cases"] if case["workflow"] == "metric_placement")


def _ideal_ast():
    ast = deepcopy(CASE["seed"]["base_document"])
    next(s for s in ast["sections"] if s["id"] == "5.5")["blocks"] = [
        {"type": "paragraph", "text": " ".join(r["text"] for r in CASE["seed"]["requirements"] if r["section_id"] == "5.5")},
        {"type": "paragraph", "text": "Состав метрик и правила расчёта определены в 5.4."},
    ]
    ast["sections"].insert(-1, {
        "id": "5.4", "title": "Требования к отчетности", "blocks": [{"type": "table", "headers": ["Метрика", "Определение"],
        "rows": [[r["label"], r["text"].split(": ", 1)[1]] for r in CASE["seed"]["requirements"] if r["section_id"] == "5.4"]}],
    })
    return ast


def test_fixture_has_complete_clause_inventory_and_valid_template():
    from api.apps.business_documents.assets import validate_document_ast

    base = validate_document_ast(CASE["seed"]["base_document"])
    assert "5.4" not in {s["id"] for s in base["sections"]}
    assert CASE["seed"]["question_section_id"] == "5"
    assert CASE["seed"]["proposal_section_id"] == "5.5"
    assert len(CASE["seed"]["requirements"]) == 10
    assert len({r["id"] for r in CASE["seed"]["requirements"]}) == 10
    assert not placement_failures(CASE, validate_document_ast(_ideal_ast()))


def test_active_suite_preserves_original_cases_and_registers_admin_diagnostic():
    from admin.server.document_quality_runs import _known_case_ids

    baseline = json.loads((live.ASSET_ROOT / "golden_model_quality/v1.json").read_text(encoding="utf-8"))
    active = json.loads(live.MODEL_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert active["cases"][:-1] == baseline["cases"]
    assert CASE["id"] in _known_case_ids()


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

    def generate(self, tenant, system, payload):
        job = payload["job_input"]
        self.audit = {"provider": "Fixture", "model": "unit", "model_type": "chat", "duration_ms": 1,
                      "parameters": {"temperature": 0, "top_p": 0.1, "max_tokens": 65536 if job["task_type"] == "PLAN_CHANGES" else 4096},
                      "token_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        if job["task_type"] != "PLAN_CHANGES":
            if "fragments" in job:
                return {"checks": [{"fragment_id": fragment_id, "required_section_ids": [], "status": "PRESENT",
                                    "matches": list(job["final_blocks"]), "superseded_by": None}
                                   for fragment_id in job["fragments"]]}
            return {"status": "PASS", "issues": []}
        sections = {s["id"]: s for s in job["current_revision"]["document_ast"]["sections"]}
        ideal = {s["id"]: s for s in _ideal_ast()["sections"]}
        answer = job["protocol"]["questions"][0]["answer"]["source_event_id"]
        proposal = job["protocol"]["proposals"][0]["decision_event_id"]
        return {"schema_version": "1", "base_revision_id": job["current_revision"]["revision_id"], "source_state_version": job["state_version"],
                "acknowledged_no_change_event_ids": [], "operations": [
                    {"operation_id": section, "type": "REPLACE_SECTION_CONTENT", "section_id": section,
                     "expected_section_hash": section_hash(sections[section]), "source_event_ids": [source],
                     "content": {"blocks": [*deepcopy(sections["5"]["blocks"]), deepcopy(ideal["5.4"])] if section == "5" else deepcopy(ideal["5.5"]["blocks"])}}
                    for section, source in [("5", answer), ("5.5", proposal)]
                ]}

    def consume_execution_audit(self):
        audit, self.audit = self.audit, None
        return audit


@pytest.fixture()
def case_execution(monkeypatch):
    database = SqliteDatabase(":memory:")
    with database.bind_ctx((*DOCUMENT_TABLES, User), bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables((*DOCUMENT_TABLES, User))
        monkeypatch.setattr(live, "RAGFlowLLMAdapter", lambda **_: FixedTransport())
        execution = live._run_model_case(CASE, "metric-placement-quality", [])
        assert execution.result["status"] == "PASS", execution.result["failures"]
        yield execution
        database.close()


def test_registered_workflow_records_actual_prompt_and_model_exchanges(case_execution, tmp_path):
    evidence = case_execution.result["evidence"]
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


@pytest.mark.parametrize("damage", [None, "source", "metric", "hash", "prompt", "missing", "plan", "source_inventory"])
def test_release_report_requires_replayable_placement_proof(case_execution, tmp_path, damage):
    report = _report()
    result = next(r for r in report["case_results"] if r["case_id"] == CASE["id"])
    result.update(deepcopy(case_execution.result))
    evidence = result["evidence"]
    if damage == "source":
        evidence["change_plan"]["operations"][0]["source_event_ids"] = evidence["expected_sources"]["5.5"]
    elif damage == "metric":
        next(s for s in evidence["final_ast"]["sections"] if s["id"] == "5.4")["blocks"][0]["rows"].pop()
    elif damage == "hash":
        evidence["model_calls"][0]["input_sha256"] = "0" * 64
    elif damage == "prompt":
        evidence["model_calls"][0]["system_prompt"] = "Invent a plan"
    elif damage == "missing":
        del result["evidence"]
    elif damage == "plan":
        evidence["change_plan"]["operations"][1]["content"]["blocks"] = [{"type": "paragraph", "text": "SLA 8 часов"}]
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
