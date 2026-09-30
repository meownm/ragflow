import json
from contextlib import contextmanager

import pytest
from peewee import SqliteDatabase

from api.db.services import business_document_catalog_service as service
from api.db.db_models import BusinessDocument, BusinessDocumentCatalog, SystemSettings
from api.db.services.business_document_catalog_service import (
    BUSINESS_DOCUMENTS_CATALOG_SETTING,
    catalog_for_bootstrap,
    get_business_document_catalog_status,
    import_business_document_catalog,
)
from business_documents.domain.catalog_import import CatalogImportError


TABLES = (BusinessDocumentCatalog, BusinessDocument, SystemSettings)


@pytest.fixture()
def database():
    database = SqliteDatabase(":memory:")
    with database.bind_ctx(TABLES, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(TABLES)
        yield database
        database.drop_tables(TABLES)
        database.close()


def _source(title="Новый разрешённый документ"):
    return {
        "L1": [
            {
                "L1_ID": "01",
                "L1_RU": "Клиенты",
                "L2": [
                    {
                        "L2_ID": "01.01",
                        "L2_RU": "Стратегия",
                        "L3": [
                            {
                                "L3_ID": "01.01.01",
                                "L3_RU": "Цикл",
                                "L4": [
                                    {
                                        "L4_ID": "01.01.01.01",
                                        "L4_RU": "Онбординг",
                                        "L5": [{"L5_ID": "01.01.01.01.01", "L5_RU": title, "Capability_Level": "L3"}],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _document(catalog_entry_id):
    return BusinessDocument.create(
        id="document-1",
        tenant_id="tenant-1",
        owner_id="owner-1",
        chat_id="chat-1",
        catalog_entry_id=catalog_entry_id,
        title="Существующий документ",
        title_key="existing-document",
        idea="Не менять документ при обновлении справочника",
        dataset_ids=[],
        template_version="1",
        policy_version="1",
    )


def test_import_replaces_active_catalog_transactionally_and_preserves_documents(database):
    BusinessDocumentCatalog.create(
        id="obsolete",
        title="Старая запись",
        capability_level="L5",
        hierarchy={},
        details={},
        source_id="BCM_Bank",
        source_version="v23",
        source_sha256="0" * 64,
        sort_order=0,
    )
    _document("obsolete")
    content = json.dumps(_source(), ensure_ascii=False).encode()

    result = import_business_document_catalog(filename="BCM_v25.json", content=content, actor_id="admin-1")

    assert result["source_version"] == "v25"
    assert result["active_items"] == 1
    assert result["created_items"] == 1
    assert result["deactivated_items"] == 1
    assert BusinessDocumentCatalog.get_by_id("obsolete").is_active is False
    document = BusinessDocument.get_by_id("document-1")
    assert document.catalog_entry_id == "obsolete"
    assert document.title == "Существующий документ"
    envelope = json.loads(SystemSettings.get_by_id(BUSINESS_DOCUMENTS_CATALOG_SETTING).value)
    assert envelope["imported_by"] == "admin-1"
    assert envelope["catalog_items"] == 1
    assert "catalog" not in envelope
    assert catalog_for_bootstrap()["source_version"] == "v25"
    assert get_business_document_catalog_status()["storage"] == "uploaded"


def test_reimport_is_idempotent(database):
    content = json.dumps(_source(), ensure_ascii=False).encode()
    import_business_document_catalog(filename="BCM_v25.json", content=content, actor_id="admin-1")

    result = import_business_document_catalog(filename="BCM_v25.json", content=content, actor_id="admin-1")

    assert result["created_items"] == 0
    assert result["updated_items"] == 0
    assert result["reactivated_items"] == 0
    assert result["deactivated_items"] == 0


def test_invalid_upload_does_not_change_catalog_or_setting(database):
    BusinessDocumentCatalog.create(
        id="current",
        title="Текущая запись",
        capability_level="L5",
        hierarchy={},
        details={},
        source_id="BCM_Bank",
        source_version="v24",
        source_sha256="0" * 64,
        sort_order=0,
    )

    with pytest.raises(CatalogImportError, match="L1"):
        import_business_document_catalog(filename="BCM_v25.json", content=b"{}", actor_id="admin-1")

    assert BusinessDocumentCatalog.get_by_id("current").is_active is True
    assert SystemSettings.select().count() == 0


def test_database_conflict_rolls_back_all_catalog_changes(database):
    source = _source()
    l5_items = source["L1"][0]["L2"][0]["L3"][0]["L4"][0]["L5"]
    l5_items.append({"L5_ID": "01.01.01.01.02", "L5_RU": "Конфликтующая запись"})
    BusinessDocumentCatalog.create(
        id="01.01.01.01.02",
        title="Запись другого источника",
        capability_level="L5",
        hierarchy={},
        details={},
        source_id="other",
        source_version="1",
        source_sha256="0" * 64,
        sort_order=0,
    )

    with pytest.raises(CatalogImportError, match="already owned"):
        import_business_document_catalog(
            filename="BCM_v25.json",
            content=json.dumps(source, ensure_ascii=False).encode(),
            actor_id="admin-1",
        )

    assert BusinessDocumentCatalog.get_or_none(BusinessDocumentCatalog.id == "01.01.01.01.01") is None
    assert SystemSettings.select().count() == 0


def test_large_catalog_content_is_not_copied_into_system_settings(database):
    source = _source()
    source["L1"][0]["L2"][0]["L3"][0]["L4"][0]["L5"][0]["Notes"] = "x" * 70_000

    import_business_document_catalog(
        filename="BCM_v25.json",
        content=json.dumps(source, ensure_ascii=False).encode(),
        actor_id="admin-1",
    )

    value = SystemSettings.get_by_id(BUSINESS_DOCUMENTS_CATALOG_SETTING).value
    assert len(value.encode()) < 65_535
    assert "catalog" not in json.loads(value)
    assert catalog_for_bootstrap()["items"][0]["details"]["notes"] == "x" * 70_000


def test_import_holds_catalog_lock_while_synchronizing(database, monkeypatch):
    locked = False

    @contextmanager
    def tracked_lock(_database):
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    synchronize = service.synchronize_business_document_catalog

    def checked_synchronize(catalog):
        assert locked
        return synchronize(catalog)

    monkeypatch.setattr(service, "_catalog_import_lock", tracked_lock)
    monkeypatch.setattr(service, "synchronize_business_document_catalog", checked_synchronize)

    import_business_document_catalog(
        filename="BCM_v25.json",
        content=json.dumps(_source(), ensure_ascii=False).encode(),
        actor_id="admin-1",
    )

    assert locked is False
