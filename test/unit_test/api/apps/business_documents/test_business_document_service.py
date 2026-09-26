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

import json
from common.time_utils import current_timestamp
from copy import deepcopy
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from peewee import IntegrityError, SqliteDatabase

# Importing a submodule normally executes api.apps, which boots the complete
# server and external document store. Domain tests deliberately isolate that
# package boundary.
if "api.apps" not in sys.modules:
    api_apps = ModuleType("api.apps")
    api_apps.__path__ = [str(Path(__file__).resolve().parents[5] / "api" / "apps")]
    sys.modules["api.apps"] = api_apps

from api.apps.business_documents.assets import import_document_markdown, published_template, render_document_ast, validate_document_ast
from business_documents.domain.content import render_section_text, section_hash
from api.apps.business_documents.adapters.assignment import assign_business_document
from business_documents.application.errors import BusinessDocumentError
from api.apps.business_documents.runtime import document_queries, document_writer, job_completion
from api.apps.business_documents.runtime import document_commands
from api.apps.business_documents.runtime import eva_synchronization
from test.unit_test.api.apps.business_documents.helpers import append_document_event
from api.apps.business_documents.runtime import document_creation
from api.apps.business_documents.adapters.persistence import DOCUMENT_TABLES
from api.apps.business_documents.runtime import document_deletion, change_user_role
from api.apps.business_documents.ai import BusinessDocumentAI
from api.apps.business_documents.stream_events import BusinessDocumentStreamEvents
from api.apps.business_documents.worker import BusinessDocumentJobQueue, BusinessDocumentWorker
from api.db.db_models import (
    BusinessDocument,
    BusinessDocumentAnswer,
    BusinessDocumentCatalog,
    BusinessDocumentCommand,
    BusinessDocumentComment,
    BusinessDocumentEvent,
    BusinessDocumentEvaBinding,
    BusinessDocumentJob,
    BusinessDocumentJobStreamEvent,
    BusinessDocumentProposal,
    BusinessDocumentProposalDecision,
    BusinessDocumentQuestion,
    BusinessDocumentRevision,
    User,
)
from business_documents.domain.catalog import load_document_catalog
from test.unit_test.api.apps.business_documents.helpers import VALID_ACTIVITY_SCENARIO, required_section_blocks


TENANT = "tenant-1"
AUTHOR = "author-1"


@pytest.fixture()
def database():
    database = SqliteDatabase(":memory:")
    tables = (*DOCUMENT_TABLES, User)
    with database.bind_ctx(tables, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(tables)
        yield database
        database.drop_tables(tables)
        database.close()


def _create(**overrides):
    request = {
        "schema_version": "1",
        "document_type": "business_requirements",
        "title": "Переводы одной кнопкой",
        "idea": "Создать безопасный сервис переводов",
        **overrides,
    }
    return document_creation.execute(TENANT, AUTHOR, request)


def test_exported_business_document_markdown_can_be_imported_without_an_llm_rewrite():
    markdown = render_document_ast(_draft())

    imported = import_document_markdown(markdown)

    assert render_document_ast(imported) == markdown
    conceptual = next(section for section in imported["sections"] if section["id"] == "4.1")
    assert any(block["type"] == "plantuml" for block in conceptual["blocks"])


def test_eva_import_recovers_plantuml_after_html_round_trip_drops_the_fence_language():
    markdown = render_document_ast(_draft()).replace("```plantuml\n", "```\n")

    imported = import_document_markdown(markdown)

    conceptual = next(section for section in imported["sections"] if section["id"] == "4.1")
    assert any(block["type"] == "plantuml" for block in conceptual["blocks"])


def test_eva_import_rejects_a_page_outside_the_business_document_template():
    with pytest.raises(BusinessDocumentError) as caught:
        import_document_markdown("# Произвольная страница\n\nТекст")

    assert caught.value.code == "EVA_DOCUMENT_TEMPLATE_MISMATCH"


@pytest.mark.p0
def test_catalog_sync_replaces_previous_source_rows_and_v3_derives_the_title(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    catalog = load_document_catalog()
    from api.db.db_models import migrate_business_document_catalog

    monkeypatch.setattr(EvaDocumentChangeService, "find_title_matches", staticmethod(lambda *_args: []))
    BusinessDocumentCatalog.create(
        id="obsolete-from-prior-catalog",
        title="Устаревшая запись",
        capability_level="L5",
        hierarchy={},
        details={},
        source_id=catalog["source_id"],
        source_version="v15_L5",
        source_sha256="0" * 64,
        sort_order=99,
    )
    prior_document = document_creation.execute(
        TENANT,
        AUTHOR,
        {
            "schema_version": "3",
            "document_type": "business_requirements",
            "catalog_entry_id": "obsolete-from-prior-catalog",
            "idea": "Сохранить документ после замены справочника",
        },
    )
    migrate_business_document_catalog()
    first_sync_timestamps = {row.id: (row.update_time, row.update_date) for row in BusinessDocumentCatalog.select()}
    migrate_business_document_catalog()
    first = catalog["items"][0]
    assert BusinessDocumentCatalog.select().where(BusinessDocumentCatalog.is_active == True).count() == len(catalog["items"])  # noqa: E712
    assert {row.id: (row.update_time, row.update_date) for row in BusinessDocumentCatalog.select()} == first_sync_timestamps
    obsolete_catalog_entry = BusinessDocumentCatalog.get_by_id("obsolete-from-prior-catalog")
    assert obsolete_catalog_entry.is_active is False
    assert BusinessDocument.get_by_id(prior_document["document_id"]).title == "Устаревшая запись"
    BusinessDocumentCatalog.create(
        id="L4-test",
        title="Не разрешённый уровень",
        capability_level="L4",
        hierarchy={},
        details={},
        source_id="test",
        source_version="1",
        source_sha256="0" * 64,
        sort_order=99,
    )
    BusinessDocumentCatalog.create(
        id="inactive-l5-test",
        title="Неактивная запись L5",
        capability_level="L5",
        hierarchy={},
        details={},
        source_id="test",
        source_version="1",
        source_sha256="0" * 64,
        sort_order=100,
        is_active=False,
    )

    listed = document_queries.list_catalog()
    created = document_creation.execute(
        TENANT,
        AUTHOR,
        {
            "schema_version": "3",
            "document_type": "business_requirements",
            "catalog_entry_id": first["id"],
            "idea": "Создать документ из разрешённого справочника",
        },
    )

    assert [item["id"] for item in catalog["items"]] == ["L2-01.01.04.01.01"]
    assert listed["total"] == 1
    assert all(item["capability_level"] == "L5" for item in listed["items"])
    assert created["catalog_entry_id"] == first["id"]
    assert created["title"] == first["title"]


@pytest.mark.p0
def test_v3_imports_the_only_unbound_title_match_as_revision_one(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import migrate_business_document_catalog

    migrate_business_document_catalog()
    first = load_document_catalog()["items"][0]
    observed_titles = []
    page_url = "https://eva.example.com/project/Document/BR-42"
    binding = {
        "page_url": page_url,
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "connector-1",
        "eva_origin": "https://eva-api.example.com",
        "project_id": "CmfProject:portal",
        "document_id": "CmfDocument:doc-1",
        "document_code": "BR-42",
        "document_name": first["title"],
        "remote_version": "7",
        "remote_content_hash": "sha256:remote",
    }
    remote_markdown = render_document_ast(_draft())
    monkeypatch.setattr(
        EvaDocumentChangeService,
        "find_title_matches",
        staticmethod(
            lambda _actor_id, title: (
                observed_titles.append(title)
                or [
                    {
                        "id": binding["document_id"],
                        "name": title,
                        "code": binding["document_code"],
                        "project_id": binding["project_id"],
                        "web_url": page_url,
                        "breadcrumbs": [],
                        "hierarchy": title,
                        "connector_id": binding["connector_id"],
                        "connector_name": "EVA Wiki",
                        "eva_origin": binding["eva_origin"],
                    }
                ]
            ),
        ),
    )
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: binding))
    monkeypatch.setattr(EvaDocumentChangeService, "read_connected_page", staticmethod(lambda *_args: (binding, remote_markdown)))

    request = {
        "schema_version": "3",
        "document_type": "business_requirements",
        "catalog_entry_id": first["id"],
        "idea": "Проверить существующий документ EVA",
    }
    created = document_creation.execute(TENANT, AUTHOR, request)

    assert observed_titles == [first["title"]]
    assert created["lifecycle_state"] == "REVIEW"
    assert created["active_review_cycle"] == 1
    assert created["current_revision"]["body_markdown"] == remote_markdown
    assert created["eva_binding"]["last_pulled_content_hash"] == "sha256:remote"
    assert "ADD_COMMENT" in created["allowed_commands"]
    assert "REQUEST_REVIEW_ASSESSMENT" in created["allowed_commands"]
    assert BusinessDocumentJob.select().count() == 0
    imported = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == created["document_id"]) & (BusinessDocumentEvent.event_type == "EvaDocumentImported"))
    assert created["current_revision"]["source_event_ids"] == [imported.id]

    document_commands.execute(
        TENANT,
        AUTHOR,
        created["document_id"],
        _command(
            created,
            "ADD_COMMENT",
            {
                "revision_id": created["current_revision"]["revision_id"],
                "section_id": None,
                "text": "Уточнить исходную версию из EVA",
                "anchor": None,
            },
        ),
    )
    commented = document_queries.get_document(created["document_id"], AUTHOR)
    assert commented["protocol"]["comments"][0]["text"] == "Уточнить исходную версию из EVA"
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        created["document_id"],
        _command(commented, "REQUEST_REVIEW_ASSESSMENT"),
    )
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).job_type == "ASSESS_REVIEW"


@pytest.mark.p0
def test_v3_requires_selection_when_multiple_unbound_eva_pages_match(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import migrate_business_document_catalog

    migrate_business_document_catalog()
    first = load_document_catalog()["items"][0]
    matches = [
        {
            "id": f"eva-{index}",
            "name": first["title"],
            "web_url": f"https://eva.example.com/project/Document/BR-{index}",
        }
        for index in (1, 2)
    ]
    monkeypatch.setattr(EvaDocumentChangeService, "find_title_matches", staticmethod(lambda *_args: matches))

    with pytest.raises(BusinessDocumentError) as caught:
        document_creation.execute(
            TENANT,
            AUTHOR,
            {
                "schema_version": "3",
                "document_type": "business_requirements",
                "catalog_entry_id": first["id"],
                "idea": "Выбрать одну из страниц",
            },
        )

    assert caught.value.code == "EVA_BINDING_DECISION_REQUIRED"
    assert len(caught.value.details["matches"]) == 2


@pytest.mark.p0
def test_v3_does_not_import_an_occupied_title_match(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import migrate_business_document_catalog

    migrate_business_document_catalog()
    first = load_document_catalog()["items"][0]
    match = {
        "name": first["title"],
        "web_url": "https://eva.example.com/project/Document/BR-42",
        "binding_available": False,
        "linked_document": {"document_id": "other-document", "title": "Другой документ"},
    }
    monkeypatch.setattr(EvaDocumentChangeService, "find_title_matches", staticmethod(lambda *_args: [match]))
    monkeypatch.setattr(document_creation, "_matches_with_occupancy", lambda matches: matches)
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: pytest.fail("Occupied page must not be imported")))

    with pytest.raises(BusinessDocumentError) as caught:
        document_creation.execute(
            TENANT,
            AUTHOR,
            {
                "schema_version": "3",
                "document_type": "business_requirements",
                "catalog_entry_id": first["id"],
                "idea": "Проверить занятую страницу",
            },
        )

    assert caught.value.code == "EVA_BINDING_DECISION_REQUIRED"
    assert caught.value.details["matches"] == [match]
    assert BusinessDocument.select().count() == 0


@pytest.mark.p0
def test_v3_explicit_skip_does_not_search_for_or_import_an_eva_page(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import migrate_business_document_catalog

    migrate_business_document_catalog()
    first = load_document_catalog()["items"][0]
    monkeypatch.setattr(
        EvaDocumentChangeService,
        "find_title_matches",
        staticmethod(lambda *_args: pytest.fail("SKIP must not search EVA")),
    )

    created = document_creation.execute(
        TENANT,
        AUTHOR,
        {
            "schema_version": "3",
            "document_type": "business_requirements",
            "catalog_entry_id": first["id"],
            "idea": "Создать без EVA",
            "eva_decision": {"mode": "SKIP"},
        },
    )

    assert created["eva_binding"] is None
    assert created["current_revision"] is None


@pytest.mark.p0
def test_v3_template_mismatch_does_not_leave_a_partial_document_or_binding(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import migrate_business_document_catalog

    migrate_business_document_catalog()
    first = load_document_catalog()["items"][0]
    page_url = "https://eva.example.com/project/Document/BR-42"
    binding = {
        "page_url": page_url,
        "status": "CONNECTED",
        "connector_id": "connector-1",
        "eva_origin": "https://eva-api.example.com",
        "project_id": "CmfProject:portal",
        "document_id": "CmfDocument:doc-1",
        "document_name": first["title"],
    }
    monkeypatch.setattr(
        EvaDocumentChangeService,
        "find_title_matches",
        staticmethod(lambda *_args: [{"name": first["title"], "web_url": page_url}]),
    )
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: binding))
    monkeypatch.setattr(
        EvaDocumentChangeService,
        "read_connected_page",
        staticmethod(lambda *_args: (binding, "# Произвольная страница\n\nТекст")),
    )

    with pytest.raises(BusinessDocumentError) as caught:
        document_creation.execute(
            TENANT,
            AUTHOR,
            {
                "schema_version": "3",
                "document_type": "business_requirements",
                "catalog_entry_id": first["id"],
                "idea": "Импортировать страницу",
                "eva_page_url": page_url,
                "eva_decision": {"mode": "BIND"},
            },
        )

    assert caught.value.code == "EVA_DOCUMENT_TEMPLATE_MISMATCH"
    assert BusinessDocument.select().count() == 0
    assert BusinessDocumentEvaBinding.select().count() == 0
    assert BusinessDocumentEvent.select().count() == 0


@pytest.mark.p0
@pytest.mark.parametrize("catalog_entry_id", ["missing", "L4-test"])
def test_v3_rejects_catalog_entries_that_are_not_active_l5(database, catalog_entry_id):
    from api.db.db_models import migrate_business_document_catalog

    migrate_business_document_catalog()
    BusinessDocumentCatalog.create(
        id="L4-test",
        title="Не разрешённый уровень",
        capability_level="L4",
        hierarchy={},
        details={},
        source_id="test",
        source_version="1",
        source_sha256="0" * 64,
        sort_order=99,
    )

    with pytest.raises(BusinessDocumentError) as caught:
        document_creation.execute(
            TENANT,
            AUTHOR,
            {
                "schema_version": "3",
                "document_type": "business_requirements",
                "catalog_entry_id": catalog_entry_id,
                "idea": "Недопустимый документ",
            },
        )

    assert caught.value.code == "DOCUMENT_CATALOG_ENTRY_NOT_ALLOWED"
    assert BusinessDocument.select().count() == 0


