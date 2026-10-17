from copy import deepcopy

import pytest

from business_documents.domain.errors import RuleViolation
from business_documents.domain.requirement_coverage import validate_requirement_coverage


SOURCES = {"comment": {"text": "Хранить 90 дней. Повтор не меняет первую дату."}, "section:1": {"text": "Не создавать дубликат."}}
SECTIONS = {"1": "Общие требования", "5.4": "Требования к отчетности", "5.5": "Мониторинг"}


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
