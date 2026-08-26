#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from peewee import SqliteDatabase

if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.errors import BusinessDocumentError
from api.apps.business_documents.eva_changes import EvaChangeState, EvaDocumentChangeService
from api.db.db_models import BusinessDocumentEvaChange, BusinessDocumentEvaChangeEvent
from common.time_utils import current_timestamp


TENANT = "tenant-1"
AUTHOR = "author-1"
CONNECTOR = SimpleNamespace(id="connector-1", name="EVA Wiki")


class FakeEvaClient:
    def __init__(self):
        self.publish_calls = 0
        self.document = {
            "id": "CmfDocument:doc-1",
            "name": "Переводы одной кнопкой",
            "code": "BR-42",
            "project_id": "CmfProject:portal",
            "version": "1|CmfVersion:v1|2026-08-26T09:00:00+03:00",
            "modified_at": "2026-08-26T09:00:00+03:00",
            "web_url": "https://eva.example.com/project/Document/BR-42",
            "html": "<h1>Бизнес-требования</h1><h2>Цель</h2><p>Старый текст.</p>",
            "draft_html": "",
        }

    def get_document_for_edit(self, document_id):
        assert document_id == self.document["id"]
        return dict(self.document)

    def update_document_draft(self, document_id, html):
        assert document_id == self.document["id"]
        self.document["draft_html"] = html
        return True

    def publish_document(self, document_id):
        assert document_id == self.document["id"]
        self.publish_calls += 1
        self.document["html"] = self.document["draft_html"]
        self.document["version"] = "2|CmfVersion:v2|2026-08-26T10:00:00+03:00"
        return True


@pytest.fixture()
def database():
    database = SqliteDatabase(":memory:")
    tables = [BusinessDocumentEvaChange, BusinessDocumentEvaChangeEvent]
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)
        database.close()


def _create(client):
    with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
        return EvaDocumentChangeService.create_change(
            TENANT,
            AUTHOR,
            {
                "connector_id": CONNECTOR.id,
                "document_id": client.document["id"],
                "change_summary": "Уточнить ожидаемый результат и ограничения.",
            },
        )


def test_full_eva_change_flow_keeps_publish_as_separate_action(database):
    client = FakeEvaClient()
    created = _create(client)

    assert created["workflow_state"] == "EDITING"
    assert created["diff"]["changed"] is False
    assert created["allowed_actions"] == ["SAVE_DRAFT"]

    draft = EvaDocumentChangeService.save_draft(
        TENANT,
        AUTHOR,
        created["change_id"],
        {
            "expected_state_version": created["state_version"],
            "draft_markdown": "# Бизнес-требования\n\n## Цель\n\nНовый проверяемый текст.",
        },
    )
    assert draft["diff"]["changed_sections"] == 1
    assert draft["diff"]["added_lines"] == 1
    assert draft["diff"]["removed_lines"] == 1
    assert draft["allowed_actions"] == ["SAVE_DRAFT", "APPROVE"]

    approved = EvaDocumentChangeService.approve(
        TENANT,
        AUTHOR,
        created["change_id"],
        {"expected_state_version": draft["state_version"]},
    )
    assert approved["workflow_state"] == "APPROVED"
    assert approved["allowed_actions"] == ["SAVE_DRAFT", "PREPARE_EVA_DRAFT"]
    assert client.document["draft_html"] == ""

    with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
        prepared = EvaDocumentChangeService.prepare_eva_draft(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": approved["state_version"]},
        )
    assert prepared["workflow_state"] == "EVA_DRAFT_READY"
    assert prepared["allowed_actions"] == ["PUBLISH_EVA"]
    assert "Новый проверяемый текст" in client.document["draft_html"]
    assert "Старый текст" in client.document["html"]

    with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
        published = EvaDocumentChangeService.publish(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": prepared["state_version"]},
        )
    assert published["workflow_state"] == "PUBLISHED"
    assert published["allowed_actions"] == []
    assert published["published_version"].startswith("2|")
    assert "Новый проверяемый текст" in client.document["html"]
    assert [event["event_type"] for event in published["events"]] == [
        "CHANGE_REQUEST_CREATED",
        "DRAFT_UPDATED",
        "DRAFT_APPROVED",
        "EVA_DRAFT_SAVED",
        "EVA_DOCUMENT_PUBLISHED",
    ]


def test_prepare_rejects_changed_published_source_and_keeps_it_untouched(database):
    client = FakeEvaClient()
    created = _create(client)
    draft = EvaDocumentChangeService.save_draft(
        TENANT,
        AUTHOR,
        created["change_id"],
        {
            "expected_state_version": created["state_version"],
            "draft_markdown": "# Бизнес-требования\n\n## Цель\n\nНовый текст.",
        },
    )
    approved = EvaDocumentChangeService.approve(
        TENANT,
        AUTHOR,
        created["change_id"],
        {"expected_state_version": draft["state_version"]},
    )
    client.document["html"] = "<h1>Бизнес-требования</h1><p>Изменено другим автором.</p>"

    with (
        patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)),
        pytest.raises(BusinessDocumentError) as exc_info,
    ):
        EvaDocumentChangeService.prepare_eva_draft(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": approved["state_version"]},
        )

    assert exc_info.value.code == "EVA_SOURCE_VERSION_CONFLICT"
    restored = EvaDocumentChangeService.get_change(TENANT, AUTHOR, created["change_id"])
    assert restored["workflow_state"] == "APPROVED"
    assert restored["last_error"]["code"] == "EVA_SOURCE_VERSION_CONFLICT"
    assert client.document["draft_html"] == ""


