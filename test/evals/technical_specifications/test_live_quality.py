"""Negative controls for the frozen real-model acceptance checks."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from api.technical_specifications.domain import SpecificationError
from test.evals.technical_specifications.live_quality import chat_input, merge_inputs, score_chat, score_merge, score_verification

CORPUS = json.loads(Path(__file__).with_name("golden.v1.json").read_text(encoding="utf-8"))


def test_frozen_cases_cover_question_context_selection_injection_and_both_merge_choices():
    assert [case["id"] for case in CORPUS["cases"]] == ["C01", "C02", "C03", "C04", "C05", "V01", "V02", "M01", "M02"]


def test_chat_scorer_rejects_missing_edit_and_changes_outside_context():
    case = CORPUS["cases"][1]
    document, message = chat_input(CORPUS["sections"], case)
    assert score_chat(CORPUS["sections"], case, document, message, {"answer": "Готово", "patches": {}})
    with pytest.raises(SpecificationError, match="других разделов"):
        score_chat(CORPUS["sections"], case, document, message, {"answer": "Готово", "patches": {"4": "Изменено"}})


def test_scope_refusal_explains_the_required_user_action():
    case = CORPUS["cases"][3]
    document, message = chat_input(CORPUS["sections"], case)
    assert score_chat(CORPUS["sections"], case, document, message, {"answer": "Готово", "patches": {}})
    assert score_chat(CORPUS["sections"], case, document, message, {"answer": "Выберите контекст «Всё ТЗ»", "patches": {}}) == []


def test_consistent_wrong_merge_choice_and_lost_independent_edits_fail():
    base = CORPUS["sections"]
    _local, remote = merge_inputs(base)
    correct = deepcopy(remote)
    correct["4"] = correct["4"].replace("MAX_FILE_SIZE_MB = 10", "MAX_FILE_SIZE_MB = 40")
    assert score_merge(base, correct, "remote") == []
    assert "Chosen batch limit" in score_merge(base, correct, "local")[0]
    correct["9"] = base["9"]
    assert "Independent remote logging change lost" in score_merge(base, correct, "remote")
    correct["4"] = correct["4"].replace("MAX_FILE_SIZE_MB = 40", "MAX_FILE_SIZE_MB = 10")
    assert "Independent local file-size change lost" in score_merge(base, correct, "remote")


@pytest.mark.parametrize("section", ["4", "5", "8", "9"])
def test_merge_scorer_rejects_added_conflicts_even_if_chosen_values_are_present(section):
    base = CORPUS["sections"]
    _local, candidate = merge_inputs(base)
    candidate["4"] = candidate["4"].replace("MAX_FILE_SIZE_MB = 10", "MAX_FILE_SIZE_MB = 40")
    candidate[section] += "\nВ одном пакете допускается 20 файлов."
    assert "Controlled merge changed unrelated text or retained unchosen facts" in score_merge(base, candidate, "remote")


def test_verifier_scorer_rejects_false_success_and_unexplained_rejection():
    assert score_verification({"valid": True, "issues": []}, valid=False)
    assert score_verification({"valid": False, "issues": []}, valid=False)
    assert score_verification({"valid": False, "issues": ["Есть противоречие"]}, valid=False)
    assert score_verification({"valid": False, "issues": ["Раздел 4: 35 файлов; разделы 5 и 8: 50 файлов"]}, valid=False) == []
