"""Canonical content ordering and independent AST ownership."""

from copy import deepcopy

import pytest

from business_documents.domain.content import normalize_document_ast, render_document_ast, render_section_text, section_hash, validate_contract
from business_documents.domain.errors import RuleViolation


def _section(section_id, *blocks):
    return {"id": section_id, "title": section_id, "blocks": list(blocks)}


def _template(*sections):
    return {"sections": [{"id": section_id, "title": section_id, "required": required, "allowed_blocks": allowed} for section_id, required, allowed in sections]}


def test_markdown_separates_paragraphs_from_lists_without_changing_block_contents():
    section = _section(
        "4.3",
        {"type": "paragraph", "text": "Просмотр статьи."},
        {"type": "list", "items": ["Поиск статьи.", "Проверка доступа."]},
        {"type": "paragraph", "text": "Создание статьи.\nСопровождающий текст."},
        {"type": "list", "items": ["Сохранение черновика.", "Публикация."]},
        {"type": "paragraph", "text": "Конец сценариев."},
    )
    original = deepcopy(section)
    expected = "Просмотр статьи.\n\n- Поиск статьи.\n- Проверка доступа.\n\nСоздание статьи.\nСопровождающий текст.\n\n- Сохранение черновика.\n- Публикация.\n\nКонец сценариев."

    assert render_section_text(section) == expected
    assert render_document_ast({"sections": [section]}, {}) == "### 4.3. 4.3\n" + expected
    assert section == original


def test_missing_parent_does_not_copy_child_content():
    template = _template(("1", True, ["paragraph", "list"]))
    document = {"sections": [_section("1.3", {"type": "list", "items": ["First"]}, {"type": "paragraph", "text": "Second"}), _section("1.1", {"type": "paragraph", "text": "Third"})]}

    normalized = normalize_document_ast(document, template)

    assert [section["id"] for section in normalized["sections"]] == ["1", "1.1", "1.3"]
    assert normalized["sections"][0]["blocks"] == []
    assert normalized["sections"][2]["blocks"] == document["sections"][0]["blocks"]


def test_empty_ancestors_preserve_child_content_without_mutable_sharing():
    template = _template(("1", True, ["paragraph"]), ("1.1", True, ["image"]))
    document = {"sections": [_section("1.1.2", {"type": "image", "url": "image.png"}, {"type": "paragraph", "text": "Text"})]}
    original = deepcopy(document)

    normalized = normalize_document_ast(document, template)

    assert normalized["sections"][0]["blocks"] == []
    assert normalized["sections"][1]["blocks"] == []
    normalized["sections"][2]["blocks"][0]["url"] = "updated.png"
    assert document == original


def test_nested_sections_are_flattened_without_copying_their_content():
    template = _template(("1", True, ["paragraph"]))
    nested = _section("1.2.1", {"type": "paragraph", "text": "Nested"})
    document = {"sections": [_section("1.2", {"type": "paragraph", "text": "Parent"}, nested)]}

    normalized = normalize_document_ast(document, template)

    assert normalized["sections"][0]["blocks"] == []
    assert normalized["sections"][1]["blocks"] == [{"type": "paragraph", "text": "Parent"}]
    assert normalized["sections"][2]["blocks"] == nested["blocks"]
    assert document["sections"][0]["blocks"][1] == nested


@pytest.mark.parametrize("other_id", ["10.1", "11.1", "1x.1"])
def test_inheritance_requires_an_exact_dotted_parent(other_id):
    document = {"sections": [_section(other_id, {"type": "paragraph", "text": "Other"})]}

    normalized = normalize_document_ast(document, _template(("1", True, ["paragraph"])))

    assert normalized["sections"][0]["blocks"] == []


def test_rendering_keeps_empty_section_headings_without_inventing_requirements():
    document = {"sections": [_section("1", {"type": "paragraph", "text": "Цель проекта"}), _section("2"), _section("3", {"type": "paragraph", "text": "  "})]}
    original = deepcopy(document)

    markdown = render_document_ast(document, {"rendering": {"body_heading_base_level": 2}})

    assert markdown.rstrip() == "## 1. 1\nЦель проекта\n\n## 2. 2\n\n## 3. 3"
    assert document == original


def test_optional_sections_stay_empty_and_template_fields_are_removed_from_owned_copy():
    template = _template(("1", False, ["paragraph"]), ("2", True, ["paragraph"]))
    section = {**_section("1.1", {"type": "paragraph", "text": "Child"}), "parent_id": "1", "required": True, "allowed_blocks": ["paragraph"], "semantic_requirements": ["requirement"]}
    document = {"sections": [section, _section("2", {"type": "paragraph", "text": "Existing"})], "metadata": {"tags": ["original"]}}
    original = deepcopy(document)

    normalized = normalize_document_ast(document, template)

    assert normalized["sections"][0]["blocks"] == []
    assert set(normalized["sections"][1]) == {"id", "title", "blocks"}
    assert normalized["sections"][2]["blocks"] == [{"type": "paragraph", "text": "Existing"}]
    assert "metadata" not in normalized
    assert "original" in normalized["warnings"][0]["message"]
    normalized["warnings"][0]["message"] = "Updated"
    assert document == original


def test_duplicate_section_ids_remain_visible_to_validation():
    document = {"sections": [_section("1"), _section("1")]}

    assert normalize_document_ast(document, _template(("1", False, []))) == document


def test_required_scope_restores_the_complete_outline_without_copying_child_content():
    template = _template(("1", True, ["paragraph"]), ("1.1", True, ["paragraph"]), ("2", False, ["paragraph"]))
    document = {"section_scope": "required", "sections": [_section("1"), _section("1.1", {"type": "paragraph", "text": "Child content"})]}
    original = deepcopy(document)

    normalized = normalize_document_ast(document, template)

    assert normalized == {**original, "sections": [*original["sections"], _section("2")]}
    normalized["sections"][1]["blocks"][0]["text"] = "Changed"
    assert document == original


def test_required_scope_keeps_explicit_optional_sections():
    document = {"section_scope": "required", "sections": [_section("1"), _section("2", {"type": "paragraph", "text": "Optional content"})]}

    assert normalize_document_ast(document, _template(("1", True, ["paragraph"]), ("2", False, ["paragraph"]))) == document


def test_content_without_an_allowed_descendant_stays_empty():
    document = {"sections": [_section("1.1", {"type": "paragraph", "text": " "}, {"type": "image", "url": "image.png"})]}

    normalized = normalize_document_ast(document, _template(("1", True, ["paragraph"])))

    assert normalized["sections"][0]["blocks"] == []


def test_contract_failure_retains_first_path_and_details_without_http_dependency():
    schema = {"type": "object", "properties": {"z": {"type": "integer"}, "a": {"type": "integer"}}}

    with pytest.raises(RuleViolation) as caught:
        validate_contract("example", {"z": "z", "a": "a"}, schema)

    assert caught.value.code == "INVALID_EXAMPLE"
    assert caught.value.details == {"path": "a", "validator": "type"}
    assert "at a" in caught.value.message
    assert not hasattr(caught.value, "status")


def test_renderer_uses_supplied_heading_level_and_hash_retains_all_ast_fields():
    section = _section("1.2", {"type": "table", "headers": ["Count"], "rows": [[1.0]]})

    markdown = render_document_ast({"sections": [section]}, {"rendering": {"body_heading_base_level": 1}})

    assert markdown == "## 1.2. 1.2\n| Count |\n| --- |\n| 1 |"
    assert section_hash(section) == section_hash(dict(reversed(list(section.items()))))
    assert section_hash({**section, "evidence_refs": ["source"]}) != section_hash(section)
