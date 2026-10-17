"""Document quality issues survive worker completion, preview and confirmation."""

from copy import deepcopy

import pytest

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
from api.apps.business_documents.assets import validate_contract
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