@pytest.mark.p0
def test_document_titles_are_unique_after_unicode_case_and_whitespace_normalization(database):
    first = _create(title="  Требования   CRM  ")

    with pytest.raises(BusinessDocumentError) as caught:
        _create(title="ТРЕБОВАНИЯ CRM")

    assert caught.value.code == "DOCUMENT_TITLE_ALREADY_EXISTS"
    assert BusinessDocument.select().count() == 1
    assert first["title"] == "Требования CRM"
    created_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == first["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
    assert created_event.payload["submitted_title"] == "  Требования   CRM  "
    assert created_event.payload["title"] == "Требования CRM"


@pytest.mark.p0
def test_v1_cannot_bind_an_eva_page_without_the_v2_confirmation_contract(database):
    with pytest.raises(BusinessDocumentError) as caught:
        _create(eva_page_url="https://eva.example.com/project/Document/BR-42")

    assert caught.value.code == "EVA_BINDING_REQUIRES_SCHEMA_V2"
    assert BusinessDocument.select().count() == 0


@pytest.mark.p0
def test_v2_creation_requires_an_explicit_decision_for_matching_eva_pages(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    match = {
        "id": "CmfDocument:doc-1",
        "name": "Переводы одной кнопкой",
        "code": "BR-42",
        "project_id": "CmfProject:portal",
        "web_url": "https://eva.example.com/project/Document/BR-42",
        "breadcrumbs": [{"id": "CmfDocument:doc-1", "name": "Переводы одной кнопкой", "web_url": "https://eva.example.com/project/Document/BR-42"}],
        "hierarchy": "Переводы одной кнопкой",
        "connector_id": "connector-1",
        "connector_name": "EVA Wiki",
        "eva_origin": "https://eva.example.com",
    }
    monkeypatch.setattr(EvaDocumentChangeService, "find_title_matches", staticmethod(lambda *_args: [match]))

    with pytest.raises(BusinessDocumentError) as caught:
        _create(schema_version="2")

    assert caught.value.code == "EVA_BINDING_DECISION_REQUIRED"
    assert caught.value.details["matches"][0]["binding_available"] is True
    assert BusinessDocument.select().count() == 0


@pytest.mark.p0
def test_v2_skip_creates_the_document_without_an_eva_binding(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    monkeypatch.setattr(
        EvaDocumentChangeService,
        "find_title_matches",
        staticmethod(lambda *_args: pytest.fail("SKIP must not repeat EVA title search")),
    )

    created = _create(schema_version="2", eva_decision={"mode": "SKIP"})

    assert created["eva_binding"] is None
    assert BusinessDocumentEvaBinding.select().count() == 0


@pytest.mark.p0
def test_v2_binding_requires_replace_confirmation_and_matching_remote_title(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    binding = {
        "page_url": "https://eva.example.com/project/Document/BR-42",
        "status": "CONNECTED",
        "connector_id": "connector-1",
        "eva_origin": "https://eva-api.example.com",
        "project_id": "CmfProject:portal",
        "document_id": "CmfDocument:doc-1",
        "document_name": "Переводы одной кнопкой",
    }
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: binding))

    with pytest.raises(BusinessDocumentError) as missing_confirmation:
        _create(
            schema_version="2",
            eva_page_url=binding["page_url"],
            eva_decision={"mode": "BIND", "confirm_replace": False},
        )

    assert missing_confirmation.value.code == "EVA_REPLACE_CONFIRMATION_REQUIRED"
    created = _create(
        schema_version="2",
        eva_page_url=binding["page_url"],
        eva_decision={"mode": "BIND", "confirm_replace": True},
    )
    assert created["eva_binding"]["document_id"] == "CmfDocument:doc-1"


@pytest.mark.p0
def test_one_eva_page_cannot_be_linked_to_different_documents(database, monkeypatch):
    binding = {
        "page_url": "https://eva.example.com/project/Document/BR-42",
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "connector-1",
        "eva_origin": "https://eva-api.example.com",
        "project_id": "CmfProject:portal",
        "document_id": "CmfDocument:doc-1",
        "document_code": "BR-42",
        "document_name": "Переводы одной кнопкой",
    }
    monkeypatch.setattr(document_creation, "_resolve_binding", lambda *_args: binding)
    confirmed = {"mode": "BIND", "confirm_replace": True}
    _create(schema_version="2", eva_page_url=binding["page_url"], eva_decision=confirmed)

    with pytest.raises(BusinessDocumentError) as caught:
        _create(title="Другой документ", schema_version="2", eva_page_url=binding["page_url"], eva_decision=confirmed)

    assert caught.value.code == "EVA_PAGE_ALREADY_LINKED"
    assert BusinessDocument.select().count() == 1
    assert BusinessDocumentEvaBinding.select().count() == 1


def test_legacy_event_backfill_populates_unique_eva_binding_projection(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService
    from api.db.db_models import migrate_business_document_eva_bindings

    binding = {
        "page_url": "https://eva.example.com/project/Document/BR-42",
        "status": "CONNECTED",
        "connector_id": "connector-1",
        "eva_origin": "https://eva-api.example.com",
        "project_id": "CmfProject:portal",
        "document_id": "CmfDocument:doc-1",
        "document_name": "Переводы одной кнопкой",
    }
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: binding))
    created = _create(
        schema_version="2",
        eva_page_url=binding["page_url"],
        eva_decision={"mode": "BIND", "confirm_replace": True},
    )
    pull_event_id = append_document_event(
        created["document_id"],
        2,
        "EvaDocumentPulled",
        "USER",
        AUTHOR,
        {
            "remote_version": "7",
            "remote_content_hash": "sha256:remote-content",
            "review_cycle": 3,
        },
        "legacy-pull",
    )
    BusinessDocumentEvaBinding.delete().execute()

    migrate_business_document_eva_bindings()
    migrate_business_document_eva_bindings()

    projection = BusinessDocumentEvaBinding.get_by_id(created["document_id"])
    assert projection.binding["page_url"] == binding["page_url"]
    assert projection.binding["remote_version"] == "7"
    assert projection.binding["last_pulled_content_hash"] == "sha256:remote-content"
    assert projection.binding["last_pull_event_id"] == pull_event_id
    assert projection.binding["last_pull_review_cycle"] == 3


def test_matching_eva_page_reports_the_document_that_already_owns_it(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    binding = {
        "page_url": "https://eva.example.com/project/Document/BR-42",
        "status": "CONNECTED",
        "connector_id": "connector-1",
        "eva_origin": "https://eva-api.example.com",
        "project_id": "CmfProject:portal",
        "document_id": "CmfDocument:doc-1",
        "document_name": "Переводы одной кнопкой",
    }
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: binding))
    owner = _create(
        schema_version="2",
        eva_page_url=binding["page_url"],
        eva_decision={"mode": "BIND", "confirm_replace": True},
    )
    match = {
        "id": binding["document_id"],
        "name": "Другой документ",
        "code": "BR-42",
        "project_id": binding["project_id"],
        "web_url": binding["page_url"],
        "breadcrumbs": [],
        "hierarchy": "Проекты › Другой документ",
        "connector_id": binding["connector_id"],
        "connector_name": "EVA Wiki",
        "eva_origin": binding["eva_origin"],
    }
    monkeypatch.setattr(EvaDocumentChangeService, "find_title_matches", staticmethod(lambda *_args: [match]))

    with pytest.raises(BusinessDocumentError) as caught:
        _create(title="Другой документ", schema_version="2")

    assert caught.value.code == "EVA_BINDING_DECISION_REQUIRED"
    occupied = caught.value.details["matches"][0]
    assert occupied["binding_available"] is False
    assert occupied["linked_document"] == {"document_id": owner["document_id"], "title": "Переводы одной кнопкой"}


def _command(document, command_type, payload=None, *, key=None, command_id=None, expected=None):
    return {
        "schema_version": "1",
        "command_id": command_id or f"cmd-{command_type.lower()}-{document['state_version']}",
        "idempotency_key": key or f"idem-{command_type.lower()}-{document['state_version']}",
        "expected_state_version": expected or document["state_version"],
        "type": command_type,
        "payload": payload or {},
    }


def _complete(job_id, output, worker="worker-1"):
    job = BusinessDocumentJobQueue.claim(worker)
    assert job is not None and job.id == job_id
    return job_completion.complete(TENANT, worker, job_id, output, job.lease_token)


def _question_batch(stage="INTAKE", semantic_tag="audience"):
    return {
        "schema_version": "1",
        "outcome": "NEEDS_INPUT",
        "questions": [
            {
                "semantic_tag": semantic_tag,
                "stage": stage,
                "target_section_id": "3.1",
                "text": "Кто будет пользоваться сервисом?",
                "options": [
                    {"option_id": "individuals", "label": "Физические лица"},
                    {"option_id": "companies", "label": "Юридические лица"},
                ],
                "allow_custom_answer": True,
            }
        ],
    }


def _draft(suffix="исходная версия"):
    template = published_template()
    return {
        "schema_version": "1",
        "document_type": "business_requirements",
        "template_version": template["template_version"],
        "sections": [
            {
                "id": section["id"],
                "title": section["title"],
                "blocks": required_section_blocks(section["id"], f"Раздел {section['id']}: {suffix}."),
            }
            for section in template["sections"]
        ],
    }


def _request_and_complete_draft(document, *, review_questions=None, proposals=None):
    if "REQUEST_DRAFT" not in document["allowed_commands"]:
        assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
        document = _complete(
            assessment["job_id"],
            {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
        )
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    created_event_id = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentCreated")).id
    normalized_proposals = [
        {
            "target_section_id": proposal.get("target_section_id", "5.5"),
            "text": proposal["text"],
            "rationale": proposal.get("rationale", "Уточняет проверяемость требований"),
            "source_event_ids": proposal.get("source_event_ids", [created_event_id]),
        }
        for proposal in (proposals or [])
    ]
    return _complete(
        requested["job_id"],
        {
            "draft": _draft(),
            "review_questions": review_questions or {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
            "proposals": normalized_proposals,
        },
    )


def _complete_review_assessment(document, *, comment_disposition="CONFIRMED_CHANGE"):
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))
    dispositions = [{"comment_event_id": comment["source_event_id"], "disposition": comment_disposition} for comment in document["protocol"]["comments"]]
    return _complete(
        requested["job_id"],
        {"schema_version": "1", "questions": [], "proposals": [], "comment_dispositions": dispositions},
    )


@pytest.mark.p0
def test_change_preview_requires_explicit_confirmation_and_shows_section_diff(database):
    document = _request_and_complete_draft(_create())
    base = document["current_revision"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": base["revision_id"], "section_id": None, "text": "Уточнить контроль ошибок", "anchor": None}),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    comment_event_id = document["protocol"]["comments"][0]["source_event_id"]
    assert "PREPARE_CHANGES" in document["allowed_commands"]
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": base["revision_id"]}))
    before = next(section for section in base["document_ast"]["sections"] if section["id"] == "5.5")
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": base["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [
                    {
                        "operation_id": "op-preview-1",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": "5.5",
                        "expected_section_hash": section_hash(before),
                        "source_event_ids": [comment_event_id],
                        "content": {"blocks": [{"type": "paragraph", "text": "Новый контроль ошибок"}]},
                    }
                ],
            }
        },
    )
    assert document["operation_state"] == "IDLE"
    assert document["current_revision"]["revision_id"] == base["revision_id"]
    assert BusinessDocumentRevision.select().count() == 1
    assert document["change_preview"]["job_id"] == requested["job_id"]
    assert "CONFIRM_PREPARED_CHANGES" in document["allowed_commands"]
    from playhouse.test_utils import count_queries

    with count_queries(only_select=True) as preview_reads:
        preview = document_queries.get_change_preview(document["document_id"], requested["job_id"])
    assert preview_reads.count == 3
    assert preview["sections"][0]["section_id"] == "5.5"
    assert preview["sections"][0]["after"] == "Новый контроль ошибок"
    assert preview["sections"][0]["before_evidence_refs"] == []
    assert preview["sections"][0]["after_evidence_refs"] == []
    assert preview["sections"][0]["source_event_ids"] == [comment_event_id]
    assert preview["sections"][0]["sources"] == [
        {"event_id": comment_event_id, "kind": "comment", "entity_id": document["protocol"]["comments"][0]["comment_id"], "label": "Комментарий автора", "text": "Уточнить контроль ошибок"}
    ]

    with pytest.raises(BusinessDocumentError) as bypass:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "APPLY_CHANGES", {"base_revision_id": base["revision_id"]}, key="bypass-preview"),
        )
    assert bypass.value.code == "CHANGE_PREVIEW_CONFIRMATION_REQUIRED"

    with pytest.raises(BusinessDocumentError) as denied:
        document_commands.execute(
            TENANT,
            "another-author",
            document["document_id"],
            _command(document, "CONFIRM_PREPARED_CHANGES", {"job_id": requested["job_id"]}, key="foreign-confirm"),
        )
    assert denied.value.code == "DOCUMENT_PERMISSION_DENIED"
    assert BusinessDocumentRevision.select().count() == 1

    command = _command(document, "CONFIRM_PREPARED_CHANGES", {"job_id": requested["job_id"]})
    applied = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert document_commands.execute(TENANT, AUTHOR, document["document_id"], command)["idempotent_replay"] is True
    events, _ = BusinessDocumentStreamEvents.read(requested["job_id"], 0)
    assert [event["type"] for event in events].count("applied") == 1
    assert events[-1]["payload"]["state_version"] == applied["state_version"]
    document = document_queries.get_document(applied["document_id"], AUTHOR)
    assert document["current_revision"]["revision_number"] == 2
    assert document["change_preview"] is None
    assert BusinessDocumentRevision.select().count() == 2


@pytest.mark.p0
def test_prepared_change_can_be_discarded_without_creating_a_revision(database):
    document = _request_and_complete_draft(_create())
    base = document["current_revision"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": base["revision_id"], "section_id": None, "text": "Проверить требования", "anchor": None}),
    )
    document = _complete_review_assessment(document_queries.get_document(response["document_id"], AUTHOR), comment_disposition="NO_CHANGE")
    source_id = document["protocol"]["comments"][0]["source_event_id"]
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": base["revision_id"]}))
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": base["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [source_id],
                "operations": [],
            }
        },
    )
    preview = document_queries.get_change_preview(document["document_id"], requested["job_id"])
    assert preview["sections"] == []
    assert preview["acknowledged_no_change_sources"] == [
        {"event_id": source_id, "kind": "comment", "entity_id": document["protocol"]["comments"][0]["comment_id"], "label": "Комментарий автора", "text": "Проверить требования"}
    ]
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "DISCARD_PREPARED_CHANGES", {"job_id": requested["job_id"]}))
    document = document_queries.get_document(document["document_id"], AUTHOR)
    assert document["change_preview"] is None
    assert document["current_revision"]["revision_id"] == base["revision_id"]
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "CONFIRM_PREPARED_CHANGES", {"job_id": requested["job_id"]}))
    assert caught.value.code == "CHANGE_PREVIEW_STALE"
    events, _ = BusinessDocumentStreamEvents.read(requested["job_id"], 0)
    assert events[-1]["type"] == "discarded"
    assert events[-1]["payload"]["state_version"] == document["state_version"]


@pytest.mark.p0
def test_new_feedback_invalidates_a_prepared_change(database):
    document = _request_and_complete_draft(_create())
    base = document["current_revision"]
    added = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": base["revision_id"], "section_id": None, "text": "Проверить текст", "anchor": None}),
    )
    document = _complete_review_assessment(document_queries.get_document(added["document_id"], AUTHOR), comment_disposition="NO_CHANGE")
    source_id = document["protocol"]["comments"][0]["source_event_id"]
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": base["revision_id"]}))
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": base["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [source_id],
                "operations": [],
            }
        },
    )
    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": base["revision_id"], "section_id": None, "text": "Новые замечания", "anchor": None}),
    )
    document = document_queries.get_document(document["document_id"], AUTHOR)
    assert document["change_preview"] is None
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "CONFIRM_PREPARED_CHANGES", {"job_id": requested["job_id"]}),
        )
    assert caught.value.code == "CHANGE_PREVIEW_STALE"
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.p0
def test_create_contract_rejects_unknown_fields(database):
    with pytest.raises(BusinessDocumentError) as caught:
        _create(attachment_ids=["not-yet-supported"])
    assert caught.value.code == "INVALID_CREATE_DOCUMENT"
    assert BusinessDocument.select().count() == 0


