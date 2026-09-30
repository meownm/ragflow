# Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from business_documents.domain.catalog import validate_document_catalog


_L5_FIELDS = (
    "L5_ID",
    "L5_RU",
    "L5_EN",
    "L5_Description_RU",
    "Capability_Level",
    "Capability_Type",
    "Business_Object",
    "BIAN_Indicative_Service_Domain",
    "Description_RU",
    "Source_IDs",
    "Source_URLs",
    "Owner_Function",
    "Maturity_Target",
    "Criticality",
    "Notes",
)
_ASSIGNMENT = re.compile(rf"(?:^|;\s*)(?P<key>{'|'.join(map(re.escape, _L5_FIELDS))})\s*=")
_VERSION = re.compile(r"(?:^|[_\-\s])v(?P<version>\d+(?:[._-]\d+)*)", re.IGNORECASE)


class CatalogImportError(ValueError):
    """The uploaded BCM source cannot be converted into the governed catalog."""


def _text(value: object, *, required: bool = False, field: str = "value") -> str | None:
    if value is None:
        if required:
            raise CatalogImportError(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise CatalogImportError(f"{field} must be a string or null")
    value = value.strip()
    if value.lower() in {"$null", "null"}:
        value = ""
    if required and not value:
        raise CatalogImportError(f"{field} is required")
    return value or None


def _parse_hashtable(value: str, *, location: str) -> dict[str, str | None]:
    source = value.strip()
    if source.startswith("@{") and source.endswith("}"):
        source = source[2:-1].strip()
    matches = list(_ASSIGNMENT.finditer(source))
    if not matches or matches[0].start() != 0:
        raise CatalogImportError(f"{location} must be an object or a PowerShell hashtable string")
    parsed: dict[str, str | None] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        raw = source[match.end() : end].strip().rstrip(";").strip()
        parsed[match.group("key")] = _text(raw)
    return parsed


def _l5_object(value: object, *, location: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return _parse_hashtable(value, location=location)
    raise CatalogImportError(f"{location} must be an object or a PowerShell hashtable string")


def _children(node: dict[str, Any], key: str, *, location: str) -> list[Any]:
    children = node.get(key)
    if not isinstance(children, list) or not children:
        raise CatalogImportError(f"{location}.{key} must be a non-empty array")
    return children


def _source_version(raw: dict[str, Any], filename: str) -> str:
    explicit = raw.get("source_version")
    if explicit is not None:
        return _text(explicit, required=True, field="source_version") or ""
    match = _VERSION.search(filename)
    if not match:
        raise CatalogImportError("The file name must contain a version such as v24")
    return f"v{match.group('version')}"


def _source_id(raw: dict[str, Any]) -> str:
    source_id = _text(raw.get("source_id")) or "BCM_Bank"
    if source_id != "BCM_Bank":
        raise CatalogImportError("source_id must be BCM_Bank")
    return source_id


def _walk_l5(raw: dict[str, Any]) -> list[tuple[dict[str, str], dict[str, Any]]]:
    records: list[tuple[dict[str, str], dict[str, Any]]] = []
    roots = raw.get("L1")
    if not isinstance(roots, list) or not roots:
        raise CatalogImportError("L1 must be a non-empty array")
    for l1_index, l1_value in enumerate(roots):
        if not isinstance(l1_value, dict):
            raise CatalogImportError(f"L1[{l1_index}] must be an object")
        hierarchy: dict[str, str] = {}
        for key in ("L1_ID", "L1_RU"):
            hierarchy[key] = _text(l1_value.get(key), required=True, field=f"L1[{l1_index}].{key}") or ""
        for l2_index, l2_value in enumerate(_children(l1_value, "L2", location=f"L1[{l1_index}]")):
            if not isinstance(l2_value, dict):
                raise CatalogImportError(f"L1[{l1_index}].L2[{l2_index}] must be an object")
            l2_hierarchy = dict(hierarchy)
            for key in ("L2_ID", "L2_RU"):
                l2_hierarchy[key] = _text(l2_value.get(key), required=True, field=f"L2[{l2_index}].{key}") or ""
            for l3_index, l3_value in enumerate(_children(l2_value, "L3", location=f"L2[{l2_index}]")):
                if not isinstance(l3_value, dict):
                    raise CatalogImportError(f"L2[{l2_index}].L3[{l3_index}] must be an object")
                l3_hierarchy = dict(l2_hierarchy)
                for key in ("L3_ID", "L3_RU"):
                    l3_hierarchy[key] = _text(l3_value.get(key), required=True, field=f"L3[{l3_index}].{key}") or ""
                for l4_index, l4_value in enumerate(_children(l3_value, "L4", location=f"L3[{l3_index}]")):
                    if not isinstance(l4_value, dict):
                        raise CatalogImportError(f"L3[{l3_index}].L4[{l4_index}] must be an object")
                    l4_hierarchy = dict(l3_hierarchy)
                    for key in ("L4_ID", "L4_RU"):
                        l4_hierarchy[key] = _text(l4_value.get(key), required=True, field=f"L4[{l4_index}].{key}") or ""
                    for l5_index, l5_value in enumerate(_children(l4_value, "L5", location=f"L4[{l4_index}]")):
                        location = f"L4[{l4_index}].L5[{l5_index}]"
                        records.append((l4_hierarchy, _l5_object(l5_value, location=location)))
    return records


def build_document_catalog(raw: object, *, filename: str, source_sha256: str) -> dict[str, Any]:
    """Convert a nested BCM L1-L5 export into the normalized L5 catalog."""

    if not isinstance(raw, dict):
        raise CatalogImportError("The BCM JSON root must be an object")
    records = _walk_l5(raw)
    source_ids = [_text(item.get("L5_ID"), required=True, field="L5_ID") or "" for _, item in records]
    duplicate_ids = {item_id for item_id, count in Counter(source_ids).items() if count > 1}
    items = []
    for item_index, (hierarchy, source) in enumerate(records):
        source_l5_id = _text(source.get("L5_ID"), required=True, field=f"L5[{item_index}].L5_ID") or ""
        title_ru = _text(source.get("L5_RU"), required=True, field=f"L5[{item_index}].L5_RU") or ""
        item_id = source_l5_id
        if source_l5_id in duplicate_ids:
            item_id = f"{source_l5_id}-{hashlib.sha256(title_ru.encode('utf-8')).hexdigest()[:8]}"
        items.append(
            {
                "id": item_id,
                "title": f"{source_l5_id} {title_ru}",
                "title_en": _text(source.get("L5_EN")),
                "description": _text(source.get("L5_Description_RU")) or _text(source.get("Description_RU")),
                "capability_level": "L5",
                "capability_type": _text(source.get("Capability_Type")),
                "hierarchy": dict(hierarchy),
                "details": {
                    "source_l5_id": source_l5_id,
                    "source_capability_level": _text(source.get("Capability_Level")),
                    "l5_description_ru": _text(source.get("L5_Description_RU")),
                    "business_object": _text(source.get("Business_Object")),
                    "bian_indicative_service_domain": _text(source.get("BIAN_Indicative_Service_Domain")),
                    "source_ids": _text(source.get("Source_IDs")),
                    "source_urls": _text(source.get("Source_URLs")),
                    "owner_function": _text(source.get("Owner_Function")),
                    "maturity_target": _text(source.get("Maturity_Target")),
                    "criticality": _text(source.get("Criticality")),
                    "notes": _text(source.get("Notes")),
                },
            }
        )
    catalog = {
        "source_id": _source_id(raw),
        "source_version": _source_version(raw, filename),
        "source_sha256": source_sha256.lower(),
        "items": items,
    }
    try:
        return validate_document_catalog(catalog)
    except ValueError as error:
        raise CatalogImportError(str(error)) from error
