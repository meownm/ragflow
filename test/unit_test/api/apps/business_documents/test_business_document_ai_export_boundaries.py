#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
import sys
from types import ModuleType

import pytest
from peewee import SqliteDatabase


if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.assets import prompt_text, published_template, render_document_ast, validate_document_ast
from api.apps.business_documents.errors import BusinessDocumentError
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.service import BusinessDocumentService
from api.apps.business_documents.worker import BusinessDocumentJobQueue, BusinessDocumentWorker
from api.db.db_models import BusinessDocument, BusinessDocumentEvent, BusinessDocumentExportArtifact, BusinessDocumentJob, BusinessDocumentRevision
from test.unit_test.api.apps.business_documents.helpers import required_section_blocks
from common.time_utils import current_timestamp


TENANT = "tenant-ai-boundary"
AUTHOR = "author-ai-boundary"


@pytest.fixture()
def database():
    database = SqliteDatabase(":memory:")
    tables = BusinessDocumentService.model_tables()
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)
        database.close()


def _create():
    return BusinessDocumentService.create_document(
        TENANT,
        AUTHOR,
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": "Проверяемый документ",
            "idea": "Создать проверяемый сервис",
        },
    )


def _command(document, command_type, payload=None, suffix=""):
    return {
        "schema_version": "1",
        "command_id": f"cmd-{command_type}-{document['state_version']}{suffix}",
        "idempotency_key": f"idem-{command_type}-{document['state_version']}{suffix}",
        "expected_state_version": document["state_version"],
        "type": command_type,
        "payload": payload or {},
    }


def _draft():
    return {
        "schema_version": "1",
        "document_type": "business_requirements",
        "template_version": published_template()["template_version"],
        "sections": [
            {
                "id": section["id"],
                "title": section["title"],
                "blocks": required_section_blocks(section["id"], f"Содержание раздела {section['id']}."),
            }
            for section in published_template()["sections"]
        ],
    }


def _claim_complete(job_id, output, worker_id="boundary-worker"):
    job = BusinessDocumentJobQueue.claim(worker_id, lease_ms=60_000)
    assert job is not None and job.id == job_id
    return BusinessDocumentService.complete_job(TENANT, worker_id, job.id, output, job.lease_token)


class CapturingAdapter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate(self, tenant_id, system_prompt, input_payload):
        self.calls.append((tenant_id, system_prompt, input_payload))
        return self.response


@pytest.mark.p0
def test_ai_repairs_json_validates_schema_and_persists_pinned_prompt_audit(database):
    document = _create()
    requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    adapter = CapturingAdapter("```json\n{'schema_version':'1','outcome':'COMPLETE','questions':[],}\n```")
    worker = BusinessDocumentWorker(worker_id="ai-worker", ai=BusinessDocumentAI(adapter), lease_ms=60_000)
    assert worker.run_once() is True

    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    prompt = job.payload["prompt"]
    assert job.status == "COMPLETED"
    assert job.result["prompt_version"] == prompt["version"]
    assert job.result["prompt_hash"] == prompt["content_hash"]
    assert job.result["output"] == {"schema_version": "1", "outcome": "COMPLETE", "questions": []}
    tenant_id, system_prompt, input_payload = adapter.calls[0]
    assert tenant_id == TENANT
    assert prompt_text("intake").splitlines()[0] in system_prompt
    assert "{{context_json}}" not in system_prompt
    assert '"id": "5.5"' in system_prompt
    assert '"policy_id": "business-requirements-process"' in system_prompt
    assert "Не создавай и не угадывай идентификаторы событий" in system_prompt
    assert input_payload["prompt"] == prompt
    allowed_event_ids = {event["event_id"] for event in input_payload["job_input"]["source_events"]}
    assert input_payload["job_input"]["idea_source_event_id"] in allowed_event_ids
    event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "IntakeAssessed"))
    assert event.payload["prompt_version"] == prompt["version"]
    assert event.payload["prompt_hash"] == prompt["content_hash"]

    next_request = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(BusinessDocumentService.get_document(TENANT, document["document_id"], AUTHOR), "REQUEST_DRAFT"),
    )
    invalid = CapturingAdapter("{}")
    with pytest.raises(BusinessDocumentError) as caught:
        BusinessDocumentAI(invalid).process(BusinessDocumentJob.get_by_id(next_request["job_id"]))
    assert caught.value.code == "INVALID_DRAFT_BUNDLE"


@pytest.mark.p0
def test_ai_unwraps_exact_contract_name_envelope(database):
    document = _create()
    requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    adapter = CapturingAdapter(
        {
            "question_batch": {
                "schema_version": "1",
                "outcome": "COMPLETE",
                "questions": [],
            }
        }
    )

    worker = BusinessDocumentWorker(worker_id="ai-worker", ai=BusinessDocumentAI(adapter), lease_ms=60_000)
    assert worker.run_once() is True

    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "COMPLETED"
    assert job.result["output"] == {"schema_version": "1", "outcome": "COMPLETE", "questions": []}


