from copy import deepcopy

import pytest

from business_documents.domain.errors import RuleViolation
from business_documents.domain.requirement_coverage import agreement_decisions, validate_requirement_coverage


SOURCES = {"comment": {"text": "Хранить 90 дней. Повтор не меняет первую дату."}, "section:1": {"text": "Не создавать дубликат."}}
SECTIONS = {"1": "Общие требования", "5.4": "Требования к отчетности", "5.5": "Мониторинг"}


@pytest.mark.parametrize(
    "changes,status,event_id",
    [
        ([], "ACCEPTED", "accept"),
        ([("cancel", "ProposalAcceptanceCancelled", {"proposal_id": "p", "decision_event_id": "accept"})], "CANCELLED", "accept"),
        (
            [("cancel", "ProposalAcceptanceCancelled", {"proposal_id": "p", "decision_event_id": "accept"}), ("reaccept", "ProposalDecided", {"proposal_id": "p", "decision": "ACCEPTED"})],
            "ACCEPTED",
            "reaccept",
        ),
        (
            [("reaccept", "ProposalDecided", {"proposal_id": "p", "decision": "ACCEPTED"}), ("stale-cancel", "ProposalAcceptanceCancelled", {"proposal_id": "p", "decision_event_id": "accept"})],
            "ACCEPTED",
            "reaccept",
        ),
        ([("reject", "ProposalDecided", {"proposal_id": "p", "decision": "REJECTED"})], "REJECTED", "reject"),
    ],
)
def test_proposal_projection_resolves_exact_cancellation_and_latest_decision(changes, status, event_id):
    records = [("accept", "ProposalDecided", {"proposal_id": "p", "decision": "ACCEPTED"}), *changes]
    snapshot = {
        "agreement_history": {"proposals": [{"id": "p", "review_cycle": 1, "text": "Не создавать дубликаты."}]},
        "source_events": [{"event_id": identifier, "sequence": sequence, "event_type": kind, "payload": payload} for sequence, (identifier, kind, payload) in enumerate(records, 1)],
        "active_change_input_event_ids": [],
    }
    original = deepcopy(snapshot)
    state = agreement_decisions(snapshot)["proposals"]
    assert len(state) == 1
    assert state[0]["status"] == status and state[0]["event_id"] == event_id
    assert state[0]["text"] == "Не создавать дубликаты."
    assert ("cancellation_event_id" in state[0]) == (status == "CANCELLED")
    assert snapshot == original


def test_answer_projection_keeps_old_limits_selected_text_and_later_clarifications():
    snapshot = {
        "agreement_history": {
            "questions": [
                {"id": "q-old", "review_cycle": 0, "text": "Срок хранения?", "options": [{"option_id": "chosen", "label": "90 дней."}, {"option_id": "other", "label": "Без ограничений."}]},
                {"id": "q-later", "review_cycle": 1, "text": "Что меняется для архива?", "options": []},
            ],
            "answers": [
                {"id": "a-old", "question_id": "q-old", "selected_option_id": "chosen"},
                {"id": "a-later", "question_id": "q-later", "custom_answer": "Для архива 120 дней, остальные сроки без изменений."},
            ],
        },
        "source_events": [
            {"event_id": "old", "sequence": 1, "event_type": "QuestionAnswered", "payload": {"question_id": "q-old", "answer_id": "a-old"}},
            {"event_id": "later", "sequence": 2, "event_type": "QuestionAnswered", "payload": {"question_id": "q-later", "answer_id": "a-later"}},
        ],
    }
    original = deepcopy(snapshot)
    answers = agreement_decisions(snapshot)["answers"]
    assert [(answer["event_id"], answer["text"]) for answer in answers] == [("old", "90 дней."), ("later", "Для архива 120 дней, остальные сроки без изменений.")]
    assert answers[0]["context"] == "Срок хранения?" and answers[0]["review_cycle"] == 0
    assert "Без ограничений." not in str(answers)
    assert snapshot == original


def test_missing_historical_text_is_explicit_and_current_protocol_can_supply_known_bodies():
    snapshot = {
        "source_events": [
            {"event_id": "old", "event_type": "QuestionAnswered", "payload": {"question_id": "q-old", "answer_id": "a-old"}},
            {"event_id": "current", "event_type": "QuestionAnswered", "payload": {"question_id": "q", "answer_id": "a"}},
            {"event_id": "accepted", "event_type": "ProposalDecided", "payload": {"proposal_id": "p", "decision": "ACCEPTED"}},
        ],
        "protocol": {
            "questions": [{"question_id": "q", "text": "Уточнение?", "answer": {"answer_id": "a", "custom_answer": "Сохранить запрет."}}],
            "proposals": [{"proposal_id": "p", "text": "Не менять первую дату."}],
        },
    }
    state = agreement_decisions(snapshot)
    assert state["answers"][0]["event_id"] == "old" and state["answers"][0]["text"] is None
    assert state["answers"][1]["text"] == "Сохранить запрет."
    assert state["proposals"][0]["status"] == "ACCEPTED" and state["proposals"][0]["text"] == "Не менять первую дату."


def _failure():
    return {"status": "FAIL", "issues": [{"source_id": "comment", "section_id": "1", "reason": "При повторе теряется первая дата."}]}


def test_pass_is_one_verdict_without_per_sentence_citations():
    report = {"status": "PASS", "issues": []}
    assert validate_requirement_coverage(report, SOURCES, SECTIONS) == report


@pytest.mark.parametrize("source_id", ["comment", "section:1"])
def test_failure_returns_source_scoped_advice(source_id):
    report = _failure()
    report["issues"][0]["source_id"] = source_id
    assert validate_requirement_coverage(report, SOURCES, SECTIONS) == report


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(status="PASS"),
        lambda r: r.update(status=[]),
        lambda r: r.update(issues=[]),
        lambda r: r.update(issues=None),
        lambda r: r["issues"][0].update(source_id="invented"),
        lambda r: r["issues"][0].update(source_id=[]),
        lambda r: r["issues"][0].update(section_id="missing"),
        lambda r: r["issues"][0].update(section_id=[]),
        lambda r: r["issues"][0].update(reason=" "),
        lambda r: r["issues"][0].update(reason=None),
        lambda r: r.pop("status"),
    ],
)
def test_malformed_or_invented_semantic_verdict_fails_closed(mutate):
    report = deepcopy(_failure())
    mutate(report)
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, SOURCES, SECTIONS)
    assert caught.value.code == "INVALID_REQUIREMENT_COVERAGE"


def test_advisory_report_accepts_extra_fields_and_longer_reasons():
    report = _failure()
    report.update(summary="Проверено")
    report["issues"][0].update(reason="Подробное замечание. " * 30, quote="Дополнительное пояснение")
    report["issues"] *= 5
    normalized = validate_requirement_coverage(report, SOURCES, SECTIONS)
    assert normalized["status"] == "FAIL" and len(normalized["issues"]) == 5
    assert normalized["issues"][0]["reason"] == report["issues"][0]["reason"]
    assert set(normalized["issues"][0]) == {"source_id", "section_id", "reason"}
    assert validate_requirement_coverage({"status": "PASS", "issues": [], "summary": "Готово"}, SOURCES, SECTIONS) == {"status": "PASS", "issues": []}
