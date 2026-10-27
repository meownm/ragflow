"""Coordinate an accepted monitoring proposal with the author's reporting choice."""

from copy import deepcopy

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_service import (
    AUTHOR,
    TENANT,
    _command,
    _complete_review_assessment,
    _create,
    _draft,
    _question_batch,
    _request_and_complete_draft,
    database as service_database,
)
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.ai_prompt import build_prompt
from api.apps.business_documents.runtime import document_commands, document_queries
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocumentJob, BusinessDocumentRevision
from business_documents.domain.content import render_section_text, section_hash


METRICS = [
    "Время появления тикета: от создания инцидента SOAR до связанного тикета в ЕЛКа.",
    "Время обработки тикета: от появления алерта в очереди ЕЛКа до назначения другой команде (не ОФМ).",
    "Время работы с инцидентом: от появления алерта в очереди ЕЛКа до закрытия тикета.",
    "Время реакции ОФМ: от появления тикета в очереди ЕЛКа до первого действия ОФМ.",
    "Время реакции ОПК: от назначения ОПК или другого подразделения до первого действия по алерту.",
    "Время исполнения решения: от решения ОФМ до результата блокировки или разблокировки.",
    "Доля возвратов за данными: тикеты с запросом дополнительной информации / все тикеты.",
    "Полнота истории: тикеты с решением, комментариями и результатом / все завершённые тикеты.",
]
MONITORING = "При недоступности API SOAR–ЕЛКа уведомлять оператора. Контролировать SLA не более 4 часов по каждому инциденту."


@pytest.fixture()
def database():
    yield from service_database.__wrapped__()


def _request_placement():
    draft = _draft()
    draft.update(schema_version="2", section_scope="required")
    next(section for section in draft["sections"] if section["id"] == "5.4")["blocks"] = [{"type": "paragraph", "text": "Требования не определены"}]
    next(section for section in draft["sections"] if section["id"] == "5.5")["blocks"] = [
        {"type": "paragraph", "text": MONITORING},
        {"type": "list", "items": METRICS[:1]},
    ]
    batch = _question_batch("REVIEW", "metrics_reporting_placement")
    batch["questions"][0].update(
        target_section_id="5",
        text="Куда поместить определения метрик: 5.4 или 5.5?",
        options=[
            {"option_id": "reporting", "label": "Заполнить 5.4 «Требования к отчетности», перенести туда все метрики и правила отчётности."},
            {"option_id": "monitoring", "label": "Дополнить 5.5 «Мониторинг» новыми метриками."},
        ],
    )
    document = _request_and_complete_draft(
        _create(),
        draft=draft,
        review_questions=batch,
        proposals=[{"target_section_id": "5.5", "text": "Мониторинг включает:\n" + MONITORING + "\n" + "\n".join(METRICS)}],
    )
    for command, payload in [
        ("DECIDE_PROPOSAL", {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"], "decision": "ACCEPTED"}),
        ("ANSWER_QUESTION", {"question_id": document["protocol"]["questions"][0]["question_id"], "selected_option_id": "reporting", "custom_answer": None}),
        (
            "ADD_COMMENT",
            {
                "revision_id": document["current_revision"]["revision_id"],
                "section_id": None,
                "anchor": None,
                "text": "Все определения метрик перенести в 5.4; независимые требования доступности API и SLA сохранить в 5.5.\n" + "\n".join(METRICS),
            },
        ),
    ]:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, command, payload))
        document = document_queries.get_document(document["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "PREPARE_CHANGES",
            {"base_revision_id": document["current_revision"]["revision_id"]},
        ),
    )
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    sections = {section["id"]: section for section in document["current_revision"]["document_ast"]["sections"]}
    proposal = document["protocol"]["proposals"][0]["decision_event_id"]
    answer = document["protocol"]["questions"][0]["answer"]["source_event_id"]
    comment = document["protocol"]["comments"][0]["source_event_id"]
    plan = {
        "schema_version": "1",
        "base_revision_id": job.base_revision_id,
        "source_state_version": job.source_state_version,
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {
                "operation_id": "reporting",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "5.4",
                "expected_section_hash": section_hash(sections["5.4"]),
                "source_event_ids": [answer, comment],
                "content": {"blocks": [{"type": "list", "items": list(METRICS)}]},
            },
            {
                "operation_id": "monitoring",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "5.5",
                "expected_section_hash": section_hash(sections["5.5"]),
                "source_event_ids": [proposal, comment],
                "content": {
                    "blocks": [
                        {"type": "paragraph", "text": MONITORING},
                        {
                            "type": "paragraph",
                            "text": "Состав метрик и правила их расчёта определены в разделе 5.4.",
                        },
                    ]
                },
            },
        ],
    }
    return document, job, plan


class PlacementAdapter:
    def __init__(self, plan, verdict=None):
        self.plan = plan
        self.verdict = verdict or {"status": "PASS", "issues": []}
        self.calls = []

    def generate(self, tenant, system, payload, *, timeout_seconds=None):
        self.calls.append((system, deepcopy(payload)))
        return deepcopy(self.plan if payload["job_input"]["task_type"] == "PLAN_CHANGES" else self.verdict)