class MemoryStorage:
    def __init__(self, *, discard=False):
        self.discard = discard
        self.objects = {}
        self.removed = []

    def put(self, bucket, key, content):
        if not self.discard:
            self.objects[(bucket, key)] = content

    def get(self, bucket, key):
        return self.objects.get((bucket, key))

    def rm(self, bucket, key):
        self.removed.append((bucket, key))
        self.objects.pop((bucket, key), None)


def _agreed_document():
    projection = _create()
    ast = _draft()
    body = render_document_ast(ast)
    created = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == projection["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
    now_ms = current_timestamp()
    revision = BusinessDocumentRevision.create(
        id="agreed-revision-ai-boundary",
        document_id=projection["document_id"],
        revision_number=1,
        document_ast=ast,
        body_markdown=body,
        content_hash=f"sha256:{hashlib.sha256(body.encode()).hexdigest()}",
        source_event_ids=[created.id],
        create_time=now_ms,
        create_date=datetime.now(),
        update_time=now_ms,
        update_date=datetime.now(),
    )
    BusinessDocument.update(
        lifecycle_state="AGREED",
        operation_state="IDLE",
        state_version=2,
        current_revision_id=revision.id,
    ).where(BusinessDocument.id == projection["document_id"]).execute()
    return BusinessDocumentService.get_document(TENANT, projection["document_id"], AUTHOR)


@pytest.mark.p0
def test_docx_export_requires_verified_storage_write(database):
    document = _agreed_document()
    requested = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "REQUEST_EXPORT",
            {"revision_id": document["current_revision"]["revision_id"], "format": "DOCX"},
        ),
    )
    job = BusinessDocumentJobQueue.claim("export-boundary-worker", lease_ms=60_000)
    assert job is not None and job.id == requested["job_id"]
    discarded = MemoryStorage(discard=True)
    with pytest.raises(BusinessDocumentError) as caught:
        BusinessDocumentExportService.generate(job, storage=discarded)
    assert caught.value.code == "EXPORT_STORAGE_WRITE_FAILED"
    assert discarded.removed
    assert BusinessDocumentExportArtifact.select().count() == 0

    storage = MemoryStorage()
    artifact = BusinessDocumentExportService.generate(job, storage=storage)
    row = BusinessDocumentExportArtifact.get_by_id(artifact["artifact_id"])
    assert storage.get(row.storage_bucket, row.storage_key).startswith(b"PK")


@pytest.mark.p0
@pytest.mark.parametrize("unsafe_url", ["javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4="])
def test_document_schema_rejects_unsafe_external_urls(database, unsafe_url):
    draft = _draft()
    draft["sections"][0]["blocks"] = [{"type": "reference", "label": "Unsafe", "url": unsafe_url}]
    with pytest.raises(BusinessDocumentError) as caught:
        validate_document_ast(draft)
    assert caught.value.code == "INVALID_DOCUMENT_DRAFT"


@pytest.mark.p0
def test_dead_operation_can_be_retried_and_draft_sources_are_snapshot_bound(database):
    class FailingAI:
        def process(self, _job):
            raise RuntimeError("permanent failure")

    document = _create()
    failed_request = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    BusinessDocumentJob.update(max_attempts=1).where(BusinessDocumentJob.id == failed_request["job_id"]).execute()
    assert BusinessDocumentWorker(worker_id="dead-worker", ai=FailingAI()).run_once() is True
    failed = BusinessDocumentService.get_document(TENANT, document["document_id"], AUTHOR)
    assert failed["operation_state"] == "FAILED"
    assert "REQUEST_INTAKE_ASSESSMENT" in failed["allowed_commands"]
    retried = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(failed, "REQUEST_INTAKE_ASSESSMENT", suffix="-retry"),
    )
    assert retried["operation_state"] == "ANALYZING"

    completed = _claim_complete(
        retried["job_id"],
        {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
    )
    draft_request = BusinessDocumentService.execute_command(TENANT, AUTHOR, completed["document_id"], _command(completed, "REQUEST_DRAFT"))
    job = BusinessDocumentJob.get_by_id(draft_request["job_id"])
    idea_event_id = job.payload["idea_source_event_id"]
    assert idea_event_id in {event["event_id"] for event in job.payload["source_events"]}
    proposal = {
        "target_section_id": "5.5",
        "text": "Добавить мониторинг",
        "rationale": "Следует из постановки",
        "source_event_ids": [idea_event_id],
    }
    reviewed = _claim_complete(
        job.id,
        {
            "draft": _draft(),
            "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
            "proposals": [proposal],
        },
    )
    assert idea_event_id in reviewed["protocol"]["proposals"][0]["source_event_ids"]

    other = _create()
    assessment = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        other["document_id"],
        _command(other, "REQUEST_INTAKE_ASSESSMENT", suffix="-other"),
    )
    other = _claim_complete(
        assessment["job_id"],
        {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
    )
    requested = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        other["document_id"],
        _command(other, "REQUEST_DRAFT", suffix="-other"),
    )
    with pytest.raises(BusinessDocumentError) as unknown:
        _claim_complete(
            requested["job_id"],
            {
                "draft": _draft(),
                "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
                "proposals": [{**proposal, "source_event_ids": ["invented-event-id"]}],
            },
        )
    assert unknown.value.code == "SOURCE_NOT_IN_JOB_SNAPSHOT"
