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
import io
from datetime import UTC, datetime as DateTime
from pathlib import Path
import sys
import threading
from types import ModuleType
import zipfile

import pytest
from peewee import SqliteDatabase


if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents import exports as exports_module
from api.apps.business_documents.assets import published_template, render_document_ast
from api.apps.business_documents.errors import BusinessDocumentError
from api.apps.business_documents.exports import BusinessDocumentExportService
from api.apps.business_documents.service import BusinessDocumentService
from api.apps.business_documents import worker as worker_module
from api.apps.business_documents.worker import BusinessDocumentJobQueue, BusinessDocumentWorker
from api.db.db_models import (
    BusinessDocument,
    BusinessDocumentEvent,
    BusinessDocumentExportArtifact,
    BusinessDocumentJob,
    BusinessDocumentProposal,
    BusinessDocumentRevision,
)
from test.unit_test.api.apps.business_documents.helpers import required_section_blocks
from common.time_utils import current_timestamp


TENANT = "tenant-worker"
AUTHOR = "author-worker"


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


class MemoryStorage:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_count = 0

    def put(self, bucket: str, key: str, content: bytes):
        self.put_count += 1
        self.objects[(bucket, key)] = content

    def get(self, bucket: str, key: str):
        return self.objects.get((bucket, key))

    def rm(self, bucket: str, key: str):
        self.objects.pop((bucket, key), None)


class FailingAI:
    def process(self, _job):
        raise RuntimeError("transient model failure")


class CompleteIntakeAI:
    def process(self, _job):
        return {"schema_version": "1", "outcome": "COMPLETE", "questions": []}


def _create():
    return BusinessDocumentService.create_document(
        TENANT,
        AUTHOR,
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": "Безопасный экспорт / требования",
            "idea": "Подготовить проверяемые бизнес-требования",
        },
    )


def _command(document, command_type: str, payload=None, *, suffix=""):
    version = document["state_version"]
    return {
        "schema_version": "1",
        "command_id": f"cmd-{command_type.lower()}-{version}{suffix}",
        "idempotency_key": f"idem-{command_type.lower()}-{version}{suffix}",
        "expected_state_version": version,
        "type": command_type,
        "payload": payload or {},
    }


def _question_batch():
    return {
        "schema_version": "1",
        "outcome": "NEEDS_INPUT",
        "questions": [
            {
                "semantic_tag": "audience",
                "stage": "INTAKE",
                "target_section_id": "3.1",
                "text": "Кто использует сервис?",
                "options": [
                    {"option_id": "individuals", "label": "Физические лица"},
                    {"option_id": "companies", "label": "Юридические лица"},
                ],
                "allow_custom_answer": True,
            }
        ],
    }


def _claim_and_complete(job_id: str, output: dict, *, worker_id="worker"):
    job = BusinessDocumentJobQueue.claim(worker_id, lease_ms=60_000)
    assert job is not None and job.id == job_id and job.lease_token
    return BusinessDocumentService.complete_job(TENANT, worker_id, job.id, output, job.lease_token)


def _minimal_ast():
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


def _agreed_document():
    projection = _create()
    document = BusinessDocument.get_by_id(projection["document_id"])
    ast = _minimal_ast()
    body = render_document_ast(ast)
    revision_id = "revision-agreed"
    now_ms = current_timestamp()
    now = DateTime.now()
    created_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document.id) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
    BusinessDocumentRevision.create(
        id=revision_id,
        document_id=document.id,
        revision_number=1,
        document_ast=ast,
        body_markdown=body,
        content_hash=f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()}",
        source_event_ids=[created_event.id],
        create_time=now_ms,
        create_date=now,
        update_time=now_ms,
        update_date=now,
    )
    BusinessDocument.update(
        lifecycle_state="AGREED",
        operation_state="IDLE",
        current_revision_id=revision_id,
        state_version=2,
    ).where(BusinessDocument.id == document.id).execute()
    return BusinessDocumentService.get_document(TENANT, document.id, AUTHOR)


def _request_export(document, storage: MemoryStorage, export_format="EVA_WIKI"):
    requested = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "REQUEST_EXPORT",
            {"revision_id": document["current_revision"]["revision_id"], "format": export_format},
        ),
    )
    job = BusinessDocumentJobQueue.claim("export-worker", lease_ms=60_000)
    assert job is not None and job.id == requested["job_id"]
    artifact = BusinessDocumentExportService.generate(job, storage=storage)
    assert BusinessDocumentExportService.generate(job, storage=storage) == artifact
    projection = BusinessDocumentService.complete_job(TENANT, "export-worker", job.id, artifact, job.lease_token)
    return artifact, projection


