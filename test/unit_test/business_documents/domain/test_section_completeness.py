from copy import deepcopy

from business_documents.domain.assessment import empty_section_questions, without_answered_gap_warnings


TEMPLATE = {"sections": [{"id": "1", "title": "Цель"}, {"id": "2", "title": "НПА"}, {"id": "5.4", "title": "Отчетность"}, {"id": "5.5", "title": "Мониторинг"}]}


def test_questions_cover_every_missing_or_empty_template_section():
    document = {"sections": [{"id": "1", "blocks": [{"type": "paragraph", "text": "Цель известна"}]}, {"id": "2", "blocks": []}]}
    questions = empty_section_questions(document, TEMPLATE, [])

    assert [q["target_section_id"] for q in questions] == ["2", "5.4", "5.5"]
    assert all(q["allow_custom_answer"] and "подтвердите" in q["text"] for q in questions)
    assert document["sections"][1]["blocks"] == []


def test_existing_question_or_confirmation_is_not_repeated():
    generated = [{"target_section_id": "2"}]
    known = [{"semantic_tag": "empty_section:5.4"}]
    questions = empty_section_questions({"sections": []}, TEMPLATE, generated, known)

    assert [q["target_section_id"] for q in questions] == ["1", "5.5"]


def test_undefined_requirements_placeholder_does_not_close_a_gap_or_repeat_a_known_question():
    document = {"sections": [{"id": section["id"], "blocks": [{"type": "paragraph", "text": "Требования не определены"}]} for section in TEMPLATE["sections"]]}

    questions = empty_section_questions(document, TEMPLATE, [{"target_section_id": "2"}], [{"semantic_tag": "empty_section:5.4"}])

    assert [q["target_section_id"] for q in questions] == ["1", "5.5"]


def test_explicit_absence_is_preserved_as_section_content():
    document = {"sections": [{"id": "2", "blocks": [{"type": "paragraph", "text": "Автор подтвердил: НПА неприменимы."}]}]}

    assert "2" not in {q["target_section_id"] for q in empty_section_questions(document, TEMPLATE, [])}


def test_answered_placeholder_does_not_repeat_a_gap_but_unanswered_sections_still_do():
    document = {"sections": [{"id": section["id"], "blocks": [{"type": "paragraph", "text": "Требования не определены"}]} for section in TEMPLATE["sections"]]}

    questions = empty_section_questions(document, TEMPLATE, [], answered_section_ids=frozenset({"2"}))

    assert [q["target_section_id"] for q in questions] == ["1", "5.4", "5.5"]


def test_only_structural_warnings_for_exact_answered_placeholders_are_removed():
    document = {
        "sections": [{"id": "2", "blocks": [{"type": "paragraph", "text": "Требования не определены"}]}, {"id": "5.5", "blocks": []}],
        "warnings": [
            {"code": "REQUIRED_SECTION_EMPTY", "section_id": "2", "message": "Clarify"},
            {"code": "REQUIREMENT_COVERAGE_FAILED", "section_id": "2", "message": "A substantive conflict"},
            {"code": "REQUIRED_SECTION_EMPTY", "section_id": "5.5", "message": "Clarify another section"},
        ],
    }
    original = deepcopy(document)

    result = without_answered_gap_warnings(document, frozenset({"2", "5.5"}))

    assert result["warnings"] == original["warnings"][1:]
    assert document == original
    assert without_answered_gap_warnings(document, frozenset()) == original
