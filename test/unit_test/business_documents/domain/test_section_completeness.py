from business_documents.domain.assessment import empty_section_questions


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


def test_explicit_absence_is_preserved_as_section_content():
    document = {"sections": [{"id": "2", "blocks": [{"type": "paragraph", "text": "Автор подтвердил: НПА неприменимы."}]}]}

    assert "2" not in {q["target_section_id"] for q in empty_section_questions(document, TEMPLATE, [])}
