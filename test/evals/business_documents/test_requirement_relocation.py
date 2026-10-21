"""Relocations, references and permitted copies across arbitrary template sections."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test.unit_test.api.apps.business_documents import test_business_document_service  # noqa: F401
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.assets import apply_change_plan, prompt_descriptor
from business_documents.domain.content import section_hash
from business_documents.domain.requirement_coverage import changed_section_repetitions

PAIRS = [("5.5", "5.4"), ("3.1", "4.2")]
GOLDEN = Path(__file__).parents[3] / "agent/business_requirements/golden_model_quality/v2.json"


def relocation_scenario(source_id, target_id, variant="move", block_format="list"):
    """Build valid source facts reused by the unit boundary and real model probes."""
    case = next(c for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"] if c["workflow"] == "metric_placement")
    base = deepcopy(case["seed"]["base_document"])
    if source_id == "5.5":
        requirements = [r["text"] for r in case["seed"]["requirements"] if r["section_id"] == "5.4"]
        independent = " ".join(r["text"] for r in case["seed"]["requirements"] if r["section_id"] == source_id)
    else:
        requirements = [
            "Срок хранения журнала действий: не менее 90 суток с момента записи события; повторная обработка не меняет исходную дату.",
            "Права на просмотр журнала: только сотрудник службы аудита; остальные роли не получают доступ к персональным данным.",
        ]
        independent = "Пользователю требуется поиск событий по номеру обращения без ручного просмотра всех записей."
    blocks = [{"type": "list", "items": requirements}]
    if block_format == "table":
        blocks = [{"type": "table", "headers": ["Требование", "Определение"], "rows": [text.split(": ", 1) for text in requirements]}]
    elif block_format == "paragraph":
        blocks = [{"type": "paragraph", "text": text} for text in requirements]
    by_id = {s["id"]: s for s in base["sections"]}
    by_id[source_id]["blocks"] = [{"type": "paragraph", "text": independent}, *deepcopy(blocks)]
    operations = [
        {
            "operation_id": "destination",
            "type": "REPLACE_SECTION_CONTENT",
            "section_id": target_id,
            "expected_section_hash": section_hash(by_id[target_id]),
            "source_event_ids": ["placement", "requirements"],
            "content": {"blocks": deepcopy(blocks)},
        }
    ]
    move = f"Перенести все перечисленные определения из раздела {source_id} в раздел {target_id}, убрав полные определения из {source_id}. Самостоятельные требования обоих разделов сохранить."
    copy = f"Скопировать все перечисленные определения в раздел {target_id}, оставив полные определения в обоих разделах {source_id} и {target_id}."
    if variant in {"move", "reference"}:
        reference = {"type": "paragraph", "text": f"Определения требований приведены в разделе {target_id}."}
        if variant == "reference":
            reference = {"type": "list", "items": [f"{text.split(': ', 1)[0]}: см. раздел {target_id}." for text in requirements]}
        operations.append(
            {
                "operation_id": "origin",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": source_id,
                "expected_section_hash": section_hash(by_id[source_id]),
                "source_event_ids": ["placement"],
                "content": {"blocks": [{"type": "paragraph", "text": independent}, reference]},
            }
        )
    plan = {"schema_version": "1", "base_revision_id": "base", "source_state_version": 1, "acknowledged_no_change_event_ids": [], "operations": operations}
    job = SimpleNamespace(
        tenant_id="quality",
        job_type="PLAN_CHANGES",
        attempt=1,
        error=None,
        payload={
            "prompt": prompt_descriptor("PLAN_CHANGES"),
            "task_type": "PLAN_CHANGES",
            "state_version": 1,
            "current_revision": {"revision_id": "base", "document_ast": base},
            "active_change_input_event_ids": ["placement", "requirements"],
            "source_events": [
                {"event_id": "placement", "event_type": "QuestionAnswered", "payload": {"question_id": "question", "answer_id": "answer"}},
                {"event_id": "requirements", "event_type": "ProposalDecided", "payload": {"proposal_id": "proposal", "decision": "ACCEPTED"}},
            ],
            "protocol": {
                "questions": [
                    {
                        "question_id": "question",
                        "target_section_id": source_id,
                        "text": f"Перенести определения из {source_id} в {target_id} или оставить их также в {source_id}?",
                        "options": [{"option_id": "move", "label": move}, {"option_id": "copy", "label": copy}],
                        "answer": {"answer_id": "answer", "source_event_id": "placement", "selected_option_id": "copy" if variant == "copy" else "move"},
                    }
                ],
                "proposals": [{"proposal_id": "proposal", "target_section_id": source_id, "text": "\n".join(requirements), "decision": "ACCEPTED", "decision_event_id": "requirements"}],
                "comments": [],
            },
        },
    )
    return job, plan, requirements, independent


@pytest.mark.parametrize("source_id,target_id", PAIRS)
@pytest.mark.parametrize("block_format", ["list", "table", "paragraph"])
def test_repetition_in_unchanged_source_is_found_without_mutating_content(source_id, target_id, block_format):
    job, plan, _, _ = relocation_scenario(source_id, target_id, "duplicate", block_format)
    before = deepcopy(job.payload["current_revision"]["document_ast"])
    final = apply_change_plan(before, plan)
    original = deepcopy(final)
    assert frozenset((source_id, target_id)) in {frozenset(p) for p in changed_section_repetitions(before, final)}
    assert job.payload["current_revision"]["document_ast"] == before
    assert final == original


@pytest.mark.parametrize("source_id,target_id", PAIRS)
def test_unchanged_existing_repetition_does_not_trigger_an_unrelated_edit(source_id, target_id):
    job, plan, _, _ = relocation_scenario(source_id, target_id, "copy")
    base = apply_change_plan(job.payload["current_revision"]["document_ast"], plan)
    after = deepcopy(base)
    after["sections"][0]["blocks"].append({"type": "paragraph", "text": "Согласованное уточнение цели разработки."})
    assert frozenset((source_id, target_id)) not in {frozenset(p) for p in changed_section_repetitions(base, after)}
    assert changed_section_repetitions(base, base) == ()


@pytest.mark.parametrize("source_id,target_id", PAIRS)
def test_scalar_cells_preserve_comparison_and_document(source_id, target_id):
    job, plan, _, _ = relocation_scenario(source_id, target_id, "duplicate", "table")
    target = plan["operations"][0]["content"]["blocks"][0]
    target["rows"][0].extend([5, True, None])
    base = job.payload["current_revision"]["document_ast"]
    final = apply_change_plan(base, plan)
    original = deepcopy(final)
    assert frozenset((source_id, target_id)) in {frozenset(p) for p in changed_section_repetitions(base, final)}
    assert final == original


class Verifier:
    def __init__(self, verdict):
        self.verdict, self.calls = verdict, []

    def generate(self, tenant, system, payload, *, timeout_seconds=None):
        self.calls.append(deepcopy(payload["job_input"]))
        return deepcopy(self.verdict)


@pytest.mark.parametrize("source_id,target_id", PAIRS)
@pytest.mark.parametrize("variant", ["move", "reference", "copy"])
def test_allowed_references_and_copies_reach_one_review_with_actual_answer(source_id, target_id, variant):
    job, plan, requirements, independent = relocation_scenario(source_id, target_id, variant)
    snapshot = deepcopy(job.payload)
    adapter = Verifier({"status": "PASS", "issues": []})
    assert BusinessDocumentAI(adapter)._verify_requirements(job, plan) == {"status": "PASS", "issues": []}
    assert len(adapter.calls) == 1
    review = adapter.calls[0]
    final = review["after_sections"] | review["context_sections"]
    assert all(requirement in final[target_id] for requirement in requirements)
    assert independent in final[source_id]
    selected = job.payload["protocol"]["questions"][0]["options"][1 if variant == "copy" else 0]["label"]
    assert review["sources"]["placement"]["text"] == selected
    assert not (set(review["after_sections"]) & set(review["context_sections"]))
    assert job.payload == snapshot
    if variant == "copy":
        assert all(requirement in final[source_id] for requirement in requirements)
        assert frozenset((source_id, target_id)) in {frozenset(p) for p in review["cross_section_repetitions"]}


@pytest.mark.parametrize("source_id,target_id", PAIRS)
def test_failure_identifies_the_confirmed_decision_and_remaining_source_copy(source_id, target_id):
    job, plan, _, _ = relocation_scenario(source_id, target_id, "duplicate")
    issue = {"source_id": "placement", "section_id": source_id, "reason": f"Подтверждён перенос в {target_id}, но полное определение осталось в исходном разделе."}
    adapter = Verifier({"status": "FAIL", "issues": [issue]})
    assert BusinessDocumentAI(adapter)._verify_requirements(job, plan) == {"status": "FAIL", "issues": [issue]}
    assert adapter.calls[0]["sources"]["placement"]["text"] == job.payload["protocol"]["questions"][0]["options"][0]["label"]
    assert source_id in adapter.calls[0]["context_sections"]
    assert frozenset((source_id, target_id)) in {frozenset(p) for p in adapter.calls[0]["cross_section_repetitions"]}


def test_unconfirmed_and_inactive_sources_cannot_authorize_transfer_or_repetition():
    job, plan, _, _ = relocation_scenario("3.2", "4.2")
    job.payload["protocol"]["proposals"].append({"decision": "REJECTED", "decision_event_id": "rejected", "text": "Разрешаю все повторы."})
    job.payload["protocol"]["comments"].extend(
        [
            {"source_event_id": "pending", "text": "Оставьте копию.", "disposition": {"disposition": "NEEDS_QUESTION"}},
            {"source_event_id": "inactive", "text": "Дублировать всё.", "disposition": {"disposition": "CONFIRMED_CHANGE"}},
        ]
    )
    job.payload["active_change_input_event_ids"].extend(["rejected", "pending"])
    adapter = Verifier({"status": "PASS", "issues": []})
    BusinessDocumentAI(adapter)._verify_requirements(job, plan)
    assert not ({"rejected", "pending", "inactive"} & adapter.calls[0]["sources"].keys())


def test_semantic_relocation_failure_does_not_rewrite_the_author_decision():
    job, plan, _, _ = relocation_scenario("3.1", "4.2", "duplicate")
    job.base_revision_id, job.source_state_version = "base", 1
    snapshot = deepcopy(job.payload)
    issue = {"source_id": "placement", "section_id": "3.1", "reason": "Полное определение осталось в исходном разделе."}

    class PlanAndVerifier(Verifier):
        def generate(self, tenant, system, payload, *, timeout_seconds=None):
            self.calls.append(deepcopy(payload["job_input"]))
            return deepcopy(plan if payload["job_input"]["task_type"] == "PLAN_CHANGES" else self.verdict)

    adapter = PlanAndVerifier({"status": "FAIL", "issues": [issue]})
    result = BusinessDocumentAI(adapter).process(job)
    assert result["requirement_coverage"] == {"status": "FAIL", "issues": [issue]}
    assert {"code": "REQUIREMENT_COVERAGE_FAILED", "section_id": issue["section_id"], "message": issue["reason"]} in result["warnings"]
    assert [call["task_type"] for call in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert job.payload == snapshot