@pytest.mark.p0
def test_expired_lease_is_fenced_and_reclaimed_without_duplicate_completion(database):
    document = _create()
    requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    now_ms = current_timestamp() + 10
    stale = BusinessDocumentJobQueue.claim("worker-a", lease_ms=10, now_ms=now_ms)
    assert stale is not None and stale.id == requested["job_id"]
    stale_token = stale.lease_token

    retry_count, dead_count = BusinessDocumentJobQueue.recover_stale(now_ms=stale.lease_expires_at + 1)
    assert (retry_count, dead_count) == (1, 0)
    current = BusinessDocumentJobQueue.claim("worker-b", lease_ms=60_000, now_ms=stale.lease_expires_at + 1)
    assert current is not None and current.attempt == 2 and current.lease_token != stale_token

    with pytest.raises(BusinessDocumentError, match="Worker no longer owns") as lost:
        BusinessDocumentService.complete_job(
            TENANT,
            "worker-a",
            stale.id,
            {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
            stale_token,
        )
    assert lost.value.code == "JOB_LEASE_LOST"

    projection = BusinessDocumentService.complete_job(
        TENANT,
        "worker-b",
        current.id,
        {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
        current.lease_token,
    )
    assert projection["operation_state"] == "IDLE"
    assert BusinessDocumentJob.get_by_id(current.id).status == "COMPLETED"
    assert BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "IntakeAssessed")).count() == 1


@pytest.mark.p0
def test_expired_exhausted_lease_is_dead_lettered_with_persistable_system_identity(database):
    document = _create()
    requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    BusinessDocumentJob.update(max_attempts=1).where(BusinessDocumentJob.id == requested["job_id"]).execute()
    now_ms = current_timestamp() + 10
    stale = BusinessDocumentJobQueue.claim("worker-a", lease_ms=10, now_ms=now_ms)
    assert stale is not None

    retry_count, dead_count = BusinessDocumentJobQueue.recover_stale(now_ms=stale.lease_expires_at + 1)

    assert (retry_count, dead_count) == (0, 1)
    assert BusinessDocumentJob.get_by_id(stale.id).status == "DEAD"
    assert BusinessDocumentService.get_document(TENANT, document["document_id"], AUTHOR)["operation_state"] == "FAILED"


@pytest.mark.p0
def test_retry_backoff_exhaustion_dead_letters_once_and_rejects_wrong_token(database):
    document = _create()
    requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    claimed = BusinessDocumentJobQueue.claim("probe-worker", lease_ms=60_000)
    assert claimed is not None
    assert BusinessDocumentJobQueue.retry(claimed.id, "probe-worker", "wrong-token", {}, delay_ms=0) is False
    assert BusinessDocumentJob.get_by_id(claimed.id).status == "RUNNING"
    assert BusinessDocumentJobQueue.retry(claimed.id, "probe-worker", claimed.lease_token, {}, delay_ms=0) is True

    worker = BusinessDocumentWorker(ai=FailingAI(), retry_base_ms=0, lease_ms=60_000)
    assert len(worker.worker_id) == 32
    assert worker.run_once() is True
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "RETRY"
    assert worker.run_once() is True

    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    projection = BusinessDocumentService.get_document(TENANT, document["document_id"], AUTHOR)
    assert job.status == "DEAD"
    assert job.attempt == job.max_attempts == 3
    assert job.error == {"code": "WORKER_FAILURE", "message": "transient model failure"}
    assert projection["operation_state"] == "FAILED"
    assert worker.run_once() is False
    assert BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "BusinessDocumentJobFailed")).count() == 1

    retry_command = _command(projection, "REQUEST_INTAKE_ASSESSMENT", suffix="-after-dead")
    retried = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], retry_command)
    assert retried["job_id"] != requested["job_id"]
    recovery_worker = BusinessDocumentWorker(worker_id="recovery-worker", ai=CompleteIntakeAI(), retry_base_ms=0, lease_ms=60_000)
    assert recovery_worker.run_once() is True
    recovered = BusinessDocumentService.get_document(TENANT, document["document_id"], AUTHOR)
    assert recovered["operation_state"] == "IDLE"
    assert BusinessDocumentJob.get_by_id(retried["job_id"]).status == "COMPLETED"
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "DEAD"