@pytest.mark.p0
def test_failed_optimistic_cas_rolls_back_child_write_but_records_rejection(database, monkeypatch):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(requested["job_id"], _question_batch())
    question = document["protocol"]["questions"][0]
    command = _command(
        document,
        "ANSWER_QUESTION",
        {"question_id": question["question_id"], "selected_option_id": "individuals", "custom_answer": None},
        key="cas-after-child-write",
    )

    def lose_cas(_document, _changes):
        raise BusinessDocumentError("STATE_VERSION_CONFLICT", "The document changed concurrently", 409)

    monkeypatch.setattr(document_writer, "update_document", lose_cas)
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], command)

    assert caught.value.code == "STATE_VERSION_CONFLICT"
    assert BusinessDocumentAnswer.select().count() == 0
    ledger = BusinessDocumentCommand.get(BusinessDocumentCommand.idempotency_key == "cas-after-child-write")
    assert ledger.response["accepted"] is False
    assert ledger.response["error"]["code"] == "STATE_VERSION_CONFLICT"


@pytest.mark.p0
def test_rejection_after_document_cas_records_and_replays_the_rolled_back_version(database, monkeypatch):
    document = _create()
    event_count = BusinessDocumentEvent.select().count()
    command = _command(document, "ARCHIVE", key="failure-after-document-cas")

    def reject_event(*_args, **_kwargs):
        raise BusinessDocumentError("AUDIT_REJECTED", "Forced rejection after CAS", 409)

    monkeypatch.setattr(document_writer, "append_event", reject_event)
    for _ in range(2):
        with pytest.raises(BusinessDocumentError) as caught:
            document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
        assert (caught.value.code, caught.value.status) == ("AUDIT_REJECTED", 409)
    row = BusinessDocument.get_by_id(document["document_id"])
    receipt = BusinessDocumentCommand.get(BusinessDocumentCommand.idempotency_key == command["idempotency_key"])
    assert row.lifecycle_state == "INTAKE"
    assert row.state_version == receipt.response["state_version"] == document["state_version"]
    assert receipt.response["accepted"] is False
    assert BusinessDocumentEvent.select().count() == event_count


@pytest.mark.p0
def test_job_insert_failure_rolls_back_claimed_document_version(database, monkeypatch):
    document = _create()
    event_count = BusinessDocumentEvent.select().count()

    def fail_insert(**kwargs):
        raise RuntimeError("job storage unavailable")

    monkeypatch.setattr(BusinessDocumentJob, "create", fail_insert)
    with pytest.raises(RuntimeError, match="job storage unavailable"):
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    persisted = BusinessDocument.get_by_id(document["document_id"])
    assert persisted.state_version == document["state_version"]
    assert persisted.operation_state == document["operation_state"]
    assert BusinessDocumentJob.select().count() == 0
    assert BusinessDocumentCommand.select().count() == 0
    assert BusinessDocumentEvent.select().count() == event_count


@pytest.mark.p0
def test_concurrent_idempotency_insert_replays_winning_ledger(database, monkeypatch):
    document = _create()
    command = _command(document, "REQUEST_INTAKE_ASSESSMENT", key="same-key-race")
    first = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    winner = BusinessDocumentCommand.get(BusinessDocumentCommand.idempotency_key == "same-key-race")
    original_get_or_none = BusinessDocumentCommand.get_or_none
    lookups = 0

    def racing_lookup(*query):
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else original_get_or_none(*query)

    def lose_insert(**_kwargs):
        raise IntegrityError("simulated concurrent unique-key winner")

    monkeypatch.setattr(BusinessDocumentCommand, "get_or_none", racing_lookup)
    monkeypatch.setattr(BusinessDocumentCommand, "create", lose_insert)
    replay = document_commands.execute(TENANT, AUTHOR, document["document_id"], command)

    assert replay["idempotent_replay"] is True
    assert replay["job_id"] == first["job_id"]
    assert winner.request_hash == BusinessDocumentCommand.get_by_id(winner.id).request_hash


@pytest.mark.p0
def test_full_workflow_is_versioned_idempotent_and_append_only(database):
    document = _create()
    assert document["lifecycle_state"] == "INTAKE"
    assert document["state_version"] == 1
    assert document["current_revision"] is None

    assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(assessment["job_id"], _question_batch())
    question = document["protocol"]["questions"][0]
    answer_command = _command(
        document,
        "ANSWER_QUESTION",
        {"question_id": question["question_id"], "selected_option_id": "individuals", "custom_answer": None},
    )
    document = document_commands.execute(TENANT, AUTHOR, document["document_id"], answer_command)
    document = document_queries.get_document(document["document_id"], AUTHOR)

    document = _request_and_complete_draft(
        document,
        review_questions=_question_batch("REVIEW", "monitoring"),
        proposals=[
            {"id": "proposal-accepted", "target_section_id": "5.5", "text": "Добавить метрику ошибок"},
            {"id": "proposal-rejected", "target_section_id": "5.5", "text": "Удалить мониторинг"},
        ],
    )
    revision_one = deepcopy(document["current_revision"])
    review_question = document["protocol"]["questions"][0]

    proposal_ids = {proposal["text"]: proposal["proposal_id"] for proposal in document["protocol"]["proposals"]}
    for proposal_id, decision in (
        (proposal_ids["Добавить метрику ошибок"], "ACCEPTED"),
        (proposal_ids["Удалить мониторинг"], "REJECTED"),
    ):
        response = document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": decision}),
        )
        document = document_queries.get_document(response["document_id"], AUTHOR)

    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ANSWER_QUESTION",
            {"question_id": review_question["question_id"], "selected_option_id": "individuals", "custom_answer": None},
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    accepted_event = next(
        event
        for event in BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ProposalDecided"))
        if event.payload["decision"] == "ACCEPTED"
    )
    review_answer_event = next(
        event
        for event in BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "QuestionAnswered"))
        if event.payload["question_id"] == review_question["question_id"]
    )
    apply_command = _command(document, "APPLY_CHANGES", {"base_revision_id": revision_one["revision_id"]}, key="apply-once")
    apply_response = document_commands.execute(TENANT, AUTHOR, document["document_id"], apply_command)
    replay = document_commands.execute(TENANT, AUTHOR, document["document_id"], apply_command)
    assert replay["idempotent_replay"] is True
    assert replay["job_id"] == apply_response["job_id"]
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 1

    document = _complete(
        apply_response["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": revision_one["revision_id"],
                "source_state_version": apply_response["state_version"],
                "acknowledged_no_change_event_ids": [review_answer_event.id],
                "operations": [
                    {
                        "operation_id": "op-1",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": "5.5",
                        "expected_section_hash": section_hash(next(section for section in revision_one["document_ast"]["sections"] if section["id"] == "5.5")),
                        "source_event_ids": [accepted_event.id],
                        "content": {"blocks": [{"type": "paragraph", "text": "Контроль ошибок"}]},
                    }
                ],
            },
        },
    )

    assert document["lifecycle_state"] == "AGREED"
    assert document["operation_state"] == "IDLE"
    assert document["current_revision"]["revision_number"] == 2
    assert document["current_revision"]["content_hash"] != revision_one["content_hash"]
    before_sections = {section["id"]: section for section in revision_one["document_ast"]["sections"]}
    after_sections = {section["id"]: section for section in document["current_revision"]["document_ast"]["sections"]}
    assert after_sections["1"] == before_sections["1"]
    assert after_sections["5.5"]["blocks"] == [{"type": "paragraph", "text": "Контроль ошибок"}]
    revisions = document_queries.list_revisions(document["document_id"])
    assert revisions[0] == revision_one
    assert len(revisions) == 2
    assert BusinessDocumentAnswer.select().count() == 2
    assert BusinessDocumentProposalDecision.select().count() == 2
    assert all(event.create_time for event in BusinessDocumentEvent.select())


@pytest.mark.p0
def test_partial_proposal_changes_keep_review_open_and_apply_each_decision_once(database):
    document = _request_and_complete_draft(
        _create(),
        proposals=[
            {"text": "Добавить метрику ошибок"},
            {"text": "Добавить порог предупреждения"},
        ],
    )
    review_cycle = document["active_review_cycle"]
    proposal_ids = {proposal["text"]: proposal["proposal_id"] for proposal in document["protocol"]["proposals"]}

    first_decision = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "DECIDE_PROPOSAL",
            {
                "proposal_id": proposal_ids["Добавить метрику ошибок"],
                "decision": "ACCEPTED",
            },
        ),
    )
    document = document_queries.get_document(first_decision["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    first_event = next(
        event
        for event in BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ProposalDecided"))
        if event.payload["proposal_id"] == proposal_ids["Добавить метрику ошибок"]
    )
    first_revision = document["current_revision"]
    first_apply = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "APPLY_CHANGES",
            {"base_revision_id": first_revision["revision_id"]},
        ),
    )
    target = next(section for section in first_revision["document_ast"]["sections"] if section["id"] == "5.5")
    document = _complete(
        first_apply["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": first_revision["revision_id"],
                "source_state_version": first_apply["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [
                    {
                        "operation_id": "op-first-proposal",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": "5.5",
                        "expected_section_hash": section_hash(target),
                        "source_event_ids": [first_event.id],
                        "content": {"blocks": [{"type": "paragraph", "text": "Контроль ошибок"}]},
                    }
                ],
            }
        },
    )

    assert document["lifecycle_state"] == "REVIEW"
    assert document["active_review_cycle"] == review_cycle
    assert document["current_revision"]["revision_number"] == 2
    assert "DECIDE_PROPOSAL" in document["allowed_commands"]
    assert "APPLY_CHANGES" not in document["allowed_commands"]
    decisions = {proposal["text"]: proposal["decision"] for proposal in document["protocol"]["proposals"]}
    assert decisions == {
        "Добавить метрику ошибок": "ACCEPTED",
        "Добавить порог предупреждения": "PENDING",
    }

    second_decision = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "DECIDE_PROPOSAL",
            {
                "proposal_id": proposal_ids["Добавить порог предупреждения"],
                "decision": "ACCEPTED",
            },
        ),
    )
    document = document_queries.get_document(second_decision["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    second_event = (
        BusinessDocumentEvent.select()
        .where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ProposalDecided"))
        .order_by(BusinessDocumentEvent.sequence.desc())
        .get()
    )
    second_revision = document["current_revision"]
    second_apply = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "APPLY_CHANGES",
            {"base_revision_id": second_revision["revision_id"]},
        ),
    )
    second_job = BusinessDocumentJob.get_by_id(second_apply["job_id"])
    assert second_job.payload["active_change_input_event_ids"] == [second_event.id]
    target = next(section for section in second_revision["document_ast"]["sections"] if section["id"] == "5.5")
    change_plan = {
        "schema_version": "1",
        "base_revision_id": second_revision["revision_id"],
        "source_state_version": second_apply["state_version"],
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {
                "operation_id": "op-second-proposal",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "5.5",
                "expected_section_hash": section_hash(target),
                "source_event_ids": [second_event.id],
                "content": {
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": "Контроль ошибок с порогом предупреждения",
                        }
                    ]
                },
            }
        ],
    }
    claimed_job = BusinessDocumentJobQueue.claim("worker-1")
    assert claimed_job is not None and claimed_job.id == second_apply["job_id"]
    invalid_plan = deepcopy(change_plan)
    invalid_plan["operations"][0]["source_event_ids"] = [first_event.id, second_event.id]
    with pytest.raises(BusinessDocumentError) as caught:
        job_completion.complete(
            TENANT,
            "worker-1",
            second_apply["job_id"],
            {"change_plan": invalid_plan},
            claimed_job.lease_token,
        )
    assert caught.value.code == "CHANGE_SOURCE_NOT_ACTIVE"
    assert caught.value.details == {"event_id": first_event.id}

    document = job_completion.complete(
        TENANT,
        "worker-1",
        second_apply["job_id"],
        {"change_plan": change_plan},
        claimed_job.lease_token,
    )

    assert document["lifecycle_state"] == "AGREED"
    assert document["active_review_cycle"] == review_cycle
    assert document["current_revision"]["revision_number"] == 3
    assert BusinessDocumentRevision.select().count() == 3


@pytest.mark.p0
def test_pending_proposals_without_ready_changes_cannot_close_review(database):
    document = _request_and_complete_draft(_create(), proposals=[{"text": "Добавить метрику ошибок"}])
    document = _complete_review_assessment(document)

    assert document["lifecycle_state"] == "REVIEW"
    assert "DECIDE_PROPOSAL" in document["allowed_commands"]
    assert "APPLY_CHANGES" not in document["allowed_commands"]
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(
                document,
                "APPLY_CHANGES",
                {"base_revision_id": document["current_revision"]["revision_id"]},
            ),
        )

    assert caught.value.code == "PENDING_PROPOSAL_DECISIONS"
    assert caught.value.details["proposal_ids"] == [document["protocol"]["proposals"][0]["proposal_id"]]
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 0


@pytest.mark.p0
def test_no_change_disposition_is_recorded_without_closing_pending_proposals(database):
    document = _request_and_complete_draft(_create(), proposals=[{"text": "Добавить метрику ошибок"}])
    revision = document["current_revision"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": revision["revision_id"],
                "section_id": None,
                "text": "Проверено, менять не нужно",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document, comment_disposition="NO_CHANGE")
    assert "APPLY_CHANGES" in document["allowed_commands"]
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "APPLY_CHANGES",
            {"base_revision_id": revision["revision_id"]},
        ),
    )
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": revision["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [comment_event.id],
                "operations": [],
            }
        },
    )

    assert document["lifecycle_state"] == "REVIEW"
    assert document["current_revision"] == revision
    assert "DECIDE_PROPOSAL" in document["allowed_commands"]
    assert "APPLY_CHANGES" not in document["allowed_commands"]
    continued = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ReviewContinuedWithoutChanges"))
    assert continued.payload["acknowledged_no_change_event_ids"] == [comment_event.id]
    assert continued.payload["review_continues"] is True


