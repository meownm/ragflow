"""HTTP commands, SQLite, worker and downloaded exports share warning policy."""

from copy import deepcopy
import hashlib

import pytest

from test.unit_test.api.apps.business_documents import test_business_document_service as service
from test.unit_test.api.apps.business_documents import test_business_document_api as api_tests
from test.unit_test.api.apps.business_documents.test_business_document_worker_exports import MemoryStorage
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.worker import BusinessDocumentWorker
from api.db.db_models import BusinessDocumentCommand, BusinessDocumentJob, BusinessDocumentRevision, migrate_business_document_catalog


SOURCE_WARNING = {"code": "SOURCE_NOTE", "message": "Периодичность сверки источника уточняется владельцем."}
UPDATED_GOAL = "Сократить время обработки заявки до пяти минут."


@pytest.fixture()
def database():
    yield from service.database.__wrapped__()


@pytest.fixture()
def http_app(monkeypatch):
    yield from api_tests.route_app.__wrapped__(monkeypatch)


class DialogueAdapter:
    def __init__(self, verdict):
        self.verdict = verdict
        self.plan = None
        self.calls = []

    def generate(self, tenant_id, system_prompt, payload, *, timeout_seconds=None):
        job = payload["job_input"]
        task = job["task_type"]
        self.calls.append(task)
        if task == "ASSESS_INTAKE":
            return {"schema_version": "1", "outcome": "COMPLETE", "questions": []}
        if task == "GENERATE_DRAFT":
            draft = service._draft()
            draft.update(schema_version="2", section_scope="required", warnings=[deepcopy(SOURCE_WARNING)])
            return {"draft": draft, "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, "proposals": []}
        if task == "ASSESS_REVIEW":
            comments = job["protocol"]["comments"]
            return {
                "schema_version": "1",
                "questions": [],
                "proposals": [],
                "comment_dispositions": [{"comment_event_id": comment["source_event_id"], "disposition": "CONFIRMED_CHANGE"} for comment in comments],
            }
        if task == "PLAN_CHANGES":
            assert self.plan is not None
            return {"change_plan": deepcopy(self.plan)}
        assert task == "VERIFY_REQUIREMENT_COVERAGE"
        return {
            "status": self.verdict,
            "issues": [] if self.verdict == "PASS" else [{"source_id": "section:1", "section_id": "1", "reason": "Проверьте измеримость цели."}],
        }


@pytest.mark.p0
@pytest.mark.asyncio
@pytest.mark.parametrize("export_format", ["MARKDOWN", "EVA_WIKI"])
@pytest.mark.parametrize(("changed", "verdict"), [(False, "PASS"), (True, "PASS"), (True, "FAIL")])
async def test_apply_with_warnings_finishes_and_downloads_export(database, http_app, monkeypatch, changed, verdict, export_format):
    app, module = http_app
    storage = MemoryStorage()
    adapter = DialogueAdapter(verdict)
    worker = BusinessDocumentWorker(ai=BusinessDocumentAI(adapter), storage=storage)

    # Keep the in-memory database on its owning thread. The production route,
    # command service, queue, AI validation and export publication execute below.
    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(module, "thread_pool_exec", inline_call)
    monkeypatch.setattr(module, "wake_business_document_worker", lambda: None)
    monkeypatch.setattr(BusinessDocumentExportService, "_default_storage", staticmethod(lambda: storage))
    migrate_business_document_catalog()
    entry = service.load_document_catalog()["items"][0]
    client = app.test_client()
    response = await client.post(
        "/business-documents",
        json={
            "schema_version": "4",
            "document_type": "business_requirements",
            "catalog_entry_id": entry["id"],
            "idea": "Ускорить обработку заявок.",
            "dataset_ids": [],
            "eva_decision": {"mode": "SKIP"},
        },
    )
    assert response.status_code == 201, await response.get_json()
    document = (await response.get_json())["data"]
    path = f"/business-documents/{document['document_id']}"

    async def read_document():
        response = await client.get(path)
        assert response.status_code == 200
        return (await response.get_json())["data"]

    async def command(document, command_type, payload=None):
        response = await client.post(path + "/commands", json=service._command(document, command_type, payload))
        assert response.status_code in {200, 202}, await response.get_json()
        return (await response.get_json())["data"]

    async def run_job(document, command_type, payload=None):
        result = await command(document, command_type, payload)
        assert worker.run_once()
        job = BusinessDocumentJob.get_by_id(result["job_id"])
        assert job.status == "COMPLETED", job.error
        return await read_document()

    document = await run_job(document, "REQUEST_INTAKE_ASSESSMENT")
    document = await run_job(document, "REQUEST_DRAFT")
    assert document["current_revision"]["document_ast"]["warnings"] == [SOURCE_WARNING]
    if changed:
        await command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "text": UPDATED_GOAL, "anchor": None})
        document = await read_document()
    document = await run_job(document, "REQUEST_REVIEW_ASSESSMENT")
    base = document["current_revision"]
    section = next(section for section in base["document_ast"]["sections"] if section["id"] == "1")
    adapter.plan = {
        "schema_version": "1",
        "base_revision_id": base["revision_id"],
        "source_state_version": document["state_version"] + 1,
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {
                "operation_id": "goal",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "1",
                "expected_section_hash": service.section_hash(section),
                "source_event_ids": [document["protocol"]["comments"][0]["source_event_id"]],
                "content": {"blocks": [{"type": "paragraph", "text": UPDATED_GOAL}]},
            }
        ]
        if changed
        else [],
    }
    apply = service._command(document, "APPLY_CHANGES", {"base_revision_id": base["revision_id"]})
    response = await client.post(path + "/commands", json=apply)
    assert response.status_code == 202
    assert worker.run_once()
    document = await read_document()

    assert document["latest_job"]["status"] == "COMPLETED", document["latest_job"]["error"]
    assert document["lifecycle_state"] == "AGREED"
    assert document["operation_state"] == "IDLE"
    assert document["change_preview"] is None
    assert "REQUEST_EXPORT" in document["allowed_commands"]
    assert "CONFIRM_PREPARED_CHANGES" not in document["allowed_commands"]
    warnings = document["current_revision"]["document_ast"]["warnings"]
    assert SOURCE_WARNING in warnings
    assert (any(warning["code"] == "REQUIREMENT_COVERAGE_FAILED" for warning in warnings)) == (verdict == "FAIL")
    revision_count = BusinessDocumentRevision.select().count()
    assert revision_count == (2 if changed else 1)
    assert document["current_revision"]["section_texts"]["1"] == (UPDATED_GOAL if changed else base["section_texts"]["1"])
    replay = await client.post(path + "/commands", json=apply)
    assert replay.status_code == 202
    assert (await replay.get_json())["data"]["idempotent_replay"] is True
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 1
    assert adapter.calls.count("PLAN_CHANGES") == 1

    document = await run_job(document, "REQUEST_EXPORT", {"revision_id": document["current_revision"]["revision_id"], "format": export_format})
    response = await client.get(path + "/exports")
    assert response.status_code == 200
    artifacts = (await response.get_json())["data"]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact["revision_id"] == document["current_revision"]["revision_id"]
    response = await client.get(path + f"/exports/{artifact['artifact_id']}/download")
    content = await response.get_data()
    assert response.status_code == 200
    assert SOURCE_WARNING["message"] in content.decode("utf-8")
    if verdict == "FAIL":
        assert "Проверьте измеримость цели." in content.decode("utf-8")
    if changed:
        assert UPDATED_GOAL in content.decode("utf-8")
    assert len(content) == artifact["size"]
    assert artifact["content_hash"] == "sha256:" + hashlib.sha256(content).hexdigest()
    assert "attachment;" in response.headers["Content-Disposition"]
    assert BusinessDocumentRevision.select().count() == revision_count
    assert (await read_document())["current_revision"]["document_ast"]["warnings"] == warnings
    assert BusinessDocumentCommand.select().count() == (6 if changed else 5)
