"""Approved facts are bounded task data, distinct from saved schema identity."""
from copy import deepcopy

import pytest

from business_documents.sql_query.requirements_analysis import AcceptedClarification, RequirementsAnalysisValidationError, answered_clarifications, parse_clarifications, requirements_for_planning


PAIR = {"question": "Год и часовой пояс?", "answer": "2026, Pacific/Apia"}


def test_empty_context_stays_identical_and_approved_facts_preserve_values_and_order():
    base = "Исходное согласованное условие"
    assert requirements_for_planning(base, ()) == base
    pairs = [PAIR, {"question": "Нужны неоплаченные?", "answer": 'Да, включая NULL и ""'}]
    parsed = parse_clarifications(pairs)
    assert parsed == (AcceptedClarification(**PAIR), AcceptedClarification(**pairs[1]))
    text = requirements_for_planning(base, parsed)
    assert text.startswith(base + "\n")
    assert PAIR["answer"] in text
    assert text.index(PAIR["question"]) < text.index(pairs[1]["question"])


def test_only_explicitly_answered_facts_are_forwarded_and_source_is_unchanged():
    payload = {"questions": [{**PAIR, "status": "ANSWERED"}, {"question": "Не принято", "answer": "must-not-be-forwarded", "status": "OPEN"}, {"question": "Необязательно", "answer": None, "status": "SKIPPED"}]}
    before = deepcopy(payload)
    assert answered_clarifications(payload) == [PAIR]
    assert payload == before


def test_declared_maximum_is_not_truncated():
    pairs = [{"question": "q" * 1000, "answer": "a" * 2000} for _ in range(12)]
    parsed = parse_clarifications(pairs)
    assert len(parsed) == 12
    assert parsed[-1].question == pairs[-1]["question"] and parsed[-1].answer == pairs[-1]["answer"]


@pytest.mark.parametrize("value", [None, {}, "text", [None], [{"question": "q", "answer": False}], [{"question": "", "answer": "a"}], [{"question": "q", "answer": ""}], [{**PAIR, "approved": True}], [{"question": "q" * 1001, "answer": "a"}], [{"question": "q", "answer": "a" * 2001}], [PAIR] * 13])
def test_invalid_or_excessive_facts_are_rejected_before_model_use(value):
    with pytest.raises(RequirementsAnalysisValidationError):
        parse_clarifications(value)