@pytest.mark.p0
@pytest.mark.parametrize("feedback_type", ["ANSWER_QUESTION", "DECIDE_PROPOSAL", "ADD_COMMENT"])
def test_partial_review_feedback_can_be_assessed_once_without_changing_revision(database, feedback_type):
    questions = _question_batch("REVIEW")
    questions["questions"].extend(_question_batch("REVIEW", "other_audience")["questions"])
    document = _request_and_complete_draft(_create(), review_questions=questions, proposals=[{"text": "Уточнить метрику"}, {"text": "Добавить ограничение"}])
    revision = deepcopy(document["current_revision"])
    assert "REQUEST_REVIEW_ASSESSMENT" not in document["allowed_commands"]
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))
    assert caught.value.code == "OPEN_REVIEW_QUESTIONS"

    payload = {
        "ANSWER_QUESTION": {"question_id": document["protocol"]["questions"][0]["question_id"], "selected_option_id": "individuals", "custom_answer": None},
        "DECIDE_PROPOSAL": {"proposal_id": document["protocol"]["proposals"][0]["proposal_id"], "decision": "ACCEPTED"},
        "ADD_COMMENT": {"revision_id": revision["revision_id"], "section_id": None, "text": "Уточнить сроки", "anchor": None},
    }[feedback_type]
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, feedback_type, payload))
    document = document_queries.get_document(document["document_id"], AUTHOR)
    assert "REQUEST_REVIEW_ASSESSMENT" in document["allowed_commands"]
    assert "ANSWER_QUESTION" in document["allowed_commands"]
    assert "APPLY_CHANGES" not in document["allowed_commands"]

    document = _complete_review_assessment(document)
    assert document["current_revision"] == revision
    assert any(question["status"] == "OPEN" for question in document["protocol"]["questions"])
    assert len(document["protocol"]["questions"]) == 2
    assert len(document["protocol"]["proposals"]) == 2
    assert "REQUEST_REVIEW_ASSESSMENT" not in document["allowed_commands"]
    assert "APPLY_CHANGES" not in document["allowed_commands"]
    assessment = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ReviewAssessed"))
    assert assessment.payload["outcome"] == "NEEDS_INPUT"
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))
    assert caught.value.code == "OPEN_REVIEW_QUESTIONS"

    document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": revision["revision_id"], "section_id": None, "text": "Ещё одно уточнение", "anchor": None}),
    )
    document = document_queries.get_document(document["document_id"], AUTHOR)
    assert "REQUEST_REVIEW_ASSESSMENT" in document["allowed_commands"]


@pytest.mark.p0
def test_apply_is_rejected_while_review_question_is_open_without_mutation_or_job(database):
    document = _request_and_complete_draft(_create(), review_questions=_question_batch("REVIEW"))
    revision = deepcopy(document["current_revision"])
    command = _command(document, "APPLY_CHANGES", {"base_revision_id": revision["revision_id"]}, key="blocked-apply")

    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], command)

    assert caught.value.code == "OPEN_REVIEW_QUESTIONS"
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["state_version"] == document["state_version"]
    assert after["current_revision"] == revision
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").count() == 0
    assert BusinessDocumentCommand.get(BusinessDocumentCommand.idempotency_key == "blocked-apply").response["accepted"] is False
    with pytest.raises(BusinessDocumentError) as replayed:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
    assert replayed.value.code == "OPEN_REVIEW_QUESTIONS"


@pytest.mark.p0
def test_state_version_and_idempotency_payload_conflicts_are_rejected(database):
    document = _create()
    stale = _command(document, "REQUEST_DRAFT", expected=document["state_version"] + 1)
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], stale)
    assert caught.value.code == "STATE_VERSION_CONFLICT"

    valid = _command(document, "REQUEST_INTAKE_ASSESSMENT", key="same-key")
    document_commands.execute(TENANT, AUTHOR, document["document_id"], valid)
    conflicting = {**valid, "command_id": "different-command"}
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], conflicting)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.p0
def test_authors_can_open_foreign_documents_while_unknown_ids_stay_non_enumerable(database):
    document = _create()
    projection = document_queries.get_document(document["document_id"], "another-author")
    assert projection["document_id"] == document["document_id"]
    assert projection["permissions"] == {"read": True, "edit": False, "delete": False, "assign": False}
    assert projection["allowed_commands"] == []

    with pytest.raises(BusinessDocumentError) as caught:
        document_queries.get_document("missing-document", AUTHOR)
    assert caught.value.status == 404
    assert caught.value.code == "DOCUMENT_NOT_FOUND"


@pytest.mark.p0
def test_shared_access_list_and_server_assigned_chat(database):
    User.create(id=AUTHOR, nickname="Первый автор", email="author-1@example.com")
    first = _create(title="Первый")
    second = _create(title="Второй")
    listing = document_queries.list_documents(AUTHOR, page=1, page_size=1)
    assert listing["total"] == 2
    assert len(listing["items"]) == 1
    assert listing["items"][0]["document_id"] in {first["document_id"], second["document_id"]}
    assert listing["items"][0]["owner_name"] == "Первый автор"
    assert first["chat_id"].startswith("business-document:")

    shared = document_queries.get_document(first["document_id"], "another-user")
    assert shared["document_id"] == first["document_id"]
    assert shared["owner_name"] == "Первый автор"
    assert shared["permissions"]["edit"] is False
    with pytest.raises(BusinessDocumentError) as caught:
        _create(chat_id="client-controlled")
    assert caught.value.code == "CHAT_ID_NOT_ALLOWED"


@pytest.mark.p0
def test_business_document_role_matrix_controls_create_edit_delete_and_assignment(database):
    owned = _create(title="Документ автора")
    foreign = document_creation.execute(
        "tenant-2",
        "author-2",
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": "Чужой документ",
            "idea": "Проверить административный доступ",
        },
    )

    author_listing = document_queries.list_documents(AUTHOR)
    assert {item["document_id"] for item in author_listing["items"]} == {owned["document_id"], foreign["document_id"]}
    assert all(item["access_role"] == "AUTHOR_CREATOR" for item in author_listing["items"])
    permissions_by_id = {item["document_id"]: item["permissions"] for item in author_listing["items"]}
    assert permissions_by_id[owned["document_id"]] == {"read": True, "edit": True, "delete": False, "assign": False}
    assert permissions_by_id[foreign["document_id"]] == {"read": True, "edit": False, "delete": False, "assign": False}
    assert author_listing["capabilities"] == {
        "read": True,
        "create": True,
        "edit_own": True,
        "edit_all": False,
        "delete": False,
        "assign": False,
    }
    mine_listing = document_queries.list_documents(AUTHOR, scope="mine")
    assert [item["document_id"] for item in mine_listing["items"]] == [owned["document_id"]]
    assert mine_listing["scope"] == "mine"

    foreign_projection = document_queries.get_document(foreign["document_id"], AUTHOR)
    assert foreign_projection["permissions"]["edit"] is False
    assert foreign_projection["allowed_commands"] == []

    with pytest.raises(BusinessDocumentError) as denied:
        document_commands.execute(
            TENANT,
            AUTHOR,
            foreign["document_id"],
            _command(foreign_projection, "REQUEST_INTAKE_ASSESSMENT", key="author-command"),
        )
    assert denied.value.code == "DOCUMENT_PERMISSION_DENIED"

    command = document_commands.execute(
        TENANT,
        AUTHOR,
        foreign["document_id"],
        _command(foreign_projection, "REQUEST_INTAKE_ASSESSMENT", key="moderator-command"),
        access_role="MODERATOR_CREATOR",
    )
    event = BusinessDocumentEvent.get_by_id(command["event_id"])
    job = BusinessDocumentJob.get_by_id(command["job_id"])
    ledger = BusinessDocumentCommand.get(BusinessDocumentCommand.document_id == foreign["document_id"])
    assert event.actor_id == AUTHOR
    assert job.tenant_id == "tenant-2"
    assert job.payload["requested_by_actor_id"] == AUTHOR
    assert ledger.tenant_id == "tenant-2"

    with pytest.raises(BusinessDocumentError) as denied_create:
        document_creation.execute(
            TENANT,
            AUTHOR,
            {"schema_version": "1", "document_type": "business_requirements", "title": "Нельзя", "idea": "Нет права"},
            access_role="AUTHOR_EDITOR",
        )
    assert denied_create.value.code == "DOCUMENT_PERMISSION_DENIED"

    admin_listing = document_queries.list_documents("admin-user", is_admin=True)
    assert {item["document_id"] for item in admin_listing["items"]} == {owned["document_id"], foreign["document_id"]}
    assert all(item["access_role"] == "ADMIN" for item in admin_listing["items"])
    assert all(item["permissions"] == {"read": True, "edit": True, "delete": True, "assign": True} for item in admin_listing["items"])

    admin_projection = document_queries.get_document(foreign["document_id"], "admin-user", is_admin=True)
    assert admin_projection["access_role"] == "ADMIN"
    assert admin_projection["permissions"] == {"read": True, "edit": True, "delete": True, "assign": True}
    assert admin_projection["allowed_commands"] == []

    admin_created = document_creation.execute(
        "admin-user",
        "admin-user",
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": "Документ администратора",
            "idea": "Проверить роль в mutation-ответе",
        },
        is_admin=True,
    )
    assert admin_created["access_role"] == "ADMIN"
    assert admin_created["permissions"] == {"read": True, "edit": True, "delete": True, "assign": True}


@pytest.mark.p0
def test_access_user_listing_uses_canonical_admin_role_normalization(database):
    User.create(
        id="forged-admin",
        nickname="Forged admin",
        email="forged-admin@example.com",
        business_document_role="ADMIN",
    )
    User.create(
        id="real-admin",
        nickname="Real admin",
        email="real-admin@example.com",
        business_document_role="AUTHOR_CREATOR",
        is_superuser=True,
    )

    users = document_queries.list_access_users(
        "moderator-1",
        access_role="EXTENDED_MODERATOR",
    )

    roles = {item["user_id"]: item["role"] for item in users["items"]}
    assert roles == {
        "forged-admin": "AUTHOR_EDITOR",
        "real-admin": "ADMIN",
    }


@pytest.mark.p0
def test_extended_moderator_assigns_document_and_admin_manages_document_roles(database):
    User.create(id=AUTHOR, nickname="Первый автор", email="author-1@example.com")
    User.create(id="author-2", nickname="Второй автор", email="author-2@example.com")
    document = _create()

    users = document_queries.list_access_users("moderator-1", access_role="EXTENDED_MODERATOR")
    assert {(item["nickname"], item["email"]) for item in users["items"]} == {
        ("Первый автор", "author-1@example.com"),
        ("Второй автор", "author-2@example.com"),
    }
    assert {(item["user_id"], item["role"]) for item in users["items"]} == {
        (AUTHOR, "AUTHOR_CREATOR"),
        ("author-2", "AUTHOR_CREATOR"),
    }

    with pytest.raises(BusinessDocumentError) as denied:
        assign_business_document(
            "moderator-1",
            document["document_id"],
            {"owner_id": "author-2", "expected_state_version": document["state_version"]},
            access_role="MODERATOR_CREATOR",
        )
    assert denied.value.code == "DOCUMENT_PERMISSION_DENIED"

    assigned = assign_business_document(
        "moderator-1",
        document["document_id"],
        {"owner_id": "author-2", "expected_state_version": document["state_version"]},
        access_role="EXTENDED_MODERATOR",
    )
    assert assigned["owner_id"] == "author-2"
    assert assigned["owner_name"] == "Второй автор"
    assert assigned["state_version"] == document["state_version"] + 1
    assignment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentAssigned"))
    assert assignment_event.sequence == assigned["state_version"]
    assert assignment_event.event_type == "DocumentAssigned"
    assert assignment_event.actor_type == "USER"
    assert assignment_event.actor_id == "moderator-1"
    assert assignment_event.payload == {"previous_owner_id": AUTHOR, "owner_id": "author-2"}
    assert len(assignment_event.id) == 32
    assert len(assignment_event.correlation_id) == 32
    assert assignment_event.id != assignment_event.correlation_id
    assert assignment_event.causation_id is None
    assert assignment_event.create_time == assignment_event.update_time
    assert assignment_event.create_date == assignment_event.update_date

    assert document_queries.get_document(document["document_id"], AUTHOR)["permissions"]["edit"] is False
    assert document_queries.get_document(document["document_id"], "author-2")["permissions"]["edit"] is True

    persisted = BusinessDocument.get_by_id(document["document_id"])
    unchanged_update_time = persisted.update_time
    unchanged_update_date = persisted.update_date
    event_count = BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count()
    unchanged = assign_business_document(
        "moderator-1",
        document["document_id"],
        {"owner_id": " author-2 ", "expected_state_version": assigned["state_version"]},
        access_role="EXTENDED_MODERATOR",
    )
    persisted = BusinessDocument.get_by_id(document["document_id"])
    assert unchanged["owner_id"] == "author-2"
    assert unchanged["state_version"] == assigned["state_version"]
    assert persisted.update_time == unchanged_update_time
    assert persisted.update_date == unchanged_update_date
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count() == event_count

    with pytest.raises(BusinessDocumentError) as stale:
        assign_business_document(
            "moderator-1",
            document["document_id"],
            {"owner_id": "author-2", "expected_state_version": document["state_version"]},
            access_role="EXTENDED_MODERATOR",
        )
    assert stale.value.code == "STATE_VERSION_CONFLICT"
    assert stale.value.status == 409
    assert stale.value.details == {"expected": document["state_version"], "actual": assigned["state_version"]}

    reassigned = assign_business_document(
        "admin-user",
        document["document_id"],
        {"owner_id": AUTHOR, "expected_state_version": assigned["state_version"]},
        is_admin=True,
    )
    assert reassigned["owner_id"] == AUTHOR
    assert reassigned["access_role"] == "ADMIN"
    assert reassigned["state_version"] == assigned["state_version"] + 1

    changed_role = change_user_role.execute(
        "author-2",
        {"role": "AUTHOR_EDITOR"},
        is_admin=True,
    )
    assert changed_role["role"] == "AUTHOR_EDITOR"
    assert User.get_by_id("author-2").business_document_role == "AUTHOR_EDITOR"


@pytest.mark.p0
def test_assignment_checks_permission_before_truthy_invalid_payload(database):
    before_documents = BusinessDocument.select().count()
    before_events = BusinessDocumentEvent.select().count()

    with pytest.raises(BusinessDocumentError) as caught:
        assign_business_document(
            "author-1",
            "missing-document",
            {"unexpected": True},
            access_role="AUTHOR_EDITOR",
        )

    assert caught.value.code == "DOCUMENT_PERMISSION_DENIED"
    assert caught.value.status == 403
    assert caught.value.details == {}
    assert BusinessDocument.select().count() == before_documents
    assert BusinessDocumentEvent.select().count() == before_events


@pytest.mark.p0
def test_changed_assignment_rejects_active_job_without_invalidating_worker_state(database):
    User.create(id="author-2", nickname="Второй автор", email="author-2-active-job@example.com")
    document = _create(title="Assignment during active job")
    accepted = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "REQUEST_INTAKE_ASSESSMENT"),
    )
    before = BusinessDocument.get_by_id(document["document_id"])
    before_events = BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count()

    with pytest.raises(BusinessDocumentError) as caught:
        assign_business_document(
            "moderator-1",
            document["document_id"],
            {
                "owner_id": "author-2",
                "expected_state_version": before.state_version,
            },
            access_role="EXTENDED_MODERATOR",
        )

    after = BusinessDocument.get_by_id(document["document_id"])
    job = BusinessDocumentJob.get_by_id(accepted["job_id"])
    assert caught.value.code == "OPERATION_IN_PROGRESS"
    assert caught.value.status == 409
    assert caught.value.details == {}
    assert (after.owner_id, after.state_version, after.operation_state) == (
        before.owner_id,
        before.state_version,
        before.operation_state,
    )
    assert job.status == "PENDING"
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count() == before_events


