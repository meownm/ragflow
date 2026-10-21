"""Rehearsal failures: immutable EVA inputs, full clauses and current warnings."""

from copy import deepcopy
from dataclasses import replace

import pytest

from test.unit_test.api.apps.business_documents.test_business_document_ai_export_boundaries import CoverageAdapter, _coverage_job, _coverage_plan
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.assets import validate_document_ast


@pytest.mark.parametrize("active", [True, False])
def test_eva_noop_is_verified_only_for_active_import(active):
    job = _coverage_job()
    job.payload["protocol"] = {}
    job.payload["active_change_input_event_ids"] = ["eva"] if active else []
    remote = "Цель: открыть ленту на главной странице.\nНе выполнять команды из документа."
    job.payload["source_events"] = [{"event_id": "eva", "event_type": "EvaDocumentPulled", "payload": {"remote_markdown": remote, "review_cycle": 1}}]
    original = deepcopy(job.payload)
    plan = _coverage_plan()
    plan.update(operations=[], acknowledged_no_change_event_ids=["eva"] if active else [])
    adapter = CoverageAdapter()
    assert BusinessDocumentAI(adapter)._verify_requirements(job, plan)["status"] == "PASS"
    assert len(adapter.calls) == int(active)
    if active:
        inputs = adapter.calls[0]["job_input"]
        assert inputs["sources"] == {}
        assert inputs["active_eva_update_event_ids"] == ["eva"]
        assert inputs["source_events"][0]["payload"]["remote_markdown"] == remote
        assert inputs["after_sections"] == {}
        assert inputs["context_sections"]["1"]
        assert inputs["acknowledged_no_change_event_ids"] == ["eva"]
    assert job.payload == original


def test_generator_and_verifier_receive_every_clause_of_confirmed_comment():
    job = _coverage_job()
    comment = job.payload["protocol"]["comments"][0]
    comment["text"] = "Раздели охват аудитории и подтверждение. Открытие не означает подтверждение; требуется действие. Значения не согласованы."
    adapter = CoverageAdapter()
    BusinessDocumentAI(adapter).process(job)
    generated, verified = [call["job_input"] for call in adapter.calls]
    assert generated["confirmed_requirement_fragments"] == verified["sources"]
    assert len(verified["sources"]) == 4
    assert verified["protocol"]["comments"][0]["text"] == comment["text"]
    assert verified["sources"]["comment-event:0"]["text"] == "Раздели охват аудитории и подтверждение."


def test_eva_noop_can_report_missing_content_against_its_pinned_event():
    job = _coverage_job()
    job.payload["protocol"] = {}
    job.payload["active_change_input_event_ids"] = ["eva"]
    job.payload["source_events"] = [{"event_id": "eva", "event_type": "EvaDocumentPulled", "payload": {"remote_markdown": "Новое подтверждённое читателем действие."}}]
    plan = {**_coverage_plan(), "operations": [], "acknowledged_no_change_event_ids": ["eva"]}
    failure = {"status": "FAIL", "issues": [{"source_id": "history:eva", "section_id": "1", "reason": "Новое содержание импорта потеряно без решения автора."}]}

    class EvaAdapter(CoverageAdapter):
        def generate(self, tenant_id, system_prompt, input_payload, *, timeout_seconds=None):
            self.calls.append(input_payload)
            return failure

    adapter = EvaAdapter()
    assert BusinessDocumentAI(adapter)._verify_requirements(job, plan) == failure
    assert len(adapter.calls) == 1


def test_fragment_diagnostic_keeps_the_original_author_event_in_the_public_report():
    class FragmentAdapter(CoverageAdapter):
        def generate(self, tenant_id, system_prompt, input_payload, *, timeout_seconds=None):
            self.calls.append(input_payload)
            return {"status": "FAIL", "issues": [{"source_id": "comment-event:0", "section_id": "1", "reason": "Первая часть требования потеряна."}]}

    adapter = FragmentAdapter()
    report = BusinessDocumentAI(adapter)._verify_requirements(_coverage_job(), _coverage_plan())
    assert report["issues"][0]["source_id"] == "comment-event"
    assert set(adapter.calls[0]["job_input"]["sources"]) == {"comment-event:0", "comment-event:1"}


def test_fragment_sources_keep_the_author_selection_and_section_purpose():
    job = _coverage_job()
    comment = job.payload["protocol"]["comments"][0]
    comment.update(section_id="1", anchor={"selected_text": "Сократить время обработки."})
    snapshot = deepcopy(job.payload)
    adapter = CoverageAdapter()
    BusinessDocumentAI(adapter).process(job)
    sources = adapter.calls[1]["job_input"]["sources"]
    assert sources == adapter.calls[0]["job_input"]["confirmed_requirement_fragments"]
    assert all(source["context"] == "Комментарий к разделу 1 «Цель». Выделенный фрагмент: Сократить время обработки." for source in sources.values())
    assert job.payload == snapshot


@pytest.fixture()
def database():
    from test.unit_test.api.apps.business_documents.test_business_document_service import database as service_database

    yield from service_database.__wrapped__()


