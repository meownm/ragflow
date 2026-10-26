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

"""Deterministic document content rules over explicit template and schema values."""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

from business_documents.domain.errors import RuleViolation
from business_documents.domain.hashing import stable_hash
from business_documents.domain.block_diff import BLOCK_OPERATIONS, apply_block_operations, bind_block_operation, bind_block_section, block_changes, with_block_ids


_MARKDOWN_SECTION_HEADING = re.compile(r"^(#{1,6})\s+([0-9]+(?:\.[0-9]+)*)\.\s+(.+?)\s*$")
_NUMBERED_SECTION_ID = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_CUSTOM_SECTION_ALLOWED_BLOCKS = {"paragraph", "list", "table", "image", "reference"}
_FENCED_CODE_BLOCK = re.compile(
    r"```(?P<language>[A-Za-z0-9_-]*)[ \t]*\n(?P<source>.*?)(?:\n)?```",
    re.DOTALL,
)
_FENCE_LINE = re.compile(r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})")
_MAX_PARAGRAPH_SIZE = 20_000
UNDEFINED_REQUIREMENTS_TEXT = "Требования не определены"
SECTION_CONTENT_WARNING_CODES = frozenset(
    {"REQUIRED_SECTION_EMPTY", "ALL_SECTIONS_EMPTY", "CONCEPTUAL_DIAGRAM_REQUIRED", "ACTIVITY_SCENARIO_REQUIRED", "SECTION_4_1_PLANTUML_MISSING", "SECTION_4_3_ACTIVITY_MISSING"}
)
# Numbered outline accepted by the persisted EVA v2 required-scope contract.
_EVA_V2_REQUIRED_SECTION_IDS = ("1", "3", "3.1", "3.2", "3.3", "4", "4.1", "4.3", "5", "5.5")


def validate_contract(name: str, value: object, schema: dict[str, Any]) -> None:
    error = min(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path), default=None)
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    if name == "document_draft" and error.validator == "oneOf" and isinstance(error.instance, dict):
        allowed_types = [branch.get("properties", {}).get("type", {}).get("const") for branch in error.schema["oneOf"]]
        allowed_types = [kind for kind in allowed_types if isinstance(kind, str)]
        block_type = error.instance.get("type")
        if allowed_types and isinstance(block_type, str) and block_type not in allowed_types:
            raise RuleViolation(
                "INVALID_DOCUMENT_DRAFT",
                f"Недопустимый тип блока «{block_type[:64]}» ({path}). Допустимые типы: {', '.join(allowed_types)}. Для диаграмм используйте PlantUML в разрешённых шаблоном разделах.",
                {"path": path, "validator": error.validator, "block_type": block_type, "allowed_block_types": allowed_types},
            )
    raise RuleViolation(
        f"INVALID_{name.upper()}",
        f"Contract {name} validation failed{f' at {path}' if path else ''}: {error.message}",
        {"path": path, "validator": error.validator},
    )


