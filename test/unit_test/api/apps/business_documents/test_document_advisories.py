"""Document quality issues survive worker completion, preview and confirmation."""

from copy import deepcopy
import io
import json
from types import SimpleNamespace

import pytest
from docx import Document

from test.unit_test.api.apps.business_documents.test_business_document_ai_export_boundaries import (
    AUTHOR,
    TENANT,
    CapturingAdapter,
    _claim_complete,
    _command,
    _create,
    _draft,
    _draft_bundle,
    database as document_database,
)
from test.unit_test.api.apps.business_documents.test_metric_placement import PlacementAdapter, _request_placement
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.assets import validate_contract, apply_change_plan, import_document_markdown
from api.apps.business_documents.exports import BusinessDocumentExportService
from business_documents.domain.block_diff import with_block_ids
from business_documents.domain.content import section_hash
from api.apps.business_documents.runtime import document_commands, document_queries
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocumentJob, BusinessDocumentRevision


@pytest.fixture()
def database():
    yield from document_database.__wrapped__()


def _request_draft():
    document = _create()
    assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _claim_complete(assessment["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
    return document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))


@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "diagram",
        "unknown_block",
        "block_metadata",
        "long_text",
        "protocol",
        "plain_text",
        "markdown_diagram",
        "bare_ast",
        "section_metadata",
        "section_text",
        "root_metadata",
        "duplicate",
        "missing_metadata",
    ],
)
def test_worker_saves_document_and_advice_after_format_deviation(database, kind):
    requested = _request_draft()
    draft = _draft()
    section = next(item for item in draft["sections"] if item["id"] == "1")
    output = _draft_bundle(draft)
    text = "Клиент передаёт заявку, оператор подтверждает получение."
    if kind == "empty":
        section["blocks"] = []
    elif kind == "diagram":
        next(item for item in draft["sections"] if item["id"] == "4.1")["blocks"] = [{"type": "paragraph", "text": text}]
    elif kind == "unknown_block":
        section["blocks"] = [{"type": "mermaid", "source": "graph TD; A-->B"}]
    elif kind == "block_metadata":
        section["blocks"] = [{"type": "paragraph", "text": text, "style": "normal", "note": "Проверять SLA"}]
    elif kind == "long_text":
        section["blocks"] = [{"type": "paragraph", "text": text * 800}]
    elif kind == "protocol":
        output["review_questions"] = {"text": "Кто отвечает за получение заявки?"}
        output["proposals"] = [{"text": "Уточнить срок получения"}]
    elif kind == "plain_text":
        output = text
    elif kind == "markdown_diagram":
        text += '\n```plantuml\n@startuml\nrectangle "Получение заявки" {\n[Клиент] --> [Оператор]\n}\n@enduml\n```'
        output = text
        assert isinstance(BusinessDocumentAI._parse(output), dict)
    elif kind == "section_metadata":
        section["note"] = text
    elif kind == "section_text":
        section["blocks"] = text
    elif kind == "root_metadata":
        draft["note"] = text
    elif kind == "duplicate":
        next(item for item in draft["sections"] if item["id"] == "3")["blocks"] = [{"type": "paragraph", "text": text}]
        next(item for item in draft["sections"] if item["id"] == "3.1")["blocks"] = [{"type": "paragraph", "text": text}]
    elif kind == "missing_metadata":
        for field in ("schema_version", "document_type", "template_version"):
            draft.pop(field)
    else:
        output = draft
    original = deepcopy(output)
    adapter = CapturingAdapter(output)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(adapter)).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    assert not job.error and len(adapter.calls) == 1
    view = document_queries.get_document(job.document_id, AUTHOR)
    ast = view["current_revision"]["document_ast"]
    validate_contract("document_draft", ast)
    assert view["lifecycle_state"] == "REVIEW" and view["operation_state"] == "IDLE"
    assert output == original
    if kind not in {"bare_ast", "missing_metadata"}:
        assert ast["warnings"] == job.result["output"]["draft"]["warnings"]
    body = view["current_revision"]["body_markdown"]
    if kind == "long_text":
        actual = next(item for item in ast["sections"] if item["id"] == "1")
        assert "".join(block["text"] for block in actual["blocks"]) == text * 800
    if kind == "unknown_block":
        assert "```text\ngraph TD; A-->B\n```" in body
    if kind in {"plain_text", "markdown_diagram", "block_metadata"}:
        assert text in body
    if kind == "block_metadata":
        assert "Проверять SLA" in str(ast["warnings"])
    if kind == "protocol":
        assert "Кто отвечает" in str(ast["warnings"]) and "Уточнить срок" in str(ast["warnings"])