@pytest.mark.p0
@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"owner_id": " ", "expected_state_version": 1},
        {"owner_id": "author-2", "expected_state_version": True},
        {"owner_id": "author-2", "expected_state_version": "1"},
    ],
    ids=["null", "list", "missing-owner", "blank-owner", "boolean-version", "string-version"],
)
def test_assignment_rejects_invalid_payload_without_writes(database, payload):
    User.create(id="author-2", nickname="Второй автор", email="author-2-invalid@example.com")
    document = _create(title=f"Invalid assignment {type(payload).__name__}-{payload!s}")
    before = BusinessDocument.get_by_id(document["document_id"])
    event_count = BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count()

    with pytest.raises(BusinessDocumentError) as caught:
        assign_business_document(
            "moderator-1",
            document["document_id"],
            payload,
            access_role="EXTENDED_MODERATOR",
        )

    after = BusinessDocument.get_by_id(document["document_id"])
    assert caught.value.code == "INVALID_DOCUMENT_ASSIGNMENT"
    assert caught.value.status == 422
    assert (after.owner_id, after.state_version, after.update_time, after.update_date) == (
        before.owner_id,
        before.state_version,
        before.update_time,
        before.update_date,
    )
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count() == event_count


@pytest.mark.p0
@pytest.mark.parametrize(
    "user_state",
    [None, {"status": "0"}, {"is_active": "0"}],
    ids=["missing", "disabled-status", "inactive"],
)
def test_assignment_rejects_missing_or_inactive_owner_without_writes(database, user_state):
    document = _create(title=f"Unavailable assignment owner {user_state!s}")
    if user_state is not None:
        User.create(
            id="unavailable-owner",
            nickname="Недоступный автор",
            email=f"unavailable-{next(iter(user_state))}@example.com",
            **user_state,
        )
    event_count = BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count()

    with pytest.raises(BusinessDocumentError) as caught:
        assign_business_document(
            "moderator-1",
            document["document_id"],
            {"owner_id": "unavailable-owner", "expected_state_version": document["state_version"]},
            access_role="EXTENDED_MODERATOR",
        )

    persisted = BusinessDocument.get_by_id(document["document_id"])
    assert caught.value.code == "USER_NOT_FOUND"
    assert caught.value.status == 404
    assert persisted.owner_id == AUTHOR
    assert persisted.state_version == document["state_version"]
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count() == event_count


@pytest.mark.p0
def test_assignment_rejects_missing_document_without_events(database):
    User.create(id="author-2", nickname="Второй автор", email="author-2-missing-document@example.com")

    with pytest.raises(BusinessDocumentError) as caught:
        assign_business_document(
            "moderator-1",
            "missing-document",
            {"owner_id": "author-2", "expected_state_version": 1},
            access_role="EXTENDED_MODERATOR",
        )

    assert caught.value.code == "DOCUMENT_NOT_FOUND"
    assert caught.value.status == 404
    assert caught.value.details == {}
    assert BusinessDocumentEvent.select().count() == 0


@pytest.mark.p0
def test_assignment_event_failure_rolls_back_owner_and_version(database, monkeypatch):
    User.create(id="author-2", nickname="Второй автор", email="author-2-rollback@example.com")
    document = _create(title="Assignment rollback")
    before = BusinessDocument.get_by_id(document["document_id"])
    event_count = BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count()

    def fail_event(*_args, **_kwargs):
        raise IntegrityError("forced assignment event failure")

    monkeypatch.setattr(BusinessDocumentEvent, "insert_many", staticmethod(fail_event))
    with pytest.raises(IntegrityError, match="forced assignment event failure"):
        assign_business_document(
            "moderator-1",
            document["document_id"],
            {"owner_id": "author-2", "expected_state_version": document["state_version"]},
            access_role="EXTENDED_MODERATOR",
        )

    after = BusinessDocument.get_by_id(document["document_id"])
    assert (after.owner_id, after.state_version, after.update_time, after.update_date) == (
        before.owner_id,
        before.state_version,
        before.update_time,
        before.update_date,
    )
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count() == event_count


@pytest.mark.p0
def test_revision_history_records_the_collaborator_who_requested_an_ai_draft(database):
    User.create(id="author-2", nickname="Второй автор", email="author-2@example.com")
    document = _create()
    assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(
        assessment["job_id"],
        {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
    )
    requested = document_commands.execute(
        "collaborator-tenant",
        "author-2",
        document["document_id"],
        _command(document, "REQUEST_DRAFT", key="collaborator-draft"),
        access_role="MODERATOR_CREATOR",
    )
    document = _complete(
        requested["job_id"],
        {
            "draft": _draft(),
            "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
            "proposals": [],
        },
    )

    initial_draft = next(item for item in document["current_revision"]["change_basis"] if item["type"] == "INITIAL_DRAFT")
    assert document["current_revision"]["author_id"] == "author-2"
    assert document["current_revision"]["author_name"] == "Второй автор"
    assert document["current_revision"]["author_login"] == "author-2@example.com"
    assert initial_draft["initiated_by_actor_id"] == "author-2"
    assert initial_draft["initiated_by_actor_name"] == "Второй автор"
    assert initial_draft["initiated_by_actor_login"] == "author-2@example.com"
    assert initial_draft["actor_type"] == "AI"
    assert initial_draft["actor_id"] == "worker-1"


@pytest.mark.p0
def test_extended_moderator_can_delete_document_and_all_owned_rows_are_removed(database):
    document = _create()

    with pytest.raises(BusinessDocumentError) as caught:
        document_deletion.execute(AUTHOR, document["document_id"])
    assert caught.value.status == 403
    assert caught.value.code == "DOCUMENT_PERMISSION_DENIED"

    foreign = document_creation.execute(
        "tenant-2",
        "author-2",
        {
            "schema_version": "1",
            "document_type": "business_requirements",
            "title": "Чужой документ",
            "idea": "Проверить запрет удаления",
        },
    )
    with pytest.raises(BusinessDocumentError) as caught:
        document_deletion.execute(AUTHOR, foreign["document_id"])
    assert caught.value.code == "DOCUMENT_PERMISSION_DENIED"

    result = document_deletion.execute(
        "extended-moderator",
        document["document_id"],
        access_role="EXTENDED_MODERATOR",
    )
    assert result == {
        "document_id": document["document_id"],
        "deleted": True,
        "deleted_artifacts": 0,
        "storage_cleanup_failures": 0,
    }
    assert BusinessDocument.select().where(BusinessDocument.id == document["document_id"]).count() == 0
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.document_id == document["document_id"]).count() == 0


@pytest.mark.p0
def test_admin_cannot_delete_document_while_background_operation_is_active(database):
    document = _create()
    document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))

    with pytest.raises(BusinessDocumentError) as caught:
        document_deletion.execute("admin-user", document["document_id"], is_admin=True)
    assert caught.value.status == 409
    assert caught.value.code == "OPERATION_IN_PROGRESS"
    assert BusinessDocument.select().where(BusinessDocument.id == document["document_id"]).exists()


@pytest.mark.p0
@pytest.mark.parametrize("status", ["PENDING", "RUNNING", "RETRY"])
def test_active_job_blocks_delete_even_when_document_operation_is_idle(database, status):
    document = _create()
    command = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    BusinessDocumentJob.update(status=status).where(BusinessDocumentJob.id == command["job_id"]).execute()
    BusinessDocument.update(operation_state="IDLE").where(BusinessDocument.id == document["document_id"]).execute()

    with pytest.raises(BusinessDocumentError) as caught:
        document_deletion.execute("admin-user", document["document_id"], is_admin=True)

    assert caught.value.code == "OPERATION_IN_PROGRESS"
    assert BusinessDocument.select().where(BusinessDocument.id == document["document_id"]).exists()
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.id == command["job_id"]).exists()


@pytest.mark.parametrize(
    ("is_admin", "raw", "exists", "superuser", "code", "status"),
    [
        (False, None, True, False, "DOCUMENT_PERMISSION_DENIED", 403),
        (True, None, True, False, "INVALID_ACCESS_ROLE", 422),
        (True, {"role": "UNKNOWN"}, True, False, "INVALID_ACCESS_ROLE", 422),
        (True, {"role": "ADMIN"}, True, False, "INVALID_ACCESS_ROLE", 422),
        (True, {"role": "AUTHOR_EDITOR"}, False, False, "USER_NOT_FOUND", 404),
        (True, {"role": "AUTHOR_EDITOR"}, True, True, "ADMIN_ROLE_MANAGED_SEPARATELY", 409),
    ],
)
def test_role_changes_preserve_authorization_and_managed_admin_boundaries(database, is_admin, raw, exists, superuser, code, status):
    if exists:
        User.create(id="role-target", nickname="Target", email="role-target@example.test", is_superuser=superuser, business_document_role="AUTHOR_CREATOR")

    with pytest.raises(BusinessDocumentError) as caught:
        change_user_role.execute("role-target", raw, is_admin=is_admin)

    assert (caught.value.code, caught.value.status) == (code, status)
    if exists:
        assert User.get_by_id("role-target").business_document_role == "AUTHOR_CREATOR"


@pytest.mark.parametrize("failure_point", ["document", "binding", "event"])
def test_creation_does_not_mask_unrelated_integrity_failures_as_eva_occupancy(database, monkeypatch, failure_point):
    from peewee import IntegrityError

    binding = {"page_url": "https://eva.example.test/Document/new", "status": "LINK_ONLY"}
    monkeypatch.setattr(document_creation, "_resolve_binding", lambda *_args: binding)

    def fail_write(*_args, **_kwargs):
        raise IntegrityError("injected unrelated integrity failure")

    if failure_point == "document":
        monkeypatch.setattr(BusinessDocument, "create", staticmethod(fail_write))
    elif failure_point == "binding":
        monkeypatch.setattr(BusinessDocumentEvaBinding, "create", staticmethod(fail_write))
    else:
        monkeypatch.setattr(document_writer, "append_event", fail_write)

    with pytest.raises(IntegrityError, match="injected unrelated integrity failure"):
        _create()

    assert BusinessDocument.select().count() == 0
    assert BusinessDocumentEvaBinding.select().count() == 0
    assert BusinessDocumentEvent.select().count() == 0


@pytest.mark.parametrize("size", [1, 10, 100])
def test_creation_loads_match_occupancy_in_one_query_and_preserves_order(database, monkeypatch, size):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    occupied = _create(title="Occupied document")
    matches = [{"page_url": f"https://eva.example.test/Document/{index}", "web_url": f"https://eva.example.test/Document/{index}"} for index in range(size)]
    with database.atomic():
        document_writer.store_binding(occupied["document_id"], {**matches[-1], "status": "LINK_ONLY"})
    monkeypatch.setattr(EvaDocumentChangeService, "find_title_matches", staticmethod(lambda *_args: matches))
    selects = []
    execute_sql = database.execute_sql

    def capture(sql, *args, **kwargs):
        if sql.startswith("SELECT") and '"business_document_eva_binding"' in sql:
            selects.append(sql)
        return execute_sql(sql, *args, **kwargs)

    monkeypatch.setattr(database, "execute_sql", capture)
    with pytest.raises(BusinessDocumentError) as caught:
        document_creation.execute(TENANT, AUTHOR, {"schema_version": "2", "document_type": "business_requirements", "title": "New document", "idea": "Creation"})

    assert caught.value.code == "EVA_BINDING_DECISION_REQUIRED"
    actual = caught.value.details["matches"]
    assert [row["page_url"] for row in actual] == [row["page_url"] for row in matches]
    assert all(row["binding_available"] for row in actual[:-1])
    assert actual[-1]["binding_available"] is False
    assert actual[-1]["linked_document"] == {"document_id": occupied["document_id"], "title": "Occupied document"}
    assert len(selects) == 1
    assert "LEFT OUTER JOIN" in selects[0]


def test_role_change_rolls_back_when_persistence_fails_after_update(database, monkeypatch):
    from api.apps.business_documents.adapters.access import PeeweeUserRoleWriter

    User.create(id="role-target", nickname="Target", email="role-target@example.test", business_document_role="AUTHOR_CREATOR")
    original = PeeweeUserRoleWriter.set_role

    def fail_update(writer, user_id, role):
        original(writer, user_id, role)
        raise RuntimeError("role persistence failed")

    monkeypatch.setattr(PeeweeUserRoleWriter, "set_role", fail_update)
    with pytest.raises(RuntimeError, match="role persistence failed"):
        change_user_role.execute("role-target", {"role": "AUTHOR_EDITOR"}, is_admin=True)

    assert User.get_by_id("role-target").business_document_role == "AUTHOR_CREATOR"


@pytest.mark.p0
def test_draft_requires_complete_assessment_after_last_intake_answer(database):
    document = _create()
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    assert caught.value.code == "INTAKE_ASSESSMENT_REQUIRED"

    assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(assessment["job_id"], _question_batch(), worker="worker")
    question = document["protocol"]["questions"][0]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ANSWER_QUESTION",
            {"question_id": question["question_id"], "selected_option_id": "individuals", "custom_answer": None},
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    assert caught.value.code == "INTAKE_ASSESSMENT_REQUIRED"
    assert "REQUEST_INTAKE_ASSESSMENT" in document["allowed_commands"]


@pytest.mark.p0
def test_question_schema_boundary_rejects_five_options_and_rolls_back(database):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    invalid = _question_batch()
    invalid["questions"][0]["options"] += [
        {"option_id": "third", "label": "Третий"},
        {"option_id": "fourth", "label": "Четвертый"},
        {"option_id": "fifth", "label": "Пятый"},
    ]
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(requested["job_id"], invalid)
    assert caught.value.code == "INVALID_QUESTION_BATCH"
    job = BusinessDocumentJob.get_by_id(requested["job_id"])
    assert job.status == "RUNNING"
    assert document_queries.get_document(document["document_id"], AUTHOR)["operation_state"] == "ANALYZING"


@pytest.mark.p0
def test_question_answers_and_proposal_decisions_are_immutable(database):
    document = _create()
    assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(assessment["job_id"], _question_batch(), worker="worker")
    question_id = document["protocol"]["questions"][0]["question_id"]
    first = _command(
        document,
        "ANSWER_QUESTION",
        {"question_id": question_id, "selected_option_id": "individuals", "custom_answer": None},
    )
    document_commands.execute(TENANT, AUTHOR, document["document_id"], first)
    document = document_queries.get_document(document["document_id"], AUTHOR)
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "ANSWER_QUESTION", {"question_id": question_id, "selected_option_id": "companies", "custom_answer": None}),
        )
    assert caught.value.code == "QUESTION_ALREADY_CLOSED"
    assert BusinessDocumentAnswer.select().count() == 1

    document = _request_and_complete_draft(document, proposals=[{"text": "Добавить метрику"}])
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    first = _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "ACCEPTED"})
    document_commands.execute(TENANT, AUTHOR, document["document_id"], first)
    document = document_queries.get_document(document["document_id"], AUTHOR)
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "REJECTED"}),
        )
    assert caught.value.code == "PROPOSAL_ALREADY_DECIDED"
    assert BusinessDocumentProposalDecision.select().count() == 1