@pytest.mark.p1
def test_worker_start_is_singleton_and_wake_interrupts_idle_wait(database, monkeypatch):
    ready = threading.Event()
    stop = threading.Event()

    class FakeWorker:
        def run_forever(self, stop_event, *, poll_seconds):
            assert poll_seconds == 0.25
            ready.set()
            stop_event.wait(2)

    monkeypatch.setattr(worker_module, "BusinessDocumentWorker", FakeWorker)
    monkeypatch.setattr(worker_module, "_WORKER_THREAD", None)
    monkeypatch.setenv("BUSINESS_DOCUMENT_WORKER_ENABLED", "true")
    monkeypatch.setenv("BUSINESS_DOCUMENT_WORKER_POLL_SECONDS", "0.25")
    worker_module._WAKE_EVENT.clear()

    thread = worker_module.start_business_document_worker(stop)
    assert thread is not None and ready.wait(1)
    assert worker_module.start_business_document_worker(stop) is thread
    worker_module.wake_business_document_worker()
    assert worker_module._WAKE_EVENT.is_set()

    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive()


@pytest.mark.p0
def test_ai_snapshot_uses_real_answer_event_ids(database):
    document = _create()
    requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _claim_and_complete(requested["job_id"], _question_batch())
    question = document["protocol"]["questions"][0]
    answered = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ANSWER_QUESTION",
            {"question_id": question["question_id"], "selected_option_id": "companies", "custom_answer": None},
        ),
    )
    answer_event = BusinessDocumentEvent.get_by_id(answered["event_id"])
    document = BusinessDocumentService.get_document(TENANT, document["document_id"], AUTHOR)
    reassessment = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "REQUEST_INTAKE_ASSESSMENT", suffix="-again"),
    )
    snapshot = BusinessDocumentJob.get_by_id(reassessment["job_id"]).payload
    snapshot_answer = snapshot["protocol"]["questions"][0]["answer"]

    assert snapshot_answer["source_event_id"] == answer_event.id
    assert answer_event.event_type == "QuestionAnswered"
    assert answer_event.payload["question_id"] == question["question_id"]
    assert BusinessDocumentEvent.get_or_none(BusinessDocumentEvent.id == snapshot_answer["source_event_id"]) is not None
    snapshot_event_ids = [event["event_id"] for event in snapshot["source_events"]]
    persisted_event_ids = [event.id for event in BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).order_by(BusinessDocumentEvent.sequence.asc())]
    assert snapshot_event_ids == [event_id for event_id in persisted_event_ids if event_id != reassessment["event_id"]]
    assert reassessment["event_id"] not in snapshot_event_ids
    assert snapshot["idea_source_event_id"] == persisted_event_ids[0]


