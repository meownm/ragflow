"""Cross-section repetition checks distinguish shared actors from repeated content."""

import pytest

from business_documents.domain.content_quality import semantic_duplicate_section_pairs
from business_documents.domain.requirement_coverage import changed_section_repetitions


GOAL = "Физические лица — действующие и новые клиенты банка — получают возможность самостоятельно записаться в отделение банка в России через веб-сервис, выбрав удобное отделение, дату и время визита, без обращения к сотруднику банка."
CATEGORIES = {"type": "paragraph", "text": "Единственная категория пользователей — физические лица: действующие и новые клиенты банка."}
ACTORS = {"type": "list", "items": ["Клиент (физическое лицо)", "Веб-сервис записи"]}


@pytest.mark.parametrize("other", [CATEGORIES, ACTORS], ids=["user-categories", "scenario-actors"])
@pytest.mark.parametrize("reverse", [False, True])
def test_shared_actors_are_not_duplicate_content(other, reverse):
    sections = [{"id": "1", "blocks": [{"type": "paragraph", "text": GOAL}]}, {"id": "3.1", "blocks": [other]}]
    if reverse:
        sections.reverse()

    assert semantic_duplicate_section_pairs(sections) == ()
    assert semantic_duplicate_section_pairs(sections, section_pairs=[("1", "3.1")]) == ()
    assert changed_section_repetitions({"sections": []}, {"sections": sections}) == ()


@pytest.mark.parametrize("section_ids", [("1", "3.1"), ("1", "4.3"), ("3", "3.1")])
def test_real_duplicates_are_detected_even_in_actor_or_category_sections(section_ids):
    sections = [{"id": section_id, "blocks": [{"type": "paragraph", "text": GOAL}]} for section_id in section_ids]

    assert semantic_duplicate_section_pairs(sections) == (section_ids,)
    assert changed_section_repetitions({"sections": []}, {"sections": sections}) == (section_ids,)


@pytest.mark.parametrize("reverse", [False, True])
def test_paraphrases_remain_duplicates_with_extra_qualifiers(reverse):
    blocks = [
        "Клиент выбирает свободный слот, после чего система подтверждает запись.",
        "Пользователь выбирает доступный временной интервал, затем сервис подтверждает бронирование.",
    ]
    if reverse:
        blocks.reverse()
    sections = [{"id": section_id, "blocks": [{"type": "paragraph", "text": text}]} for section_id, text in zip(("1", "4.3"), blocks, strict=True)]

    assert semantic_duplicate_section_pairs(sections) == (("1", "4.3"),)