@pytest.mark.p0
def test_rejected_proposal_cannot_authorize_change(database):
    document = _request_and_complete_draft(_create(), proposals=[{"text": "Удалить мониторинг"}])
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    decision = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "REJECTED"}),
    )
    document = document_queries.get_document(decision["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    rejected_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ProposalDecided"))
    request = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            request["job_id"],
            {
                "change_plan": {
                    "schema_version": "1",
                    "base_revision_id": document["current_revision"]["revision_id"],
                    "source_state_version": request["state_version"],
                    "acknowledged_no_change_event_ids": [],
                    "operations": [
                        {
                            "operation_id": "op-1",
                            "type": "REPLACE_SECTION_CONTENT",
                            "section_id": "5.5",
                            "expected_section_hash": section_hash(next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")),
                            "source_event_ids": [rejected_event.id],
                            "content": {"blocks": []},
                        }
                    ],
                },
            },
        )
    assert caught.value.code == "REJECTED_PROPOSAL_SOURCE"
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.p1
def test_stale_worker_result_cannot_create_revision(database):
    document = _create()
    assessment = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(assessment["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, worker="worker")
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    BusinessDocument.update(state_version=requested["state_version"] + 1).where(BusinessDocument.id == document["document_id"]).execute()
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {"draft": _draft(), "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []}, "proposals": []},
        )
    assert caught.value.code == "STALE_AI_RESULT"
    assert BusinessDocumentRevision.select().count() == 0


@pytest.mark.p0
def test_required_sections_cannot_be_empty_and_child_headings_are_nested(database):
    draft = _draft()
    section = next(item for item in draft["sections"] if item["id"] == "5.5")
    section["blocks"] = []
    with pytest.raises(BusinessDocumentError) as caught:
        validate_document_ast(draft)
    assert caught.value.code == "REQUIRED_SECTION_EMPTY"

    markdown = render_document_ast(_draft())
    assert "## 3. Целевая аудитория" in markdown
    assert "### 3.1. Пользователи и их категории" in markdown

    activity = _draft()
    scenario = next(item for item in activity["sections"] if item["id"] == "4.3")
    scenario["blocks"] = [
        {"type": "paragraph", "text": "Основной и негативный клиентские сценарии."},
        {"type": "plantuml", "source": VALID_ACTIVITY_SCENARIO},
    ]
    assert "```plantuml" in render_document_ast(validate_document_ast(activity))

    wrong_diagram = _draft()
    scenario = next(item for item in wrong_diagram["sections"] if item["id"] == "4.3")
    scenario["blocks"] = [{"type": "paragraph", "text": "Только текст"}]
    with pytest.raises(BusinessDocumentError) as caught:
        validate_document_ast(wrong_diagram)
    assert caught.value.code == "ACTIVITY_SCENARIO_REQUIRED"

    missing_concept = _draft()
    conceptual = next(item for item in missing_concept["sections"] if item["id"] == "4.1")
    conceptual["blocks"] = [{"type": "paragraph", "text": "Только текст"}]
    with pytest.raises(BusinessDocumentError) as caught:
        validate_document_ast(missing_concept)
    assert caught.value.code == "CONCEPTUAL_DIAGRAM_REQUIRED"

    unrenderable_activity = _draft()
    scenario = next(item for item in unrenderable_activity["sections"] if item["id"] == "4.3")
    scenario["blocks"] = [
        {"type": "paragraph", "text": "Сопровождающий текст"},
        {
            "type": "plantuml",
            "source": "исходный код, который renderer может отклонить",
        },
    ]
    rendered = render_document_ast(validate_document_ast(unrenderable_activity))
    assert "исходный код, который renderer может отклонить" in rendered


@pytest.mark.p0
def test_change_plan_cannot_rewrite_unlisted_sections_or_use_stale_section_hash(database):
    document = _request_and_complete_draft(_create())
    original = deepcopy(document["current_revision"])
    comment = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {"revision_id": original["revision_id"], "section_id": None, "text": "Добавить метрику ошибок", "anchor": None},
        ),
    )
    document = document_queries.get_document(comment["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": original["revision_id"]}),
    )
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {
                "change_plan": {
                    "schema_version": "1",
                    "base_revision_id": original["revision_id"],
                    "source_state_version": requested["state_version"],
                    "acknowledged_no_change_event_ids": [],
                    "operations": [
                        {
                            "operation_id": "op-1",
                            "type": "REPLACE_SECTION_CONTENT",
                            "section_id": "5.5",
                            "expected_section_hash": "sha256:" + "0" * 64,
                            "source_event_ids": [comment_event.id],
                            "content": {"blocks": [{"type": "paragraph", "text": "Новая метрика"}]},
                        }
                    ],
                }
            },
        )
    assert caught.value.code == "SECTION_HASH_CONFLICT"
    assert document_queries.list_revisions(document["document_id"]) == [original]


@pytest.mark.p0
def test_no_op_review_agrees_existing_revision_without_duplicate(database):
    document = _request_and_complete_draft(_create())
    original_revision = deepcopy(document["current_revision"])
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": original_revision["revision_id"]}),
    )
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": original_revision["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [],
            }
        },
    )
    assert document["lifecycle_state"] == "AGREED"
    assert document["current_revision"] == original_revision
    assert BusinessDocumentRevision.select().count() == 1
    assert BusinessDocumentEvent.select().where(BusinessDocumentEvent.event_type == "ReviewAgreedWithoutChanges").count() == 1


@pytest.mark.p0
def test_no_op_requires_explicit_disposition_for_current_review_inputs(database):
    document = _request_and_complete_draft(_create())
    original_revision = deepcopy(document["current_revision"])
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {"revision_id": original_revision["revision_id"], "section_id": None, "text": "Проверено, менять не нужно", "anchor": None},
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document, comment_disposition="NO_CHANGE")
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": original_revision["revision_id"]}),
    )
    job = BusinessDocumentJobQueue.claim("no-op-worker")
    assert job is not None and job.id == requested["job_id"]
    plan = {
        "change_plan": {
            "schema_version": "1",
            "base_revision_id": original_revision["revision_id"],
            "source_state_version": requested["state_version"],
            "acknowledged_no_change_event_ids": [],
            "operations": [],
        }
    }
    base_section = next(section for section in original_revision["document_ast"]["sections"] if section["id"] == "5.5")
    plan["change_plan"]["operations"] = [
        {
            "operation_id": "invalid-no-change-source",
            "type": "REPLACE_SECTION_CONTENT",
            "section_id": "5.5",
            "expected_section_hash": section_hash(base_section),
            "source_event_ids": [comment_event.id],
            "content": {"blocks": [{"type": "paragraph", "text": "Не должно примениться"}]},
        }
    ]
    with pytest.raises(BusinessDocumentError) as caught:
        job_completion.complete(TENANT, "no-op-worker", job.id, plan, job.lease_token)
    assert caught.value.code == "COMMENT_CHANGE_NOT_CONFIRMED"

    plan["change_plan"]["operations"] = []
    with pytest.raises(BusinessDocumentError) as caught:
        job_completion.complete(TENANT, "no-op-worker", job.id, plan, job.lease_token)
    assert caught.value.code == "CHANGE_INPUT_OMITTED"

    plan["change_plan"]["acknowledged_no_change_event_ids"] = [comment_event.id]
    agreed = job_completion.complete(TENANT, "no-op-worker", job.id, plan, job.lease_token)
    assert agreed["lifecycle_state"] == "AGREED"
    assert agreed["current_revision"] == original_revision
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.p0
def test_accepted_proposal_cannot_be_silently_omitted(database):
    document = _request_and_complete_draft(_create(), proposals=[{"text": "Добавить метрику"}])
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "ACCEPTED"}),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    comment = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "text": "Уточнить описание", "anchor": None},
        ),
    )
    document = document_queries.get_document(comment["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    base_section = next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {
                "change_plan": {
                    "schema_version": "1",
                    "base_revision_id": document["current_revision"]["revision_id"],
                    "source_state_version": requested["state_version"],
                    "acknowledged_no_change_event_ids": [],
                    "operations": [
                        {
                            "operation_id": "op-comment-only",
                            "type": "REPLACE_SECTION_CONTENT",
                            "section_id": "5.5",
                            "expected_section_hash": section_hash(base_section),
                            "source_event_ids": [comment_event.id],
                            "content": {"blocks": [{"type": "paragraph", "text": "Обновлено"}]},
                        }
                    ],
                }
            },
        )
    assert caught.value.code == "ACCEPTED_PROPOSAL_OMITTED"
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.p0
def test_accepted_proposal_cannot_be_acknowledged_as_no_change(database):
    document = _request_and_complete_draft(_create(), proposals=[{"text": "Добавить метрику"}])
    proposal_id = document["protocol"]["proposals"][0]["proposal_id"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "DECIDE_PROPOSAL", {"proposal_id": proposal_id, "decision": "ACCEPTED"}),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    accepted_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "ProposalDecided"))
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )

    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {
                "change_plan": {
                    "schema_version": "1",
                    "base_revision_id": document["current_revision"]["revision_id"],
                    "source_state_version": requested["state_version"],
                    "acknowledged_no_change_event_ids": [accepted_event.id],
                    "operations": [],
                }
            },
        )

    assert caught.value.code == "ACCEPTED_PROPOSAL_ACKNOWLEDGED_NO_CHANGE"
    assert BusinessDocumentRevision.select().count() == 1


@pytest.mark.p0
def test_change_source_must_match_active_cycle_and_target_section(database):
    document = _request_and_complete_draft(_create(), review_questions=_question_batch("REVIEW", "audience"))
    question = document["protocol"]["questions"][0]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ANSWER_QUESTION",
            {"question_id": question["question_id"], "selected_option_id": "individuals", "custom_answer": None},
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    answer_event = next(
        event
        for event in BusinessDocumentEvent.select().where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "QuestionAnswered"))
        if event.payload["question_id"] == question["question_id"]
    )
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": document["current_revision"]["revision_id"]}),
    )
    wrong_section = next(section for section in document["current_revision"]["document_ast"]["sections"] if section["id"] == "5.5")
    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {
                "change_plan": {
                    "schema_version": "1",
                    "base_revision_id": document["current_revision"]["revision_id"],
                    "source_state_version": requested["state_version"],
                    "acknowledged_no_change_event_ids": [],
                    "operations": [
                        {
                            "operation_id": "op-wrong-section",
                            "type": "REPLACE_SECTION_CONTENT",
                            "section_id": "5.5",
                            "expected_section_hash": section_hash(wrong_section),
                            "source_event_ids": [answer_event.id],
                            "content": {"blocks": [{"type": "paragraph", "text": "Не должно примениться"}]},
                        }
                    ],
                }
            },
        )
    assert caught.value.code == "CHANGE_SOURCE_SECTION_CONFLICT"


@pytest.mark.p0
def test_confirmed_anchored_comment_may_request_a_cross_section_change(database):
    User.create(id="author-2", nickname="Второй автор", email="author-2@example.com")
    document = _request_and_complete_draft(_create())
    revision = document["current_revision"]
    selected_text = revision["section_texts"]["3.3"]
    response = document_commands.execute(
        "collaborator-tenant",
        "author-2",
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": revision["revision_id"],
                "section_id": "3.3",
                "text": "Добавить чатбот и описать через него подачу заявки",
                "anchor": {
                    "revision_id": revision["revision_id"],
                    "section_id": "3.3",
                    "selected_text": selected_text,
                    "prefix": "",
                    "suffix": "",
                    "start_offset": 0,
                    "end_offset": len(selected_text),
                },
            },
        ),
        access_role="MODERATOR_CREATOR",
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": revision["revision_id"]}),
    )
    target = next(section for section in revision["document_ast"]["sections"] if section["id"] == "4.3")
    changed = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": revision["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [
                    {
                        "operation_id": "op-cross-section-comment",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": "4.3",
                        "expected_section_hash": section_hash(target),
                        "source_event_ids": [comment_event.id],
                        "content": {"blocks": required_section_blocks("4.3", "Подача заявки через чатбот.")},
                    }
                ],
            }
        },
    )

    assert changed["lifecycle_state"] == "AGREED"
    assert changed["current_revision"]["revision_number"] == 2
    assert "Подача заявки через чатбот" in changed["current_revision"]["section_texts"]["4.3"]
    assert changed["current_revision"]["change_basis"] == [
        {
            "event_id": comment_event.id,
            "actor_id": "author-2",
            "actor_type": "USER",
            "actor_name": "Второй автор",
            "actor_login": "author-2@example.com",
            "created_at": comment_event.create_time,
            "type": "COMMENT",
            "title": "Комментарий автора",
            "summary": "Добавить чатбот и описать через него подачу заявки",
            "details": selected_text,
            "section_id": "3.3",
        }
    ]


@pytest.mark.p0
def test_confirmed_document_comment_may_authorize_changes_in_multiple_sections(database):
    document = _request_and_complete_draft(_create())
    revision = document["current_revision"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": revision["revision_id"],
                "section_id": None,
                "text": "Унифицировать терминологию во всём документе",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": revision["revision_id"]}),
    )
    targets = {section["id"]: section for section in revision["document_ast"]["sections"] if section["id"] in {"3.3", "5.5"}}
    changed = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": revision["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [
                    {
                        "operation_id": f"op-document-comment-{section_id}",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": section_id,
                        "expected_section_hash": section_hash(targets[section_id]),
                        "source_event_ids": [comment_event.id],
                        "content": {"blocks": [{"type": "paragraph", "text": text}]},
                    }
                    for section_id, text in (
                        ("3.3", "Единый термин в описании потребности."),
                        ("5.5", "Единый термин в критериях приёмки."),
                    )
                ],
            }
        },
    )

    assert changed["current_revision"]["revision_number"] == 2
    assert "Единый термин" in changed["current_revision"]["section_texts"]["3.3"]
    assert "Единый термин" in changed["current_revision"]["section_texts"]["5.5"]
    assert changed["current_revision"]["change_basis"][0]["section_id"] is None


@pytest.mark.p0
def test_link_only_eva_binding_can_be_reconnected_without_rewriting_creation_event(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    link_only = {
        "page_url": "http://host.docker.internal:8084//project/Document/DOC-001883",
        "status": "LINK_ONLY",
        "capabilities": ["OPEN"],
        "connector_id": None,
        "project_id": None,
        "document_id": None,
        "document_code": "DOC-001883",
        "document_name": None,
    }
    connected = {
        **link_only,
        "page_url": "https://eva.example.com/project/Document/DOC-001883",
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "connector-business-documents",
        "project_id": "CmfProject:business-documents",
        "document_id": "CmfDocument:doc-1",
        "document_name": "Документ1",
    }
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda _actor_id, _page_url: link_only))
    document = _create(
        schema_version="2",
        eva_page_url=link_only["page_url"],
        eva_decision={"mode": "BIND", "confirm_replace": True},
    )
    created_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "DocumentCreated"))
    original_created_payload = deepcopy(created_event.payload)
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda _actor_id, _page_url: connected))

    rebound = eva_synchronization.rebind_eva(
        TENANT,
        AUTHOR,
        document["document_id"],
        {"expected_state_version": document["state_version"]},
        is_admin=True,
    )

    assert rebound["state_version"] == document["state_version"] + 1
    assert rebound["eva_binding"] == connected
    assert rebound["permissions"]["delete"] is True
    assert BusinessDocumentEvent.get_by_id(created_event.id).payload == original_created_payload
    assert original_created_payload["title"] == document["title"]
    resolution_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "EvaBindingResolved"))
    assert resolution_event.payload == {
        "document_title": document["title"],
        "previous_eva_binding": link_only,
        "eva_binding": connected,
    }