@pytest.mark.parametrize("confirm", [True, False])
def test_preview_advice_survives_confirmation_and_disappears_after_discard(database, confirm):
    from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR as PLACEMENT_AUTHOR, TENANT as PLACEMENT_TENANT, _command as placement_command

    document, job, plan = _request_placement()
    verdict = {"status": "FAIL", "issues": [{"source_id": "section:5.5", "section_id": "5.5", "reason": "Проверьте независимые требования мониторинга."}]}
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlacementAdapter(plan, verdict))).run_once()
    preview = document_queries.get_change_preview(job.document_id, job.id)
    assert preview["warnings"][0]["message"] == verdict["issues"][0]["reason"]
    view = document_queries.get_document(job.document_id, PLACEMENT_AUTHOR)
    assert view["current_revision"]["revision_id"] == document["current_revision"]["revision_id"]
    command = "CONFIRM_PREPARED_CHANGES" if confirm else "DISCARD_PREPARED_CHANGES"
    document_commands.execute(PLACEMENT_TENANT, PLACEMENT_AUTHOR, job.document_id, placement_command(view, command, {"job_id": job.id}))
    refreshed = document_queries.get_document(job.document_id, PLACEMENT_AUTHOR)
    assert refreshed["change_preview"] is None
    assert "warnings" not in refreshed["latest_job"]
    if confirm:
        assert refreshed["current_revision"]["document_ast"]["warnings"] == preview["warnings"]
        assert refreshed["lifecycle_state"] == "AGREED"
        assert BusinessDocumentRevision.select().count() == 2
    else:
        assert refreshed["current_revision"] == view["current_revision"]
        assert BusinessDocumentRevision.select().count() == 1


def test_original_block_metadata_and_section_notes_survive_same_content_edit_and_every_export(database):
    requested = _request_draft()
    draft = _draft()
    draft["sections"][0]["blocks"] = [{"type": "mermaid", "source": "graph TD; A-->B", "caption": "CAPTION_915", "note": "BLOCK_NOTE_227"}]
    draft["sections"][0]["comment"] = {"text": "SECTION_NOTE_438", "other": "SECTION_EXTRA_351"}
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(_draft_bundle(draft)))).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    ast = document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"]
    changed = apply_change_plan(
        ast,
        {
            "operations": [
                {"type": "REPLACE_SECTION_CONTENT", "section_id": "1", "expected_section_hash": section_hash(ast["sections"][0]), "content": {"blocks": deepcopy(ast["sections"][0]["blocks"])}}
            ]
        },
    )
    assert changed["warnings"] == ast["warnings"]
    revision = SimpleNamespace(document_ast=changed, revision_number=2)
    text_outputs = [BusinessDocumentExportService._render(fmt, revision).decode("utf-8") for fmt in ("MARKDOWN", "EVA_WIKI")]
    docx = Document(io.BytesIO(BusinessDocumentExportService._render("DOCX", revision)))
    text_outputs.append("\n".join(paragraph.text for paragraph in docx.paragraphs))
    for text in text_outputs:
        assert all(value in text for value in ("CAPTION_915", "BLOCK_NOTE_227", "SECTION_NOTE_438", "SECTION_EXTRA_351", "Замечания к документу"))


@pytest.mark.parametrize("kind", ["numeric_id", "duplicate_id", "orphan", "missing_id", "wrong_type", "sections_mapping"])
def test_noncanonical_draft_shape_is_saved_without_losing_text(database, kind):
    requested = _request_draft()
    draft = _draft()
    if kind == "numeric_id":
        draft["sections"][0]["id"] = 1
    elif kind == "duplicate_id":
        draft["sections"].append({**deepcopy(draft["sections"][0]), "blocks": [{"type": "paragraph", "text": "ADDITIONAL_DUPLICATE_TEXT_351"}]})
    elif kind == "orphan":
        draft["sections"].append({"id": "99.1", "title": "Extra", "blocks": [{"type": "paragraph", "text": "ORPHAN_CONTENT_549"}]})
    elif kind == "missing_id":
        draft["sections"][0].pop("id")
    elif kind == "wrong_type":
        draft["document_type"] = "business-requirements"
    else:
        draft["sections"] = {section["id"]: section for section in draft["sections"]}
    original = deepcopy(draft)
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(_draft_bundle(draft)))).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    view = document_queries.get_document(job.document_id, AUTHOR)
    assert view["current_revision"]["document_ast"]["warnings"]
    buffer = next(section for section in view["current_revision"]["document_ast"]["sections"] if section["id"] == "1.1")
    assert json.loads("".join(block["text"] for block in buffer["blocks"]))["draft"] == original
    assert draft == original