@pytest.mark.p0
def test_draft_proposal_accepts_pinned_idea_event_and_rejects_unknown_source(database):
    def prepare(title_suffix):
        document = BusinessDocumentService.create_document(
            TENANT,
            AUTHOR,
            {
                "schema_version": "1",
                "document_type": "business_requirements",
                "title": f"Draft source {title_suffix}",
                "idea": "Источник идеи должен быть трассируемым",
            },
        )
        assessment = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT", suffix=title_suffix))
        document = _claim_and_complete(assessment["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
        requested = BusinessDocumentService.execute_command(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT", suffix=title_suffix))
        job = BusinessDocumentJobQueue.claim(f"draft-worker-{title_suffix}", lease_ms=60_000)
        assert job is not None and job.id == requested["job_id"]
        return document, job

    document, job = prepare("valid")
    idea_event_id = job.payload["idea_source_event_id"]
    output = {
        "draft": _minimal_ast(),
        "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
        "proposals": [
            {
                "target_section_id": "5.5",
                "text": "Добавить метрику ошибок",
                "rationale": "Повышает проверяемость",
                "source_event_ids": [idea_event_id],
            }
        ],
    }
    completed = BusinessDocumentService.complete_job(TENANT, "draft-worker-valid", job.id, output, job.lease_token)
    proposal = BusinessDocumentProposal.get(BusinessDocumentProposal.document_id == document["document_id"])
    assert completed["lifecycle_state"] == "REVIEW"
    assert idea_event_id in proposal.source_event_ids
    assert BusinessDocumentEvent.get_by_id(idea_event_id).event_type == "DocumentCreated"

    invalid_document, invalid_job = prepare("invalid")
    invalid_output = {
        **output,
        "proposals": [
            {
                **output["proposals"][0],
                "source_event_ids": ["unknown-source-event"],
            }
        ],
    }
    with pytest.raises(BusinessDocumentError) as unknown:
        BusinessDocumentService.complete_job(TENANT, "draft-worker-invalid", invalid_job.id, invalid_output, invalid_job.lease_token)
    assert unknown.value.code == "SOURCE_NOT_IN_JOB_SNAPSHOT"
    assert BusinessDocumentRevision.select().where(BusinessDocumentRevision.document_id == invalid_document["document_id"]).count() == 0
    assert BusinessDocumentProposal.select().where(BusinessDocumentProposal.document_id == invalid_document["document_id"]).count() == 0


@pytest.mark.p0
def test_export_generation_is_idempotent_listable_downloadable_and_hash_verified(database):
    document = _agreed_document()
    storage = MemoryStorage()
    artifact, projection = _request_export(document, storage, "MARKDOWN")
    assert storage.put_count == 1
    assert artifact["revision_number"] == 1
    assert projection["current_revision"]["revision_id"] == document["current_revision"]["revision_id"]
    assert projection["current_revision"]["content_hash"] == document["current_revision"]["content_hash"]
    assert BusinessDocumentExportService.list_artifacts(TENANT, AUTHOR, document["document_id"]) == [artifact]

    metadata, content = BusinessDocumentExportService.download(TENANT, AUTHOR, document["document_id"], artifact["artifact_id"], storage=storage)
    assert metadata == artifact
    assert f"sha256:{hashlib.sha256(content).hexdigest()}" == artifact["content_hash"]
    with pytest.raises(BusinessDocumentError) as hidden:
        BusinessDocumentExportService.download(TENANT, "another-author", document["document_id"], artifact["artifact_id"], storage=storage)
    assert hidden.value.code == "EXPORT_NOT_FOUND"

    row = BusinessDocumentExportArtifact.get_by_id(artifact["artifact_id"])
    storage.objects[(row.storage_bucket, row.storage_key)] = b"tampered"
    with pytest.raises(BusinessDocumentError) as corrupt:
        BusinessDocumentExportService.download(TENANT, AUTHOR, document["document_id"], artifact["artifact_id"], storage=storage)
    assert corrupt.value.code == "EXPORT_STORAGE_CORRUPT"


@pytest.mark.p0
@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_repeated_export_atomically_repairs_poisoned_artifact_metadata(database, damage):
    document = _agreed_document()
    storage = MemoryStorage()
    requested = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "REQUEST_EXPORT",
            {"revision_id": document["current_revision"]["revision_id"], "format": "MARKDOWN"},
            suffix=f"-{damage}",
        ),
    )
    job = BusinessDocumentJobQueue.claim(f"repair-{damage}", lease_ms=60_000)
    assert job is not None and job.id == requested["job_id"]
    original = BusinessDocumentExportService.generate(job, storage=storage)
    original_row = BusinessDocumentExportArtifact.get_by_id(original["artifact_id"])
    original_key = (original_row.storage_bucket, original_row.storage_key)
    if damage == "missing":
        storage.objects.pop(original_key)
    else:
        storage.objects[original_key] = b"corrupt"

    repaired = BusinessDocumentExportService.generate(job, storage=storage)

    assert repaired["artifact_id"] != original["artifact_id"]
    assert BusinessDocumentExportArtifact.get_or_none(BusinessDocumentExportArtifact.id == original["artifact_id"]) is None
    assert (
        BusinessDocumentExportArtifact.select()
        .where(
            (BusinessDocumentExportArtifact.document_id == document["document_id"])
            & (BusinessDocumentExportArtifact.revision_id == document["current_revision"]["revision_id"])
            & (BusinessDocumentExportArtifact.export_format == "MARKDOWN")
        )
        .count()
        == 1
    )
    assert original_key not in storage.objects
    metadata, content = BusinessDocumentExportService.download(TENANT, AUTHOR, document["document_id"], repaired["artifact_id"], storage=storage)
    assert metadata == repaired
    assert f"sha256:{hashlib.sha256(content).hexdigest()}" == repaired["content_hash"]


@pytest.mark.p0
def test_export_requires_durable_storage_write_and_docx_is_valid_zip(database):
    class NoOpStorage(MemoryStorage):
        def put(self, bucket: str, key: str, content: bytes):
            self.put_count += 1

    document = _agreed_document()
    storage = NoOpStorage()
    requested = BusinessDocumentService.execute_command(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "REQUEST_EXPORT",
            {"revision_id": document["current_revision"]["revision_id"], "format": "MARKDOWN"},
        ),
    )
    job = BusinessDocumentJobQueue.claim("no-op-storage-worker", lease_ms=60_000)
    assert job is not None and job.id == requested["job_id"]
    with pytest.raises(BusinessDocumentError) as write_failure:
        BusinessDocumentExportService.generate(job, storage=storage)
    assert write_failure.value.code == "EXPORT_STORAGE_WRITE_FAILED"
    assert BusinessDocumentExportArtifact.select().count() == 0
    assert storage.objects == {}

    docx = BusinessDocumentExportService._render_docx(_minimal_ast())
    assert docx.startswith(b"PK\x03\x04")
    with zipfile.ZipFile(io.BytesIO(docx)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert "word/document.xml" in archive.namelist()


@pytest.mark.p0
def test_eva_wiki_exact_shape_escapes_content_and_excludes_protocol(database, monkeypatch):
    class FrozenDateTime:
        @classmethod
        def now(cls, timezone=None):
            value = DateTime(2026, 8, 26, 12, 0, tzinfo=UTC)
            return value if timezone is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(exports_module, "datetime", FrozenDateTime)
    policy = exports_module.rendering_policy()["eva_wiki"]
    ast = {
        "sections": [
            {
                "id": "1",
                "title": "Scope <trusted>",
                "blocks": [
                    {"type": "paragraph", "text": "A & B"},
                    {"type": "list", "items": ["one", "<two>"]},
                    {"type": "table", "headers": ["Key"], "rows": [["<value>"]]},
                    {"type": "plantuml", "source": "Alice -> Bob"},
                    {"type": "image", "alt": "diagram", "url": "https://example.test/a?x=1&y=2"},
                    {"type": "reference", "label": "Spec & source", "url": "https://example.test/spec"},
                ],
            }
        ]
    }

    rendered = BusinessDocumentExportService._render_eva_wiki(ast, 7)
    assert rendered == "\n".join(
        [
            f'<div class="business-requirements" data-template-version="{published_template()["template_version"]}" data-revision="7" data-generated-at="2026-08-26">',
            '<h2 data-id="br-r7-s1">1. Scope &lt;trusted&gt;</h2>',
            '<p data-id="br-r7-s1-b1">A &amp; B</p>',
            f'<ul class="{policy["root_list_class"]}" style="list-style-type: disc;" data-id="br-r7-s1-b2">'
            '<li data-id="br-r7-s1-b2-li1"><p data-id="br-r7-s1-b2-li1-p">one</p></li>'
            '<li data-id="br-r7-s1-b2-li2"><p data-id="br-r7-s1-b2-li2-p">&lt;two&gt;</p></li></ul>',
            f'<div class="{policy["table_wrapper_class"]}" data-macros="{policy["table_wrapper_macro"]}" data-id="br-r7-s1-b3">'
            '<table data-id="br-r7-s1-b3-table"><thead><tr data-id="br-r7-s1-b3-r0">'
            '<th colspan="1" rowspan="1" data-x="0" data-y="0" data-id="br-r7-s1-b3-h0">'
            '<p data-id="br-r7-s1-b3-h0-p">Key</p></th></tr></thead><tbody><tr data-id="br-r7-s1-b3-r1">'
            '<td colspan="1" rowspan="1" data-x="0" data-y="1" data-id="br-r7-s1-b3-c0-1">'
            '<p data-id="br-r7-s1-b3-c0-1-p">&lt;value&gt;</p></td></tr></tbody></table></div>',
            f'<pre class="{policy["plantuml_class"]}" data-id="br-r7-s1-b4"><code data-id="br-r7-s1-b4-code">Alice -&gt; Bob</code></pre>',
            '<img data-id="br-r7-s1-b5" alt="diagram" src="https://example.test/a?x=1&amp;y=2" />',
            '<p data-id="br-r7-s1-b6"><a data-id="br-r7-s1-b6-a" href="https://example.test/spec">Spec &amp; source</a></p>',
            "</div>",
        ]
    )
    assert "Комментарии автора" not in rendered
    assert "Предложения агента" not in rendered
    assert "Вопросы агента" not in rendered

    legitimate = {"sections": [{"id": "1", "title": "Scope", "blocks": [{"type": "paragraph", "text": "Комментарии автора учтены"}]}]}
    assert "Комментарии автора учтены" in BusinessDocumentExportService._render_eva_wiki(legitimate, 1)

    for unsafe_url in ("javascript:alert(1)", "data:text/html;base64,PHNjcmlwdD4="):
        unsafe = {
            "sections": [
                {
                    "id": "1",
                    "title": "Scope",
                    "blocks": [{"type": "reference", "label": "Unsafe", "url": unsafe_url}],
                }
            ]
        }
        with pytest.raises(BusinessDocumentError) as unsafe_error:
            BusinessDocumentExportService._render_eva_wiki(unsafe, 1)
        assert unsafe_error.value.code == "UNSAFE_EXPORT_URL"