@pytest.mark.p0
def test_verified_eva_binding_supports_governed_pull_and_outbound_change(database, monkeypatch):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    binding = {
        "page_url": "https://eva.example.com/project/Document/BR-42",
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA", "CREATE_EVA_CHANGE"],
        "connector_id": "connector-1",
        "project_id": "project-1",
        "document_id": "eva-document-1",
        "document_code": "BR-42",
        "document_name": "Требования EVA",
        "remote_version": "1|published-1|2026-09-01",
        "remote_content_hash": "sha256:initial",
        "last_pulled_content_hash": None,
    }
    monkeypatch.setattr(
        EvaDocumentChangeService,
        "resolve_page_url",
        staticmethod(lambda _actor_id, _page_url: binding),
    )
    document = _request_and_complete_draft(
        _create(
            title=binding["document_name"],
            schema_version="2",
            eva_page_url=binding["page_url"],
            eva_decision={"mode": "BIND", "confirm_replace": True},
        )
    )
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "APPLY_CHANGES",
            {"base_revision_id": document["current_revision"]["revision_id"]},
        ),
    )
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": document["current_revision"]["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [],
            }
        },
    )
    assert document["lifecycle_state"] == "AGREED"
    assert document["eva_binding"]["status"] == "CONNECTED"

    refreshed_binding = {
        **binding,
        "remote_version": "2|published-2|2026-09-01",
        "remote_content_hash": "sha256:changed",
    }
    read_connected_page = EvaDocumentChangeService.read_connected_page
    monkeypatch.setattr(
        EvaDocumentChangeService,
        "read_connected_page",
        staticmethod(lambda _actor_id, _binding: (refreshed_binding, "# Изменённая EVA")),
    )
    update_status = eva_synchronization.check_eva_update(
        TENANT,
        AUTHOR,
        document["document_id"],
        is_admin=True,
    )
    assert update_status == {
        "document_id": document["document_id"],
        "changed": True,
        "direction": "FROM_EVA",
        "remote_version": "2|published-2|2026-09-01",
        "baseline_version": "1|published-1|2026-09-01",
        "can_pull": True,
    }

    captured_change = {}

    def create_change(_tenant_id, _actor_id, raw, *, allow_prefilled_draft=False):
        captured_change.update(raw)
        captured_change["allow_prefilled_draft"] = allow_prefilled_draft
        return {"change_id": "eva-change-1", "draft_markdown": raw["draft_markdown"]}

    monkeypatch.setattr(EvaDocumentChangeService, "create_change", staticmethod(create_change))
    outbound = eva_synchronization.create_eva_change_from_revision(
        TENANT,
        AUTHOR,
        document["document_id"],
        {"expected_state_version": document["state_version"]},
    )
    assert outbound["change_id"] == "eva-change-1"
    assert captured_change["connector_id"] == "connector-1"
    assert captured_change["document_id"] == "eva-document-1"
    assert captured_change["document_name"] == document["title"]
    assert captured_change["draft_markdown"] == document["current_revision"]["body_markdown"]
    assert captured_change["allow_prefilled_draft"] is True

    class EvaClient:
        @staticmethod
        def get_document_for_edit(_document_id):
            return {
                "id": "eva-document-1",
                "version": "2|published-2|2026-09-01",
                "html": "<h1>Требования EVA</h1><p>Добавлен новый процесс.</p>",
            }

    monkeypatch.setattr(
        EvaDocumentChangeService,
        "_connector",
        staticmethod(lambda _connector_id, _actor_id: (None, EvaClient())),
    )
    monkeypatch.setattr(EvaDocumentChangeService, "read_connected_page", read_connected_page)
    pulled = eva_synchronization.pull_from_eva(
        TENANT,
        AUTHOR,
        document["document_id"],
        {"expected_state_version": document["state_version"]},
        is_admin=True,
    )
    assert pulled["sync"]["changed"] is True
    assert pulled["document"]["lifecycle_state"] == "REVIEW"
    assert pulled["document"]["active_review_cycle"] == document["active_review_cycle"] + 1
    assert pulled["document"]["permissions"]["delete"] is True
    assert pulled["document"]["allowed_commands"] == [
        "DECIDE_PROPOSAL",
        "ADD_COMMENT",
        "ARCHIVE",
        "REQUEST_REVIEW_ASSESSMENT",
    ]
    assert pulled["document"]["eva_binding"]["last_pulled_content_hash"].startswith("sha256:")
    pull_event = BusinessDocumentEvent.get(BusinessDocumentEvent.id == pulled["sync"]["event_id"])
    assert pull_event.event_type == "EvaDocumentPulled"
    assert "Добавлен новый процесс" in pull_event.payload["remote_markdown"]


@pytest.mark.p0
def test_export_from_review_requires_agreed_revision(database):
    document = _request_and_complete_draft(_create())
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(
                document,
                "REQUEST_EXPORT",
                {"revision_id": document["current_revision"]["revision_id"], "format": "DOCX"},
            ),
        )
    assert caught.value.code == "AGREED_REVISION_REQUIRED"


@pytest.mark.p1
@pytest.mark.parametrize(
    ("page", "page_size"),
    [(0, 20), (1, 0), (1, 101), ("1", 20)],
)
def test_list_pagination_boundaries_are_rejected(database, page, page_size):
    _create()

    with pytest.raises(BusinessDocumentError) as caught:
        document_queries.list_documents(AUTHOR, page=page, page_size=page_size)

    assert caught.value.code == "INVALID_PAGINATION"


@pytest.mark.p0
def test_answer_requires_exactly_one_valid_option_or_custom_text(database):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(requested["job_id"], _question_batch(), worker="worker")
    question_id = document["protocol"]["questions"][0]["question_id"]

    invalid_cases = [
        (
            {"question_id": question_id, "selected_option_id": "individuals", "custom_answer": "Свой ответ"},
            "INVALID_ANSWER",
        ),
        ({"question_id": question_id, "selected_option_id": None, "custom_answer": "  "}, "INVALID_ANSWER"),
        ({"question_id": question_id, "selected_option_id": "missing", "custom_answer": None}, "INVALID_OPTION"),
    ]
    for index, (payload, error_code) in enumerate(invalid_cases):
        with pytest.raises(BusinessDocumentError) as caught:
            document_commands.execute(
                TENANT,
                AUTHOR,
                document["document_id"],
                _command(document, "ANSWER_QUESTION", payload, key=f"invalid-answer-{index}"),
            )
        assert caught.value.code == error_code

    assert BusinessDocumentAnswer.select().count() == 0
    after = document_queries.get_document(document["document_id"], AUTHOR)
    assert after["state_version"] == document["state_version"]


@pytest.mark.p0
def test_comment_anchor_requires_current_revision_and_preserves_selected_text(database):
    document = _request_and_complete_draft(_create())
    revision = document["current_revision"]
    section = next(item for item in revision["document_ast"]["sections"] if item["id"] == "5.5")
    section_text = render_section_text(section)
    selected_text = "Раздел 5.5: исходная версия."
    start_offset = section_text.index(selected_text)

    def anchor(*, anchor_revision=None, anchor_section="5.5", start=start_offset, end=None, selected=selected_text):
        end = start + len(selected) if end is None else end
        return {
            "revision_id": anchor_revision or revision["revision_id"],
            "section_id": anchor_section,
            "selected_text": selected,
            "prefix": section_text[max(0, start - 64) : start],
            "suffix": section_text[end : end + 64],
            "start_offset": start,
            "end_offset": end,
        }

    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(
                document,
                "ADD_COMMENT",
                {
                    "revision_id": "stale-revision",
                    "section_id": "5.5",
                    "text": "Уточнить",
                    "anchor": anchor(anchor_revision="stale-revision"),
                },
                key="stale-comment",
            ),
        )
    assert caught.value.code == "COMMENT_REVISION_CONFLICT"

    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(
                document,
                "ADD_COMMENT",
                {
                    "revision_id": revision["revision_id"],
                    "section_id": "5.5",
                    "text": "Уточнить",
                    "anchor": anchor(selected="Фрагмент, которого нет"),
                },
                key="missing-anchor",
            ),
        )
    assert caught.value.code == "INVALID_COMMENT_ANCHOR"

    for key, payload in (
        (
            "missing-section",
            {
                "revision_id": revision["revision_id"],
                "section_id": "9.9",
                "text": "Уточнить",
                "anchor": anchor(anchor_section="9.9"),
            },
        ),
        (
            "anchor-revision-mismatch",
            {
                "revision_id": revision["revision_id"],
                "section_id": "5.5",
                "text": "Уточнить",
                "anchor": anchor(anchor_revision="other-revision"),
            },
        ),
        (
            "anchor-offset-mismatch",
            {
                "revision_id": revision["revision_id"],
                "section_id": "5.5",
                "text": "Уточнить",
                "anchor": anchor(start=1, end=1 + len(selected_text)),
            },
        ),
    ):
        with pytest.raises(BusinessDocumentError) as caught:
            document_commands.execute(
                TENANT,
                AUTHOR,
                document["document_id"],
                _command(document, "ADD_COMMENT", payload, key=key),
            )
        assert caught.value.code in {"COMMENT_SECTION_NOT_FOUND", "INVALID_COMMENT_ANCHOR"}

    immutable_anchor = anchor()
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": revision["revision_id"],
                "section_id": "5.5",
                "text": "Добавить метрику отказов",
                "anchor": immutable_anchor,
            },
            key="valid-anchor",
        ),
    )
    stored = BusinessDocumentComment.get(BusinessDocumentComment.document_id == document["document_id"])
    assert stored.anchor == immutable_anchor
    assert response["state_version"] == document["state_version"] + 1
    document = document_queries.get_document(response["document_id"], AUTHOR)
    assert document["protocol"]["comments"][0]["anchor_status"] == "ANCHORED"

    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    document = _complete_review_assessment(document)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "APPLY_CHANGES", {"base_revision_id": revision["revision_id"]}),
    )
    base_section = next(section for section in revision["document_ast"]["sections"] if section["id"] == "5.5")
    document = _complete(
        requested["job_id"],
        {
            "change_plan": {
                "schema_version": "1",
                "base_revision_id": revision["revision_id"],
                "source_state_version": requested["state_version"],
                "acknowledged_no_change_event_ids": [],
                "operations": [
                    {
                        "operation_id": "op-anchor",
                        "type": "REPLACE_SECTION_CONTENT",
                        "section_id": "5.5",
                        "expected_section_hash": section_hash(base_section),
                        "source_event_ids": [comment_event.id],
                        "content": {"blocks": [{"type": "paragraph", "text": "Обновленный раздел"}]},
                    }
                ],
            }
        },
        worker="anchor-worker",
    )
    assert document["protocol"]["comments"][0]["anchor_status"] == "ORPHANED"
    assert BusinessDocumentComment.get_by_id(stored.id).anchor == immutable_anchor


@pytest.mark.p0
def test_comment_anchor_context_window_is_utf16_surrogate_safe(database):
    document = _create()
    assessed = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(assessed["job_id"], {"schema_version": "1", "outcome": "COMPLETE", "questions": []})
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_DRAFT"))
    draft = _draft()
    section = next(item for item in draft["sections"] if item["id"] == "5.5")
    section["blocks"] = [{"type": "paragraph", "text": "😀" + "a" * 63 + "SELECT"}]
    document = _complete(
        requested["job_id"],
        {
            "draft": draft,
            "review_questions": {"schema_version": "1", "outcome": "COMPLETE", "questions": []},
            "proposals": [],
        },
        worker="emoji-anchor-worker",
    )
    revision = document["current_revision"]
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": revision["revision_id"],
                "section_id": "5.5",
                "text": "Emoji boundary",
                "anchor": {
                    "revision_id": revision["revision_id"],
                    "section_id": "5.5",
                    "selected_text": "SELECT",
                    "prefix": "a" * 63,
                    "suffix": "",
                    "start_offset": 65,
                    "end_offset": 71,
                },
            },
        ),
    )
    assert response["accepted"] is True


@pytest.mark.p0
def test_review_reassessment_appends_questions_and_proposals_without_changing_body(database):
    document = _request_and_complete_draft(_create())
    assert document["current_revision"]["section_texts"] == {section["id"]: render_section_text(section) for section in document["current_revision"]["document_ast"]["sections"]}
    original_revision = deepcopy(document["current_revision"])
    comment_response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": original_revision["revision_id"],
                "section_id": None,
                "text": "Нужна метрика бизнес-отказов",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(comment_response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "REQUEST_REVIEW_ASSESSMENT"),
    )
    document = _complete(
        requested["job_id"],
        {
            "schema_version": "1",
            "questions": [
                {
                    "semantic_tag": "monitoring.business_failures",
                    "target_section_id": "5.5",
                    "text": "Какие бизнес-отказы учитывать?",
                    "options": [
                        {"option_id": "all", "label": "Все отказы"},
                        {"option_id": "final", "label": "Только финальные"},
                    ],
                    "allow_custom_answer": True,
                }
            ],
            "proposals": [
                {
                    "target_section_id": "5.5",
                    "text": "Добавить долю бизнес-отказов",
                    "rationale": "Комментарий автора требует наблюдаемой метрики",
                    "source_event_ids": [comment_event.id],
                }
            ],
            "comment_dispositions": [
                {
                    "comment_event_id": comment_event.id,
                    "disposition": "NEEDS_QUESTION",
                    "question_semantic_tag": "monitoring.business_failures",
                }
            ],
        },
    )

    assert document["current_revision"] == original_revision
    assert len(document["protocol"]["questions"]) == 1
    assert len(document["protocol"]["proposals"]) == 1
    assert document["protocol"]["proposals"][0]["decision"] == "PENDING"
    assert "ANSWER_QUESTION" in document["allowed_commands"]
    assert "APPLY_CHANGES" not in document["allowed_commands"]
    proposal = BusinessDocumentProposal.get(BusinessDocumentProposal.document_id == document["document_id"])
    question = BusinessDocumentQuestion.get(BusinessDocumentQuestion.document_id == document["document_id"])
    assert comment_event.id in proposal.source_event_ids
    assert comment_event.id in question.source_event_ids
    assert document["protocol"]["comments"][0]["disposition"]["question_id"] == question.id
    assert BusinessDocumentQuestion.select().where(BusinessDocumentQuestion.document_id == document["document_id"]).count() == 1


@pytest.mark.p0
def test_review_plan_requires_complete_comment_dispositions(database):
    document = _request_and_complete_draft(_create())
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": document["current_revision"]["revision_id"],
                "section_id": None,
                "text": "Проверить сценарий отказа",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))

    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {"schema_version": "1", "questions": [], "proposals": [], "comment_dispositions": []},
        )

    assert caught.value.code == "COMMENT_DISPOSITION_INCOMPLETE"


@pytest.mark.p0
def test_review_reassessment_rejects_reused_answered_question_tag_before_insertion(database):
    document = _request_and_complete_draft(_create())
    response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": document["current_revision"]["revision_id"],
                "section_id": None,
                "text": "Добавить модераторов",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))
    document = _complete(
        requested["job_id"],
        {
            "schema_version": "1",
            "questions": [
                {
                    "semantic_tag": "MODERATOR_SCOPE",
                    "target_section_id": "3.1",
                    "text": "Каких модераторов добавить?",
                    "options": [{"option_id": "all", "label": "Всех"}, {"option_id": "selected", "label": "Только отдельных"}],
                    "allow_custom_answer": True,
                }
            ],
            "proposals": [],
            "comment_dispositions": [
                {
                    "comment_event_id": comment_event.id,
                    "disposition": "NEEDS_QUESTION",
                    "question_semantic_tag": "moderator_scope",
                }
            ],
        },
    )
    question = document["protocol"]["questions"][0]
    answered = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ANSWER_QUESTION",
            {"question_id": question["question_id"], "selected_option_id": "all", "custom_answer": None},
        ),
    )
    document = document_queries.get_document(answered["document_id"], AUTHOR)
    requested = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "REQUEST_REVIEW_ASSESSMENT", key="reassess-closed-question"),
    )

    with pytest.raises(BusinessDocumentError) as caught:
        _complete(
            requested["job_id"],
            {
                "schema_version": "1",
                "questions": [
                    {
                        "semantic_tag": "MODERATOR_SCOPE",
                        "target_section_id": "3.1",
                        "text": "Нужно ли добавить всех модераторов без исключения?",
                        "options": [{"option_id": "yes", "label": "Да"}, {"option_id": "no", "label": "Нет"}],
                        "allow_custom_answer": True,
                    }
                ],
                "proposals": [],
                "comment_dispositions": [
                    {
                        "comment_event_id": comment_event.id,
                        "disposition": "NEEDS_QUESTION",
                        "question_semantic_tag": "MODERATOR_SCOPE",
                    }
                ],
            },
        )

    assert caught.value.code == "COMMENT_DISPOSITION_QUESTION_CLOSED"
    assert caught.value.details["question_semantic_tag"] == "moderator_scope"
    assert "new semantic_tag" in caught.value.details["required_action"]
    assert BusinessDocumentQuestion.select().where(BusinessDocumentQuestion.document_id == document["document_id"]).count() == 1


