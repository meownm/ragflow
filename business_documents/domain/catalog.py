from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_CATALOG_PATH = Path(__file__).with_name("bcm_bank_v24_l5.json")


def validate_document_catalog(catalog: object) -> dict[str, Any]:
    """Validate the normalized catalog contract used by persistence and UI."""

    if not isinstance(catalog, dict):
        raise ValueError("The business-document catalog must be an object")
    for key in ("source_id", "source_version", "source_sha256"):
        value = catalog.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"The business-document catalog must define {key}")
    if len(catalog["source_id"]) > 64 or len(catalog["source_version"]) > 64:
        raise ValueError("Business-document catalog source metadata is too long")
    source_sha256 = catalog["source_sha256"]
    if len(source_sha256) != 64 or any(character not in "0123456789abcdef" for character in source_sha256.lower()):
        raise ValueError("Business-document catalog source_sha256 must be a SHA-256 hex digest")
    items = catalog.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("The business-document catalog must contain items")
    item_ids: set[str] = set()
    item_titles: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Every business-document catalog item must be an object")
        item_id = item.get("id")
        title = item.get("title")
        if not isinstance(item_id, str) or not item_id.strip() or item_id in item_ids:
            raise ValueError("Business-document catalog IDs must be non-empty and unique")
        if len(item_id) > 64:
            raise ValueError(f"Business-document catalog item ID {item_id} is longer than 64 characters")
        if not isinstance(title, str) or not title.strip() or title in item_titles:
            raise ValueError(f"Business-document catalog item {item_id} has no unique title")
        if len(title) > 255:
            raise ValueError(f"Business-document catalog item {item_id} title is longer than 255 characters")
        title_en = item.get("title_en")
        if title_en is not None and (not isinstance(title_en, str) or len(title_en) > 255):
            raise ValueError(f"Business-document catalog item {item_id} has an invalid English title")
        capability_type = item.get("capability_type")
        if capability_type is not None and (not isinstance(capability_type, str) or len(capability_type) > 32):
            raise ValueError(f"Business-document catalog item {item_id} has an invalid capability type")
        description = item.get("description")
        if description is not None and not isinstance(description, str):
            raise ValueError(f"Business-document catalog item {item_id} has an invalid description")
        if item.get("capability_level") != "L5":
            raise ValueError(f"Business-document catalog item {item_id} is not L5")
        if not isinstance(item.get("hierarchy"), dict) or not isinstance(item.get("details"), dict):
            raise ValueError(f"Business-document catalog item {item_id} has invalid metadata")
        item_ids.add(item_id)
        item_titles.add(title)
    return catalog


def load_document_catalog() -> dict[str, Any]:
    """Load and validate the bundled L5 business-document catalog."""

    try:
        return validate_document_catalog(json.loads(_CATALOG_PATH.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"The bundled business-document catalog is invalid: {error}") from error