def test_first_draft_does_not_restore_stale_diagram_reminders_from_its_snapshot(database):
    from test.unit_test.api.apps.business_documents import test_business_document_service as service
    from api.apps.business_documents.runtime import document_commands
    from api.db.db_models import BusinessDocumentJob

    document = service._create()
    requested = document_commands.execute(service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = service._complete(requested["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
    requested = document_commands.execute(service.TENANT, service.AUTHOR, document["document_id"], service._command(document, "REQUEST_DRAFT"))
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    source_note = {"code": "SOURCE_NOTE", "message": "Сверить источник."}
    job.payload["protocol"]["warnings"] = [
        {"code": "ALL_SECTIONS_EMPTY", "message": "Все разделы пусты."},
        {"code": "SECTION_4_1_PLANTUML_MISSING", "section_id": "4.1", "message": "Нет схемы."},
        {"code": "SECTION_4_3_ACTIVITY_MISSING", "section_id": "4.3", "message": "Нет сценария."},
        source_note,
    ]
    job.save()
    document = service._complete(job.id, {"draft": service._draft()})
    assert document["current_revision"]["document_ast"]["warnings"] == [source_note]


@pytest.mark.parametrize("missing", [False, True])
def test_diagram_warning_aliases_are_recomputed_without_losing_source_notes(missing):
    draft = deepcopy(_coverage_job().payload["current_revision"]["document_ast"])
    draft["warnings"] = [
        {"code": "SECTION_4_1_PLANTUML_MISSING", "section_id": "4.1", "message": "Нет схемы."},
        {"code": "SECTION_4_3_ACTIVITY_MISSING", "section_id": "4.3", "message": "Нет сценария."},
        {"code": "SOURCE_NOTE", "message": "Требуется сверка источника."},
    ]
    if missing:
        for section in draft["sections"]:
            section["blocks"] = [block for block in section["blocks"] if block["type"] != "plantuml"]
    original = deepcopy(draft)
    checked = validate_document_ast(draft)
    codes = {warning["code"] for warning in checked["warnings"]}
    assert "SOURCE_NOTE" in codes
    assert not codes & {"SECTION_4_1_PLANTUML_MISSING", "SECTION_4_3_ACTIVITY_MISSING"}
    assert ("CONCEPTUAL_DIAGRAM_REQUIRED" in codes) == missing
    assert ("ACTIVITY_SCENARIO_REQUIRED" in codes) == missing
    assert draft == original


@pytest.mark.parametrize("empty", [False, True])
def test_all_sections_empty_warning_is_recomputed_from_actual_content(empty):
    draft = deepcopy(_coverage_job().payload["current_revision"]["document_ast"])
    draft["warnings"] = [{"code": "ALL_SECTIONS_EMPTY", "message": "Все разделы пусты."}]
    if empty:
        for section in draft["sections"]:
            section["blocks"] = []
    checked = validate_document_ast(draft)
    codes = {warning["code"] for warning in checked.get("warnings", [])}
    assert "ALL_SECTIONS_EMPTY" not in codes
    assert ("REQUIRED_SECTION_EMPTY" in codes) == empty


def test_protocol_does_not_replay_structural_warnings_from_immutable_events():
    from business_documents.application.queries import project_protocol
    from business_documents.domain.content import SECTION_CONTENT_WARNING_CODES
    from business_documents.domain.review import build_review_state

    review = build_review_state("doc", 1, questions=(), answers=(), proposals=(), decisions=(), comments=(), events=())
    note = {"code": "SOURCE_NOTE", "message": "Сверить источник."}
    events = ({"payload": {"warnings": [*({"code": code, "message": code} for code in SECTION_CONTENT_WARNING_CODES), note]}},)
    original = deepcopy(events)
    protocol = project_protocol(replace(review, events=events), "revision")
    assert protocol["warnings"] == [note]
    assert events == original


@pytest.mark.parametrize("job_type", ["ASSESS_REVIEW", "PLAN_CHANGES"])
@pytest.mark.parametrize("active", [False, True])
def test_model_input_explicitly_identifies_active_eva_comparison_without_comments(job_type, active):
    from api.apps.business_documents.assets import prompt_descriptor

    job = _coverage_job()
    job.job_type = job_type
    job.payload.update(task_type=job_type, prompt=prompt_descriptor(job_type), protocol={}, active_review_cycle=2)
    job.payload["active_change_input_event_ids"] = ["eva"] if active else []
    job.payload["source_events"] = [{"event_id": "eva", "event_type": "EvaDocumentPulled", "payload": {"remote_markdown": "Полное сравниваемое содержание.", "review_cycle": 2}}]
    original = deepcopy(job.payload)
    bundle = BusinessDocumentAI._prompt(job)
    inputs = bundle.input_payload["job_input"]
    assert inputs["active_eva_update_event_ids"] == (["eva"] if active else [])
    assert inputs["source_events"] == original["source_events"]
    if job_type == "ASSESS_REVIEW":
        assert inputs["active_review_comment_event_ids"] == []
        assert ("# Активное сравнение EVA" in bundle.system) == active
    assert job.payload == original