@pytest.mark.p0
def test_semantic_dedupe_keeps_questions_and_proposals_immutable(database):
    document = _create()
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT"))
    document = _complete(requested["job_id"], _question_batch(semantic_tag="AUDIENCE.USERS"))
    question = document["protocol"]["questions"][0]
    answered = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ANSWER_QUESTION",
            {"question_id": question["question_id"], "selected_option_id": "individuals", "custom_answer": None},
        ),
    )
    document = document_queries.get_document(answered["document_id"], AUTHOR)
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_INTAKE_ASSESSMENT", key="repeat-intake"))
    document = _complete(requested["job_id"], _question_batch(semantic_tag="audience.users"))
    assert BusinessDocumentQuestion.select().where(BusinessDocumentQuestion.document_id == document["document_id"]).count() == 1
    intake_event = (
        BusinessDocumentEvent.select()
        .where((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "IntakeAssessed"))
        .order_by(BusinessDocumentEvent.sequence.desc())
        .get()
    )
    assert intake_event.payload["question_count"] == 0
    assert intake_event.payload["outcome"] == "COMPLETE"
    assert "REQUEST_DRAFT" in document["allowed_commands"]

    document = _request_and_complete_draft(document)
    comment_response = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(
            document,
            "ADD_COMMENT",
            {
                "revision_id": document["current_revision"]["revision_id"],
                "section_id": None,
                "text": "Добавить метрику отказов",
                "anchor": None,
            },
        ),
    )
    document = document_queries.get_document(comment_response["document_id"], AUTHOR)
    comment_event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.event_type == "AuthorCommentAdded"))

    def assess(proposal_text, rationale):
        nonlocal document
        request = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "REQUEST_REVIEW_ASSESSMENT"))
        document = _complete(
            request["job_id"],
            {
                "schema_version": "1",
                "questions": [],
                "proposals": [
                    {
                        "target_section_id": "5.5",
                        "text": proposal_text,
                        "rationale": rationale,
                        "source_event_ids": [comment_event.id],
                    }
                ],
                "comment_dispositions": [{"comment_event_id": comment_event.id, "disposition": "CONFIRMED_CHANGE"}],
            },
        )

    assess("Добавить метрику отказов", "Первоначальное обоснование")
    original = BusinessDocumentProposal.get(BusinessDocumentProposal.document_id == document["document_id"])
    original_snapshot = (original.id, original.text, original.rationale, original.source_event_ids, original.create_time)
    assess("  ДОБАВИТЬ   МЕТРИКУ ОТКАЗОВ  ", "Новое обоснование не должно перезаписать строку")
    proposals = list(BusinessDocumentProposal.select().where(BusinessDocumentProposal.document_id == document["document_id"]))
    assert len(proposals) == 1
    assert (proposals[0].id, proposals[0].text, proposals[0].rationale, proposals[0].source_event_ids, proposals[0].create_time) == original_snapshot


@pytest.mark.p0
def test_export_requires_agreed_lifecycle_and_current_revision(database):
    document = _request_and_complete_draft(_create())
    with pytest.raises(BusinessDocumentError) as caught:
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(
                document,
                "REQUEST_EXPORT",
                {"revision_id": document["current_revision"]["revision_id"], "format": "EVA_WIKI"},
            ),
        )

    assert caught.value.code == "AGREED_REVISION_REQUIRED"
    assert BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "GENERATE_EXPORT").count() == 0


@pytest.mark.p0
@pytest.mark.parametrize("command_type", ["APPLY_CHANGES", "PREPARE_CHANGES"])
def test_eva_pull_in_current_cycle_requires_reassessment_for_direct_commands(database, monkeypatch, command_type):
    from api.apps.business_documents.eva_changes import EvaDocumentChangeService

    binding = {
        "page_url": "https://eva.example.com/project/Document/BR-42",
        "status": "CONNECTED",
        "capabilities": ["OPEN", "PULL_FROM_EVA"],
        "connector_id": "connector-1",
        "project_id": "project-1",
        "document_id": "eva-document-1",
        "document_name": "Требования EVA",
        "remote_content_hash": "sha256:initial",
    }
    client = SimpleNamespace(get_document_for_edit=lambda _document_id: {"html": "<h1>Требования EVA</h1><p>Новое требование</p>", "version": "2"})
    monkeypatch.setattr(EvaDocumentChangeService, "resolve_page_url", staticmethod(lambda *_args: binding))
    monkeypatch.setattr(EvaDocumentChangeService, "_connector", staticmethod(lambda *_args: (None, client)))
    document = _request_and_complete_draft(_create(title=binding["document_name"], schema_version="2", eva_page_url=binding["page_url"], eva_decision={"mode": "BIND", "confirm_replace": True}))
    document = _complete_review_assessment(document)
    original_cycle = document["active_review_cycle"]
    pulled = eva_synchronization.pull_from_eva(TENANT, AUTHOR, document["document_id"], {"expected_state_version": document["state_version"]})
    document = pulled["document"]
    assert pulled["sync"]["changed"]
    assert document["active_review_cycle"] == original_cycle
    assert "REQUEST_REVIEW_ASSESSMENT" in document["allowed_commands"]
    assert command_type not in document["allowed_commands"]
    command = _command(document, command_type, {"base_revision_id": document["current_revision"]["revision_id"]})
    for _ in range(2):
        with pytest.raises(BusinessDocumentError) as caught:
            document_commands.execute(TENANT, AUTHOR, document["document_id"], command)
        assert caught.value.code == "REVIEW_ASSESSMENT_REQUIRED"
    assert BusinessDocument.get_by_id(document["document_id"]).state_version == document["state_version"]
    assert not BusinessDocumentJob.select().where(BusinessDocumentJob.job_type == "PLAN_CHANGES").exists()


def test_job_snapshot_batches_review_reads_and_plan_validation_performs_no_queries(database, monkeypatch):
    from playhouse.test_utils import count_queries

    from api.apps.business_documents.adapters.review import load_review_state
    from business_documents.domain.change_plan import validate_change_inputs

    document = _request_and_complete_draft(_create())
    for number in range(10):
        document_commands.execute(
            TENANT,
            AUTHOR,
            document["document_id"],
            _command(document, "ADD_COMMENT", {"revision_id": document["current_revision"]["revision_id"], "section_id": None, "text": f"Comment {number}", "anchor": None}),
        )
        document = document_queries.get_document(document["document_id"], AUTHOR)
    document = _complete_review_assessment(document)
    row = BusinessDocument.get_by_id(document["document_id"])

    def unexpected_render(_section):
        pytest.fail("A job snapshot must not render UI section texts")

    with monkeypatch.context() as patches, count_queries(only_select=True) as snapshot_queries:
        patches.setattr("business_documents.application.queries.render_section_text", unexpected_render)
        snapshot = document_commands._job_snapshot(row.__data__, {})
    assert snapshot_queries.count <= 10
    assert len(snapshot["protocol"]["comments"]) == 10
    assert "section_texts" not in snapshot["current_revision"]
    assert {key: value for key, value in snapshot["current_revision"].items() if key != "section_hashes"} == {
        key: value for key, value in document["current_revision"].items() if key != "section_texts"
    }
    from api.apps.business_documents.ai import BusinessDocumentAI
    from api.apps.business_documents.assets import prompt_descriptor

    snapshot["prompt"] = prompt_descriptor("PLAN_CHANGES")
    new_job = SimpleNamespace(payload=snapshot, job_type="PLAN_CHANGES", attempt=1)
    old_job = SimpleNamespace(
        payload={**snapshot, "current_revision": {**snapshot["current_revision"], "section_texts": document["current_revision"]["section_texts"]}}, job_type="PLAN_CHANGES", attempt=1
    )
    assert BusinessDocumentAI._prompt(new_job).input_payload == BusinessDocumentAI._prompt(old_job).input_payload
    with count_queries(only_select=True) as review_queries:
        review = load_review_state(row.id, row.active_review_cycle)
    assert review_queries.count <= 6
    plan = {
        "operations": [{"section_id": "3.1", "source_event_ids": [comment["source_event_id"]]} for comment in snapshot["protocol"]["comments"]],
        "acknowledged_no_change_event_ids": [],
    }
    with count_queries() as validation_queries:
        inputs = validate_change_inputs(review, plan, snapshot_source_ids=frozenset(event["event_id"] for event in snapshot["source_events"]), evidence_source_refs=frozenset())
    assert validation_queries.count == 0
    assert len(inputs.source_event_ids) == 10
    assert inputs.next_lifecycle == "AGREED"


def test_assignment_event_timestamps_survive_an_advancing_orm_clock(database, monkeypatch):
    from common.time_utils import current_timestamp

    User.create(id="author-clock", nickname="Clock author", email="clock@example.com")
    document = _create(title="Assignment clock")
    now = current_timestamp()
    clock = iter(range(now, now + 100))
    monkeypatch.setattr("api.db.db_models.current_timestamp", lambda: next(clock))
    assigned = assign_business_document(
        "moderator",
        document["document_id"],
        {"owner_id": "author-clock", "expected_state_version": document["state_version"]},
        access_role="EXTENDED_MODERATOR",
    )
    event = BusinessDocumentEvent.get((BusinessDocumentEvent.document_id == document["document_id"]) & (BusinessDocumentEvent.sequence == assigned["state_version"]))
    assert event.create_time == event.update_time
    assert event.create_date == event.update_date


def test_job_stream_replays_in_order_and_fences_old_worker_attempts(database):
    document = _create()
    job = BusinessDocumentJob.create(
        id="stream-job",
        document_id=document["document_id"],
        tenant_id=TENANT,
        job_type="PLAN_CHANGES",
        status="RUNNING",
        dedupe_key="stream-job-dedupe",
        source_state_version=document["state_version"],
        base_revision_id=None,
        payload={"preview_only": True},
        attempt=1,
        max_attempts=3,
        available_at=0,
        lease_owner="worker-1",
        lease_token="lease-1",
        lease_expires_at=current_timestamp() + 60_000,
        correlation_id="stream-correlation",
    )

    assert BusinessDocumentStreamEvents.append(job.id, "section_preview", {"section_id": "1"}, lease_token="lease-1", expected_attempt=1)
    assert not BusinessDocumentStreamEvents.append(job.id, "section_preview", {"section_id": "2"}, lease_token="wrong", expected_attempt=1)
    BusinessDocumentJob.update(status="RETRY", lease_owner=None, lease_token=None, lease_expires_at=None).where(BusinessDocumentJob.id == job.id).execute()
    assert BusinessDocumentStreamEvents.append(job.id, "retry", {"reason": "TEST"})
    BusinessDocumentJob.update(status="RUNNING", attempt=2, lease_owner="worker-2", lease_token="lease-2", lease_expires_at=current_timestamp() + 60_000).where(
        BusinessDocumentJob.id == job.id
    ).execute()
    assert not BusinessDocumentStreamEvents.append(job.id, "section_preview", {"section_id": "old"}, lease_token="lease-1", expected_attempt=1)
    assert BusinessDocumentStreamEvents.append(job.id, "section_preview", {"section_id": "3"}, lease_token="lease-2", expected_attempt=2)

    replay = document_queries.read_job_stream_events(TENANT, AUTHOR, document["document_id"], job.id, 1)
    assert [(event["id"], event["attempt"], event["type"]) for event in replay["events"]] == [(2, 1, "retry"), (3, 2, "section_preview")]
    assert replay["status"] == "RUNNING"
    with pytest.raises(BusinessDocumentError):
        document_queries.read_job_stream_events(TENANT, AUTHOR, document["document_id"], "another-job", 0)
    with pytest.raises(BusinessDocumentError):
        document_queries.read_job_stream_events("another-tenant", AUTHOR, document["document_id"], job.id, 0)

    BusinessDocumentJob.update(status="COMPLETED", lease_owner=None, lease_token=None, lease_expires_at=None).where(BusinessDocumentJob.id == job.id).execute()
    document_deletion.execute(AUTHOR, document["document_id"], access_role="EXTENDED_MODERATOR")
    assert BusinessDocumentJobStreamEvent.select().count() == 0


def test_worker_publishes_checked_section_before_full_plan_is_committed(database):
    document = _request_and_complete_draft(_create())
    base = document["current_revision"]
    comment = document_commands.execute(
        TENANT,
        AUTHOR,
        document["document_id"],
        _command(document, "ADD_COMMENT", {"revision_id": base["revision_id"], "section_id": None, "text": "Уточнить контроль ошибок", "anchor": None}),
    )
    document = _complete_review_assessment(document_queries.get_document(comment["document_id"], AUTHOR))
    source_id = document["protocol"]["comments"][0]["source_event_id"]
    requested = document_commands.execute(TENANT, AUTHOR, document["document_id"], _command(document, "PREPARE_CHANGES", {"base_revision_id": base["revision_id"]}))
    before = next(section for section in base["document_ast"]["sections"] if section["id"] == "5.5")
    plan = {
        "schema_version": "1",
        "base_revision_id": base["revision_id"],
        "source_state_version": requested["state_version"],
        "acknowledged_no_change_event_ids": [],
        "operations": [
            {
                "operation_id": "stream-op",
                "type": "REPLACE_SECTION_CONTENT",
                "section_id": "5.5",
                "expected_section_hash": section_hash(before),
                "source_event_ids": [source_id],
                "content": {"blocks": [{"type": "paragraph", "text": "Новый контроль ошибок"}]},
            }
        ],
    }
    observed_during_generation = []

    class StreamingAdapter:
        def generate_stream(self, tenant_id, system_prompt, input_payload, on_chunk):
            raw = json.dumps({"change_plan": plan}, ensure_ascii=False)
            cut = raw.rfind("}]") + 1
            on_chunk(raw[:cut])
            observed_during_generation.extend(BusinessDocumentStreamEvents.read(requested["job_id"], 0)[0])
            on_chunk(raw[cut:])
            return raw

    worker = BusinessDocumentWorker(worker_id="stream-worker", ai=BusinessDocumentAI(StreamingAdapter()), lease_ms=60_000)
    assert worker.run_once() is True
    assert any(event["type"] == "section_preview" and event["payload"]["after"] == "Новый контроль ошибок" for event in observed_during_generation)
    assert BusinessDocumentJob.get_by_id(requested["job_id"]).status == "COMPLETED"
    replay = document_queries.read_job_stream_events(TENANT, AUTHOR, document["document_id"], requested["job_id"], 0)
    assert replay["events"][-1]["type"] == "preview_ready"
    assert document_queries.get_change_preview(document["document_id"], requested["job_id"])["sections"][0]["after"] == "Новый контроль ошибок"
