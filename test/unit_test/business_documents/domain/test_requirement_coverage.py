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
def test_failure_preserves_actual_source_text_for_the_next_diff(source_id):
    report = _failure()
    report["issues"][0]["source_id"] = source_id
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, SOURCES, SECTIONS)
    assert caught.value.code == "REQUIREMENT_COVERAGE_FAILED"
    assert caught.value.details["requirements"] == [{**report["issues"][0], "text": SOURCES[source_id]["text"]}]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(status="PASS"),
        lambda r: r.update(status=[]),
        lambda r: r.update(issues=[]),
        lambda r: r.update(issues=[r["issues"][0]] * 5),
        lambda r: r.update(issues=None),
        lambda r: r["issues"][0].update(source_id="invented"),
        lambda r: r["issues"][0].update(source_id=[]),
        lambda r: r["issues"][0].update(section_id="missing"),
        lambda r: r["issues"][0].update(section_id=[]),
        lambda r: r["issues"][0].update(reason=" "),
        lambda r: r["issues"][0].update(reason="x" * 201),
        lambda r: r["issues"][0].update(reason=None),
        lambda r: r["issues"][0].update(quote="invented"),
        lambda r: r.update(extra=True),
        lambda r: r.pop("status"),
    ],
)
def test_malformed_or_invented_semantic_verdict_fails_closed(mutate):
    report = deepcopy(_failure())
    mutate(report)
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, SOURCES, SECTIONS)
    assert caught.value.code == "INVALID_REQUIREMENT_COVERAGE"
