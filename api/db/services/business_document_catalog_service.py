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

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterator

from api.db.db_models import BusinessDocumentCatalog, SystemSettings
from business_documents.domain.catalog import load_document_catalog, validate_document_catalog
from business_documents.domain.catalog_import import CatalogImportError, build_document_catalog


BUSINESS_DOCUMENTS_CATALOG_SETTING = "business_documents.catalog"
MAX_CATALOG_UPLOAD_BYTES = 5 * 1024 * 1024
_SETTING_SCHEMA_VERSION = "2"
_IMPORT_LOCK = Lock()


@contextmanager
def _connection() -> Iterator[None]:
    database = BusinessDocumentCatalog._meta.database
    opened_here = database.is_closed()
    if opened_here:
        database.connect()
    try:
        yield
    finally:
        if opened_here and not database.is_closed():
            database.close()


@contextmanager
def _catalog_import_lock(database) -> Iterator[None]:
    lock_factory = getattr(database, "lock", None)
    if lock_factory is None:
        with _IMPORT_LOCK:
            yield
        return
    with lock_factory("business_document_catalog_import", 60, db=database):
        yield


def _parse_setting(row: SystemSettings | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        envelope = json.loads(row.value)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("The stored business-document catalog is not valid JSON") from error
    if not isinstance(envelope, dict) or envelope.get("schema_version") != _SETTING_SCHEMA_VERSION:
        raise RuntimeError("The stored business-document catalog has an unsupported schema version")
    for key in ("source_id", "source_version", "source_sha256", "filename", "imported_at", "imported_by"):
        if not isinstance(envelope.get(key), str) or not envelope[key].strip():
            raise RuntimeError(f"The stored business-document catalog metadata has no {key}")
    if envelope["source_id"] != "BCM_Bank":
        raise RuntimeError("The stored business-document catalog has an unsupported source")
    source_sha256 = envelope["source_sha256"]
    if len(source_sha256) != 64 or any(character not in "0123456789abcdef" for character in source_sha256.lower()):
        raise RuntimeError("The stored business-document catalog has an invalid source hash")
    if not isinstance(envelope.get("catalog_items"), int) or isinstance(envelope["catalog_items"], bool) or envelope["catalog_items"] < 1:
        raise RuntimeError("The stored business-document catalog has an invalid item count")
    return envelope


def _stored_envelope() -> dict[str, Any] | None:
    if SystemSettings._meta.database is not BusinessDocumentCatalog._meta.database:
        return None
    if not SystemSettings.table_exists():
        return None
    row = SystemSettings.get_or_none(SystemSettings.name == BUSINESS_DOCUMENTS_CATALOG_SETTING)
    return _parse_setting(row)


def catalog_for_bootstrap() -> dict[str, Any]:
    """Return the uploaded catalog when present, otherwise the bundled default."""

    envelope = _stored_envelope()
    return _catalog_from_rows(envelope) if envelope is not None else load_document_catalog()


def _catalog_from_rows(envelope: dict[str, Any]) -> dict[str, Any]:
    rows = list(
        BusinessDocumentCatalog.select()
        .where(
            (BusinessDocumentCatalog.source_id == envelope["source_id"]) & (BusinessDocumentCatalog.is_active == True)  # noqa: E712
        )
        .order_by(BusinessDocumentCatalog.sort_order.asc(), BusinessDocumentCatalog.id.asc())
    )
    if len(rows) != envelope["catalog_items"]:
        raise RuntimeError("The stored business-document catalog row count does not match its metadata")
    if any(row.source_version != envelope["source_version"] or row.source_sha256 != envelope["source_sha256"] for row in rows):
        raise RuntimeError("The stored business-document catalog rows do not match its metadata")
    catalog = {
        "source_id": envelope["source_id"],
        "source_version": envelope["source_version"],
        "source_sha256": envelope["source_sha256"],
        "items": [
            {
                "id": row.id,
                "title": row.title,
                "title_en": row.title_en,
                "description": row.description,
                "capability_level": row.capability_level,
                "capability_type": row.capability_type,
                "hierarchy": row.hierarchy,
                "details": row.details,
            }
            for row in rows
        ],
    }
    try:
        return validate_document_catalog(catalog)
    except ValueError as error:
        raise RuntimeError(f"The stored business-document catalog rows are invalid: {error}") from error


def synchronize_business_document_catalog(catalog: dict[str, Any]) -> dict[str, int]:
    """Synchronize catalog rows without modifying existing documents."""

    validate_document_catalog(catalog)
    source_id = catalog["source_id"]
    active_ids = [item["id"] for item in catalog["items"]]
    summary = {"created_items": 0, "updated_items": 0, "reactivated_items": 0, "deactivated_items": 0}
    for sort_order, item in enumerate(catalog["items"]):
        values = {
            "title": item["title"].strip(),
            "title_en": item.get("title_en"),
            "description": item.get("description"),
            "capability_level": item["capability_level"],
            "capability_type": item.get("capability_type"),
            "hierarchy": item.get("hierarchy") or {},
            "details": item.get("details") or {},
            "source_id": source_id,
            "source_version": catalog["source_version"],
            "source_sha256": catalog["source_sha256"],
            "sort_order": sort_order,
            "is_active": True,
        }
        existing = BusinessDocumentCatalog.get_or_none(BusinessDocumentCatalog.id == item["id"])
        if existing is None:
            BusinessDocumentCatalog.create(id=item["id"], **values)
            summary["created_items"] += 1
            continue
        if existing.source_id != source_id:
            raise CatalogImportError(f"Catalog ID {item['id']} is already owned by source {existing.source_id}")
        was_inactive = not existing.is_active
        changed_values = {field: value for field, value in values.items() if getattr(existing, field) != value}
        if changed_values:
            BusinessDocumentCatalog.update(**changed_values).where(BusinessDocumentCatalog.id == item["id"]).execute()
            if any(field != "is_active" for field in changed_values):
                summary["updated_items"] += 1
        if was_inactive:
            summary["reactivated_items"] += 1
    summary["deactivated_items"] = (
        BusinessDocumentCatalog.update(is_active=False)
        .where(
            (BusinessDocumentCatalog.source_id == source_id) & ~BusinessDocumentCatalog.id.in_(active_ids) & (BusinessDocumentCatalog.is_active == True)  # noqa: E712
        )
        .execute()
    )
    return summary


def _filename(value: str) -> str:
    filename = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    if not filename or len(filename) > 255 or not filename.lower().endswith(".json"):
        raise CatalogImportError("A JSON file with a valid name is required")
    return filename


def _json_object(content: bytes) -> object:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CatalogImportError("The catalog file must use UTF-8 encoding") from error

    def reject_constant(value: str) -> None:
        raise CatalogImportError(f"Invalid JSON constant: {value}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise CatalogImportError(f"Invalid JSON at line {error.lineno}, column {error.colno}") from error


def _catalog_metadata(catalog: dict[str, Any], *, filename: str, imported_at: str, imported_by: str) -> dict[str, Any]:
    return {
        "schema_version": _SETTING_SCHEMA_VERSION,
        "source_id": catalog["source_id"],
        "source_version": catalog["source_version"],
        "source_sha256": catalog["source_sha256"],
        "catalog_items": len(catalog["items"]),
        "filename": filename,
        "imported_at": imported_at,
        "imported_by": imported_by,
    }


def _save_catalog_metadata(envelope: dict[str, Any]) -> None:
    value = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    row = SystemSettings.get_or_none(SystemSettings.name == BUSINESS_DOCUMENTS_CATALOG_SETTING)
    if row is None:
        SystemSettings.create(name=BUSINESS_DOCUMENTS_CATALOG_SETTING, source="admin", data_type="json", value=value)
    else:
        SystemSettings.update(source="admin", data_type="json", value=value).where(SystemSettings.name == BUSINESS_DOCUMENTS_CATALOG_SETTING).execute()


def _status(catalog: dict[str, Any], envelope: dict[str, Any] | None) -> dict[str, Any]:
    source_id = catalog["source_id"]
    source_rows = BusinessDocumentCatalog.select().where(BusinessDocumentCatalog.source_id == source_id)
    return {
        "source_id": source_id,
        "source_version": catalog["source_version"],
        "source_sha256": catalog["source_sha256"],
        "filename": envelope.get("filename") if envelope else "bcm_bank_v24_l5.json",
        "storage": "uploaded" if envelope else "bundled",
        "catalog_items": len(catalog["items"]),
        "active_items": source_rows.where(BusinessDocumentCatalog.is_active == True).count(),  # noqa: E712
        "total_items": source_rows.count(),
        "imported_at": envelope.get("imported_at") if envelope else None,
        "imported_by": envelope.get("imported_by") if envelope else None,
    }


def get_business_document_catalog_status() -> dict[str, Any]:
    with _connection():
        envelope = _stored_envelope()
        catalog = _catalog_from_rows(envelope) if envelope else load_document_catalog()
        return _status(catalog, envelope)


def import_business_document_catalog(*, filename: str, content: bytes, actor_id: str) -> dict[str, Any]:
    """Validate and transactionally activate one uploaded BCM catalog."""

    safe_filename = _filename(filename)
    if not content:
        raise CatalogImportError("The catalog file is empty")
    if len(content) > MAX_CATALOG_UPLOAD_BYTES:
        raise CatalogImportError(f"The catalog file must not exceed {MAX_CATALOG_UPLOAD_BYTES // (1024 * 1024)} MiB")
    source_sha256 = hashlib.sha256(content).hexdigest()
    catalog = build_document_catalog(_json_object(content), filename=safe_filename, source_sha256=source_sha256)
    imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    envelope = _catalog_metadata(catalog, filename=safe_filename, imported_at=imported_at, imported_by=str(actor_id))
    with _connection():
        database = BusinessDocumentCatalog._meta.database
        with _catalog_import_lock(database):
            with database.atomic():
                changes = synchronize_business_document_catalog(catalog)
                _save_catalog_metadata(envelope)
                status = _status(catalog, envelope)
    return {**status, **changes}