def test_numbered_model_markdown_is_distributed_without_synthetic_empty_section_warnings(database):
    requested = _request_draft()
    from api.apps.business_documents.assets import render_document_ast

    draft = _draft()
    output = render_document_ast(draft).replace("## 1. Цель", "# 1. Цель")
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(output))).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    ast = document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"]
    assert not any(w["code"] == "REQUIRED_SECTION_EMPTY" for w in ast["warnings"])
    assert [s["id"] for s in ast["sections"]] == [s["id"] for s in draft["sections"]]
    assert next(s for s in ast["sections"] if s["id"] == "1")["blocks"] == with_block_ids(draft)["sections"][0]["blocks"]


def test_intake_presentation_advice_persists_after_answers_reassessment_and_draft(database):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    question = {"semantic_tag": "question", "stage": "INTAKE", "target_section_id": "1", "text": "Кто отвечает?", "options": [{"option_id": "author", "label": "Автор"}], "allow_custom_answer": True}
    output = {
        "schema_version": "1",
        "outcome": "NEEDS_INPUT",
        "questions": [{**deepcopy(question), "semantic_tag": f"question_{i}", "text": f"Кто отвечает за этап {i}?"} for i in range(5)],
        "note": "INTAKE_NOTE_918",
    }
    output = {"question_batch": output, "note": "OUTSIDE_ENVELOPE_NOTE_831"}
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(output))).run_once()
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "COMPLETED"
    document = document_queries.get_document(document["document_id"], AUTHOR)
    assert len(document["protocol"]["questions"]) == 5 and document["protocol"]["warnings"]
    assert "REQUEST_DRAFT" not in document["allowed_commands"]
    for item in document["protocol"]["questions"]:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "ANSWER_QUESTION", {"question_id": item["question_id"], "selected_option_id": None, "custom_answer": "Ответственный — руководитель отдела."}),
        )
        document = document_queries.get_document(document["document_id"], AUTHOR)
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _claim_complete(requested["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(_draft_bundle()))).run_once()
    saved = document_queries.get_document(document["document_id"], AUTHOR)
    assert "INTAKE_NOTE_918" in str(saved["protocol"]["warnings"])
    assert "INTAKE_NOTE_918" in str(saved["current_revision"]["document_ast"]["warnings"])
    assert "OUTSIDE_ENVELOPE_NOTE_831" in str(saved["current_revision"]["document_ast"]["warnings"])


def test_draft_envelope_alias_preserves_root_metadata(database):
    requested = _request_draft()
    output = {"document_draft": _draft(), "note": "DRAFT_OUTER_NOTE_519"}
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(output))).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    ast = document_queries.get_document(job.document_id, AUTHOR)["current_revision"]["document_ast"]
    assert "DRAFT_OUTER_NOTE_519" in str(ast["warnings"])
    assert not any(section["id"] == "1.1" for section in ast["sections"])


def test_unstructured_change_response_is_retained_without_preview_confirmation_or_resolving_inputs(database):
    from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR as PLACEMENT_AUTHOR

    document, job, _ = _request_placement()
    output = "Неструктурированная правка CHANGE_TEXT_195: проверить основание перед применением."
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(output))).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED" and not job.error
    view = document_queries.get_document(job.document_id, PLACEMENT_AUTHOR)
    assert view["current_revision"] == document["current_revision"] and view["change_preview"] is None
    assert view["lifecycle_state"] == "REVIEW" and "CONFIRM_PREPARED_CHANGES" not in view["allowed_commands"]
    assert "PREPARE_CHANGES" in view["allowed_commands"]
    assert output in str(view["protocol"]["warnings"])
    assert BusinessDocumentRevision.select().count() == 1