def test_markdown_html_is_sanitized_before_eva_draft(database):
    client = FakeEvaClient()
    created = _create(client)
    draft = EvaDocumentChangeService.save_draft(
        TENANT,
        AUTHOR,
        created["change_id"],
        {
            "expected_state_version": created["state_version"],
            "draft_markdown": "# Title\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(2))",
        },
    )

    stored = BusinessDocumentEvaChange.get_by_id(draft["change_id"])
    assert "<script" not in stored.draft_html
    assert "javascript:" not in stored.draft_html


def test_publish_recovers_when_eva_committed_before_client_error(database):
    client = FakeEvaClient()
    created = _create(client)
    draft = EvaDocumentChangeService.save_draft(
        TENANT,
        AUTHOR,
        created["change_id"],
        {
            "expected_state_version": created["state_version"],
            "draft_markdown": "# Бизнес-требования\n\n## Цель\n\nОпубликованный текст.",
        },
    )
    approved = EvaDocumentChangeService.approve(
        TENANT,
        AUTHOR,
        created["change_id"],
        {"expected_state_version": draft["state_version"]},
    )
    with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
        prepared = EvaDocumentChangeService.prepare_eva_draft(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": approved["state_version"]},
        )

    publish_document = client.publish_document

    def publish_then_fail(document_id):
        publish_document(document_id)
        raise RuntimeError("response lost after EVA committed the publish")

    with (
        patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)),
        patch.object(client, "publish_document", side_effect=publish_then_fail),
    ):
        published = EvaDocumentChangeService.publish(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": prepared["state_version"]},
        )

    assert published["workflow_state"] == "PUBLISHED"
    assert published["published_version"].startswith("2|")
    assert client.publish_calls == 1


def test_stale_publishing_reservation_can_resume_after_process_loss(database):
    client = FakeEvaClient()
    created = _create(client)
    draft = EvaDocumentChangeService.save_draft(
        TENANT,
        AUTHOR,
        created["change_id"],
        {
            "expected_state_version": created["state_version"],
            "draft_markdown": "# Бизнес-требования\n\n## Цель\n\nТекст после восстановления.",
        },
    )
    approved = EvaDocumentChangeService.approve(
        TENANT,
        AUTHOR,
        created["change_id"],
        {"expected_state_version": draft["state_version"]},
    )
    with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
        prepared = EvaDocumentChangeService.prepare_eva_draft(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": approved["state_version"]},
        )

    reserved_version = prepared["state_version"] + 1
    BusinessDocumentEvaChange.update(
        workflow_state="PUBLISHING",
        state_version=reserved_version,
        update_time=current_timestamp(),
    ).where(BusinessDocumentEvaChange.id == created["change_id"]).execute()

    busy = EvaDocumentChangeService.get_change(TENANT, AUTHOR, created["change_id"])
    assert busy["allowed_actions"] == []
    with (
        patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)),
        pytest.raises(BusinessDocumentError) as exc_info,
    ):
        EvaDocumentChangeService.publish(
            TENANT,
            AUTHOR,
            created["change_id"],
            {"expected_state_version": reserved_version},
        )
    assert exc_info.value.code == "EVA_CHANGE_BUSY"

    client.document["html"] = client.document["draft_html"]
    client.document["version"] = "2|CmfVersion:v2|2026-08-26T10:00:00+03:00"
    reserved = BusinessDocumentEvaChange.get_by_id(created["change_id"])
    retry_time = int(reserved.update_time) + 120_001

    with patch("api.apps.business_documents.eva_changes.current_timestamp", return_value=retry_time):
        retryable = EvaDocumentChangeService.get_change(TENANT, AUTHOR, created["change_id"])
        assert retryable["workflow_state"] == "PUBLISHING"
        assert retryable["operation_retry_after_ms"] == 0
        assert retryable["allowed_actions"] == ["PUBLISH_EVA"]
        with patch.object(EvaDocumentChangeService, "_connector", return_value=(CONNECTOR, client)):
            published = EvaDocumentChangeService.publish(
                TENANT,
                AUTHOR,
                created["change_id"],
                {"expected_state_version": reserved_version},
            )

    assert published["workflow_state"] == "PUBLISHED"
    assert client.publish_calls == 0
    assert "EXTERNAL_OPERATION_RETRIED" in [event["event_type"] for event in published["events"]]

    EvaDocumentChangeService._restore_after_external_failure(
        created["change_id"],
        reserved_version,
        EvaChangeState.PUBLISHING,
        EvaChangeState.EVA_DRAFT_READY,
        BusinessDocumentError("LATE_FAILURE", "late failure", 502),
    )
    assert EvaDocumentChangeService.get_change(TENANT, AUTHOR, created["change_id"])["workflow_state"] == "PUBLISHED"
