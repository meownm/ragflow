from copy import deepcopy

import pytest

from business_documents.sql_query.dialogue import dialogue_response


QUESTION = {"id": "region", "text": "Какой регион выбрать?", "options": ["North", "South"]}


@pytest.mark.parametrize("value", [
    {"response_kind": "EXPLANATION", "answer": "LEFT JOIN сохраняет заказы без клиента."},
    {"response_kind": "CLARIFICATION", "answer": "Уточните ограничение.", "questions": [QUESTION]},
])
def test_non_mutating_responses_are_explicit_and_copied(value):
    result = dialogue_response(value)
    assert result == value
    assert result is not value
    if "questions" in value:
        result["questions"][0]["text"] = "other"
        assert value["questions"][0]["text"] == QUESTION["text"]


@pytest.mark.parametrize("value", [
    {"response_kind": []},
    {"response_kind": "EXECUTE", "answer": "Run"},
    {"response_kind": "EXPLANATION", "answer": ""},
    {"response_kind": "EXPLANATION", "answer": "Answer", "sql": "SELECT 1"},
    {"response_kind": "CLARIFICATION", "answer": "Question", "questions": []},
    {"response_kind": "CLARIFICATION", "answer": "Question", "questions": [QUESTION, QUESTION]},
    {"response_kind": "CLARIFICATION", "answer": "Question", "questions": [{**QUESTION, "options": ["North", "North"]}]},
    {"response_kind": "CLARIFICATION", "answer": "Question", "questions": [{**QUESTION, "options": [1]}]},
    {"response_kind": "CLARIFICATION", "answer": "Question", "questions": [{**QUESTION, "sql": "DELETE FROM orders"}]},
])
def test_dialogue_response_cannot_hide_a_mutation_or_unbounded_question(value):
    before = deepcopy(value)
    with pytest.raises(ValueError):
        dialogue_response(value)
    assert value == before


def test_edit_proposals_use_the_separate_sql_validation_path():
    assert dialogue_response({"sql": "SELECT 1", "parameters": [], "summary": "Edit"}) is None