def test_plan_text_alias_and_metadata_are_saved_with_the_validated_change(database):
    from test.unit_test.api.apps.business_documents.test_business_document_service import AUTHOR as PLACEMENT_AUTHOR

    _, job, plan = _request_placement()
    plan["note"] = "PLAN_NOTE_913"
    plan["operations"][0]["content"] = {"text": "Восстановленный текст TEXT_ALIAS_258", "caption": "CONTENT_CAPTION_561"}
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(PlacementAdapter(plan, {"status": "PASS", "issues": []}))).run_once()
    job = BusinessDocumentJob.get_by_id(job.id)
    assert job.status == "COMPLETED", job.error
    view = document_queries.get_document(job.document_id, PLACEMENT_AUTHOR)
    preview = document_queries.get_change_preview(job.document_id, job.id)
    assert "TEXT_ALIAS_258" in str(preview["sections"])
    assert "PLAN_NOTE_913" in str(preview["warnings"]) and "CONTENT_CAPTION_561" in str(preview["warnings"])
    assert view["change_preview"]


def _format_question(tag="topic", text="Кто отвечает?", section="1", stage="INTAKE"):
    return {"semantic_tag": tag, "stage": stage, "target_section_id": section, "text": text, "options": [], "allow_custom_answer": True}


def _run_format_response(requested, output):
    assert BusinessDocumentWorker(ai=BusinessDocumentAI(CapturingAdapter(output))).run_once()
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED", job.error
    return document_queries.get_document(job.document_id, AUTHOR)


def test_ready_draft_survives_review_question_unknown_section(database):
    requested = _request_draft()
    output = _draft_bundle()
    output["review_questions"] = {"schema_version": "1", "outcome": "NEEDS_INPUT", "questions": [_format_question(section="77", stage="REVIEW")]}
    view = _run_format_response(requested, output)
    assert view["current_revision"] is not None
    assert view["protocol"]["questions"][0]["text"] == "Кто отвечает?"
    assert view["protocol"]["questions"][0]["status"] == "OPEN"
    assert '"target_section_id": "77"' in str(view["current_revision"]["document_ast"]["warnings"])


def test_null_warning_with_transport_note_is_saved(database):
    output = _draft_bundle()
    output["draft"]["warnings"] = None
    output["note"] = "SOURCE_NOTE_937"
    view = _run_format_response(_request_draft(), output)
    assert "SOURCE_NOTE_937" in str(view["current_revision"]["document_ast"]["warnings"])


@pytest.mark.parametrize("field,value", [("target_section_id", 1), ("reason", None)])
def test_intake_presentation_error_is_saved(database, field, value):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    question = _format_question()
    question[field] = value
    view = _run_format_response(requested, {"schema_version": "1", "outcome": "NEEDS_INPUT", "questions": [question]})
    assert view["protocol"]["questions"][0]["text"] == question["text"]
    assert "REQUEST_DRAFT" not in view["allowed_commands"]
    assert view["protocol"]["warnings"]


@pytest.mark.parametrize("first_tag,second_tag", [("Topic", "topic"), (" topic ", "ＴＯＰＩＣ")])
def test_canonical_tag_collisions_preserve_different_questions(database, first_tag, second_tag):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    questions = [_format_question(first_tag, "Кто отвечает за отправку?"), _format_question(second_tag, "Какой срок уведомления?")]
    view = _run_format_response(requested, {"schema_version": "1", "outcome": "NEEDS_INPUT", "questions": questions})
    assert [q["text"] for q in view["protocol"]["questions"]] == [q["text"] for q in questions]
    assert len({q["semantic_tag"] for q in view["protocol"]["questions"]}) == 2


def test_known_published_question_tag_does_not_discard_new_question():
    job = SimpleNamespace(job_type="ASSESS_INTAKE", payload={"protocol": {"questions": [_format_question("topic", "Кто отвечает?")]}})
    output = BusinessDocumentAI._normalize_question_fields(job, {"questions": [_format_question("TOPIC", "Какой срок?")]})
    assert output["questions"][0]["semantic_tag"].casefold() != "topic"
    assert output["warnings"]


def test_question_tag_repair_preserves_the_exact_comment_question_link():
    job = SimpleNamespace(job_type="ASSESS_REVIEW")
    output = {
        "questions": [_format_question("Topic", "Кто отвечает?"), _format_question("topic", "Какой срок?")],
        "comment_dispositions": [{"comment_event_id": "comment-1", "disposition": "NEEDS_QUESTION", "question_semantic_tag": "topic"}],
    }
    normalized = BusinessDocumentAI._normalize_question_fields(job, output)
    assert normalized["comment_dispositions"][0]["question_semantic_tag"] == normalized["questions"][1]["semantic_tag"]
    assert normalized["questions"][0]["semantic_tag"] != normalized["questions"][1]["semantic_tag"]