def validate_document_ast(document: object, template: dict[str, Any], schema: dict[str, Any], *, previous_document: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(document, dict) and isinstance(document.get("sections"), list):
        # Identity errors must not be repaired by content-format normalization.
        with_block_ids(document)
    original = document
    document = normalize_document_ast(document, template)
    if document is original and isinstance(document, dict):
        document = deepcopy(document)
    document = _normalize_blocks(document, template, schema, previous_document)
    validation_document = document
    preserved_diagrams = set()
    if previous_document is not None and isinstance(document, dict) and isinstance(document.get("sections"), list):
        previous = {section["id"]: Counter(stable_hash(block) for block in section["blocks"]) for section in previous_document["sections"]}
        validation_sections = []
        for section in document["sections"]:
            remaining = previous.get(section["id"], Counter()).copy()
            checked_blocks = []
            for block in section["blocks"]:
                fingerprint = stable_hash(block)
                if remaining[fingerprint]:
                    remaining[fingerprint] -= 1
                    # Retain positions in validation errors without revalidating saved content.
                    checked_blocks.append({"type": "paragraph", "text": "_"})
                    if isinstance(block, dict) and block.get("type") not in {"paragraph", "list", "table", "image", "reference"} and isinstance(block.get("source"), str) and block["source"].strip():
                        preserved_diagrams.add(section["id"])
                else:
                    checked_blocks.append(block)
            validation_sections.append({**section, "blocks": checked_blocks})
        validation_document = {**document, "sections": validation_sections}
    validate_contract("document_draft", validation_document, schema)
    assert isinstance(document, dict)
    if document["template_version"] != template["template_version"]:
        raise RuleViolation("TEMPLATE_VERSION_CONFLICT", "Draft does not use the document template version")
    expected = [(section["id"], section["title"]) for section in template["sections"]]
    expected_ids = {item[0] for item in expected}
    actual = [(section["id"], section["title"]) for section in document["sections"]]
    actual_template_outline = [item for item in actual if item[0] in expected_ids]
    if actual_template_outline != expected:
        raise RuleViolation(
            "TEMPLATE_STRUCTURE_MISMATCH",
            "Черновик должен сохранять все разделы опубликованного шаблона, их названия и порядок",
            {"expected_section_ids": [item[0] for item in expected], "actual_section_ids": [item[0] for item in actual]},
        )
    all_section_ids = {item[0] for item in actual}
    for section_id, _ in actual:
        if section_id in expected_ids:
            continue
        parent_id = section_id.rsplit(".", 1)[0] if "." in section_id else ""
        if not _NUMBERED_SECTION_ID.fullmatch(section_id) or parent_id not in all_section_ids:
            raise RuleViolation(
                "INVALID_SUBSECTION",
                "Новый подраздел должен иметь уникальный номер и существующий родительский раздел",
                {"section_id": section_id, "parent_section_id": parent_id or None},
            )
    required_ids = {section["id"] for section in template["sections"] if section["required"]}
    warnings = [item for item in document.get("warnings", []) if item["code"] not in SECTION_CONTENT_WARNING_CODES]
    for section in document["sections"]:
        if section["id"] not in required_ids:
            continue
        defined_blocks = [block for block in section["blocks"] if not (block.get("type") == "paragraph" and block["text"].strip() == UNDEFINED_REQUIREMENTS_TEXT)]
        if any(_block_has_content(block) for block in defined_blocks):
            section["blocks"] = defined_blocks
        else:
            placeholder = next(
                (block for block in section["blocks"] if block.get("type") == "paragraph" and block["text"].strip() == UNDEFINED_REQUIREMENTS_TEXT),
                {"type": "paragraph"},
            )
            section["blocks"] = [{**placeholder, "text": UNDEFINED_REQUIREMENTS_TEXT}]
        if render_section_text(section).strip() == UNDEFINED_REQUIREMENTS_TEXT:
            warnings.append({"code": "REQUIRED_SECTION_EMPTY", "section_id": section["id"], "message": "Обязательный раздел пока не заполнен; требуется уточнение."})
    sections = {section["id"]: section for section in document["sections"]}
    if "4.1" in sections and "4.1" not in preserved_diagrams and not any(block.get("type") == "plantuml" for block in sections["4.1"]["blocks"]):
        warnings.append({"code": "CONCEPTUAL_DIAGRAM_REQUIRED", "section_id": "4.1", "message": "Концептуальную схему рекомендуется дополнить диаграммой PlantUML."})
    if "4.3" in sections:
        blocks = sections["4.3"]["blocks"]
        if ("4.3" not in preserved_diagrams and not any(block.get("type") == "plantuml" for block in blocks)) or not any(
            block.get("type") != "plantuml" and _block_has_content(block) for block in blocks
        ):
            warnings.append({"code": "ACTIVITY_SCENARIO_REQUIRED", "section_id": "4.3", "message": "Клиентский сценарий рекомендуется дополнить диаграммой деятельности и сопровождающим текстом."})
    if warnings:
        document["warnings"] = list({stable_hash(item): item for item in warnings}.values())
    else:
        document.pop("warnings", None)
    return with_block_ids(document)


def _normalize_blocks(document, template, schema, previous_document):
    """Keep renderable text even when the model misses the block representation."""
    if not isinstance(document, dict) or not isinstance(document.get("sections"), list):
        return document
    block_schema = schema["properties"]["sections"]["items"]["properties"]["blocks"]["items"]
    validator = Draft202012Validator(block_schema)
    fields = {branch["properties"]["type"]["const"]: set(branch["properties"]) for branch in block_schema["oneOf"]}
    allowed = {section["id"]: set(section["allowed_blocks"]) for section in template["sections"]}
    previous = {section["id"]: Counter(stable_hash(block) for block in section["blocks"]) for section in (previous_document or {}).get("sections", [])}
    for section in document["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("id"), str) or not isinstance(section.get("blocks"), list):
            continue
        remaining = previous.get(section.get("id"), Counter()).copy()
        blocks = []
        for block in section["blocks"]:
            start = len(blocks)
            message = "Оформление блока упрощено; исходное содержимое сохранено."
            fingerprint = stable_hash(block)
            if remaining[fingerprint]:
                remaining[fingerprint] -= 1
                blocks.append(block)
                continue
            kind = block.get("type") if isinstance(block, dict) else None
            kind = kind if isinstance(kind, str) else None
            projected = {key: value for key, value in block.items() if key in fields.get(kind, ())} if isinstance(block, dict) else block
            if kind in {"image", "reference"} and isinstance(block.get("url"), str) and not re.match(r"^https?://[^\s]+$", block["url"]):
                validate_contract("document_draft", document, schema)
            valid = validator.is_valid(projected)
            if valid and kind in allowed.get(section.get("id"), _CUSTOM_SECTION_ALLOWED_BLOCKS):
                blocks.append(projected)
                if projected == block:
                    continue
            else:
                if valid:
                    text = render_section_text({"blocks": [projected]})
                elif kind == "paragraph" and isinstance(block.get("text"), str):
                    text = block["text"]
                else:
                    text = render_section_text({"blocks": [{"type": "literal", "source": render_block_source(block)}]})
                blocks.extend(_paragraph_blocks(text))
            if valid and projected != block:
                extra = {key: value for key, value in block.items() if key not in fields[kind]}
                message = "Дополнительные поля блока сохранены в замечаниях: " + json.dumps(extra, ensure_ascii=False, indent=2)
            elif not valid:
                message += " Исходный блок: " + json.dumps(block, ensure_ascii=False, indent=2)
            document.setdefault("warnings", []).append({"code": "BLOCK_FORMAT_NORMALIZED", "section_id": section["id"], "message": message})
            if isinstance(block, dict) and "id" in block and len(blocks) > start:
                blocks[start]["id"] = block["id"]
        section["blocks"] = blocks
    return document


def _valid_warnings(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and isinstance(item.get("message"), str)
        and set(item) <= {"code", "message", "section_id"}
        and ("section_id" not in item or isinstance(item["section_id"], str))
        for item in value
    )


def _normalized_warnings(value: object) -> list[dict[str, Any]]:
    if _valid_warnings(value):
        return value
    return [{"code": "DOCUMENT_FORMAT_NORMALIZED", "message": "Неструктурированные замечания: " + json.dumps(value, ensure_ascii=False, indent=2)}]


def with_document_warnings(document: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    combined = list({stable_hash(item): item for item in [*_normalized_warnings(document.get("warnings", [])), *_normalized_warnings(warnings)]}.values())
    return {**document, "warnings": combined} if combined else document


def document_warning_texts(warnings: list[dict[str, Any]]) -> list[str]:
    return [(f"Раздел {warning['section_id']}: " if warning.get("section_id") else "") + warning["message"] for warning in warnings]


def decode_document_warnings(payload: str) -> list[dict[str, Any]] | None:
    """Read only warning values; external metadata never grants source authority."""
    try:
        value = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return value if _valid_warnings(value) else None


def render_document_warning_appendix(warnings: list[dict[str, Any]]) -> str:
    if not warnings:
        return ""
    payload = json.dumps(warnings, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("--", "\\u002d\\u002d")
    body = "## Замечания к документу\n\n" + "\n".join("- " + warning.replace("\n", "\n  ") for warning in document_warning_texts(warnings))
    return "\n\n<!-- ragflow-document-warnings:v1\n" + payload + "\n-->\n" + body + "\n<!-- /ragflow-document-warnings -->"


def _split_document_warning_appendix(markdown: str) -> tuple[str, list[dict[str, Any]]]:
    appendix = re.search(r"\n\n<!-- ragflow-document-warnings:v1\n(.*?)\n-->\n(.*?)\n<!-- /ragflow-document-warnings -->\s*$", markdown, re.DOTALL)
    if appendix is None or (warnings := decode_document_warnings(appendix[1])) is None:
        return markdown, []
    expected = render_document_warning_appendix(warnings).split("-->\n", 1)[1].rsplit("\n<!--", 1)[0] if warnings else ""
    if appendix[2] != expected:
        warnings = with_document_warnings({"warnings": warnings}, [{"code": "EXPORTED_WARNINGS_EDITED", "message": "Изменённый текст приложения с замечаниями сохранён: " + appendix[2]}])["warnings"]
    return markdown[: appendix.start()], warnings


def generated_document_from_text(text: str, template: dict[str, Any]) -> dict[str, Any]:
    """Recover numbered model output without relaxing the EVA import contract."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    headings = _markdown_headings(normalized)
    sections, unassigned, seen = [], [], set()
    expected = {section["id"] for section in template["sections"]}
    if headings:
        unassigned.append(normalized[: headings[0][2]])
    else:
        unassigned.append(normalized)
    for index, (section_id, title, start, body_start) in enumerate(headings):
        end = headings[index + 1][2] if index + 1 < len(headings) else len(normalized)
        if section_id in expected and section_id not in seen:
            sections.append({"id": section_id, "title": title, "blocks": _import_markdown_blocks(normalized[body_start:end].strip())})
            seen.add(section_id)
        else:
            unassigned.append(normalized[start:end])
    remainder = "\n\n".join(part for part in unassigned if part.strip())
    if remainder:
        sections.append({"id": "1.1", "title": "Текст для распределения по разделам", "blocks": _paragraph_blocks(remainder)})
    return {
        "schema_version": "1",
        "document_type": "business_requirements",
        "template_version": template["template_version"],
        "sections": sections,
        "warnings": [{"code": "DOCUMENT_FORMAT_NORMALIZED", "message": "Ответ модели восстановлен из текста. Распознанные заголовки распределены по разделам; остальное сохранено для уточнения."}],
    }


def _markdown_headings(text: str, base_level: int | None = None) -> list[tuple[str, str, int, int]]:
    found, offset, active_fence = [], 0, None
    for line in text.splitlines(keepends=True):
        stripped_line = line.rstrip("\n")
        fence = _FENCE_LINE.match(stripped_line)
        if fence:
            marker = fence.group("marker")
            if active_fence is None:
                active_fence = marker[0], len(marker)
            elif marker[0] == active_fence[0] and len(marker) >= active_fence[1]:
                active_fence = None
        else:
            heading = _MARKDOWN_SECTION_HEADING.match(stripped_line)
            if active_fence is None and heading:
                section_id = heading.group(2)
                if base_level is None or len(heading.group(1)) == base_level + section_id.count("."):
                    found.append((section_id, heading.group(3).strip(), offset, offset + len(line)))
        offset += len(line)
    return found


def import_document_markdown(markdown: object, template: dict[str, Any], schema: dict[str, Any], *, required_schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert a previously exported business document back to its governed AST.

    EVA pages are accepted only when their numbered headings still match the
    published template. This avoids silently assigning arbitrary page content to
    the wrong semantic sections while keeping the import deterministic and free
    of an LLM rewrite.
    """

    if not isinstance(markdown, str) or not markdown.strip():
        raise RuleViolation("EVA_DOCUMENT_EMPTY", "Страница EVA не содержит опубликованного текста")
    normalized, imported_warnings = _split_document_warning_appendix(markdown.replace("\r\n", "\n").replace("\r", "\n").strip("\n"))
    template_sections = template["sections"]
    expected = {section["id"]: section for section in template_sections}
    base_level = int(template.get("rendering", {}).get("body_heading_base_level", 2))

    found = _markdown_headings(normalized, base_level)

    actual_ids = [item[0] for item in found if item[0] in expected]
    expected_ids = [section["id"] for section in template_sections]
    required_ids = [section["id"] for section in template_sections if section["required"]]
    required_only = required_schema is not None and (actual_ids == required_ids or tuple(actual_ids) == _EVA_V2_REQUIRED_SECTION_IDS)
    if actual_ids != expected_ids and not required_only:
        raise RuleViolation(
            "EVA_DOCUMENT_TEMPLATE_MISMATCH",
            "Страница EVA не соответствует текущему шаблону бизнес-документа",
            {"expected_section_ids": expected_ids, "actual_section_ids": actual_ids},
        )
    if normalized[: found[0][2]].strip():
        raise RuleViolation(
            "EVA_DOCUMENT_TEMPLATE_MISMATCH",
            "Перед первым разделом страницы EVA найден текст вне шаблона",
        )

    sections = []
    for index, (section_id, title, _, body_start) in enumerate(found):
        body_end = found[index + 1][2] if index + 1 < len(found) else len(normalized)
        body = normalized[body_start:body_end].strip("\n")
        sections.append(
            {
                "id": section_id,
                "title": title,
                "blocks": _import_markdown_blocks(body),
            }
        )
    return validate_document_ast(
        {
            "schema_version": "2" if required_only else "1",
            "document_type": "business_requirements",
            "template_version": template["template_version"],
            **({"section_scope": "required"} if required_only else {}),
            "sections": sections,
            **({"warnings": imported_warnings} if imported_warnings else {}),
        },
        template,
        required_schema if required_only else schema,
    )


def _import_markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    cursor = 0
    for match in _FENCED_CODE_BLOCK.finditer(markdown):
        source = match.group("source").strip()
        language = match.group("language").casefold()
        is_plantuml = language == "plantuml" or (source.startswith("@startuml") and source.endswith("@enduml"))
        if not is_plantuml:
            continue
        blocks.extend(_paragraph_blocks(markdown[cursor : match.start()].strip("\n")))
        if source:
            blocks.append({"type": "plantuml", "source": source})
        cursor = match.end()
    blocks.extend(_paragraph_blocks(markdown[cursor:].strip("\n")))
    return blocks


def _paragraph_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    if not text.strip():
        return blocks
    remaining = text
    while remaining:
        if len(remaining) <= _MAX_PARAGRAPH_SIZE:
            blocks.append({"type": "paragraph", "text": remaining})
            break
        split_at = remaining.rfind("\n", 0, _MAX_PARAGRAPH_SIZE + 1)
        if split_at <= 0:
            split_at = _MAX_PARAGRAPH_SIZE
        blocks.append({"type": "paragraph", "text": remaining[:split_at]})
        remaining = remaining[split_at:].lstrip("\n")
    return blocks


def normalize_document_ast(document: object, template: dict[str, Any]) -> object:
    """Restore the complete pinned outline without copying child content."""

    if not isinstance(document, dict) or not isinstance(document.get("sections"), list):
        return document
    template_sections = template["sections"]
    actual_sections = document["sections"]
    if not all(isinstance(section, dict) and isinstance(section.get("id"), str) for section in actual_sections):
        return document
    expected_ids = {section["id"] for section in template_sections}
    normalized = deepcopy(document)
    if "warnings" in normalized:
        normalized["warnings"] = _normalized_warnings(normalized["warnings"])
    extra_fields = set(normalized) - {"schema_version", "document_type", "template_version", "section_scope", "sections", "warnings"}
    if extra_fields:
        values = {field: normalized.pop(field) for field in sorted(extra_fields)}
        normalized.setdefault("warnings", []).append(
            {"code": "DOCUMENT_FORMAT_NORMALIZED", "message": "Дополнительные поля сохранены в замечаниях: " + json.dumps(values, ensure_ascii=False, indent=2)}
        )
    flattened_sections = []
    for section in normalized["sections"]:
        blocks = section.get("blocks")
        if isinstance(blocks, list):
            content_blocks = []
            for block in blocks:
                if isinstance(block, dict) and isinstance(block.get("id"), str) and isinstance(block.get("title"), str) and isinstance(block.get("blocks"), list) and "type" not in block:
                    flattened_sections.append(block)
                else:
                    content_blocks.append(block)
            section["blocks"] = content_blocks
        flattened_sections.append(section)
    actual_ids = [section["id"] for section in flattened_sections]
    if len(actual_ids) != len(set(actual_ids)):
        return document
    by_id = {section["id"]: section for section in flattened_sections}
    normalized_sections = []
    for template_section in template_sections:
        section = by_id.get(
            template_section["id"],
            {"id": template_section["id"], "title": template_section["title"], "blocks": []},
        )
        if section.get("title") != template_section["title"]:
            old_title = section.get("title", "")
            section["title"] = template_section["title"]
            normalized.setdefault("warnings", []).append({"code": "SECTION_TITLE_NORMALIZED", "section_id": section["id"], "message": f"Заголовок «{old_title}» приведён к шаблону."})
        normalized_sections.append(section)
    for section in flattened_sections:
        if section["id"] in expected_ids:
            continue
        normalized_sections.append(section)
    for section in normalized_sections:
        for field in ("parent_id", "required", "allowed_blocks", "semantic_requirements"):
            section.pop(field, None)
        extra_fields = set(section) - {"id", "title", "blocks", "evidence_refs"}
        if extra_fields:
            values = {field: section.pop(field) for field in sorted(extra_fields)}
            normalized.setdefault("warnings", []).append(
                {"code": "SECTION_FORMAT_NORMALIZED", "section_id": section["id"], "message": "Дополнительные поля раздела: " + json.dumps(values, ensure_ascii=False, indent=2)}
            )
        if not isinstance(section.get("blocks"), list):
            value = section.get("blocks")
            section["blocks"] = _paragraph_blocks(value if isinstance(value, str) else render_block_source(value)) if value is not None else []
            normalized.setdefault("warnings", []).append({"code": "SECTION_FORMAT_NORMALIZED", "section_id": section["id"], "message": "Содержимое раздела сохранено обычным текстом."})
    normalized_sections.sort(key=lambda section: _section_sort_key(section["id"]))
    normalized["sections"] = normalized_sections
    return normalized


def _block_has_content(block: object) -> bool:
    if not isinstance(block, dict):
        return bool(render_block_source(block).strip())
    block_type = block.get("type")
    if block_type == "paragraph":
        return bool(str(block.get("text", "")).strip())
    if block_type == "list":
        return any(str(item).strip() for item in block.get("items", []))
    if block_type == "table":
        return bool(block.get("headers") or block.get("rows"))
    if block_type == "plantuml":
        return bool(str(block.get("source", "")).strip())
    if block_type in {"image", "reference"}:
        return bool(str(block.get("url", "")).strip())
    return bool(render_block_source(block).strip())


def section_hash(section: dict[str, Any]) -> str:
    return stable_hash(section)


def bind_change_plan_snapshot(base_document: dict[str, Any], change_plan: object, *, base_revision_id: str, source_state_version: int) -> object:
    """Bind concurrency metadata from the immutable server snapshot.

    A language model selects blocks to edit; it does not calculate the
    optimistic-concurrency token used to protect their sections.
    """

    if not isinstance(change_plan, dict):
        return change_plan
    bound = deepcopy(change_plan)
    bound.update(base_revision_id=base_revision_id, source_state_version=source_state_version)
    if not isinstance(bound.get("operations"), list) or not bound["operations"]:
        return bound
    # Saved section plans hash raw content; block plans include stable block IDs.
    sections = {section["id"]: section for section in base_document.get("sections", []) if isinstance(section, dict) and isinstance(section.get("id"), str)}
    block_sections = {section["id"]: section for section in with_block_ids(base_document).get("sections", [])}
    block_owners = {block["id"]: section["id"] for section in block_sections.values() for block in section["blocks"]}
    hashes = {}
    for operation in bound["operations"]:
        if not isinstance(operation, dict):
            continue
        block_operation = operation.get("type") in BLOCK_OPERATIONS
        bind_block_section(operation, block_owners)
        section_id = operation.get("section_id")
        key = (block_operation, section_id)
        if key not in hashes:
            section = (block_sections if block_operation else sections).get(section_id)
            if section is None:
                hashes[key] = stable_hash(None) if block_operation else None
            else:
                hashes[key] = section_hash(section)
        expected_hash = hashes[key]
        if block_operation:
            bind_block_operation(operation, base_revision_id)
        if expected_hash is not None:
            operation["expected_section_hash"] = expected_hash
    return bound


def apply_change_plan(base_document: dict[str, Any], change_plan: dict[str, Any], template: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Apply block edits, retaining the reader for persisted section plans."""

    if any(operation["type"] in BLOCK_OPERATIONS for operation in change_plan["operations"]):
        if any(operation["type"] not in BLOCK_OPERATIONS for operation in change_plan["operations"]):
            raise RuleViolation("INVALID_CHANGE_PLAN", "Block edits cannot be mixed with saved section replacements")
        identified_base = with_block_ids(base_document)
        result = apply_block_operations(identified_base, change_plan["operations"])
        return validate_document_ast(result, template, schema, previous_document=identified_base)

    # Changes replace fields on shallow section copies; normalization owns the
    # single deep copy of the resulting content, including operation values.
    result = {**base_document, "sections": [dict(section) for section in base_document["sections"]]}
    sections = {section["id"]: section for section in result["sections"]}
    seen_sections: set[str] = set()
    for operation in change_plan["operations"]:
        section_id = operation["section_id"]
        if section_id in seen_sections:
            raise RuleViolation("DUPLICATE_CHANGE_TARGET", "A section may be replaced only once per change plan", {"section_id": section_id})
        seen_sections.add(section_id)
        section = sections.get(section_id)
        if section is None:
            raise RuleViolation("CHANGE_SECTION_NOT_FOUND", "Change plan targets a section outside the template", {"section_id": section_id})
        actual_hash = section_hash(section)
        if operation["expected_section_hash"] != actual_hash:
            raise RuleViolation(
                "SECTION_HASH_CONFLICT",
                "Change plan targets stale section content",
                {"section_id": section_id, "expected": operation["expected_section_hash"], "actual": actual_hash},
            )
        section["blocks"] = operation["content"]["blocks"]
        if "evidence_refs" in operation:
            section["evidence_refs"] = operation["evidence_refs"]
    validated = validate_document_ast(result, template, schema, previous_document=base_document)
    # Normalization can return its input unchanged for noncanonical saved ASTs.
    return deepcopy(validated) if validated is result else validated


def _section_sort_key(section_id: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in section_id.split("."))
    except ValueError:
        return (2**31 - 1,)


def render_section_text(section: dict[str, Any]) -> str:
    """Render the canonical Markdown body for one section, excluding its heading."""

    lines: list[str] = []
    for block in section["blocks"]:
        block_type = block.get("type") if isinstance(block, dict) else None
        if block_type == "paragraph":
            lines.append(str(block.get("text", "")).strip("\n"))
        elif block_type == "list":
            lines.extend(f"- {item}" for item in block.get("items", []))
        elif block_type == "table":
            headers = [_canonical_scalar(item) for item in block.get("headers", [])]
            rows = block.get("rows", [])
            if headers:
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                lines.extend("| " + " | ".join(_canonical_scalar(item) for item in row) + " |" for row in rows)
        elif block_type == "plantuml":
            lines.extend(["```plantuml", str(block.get("source", "")).strip(), "```"])
        elif block_type == "image":
            lines.append(f"![{block.get('alt', '')}]({block.get('url', '')})")
        elif block_type == "reference":
            label = block.get("label") or block.get("url") or "Источник"
            lines.append(f"[{label}]({block.get('url', '')})")
        else:
            source = render_block_source(block)
            fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", source)), default=0))
            lines.append(f"{fence}text\n{source}\n{fence}")
        # A block boundary must close lists and paragraphs in Markdown consumers.
        lines.append("")
    return "\n".join(lines).strip("\n")


def render_block_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "block_id": pair["block_id"],
            "kind": pair["kind"],
            "before": render_section_text({"blocks": [pair["before_block"]]}) if pair["before_block"] else "",
            "after": render_section_text({"blocks": [pair["after_block"]]}) if pair["after_block"] else "",
        }
        for pair in block_changes(before, after)
    ]


def render_block_source(block: object) -> str:
    """Keep unsupported content visible as literal source, never executable markup."""
    if isinstance(block, dict):
        block = {key: value for key, value in block.items() if key != "id"}
        for key in ("source", "text"):
            if set(block) <= {"type", key} and isinstance(block.get(key), str) and block[key].strip():
                return block[key]
    return json.dumps(block, ensure_ascii=False, indent=2)


def _canonical_scalar(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_document_ast(document: dict[str, Any], template: dict[str, Any]) -> str:
    base_level = int(template.get("rendering", {}).get("body_heading_base_level", 2))
    lines: list[str] = []
    for section in document["sections"]:
        level = base_level + section["id"].count(".")
        lines.append(f"{'#' * level} {section['id']}. {section['title']}")
        section_text = render_section_text(section)
        if section_text:
            lines.extend(section_text.splitlines())
        lines.append("")
    return "\n".join(lines).strip("\n")