def test_prepare_and_confirm_metric_relocation_preserves_content_and_scopes(database):
    document, job, plan = _request_placement()
    adapter = PlacementAdapter(plan)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    assert BusinessDocumentJob.get_by_id(job.id).status == "COMPLETED", BusinessDocumentJob.get_by_id(job.id).error
    assert BusinessDocumentRevision.select().count() == 1
    preview = document_queries.get_change_preview(document["document_id"], job.id)
    assert {section["section_id"] for section in preview["sections"]} == {"5.4", "5.5"}
    verification = adapter.calls[1][1]["job_input"]
    assert set(verification["after_sections"]) == {"5.4", "5.5"}
    assert all(metric in verification["after_sections"]["5.4"] for metric in METRICS)
    assert MONITORING in verification["before_sections"]["5.5"]
    assert MONITORING in verification["after_sections"]["5.5"]
    document = document_queries.get_document(document["document_id"], AUTHOR)
    confirm = _command(document, "CONFIRM_PREPARED_CHANGES", {"job_id": job.id})
    result = document_commands.execute(TENANT, AUTHOR, document["document_id"], confirm)["document"]
    final = {section["id"]: section for section in result["current_revision"]["document_ast"]["sections"]}
    assert final["5"] == next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5")
    assert all(metric in render_section_text(final["5.4"]) for metric in METRICS)
    assert all(metric not in render_section_text(final["5.5"]) for metric in METRICS)
    assert MONITORING in render_section_text(final["5.5"])
    assert result["protocol"]["proposals"][0]["applied"] is True
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], confirm)["idempotent_replay"] is True
    assert BusinessDocumentRevision.select().count() == 2


def test_original_source_can_also_support_another_section(database):
    _, job, plan = _request_placement()
    plan["operations"][0]["source_event_ids"].append(plan["operations"][1]["source_event_ids"][0])
    adapter = PlacementAdapter(plan)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    assert BusinessDocumentJob.get_by_id(job.id).status == "COMPLETED"
    assert len(adapter.calls) == 2
    assert BusinessDocumentRevision.select().count() == 1


def test_overlapping_comment_does_not_replace_accepted_proposal_source(database):
    _, job, plan = _request_placement()
    proposal = job.payload["protocol"]["proposals"][0]["decision_event_id"]
    plan["operations"][1]["source_event_ids"].remove(proposal)
    adapter = PlacementAdapter(plan)

    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()

    failed = BusinessDocumentJob.get_by_id(job.id)
    assert failed.error["code"] == "ACCEPTED_PROPOSAL_OMITTED"
    assert document_queries.get_document(job.document_id, AUTHOR)["change_preview"] is None
    assert [payload["job_input"]["task_type"] for _, payload in adapter.calls] == ["PLAN_CHANGES", "PLAN_CHANGES"]
    repair = adapter.calls[1][1]["format_repair"]["error"]
    assert repair["code"] == "ACCEPTED_PROPOSAL_OMITTED"
    assert repair["details"]["event_ids"] == [proposal]
    assert failed.result["recovery"]["candidate"] is None
    assert BusinessDocumentRevision.select().count() == 1


def test_planner_receives_complete_confirmed_content_independent_of_its_original_section(database):
    _, job, _ = _request_placement()
    snapshot = deepcopy(job.payload)
    prompt = build_prompt(job.job_type, job.payload)
    fragments = prompt.input_payload["job_input"]["confirmed_requirement_fragments"]
    proposal = job.payload["protocol"]["proposals"][0]["decision_event_id"]
    answer = job.payload["protocol"]["questions"][0]["answer"]["source_event_id"]
    assert all(metric in "\n".join(fragment["text"] for fragment in fragments.values() if fragment["source_id"] == proposal) for metric in METRICS)
    assert "5.4" in fragments[f"{answer}:0"]["text"]
    assert "Куда поместить" in fragments[f"{answer}:0"]["context"]
    assert {fragment["source_id"] for fragment in fragments.values()} == set(prompt.input_payload["job_input"]["active_change_input_event_ids"])
    assert prompt.input_payload["job_input"]["protocol"] == snapshot["protocol"]
    assert job.payload == snapshot

    inactive = deepcopy(job.payload["protocol"]["proposals"][0])
    inactive.update(decision="REJECTED", decision_event_id="rejected-event", text="Удалить все метрики")
    job.payload["protocol"]["proposals"].append(inactive)
    inputs = build_prompt(job.job_type, job.payload).input_payload["job_input"]
    assert "rejected-event" not in {fragment["source_id"] for fragment in inputs["confirmed_requirement_fragments"].values()}
    assert inputs["protocol"]["proposals"][-1] == inactive


@pytest.mark.parametrize("damage", ["missing_metric", "missing_monitoring", "wrong_placement"])
def test_semantic_issues_accompany_preview_of_incomplete_or_misplaced_requirements(database, damage):
    _, job, plan = _request_placement()
    plan["operations"][0]["source_event_ids"].append(plan["operations"][1]["source_event_ids"][0])
    if damage == "missing_monitoring":
        plan["operations"][1]["content"]["blocks"].pop(0)
        source, section = "section:5.5", "5.5"
    else:
        metric = plan["operations"][0]["content"]["blocks"][0]["items"].pop()
        if damage == "wrong_placement":
            plan["operations"][1]["content"]["blocks"].append({"type": "paragraph", "text": metric})
        source, section = plan["operations"][1]["source_event_ids"][0], "5.4"
    adapter = PlacementAdapter(plan, {"status": "FAIL", "issues": [{"source_id": source, "section_id": section, "reason": "Подтверждённое требование потеряно или размещено вопреки выбору автора."}]})
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    completed = BusinessDocumentJob.get_by_id(job.id)
    assert completed.status == "COMPLETED" and not completed.error
    assert completed.result["output"]["requirement_coverage"]["status"] == "FAIL"
    assert document_queries.get_document(job.document_id, AUTHOR)["change_preview"]["job_id"] == job.id
    assert document_queries.get_change_preview(job.document_id, job.id)["warnings"][0]["code"] == "REQUIREMENT_COVERAGE_FAILED"
    assert [payload["job_input"]["task_type"] for _, payload in adapter.calls] == ["PLAN_CHANGES", "VERIFY_REQUIREMENT_COVERAGE"]
    assert BusinessDocumentRevision.select().count() == 1