@pytest.mark.parametrize("malformed", [False, True])
def test_review_metadata_or_unparseable_assessment_preserves_active_comment(database, malformed):
    requested = _request_draft()
    document = _claim_complete(requested["job_id"], _draft_bundle())
    before = document["current_revision"]
    document_commands.execute(
        TENANT, AUTHOR, document["document_id"], _command(document, "ADD_COMMENT", {"text": "Уведомлять оператора", "section_id": None, "revision_id": before["revision_id"], "anchor": None})
    )
    document = document_queries.get_document(document["document_id"], AUTHOR)
    event_id = document["protocol"]["comments"][-1]["source_event_id"]
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))
    output = {
        "schema_version": "1",
        "questions": [],
        "proposals": [],
        "comment_dispositions": [{"comment_event_id": event_id, "disposition": "UNDECIDED" if malformed else "CONFIRMED_CHANGE", "reason": "REVIEW_NOTE_642"}],
    }
    view = _run_format_response(requested, output)
    assert view["current_revision"] == before
    assert "REVIEW_NOTE_642" in str(view["protocol"]["warnings"])
    assert view["operation_state"] == "IDLE"
    if malformed:
        assert "PREPARE_CHANGES" not in view["allowed_commands"]
        assert "REQUEST_REVIEW_ASSESSMENT" in view["allowed_commands"]
        assert "unapplied_assessment" in BusinessDocumentJob.get_by_id(requested["job_id"]).result["output"]
        assert view["protocol"]["comments"][-1]["disposition"] is None


@pytest.mark.parametrize("fmt", ["MARKDOWN", "EVA_WIKI"])
def test_exported_notes_roundtrip_as_notes_without_polluting_section(database, fmt):
    draft = _draft()
    note = {"code": "SOURCE_FORMAT", "section_id": "4.3", "message": 'NOTE_MARKER_592\n<check> --> "detail"'}
    draft["warnings"] = [note]
    raw = BusinessDocumentExportService._render(fmt, SimpleNamespace(document_ast=draft, revision_number=1)).decode()
    if fmt == "EVA_WIKI":
        from api.apps.business_documents.eva_changes import _html_to_markdown

        raw = _html_to_markdown(raw)
    imported = import_document_markdown(raw)
    assert note in imported.get("warnings", [])
    assert "NOTE_MARKER_592" not in str(imported["sections"][-1]["blocks"])


def test_user_edited_warning_appendix_is_preserved():
    draft = _draft()
    draft["warnings"] = [{"code": "NOTE", "message": "ORIGINAL_152"}]
    raw = BusinessDocumentExportService._render("MARKDOWN", SimpleNamespace(document_ast=draft, revision_number=1)).decode()
    raw = raw.replace("- ORIGINAL_152", "- AUTHOR_CHANGE_981")
    imported = import_document_markdown(raw)
    assert "AUTHOR_CHANGE_981" in str(imported["warnings"])
    assert "ORIGINAL_152" in str(imported["warnings"])


@pytest.mark.parametrize("edit", ["paragraph", "heading", "image", "attribute"])
def test_extra_user_text_in_html_warning_appendix_is_preserved(edit):
    from api.apps.business_documents.eva_changes import _html_to_markdown

    draft = _draft()
    draft["warnings"] = [{"code": "NOTE", "message": "ORIGINAL_152"}]
    raw = BusinessDocumentExportService._render("EVA_WIKI", SimpleNamespace(document_ast=draft, revision_number=1)).decode()
    if edit == "paragraph":
        raw = raw.replace("</aside>", "<p>AUTHOR_ADDITION_792</p></aside>")
    elif edit == "image":
        raw = raw.replace("</aside>", '<img src="https://example.test/AUTHOR_ADDITION_792.png" alt="AUTHOR_ADDITION_792"/></aside>')
    elif edit == "attribute":
        raw = raw.replace("<li>ORIGINAL_152", '<li title="AUTHOR_ADDITION_792">ORIGINAL_152')
    else:
        raw = raw.replace("Замечания к документу", "AUTHOR_ADDITION_792")
    imported = import_document_markdown(_html_to_markdown(raw))
    assert "AUTHOR_ADDITION_792" in str(imported["warnings"])
    assert "ORIGINAL_152" in str(imported["warnings"])
    assert "AUTHOR_ADDITION_792" not in str(imported["sections"][-1]["blocks"])
