from copy import deepcopy

import pytest

from business_documents.domain.errors import RuleViolation
from business_documents.domain.requirement_coverage import requirement_fragments, validate_requirement_coverage


FRAGMENTS = requirement_fragments({"comment": "Хранить 90 дней. Повтор не меняет первую дату; не создавать дубликат."})
SECTIONS = {"1": "Хранение: 90 суток.", "4.3": "Повторное действие оставляет первоначальную дату и не создаёт новую запись."}


def _report():
    return {"checks": [{"fragment_id": key, "status": "PRESENT", "matches": [section], "required_section_ids": [], "superseded_by": None} for key, section in zip(FRAGMENTS, ["1", "4.3", "4.3"])]}


def test_inventory_keeps_every_sentence_and_semicolon_clause():
    assert [fragment["text"] for fragment in FRAGMENTS.values()] == ["Хранить 90 дней.", "Повтор не меняет первую дату;", "не создавать дубликат."]


def test_resolves_semantic_verdict_to_actual_blocks_without_model_copying_text():
    report = _report()
    verified = validate_requirement_coverage(report, FRAGMENTS, SECTIONS)
    assert verified["checks"][0]["matches"] == [{"block_id": "1", "quote": SECTIONS["1"]}]
    assert report["checks"][0]["matches"] == ["1"]


@pytest.mark.parametrize("status", ["MISSING", "CONTRADICTED"])
def test_partial_loss_of_one_composite_comment_fails_closed(status):
    report = _report()
    report["checks"][1].update(status=status, matches=[])
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, FRAGMENTS, SECTIONS)
    assert caught.value.code == "REQUIREMENT_COVERAGE_FAILED"
    assert caught.value.details["requirements"][0]["text"] == "Повтор не меняет первую дату;"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["checks"].pop(),
        lambda report: report["checks"].append(deepcopy(report["checks"][0])),
        lambda report: report["checks"][0].update(fragment_id="unknown"),
        lambda report: report["checks"][0].update(matches=[]),
        lambda report: report["checks"][0].update(matches=[{"block_id": "1", "quote": "Выдуманная цитата"}]),
        lambda report: report["checks"][0].update(matches=["missing"]),
        lambda report: report["checks"][0].update(matches=[[]]),
        lambda report: report["checks"][0].update(status="SUPERSEDED", superseded_by="comment:1"),
        lambda report: report["checks"][0].update(status=[]),
        lambda report: report["checks"][0].pop("required_section_ids"),
        lambda report: report["checks"][0].update(required_section_ids=None),
        lambda report: report["checks"][0].update(required_section_ids=[[]]),
        lambda report: report["checks"][0].update(required_section_ids=["5.4", "5.4"]),
        lambda report: report["checks"][0].update(required_section_ids=["5.6"]),
    ],
)
def test_incomplete_or_fabricated_verifier_response_is_rejected(mutate):
    report = _report()
    mutate(report)
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, FRAGMENTS, SECTIONS)
    assert caught.value.code == "INVALID_REQUIREMENT_COVERAGE"


def test_explicit_deletion_can_remove_an_old_requirement_without_inventing_a_replacement():
    fragments = requirement_fragments({"section:5": "Хранить 90 дней.", "comment": "Удалить ограничение срока хранения."})
    report = {
        "checks": [
            {"fragment_id": "section:5:0", "status": "SUPERSEDED", "matches": [], "required_section_ids": [], "superseded_by": "comment:0"},
            {"fragment_id": "comment:0", "status": "REMOVAL", "matches": [], "required_section_ids": [], "superseded_by": None},
        ]
    }
    assert validate_requirement_coverage(report, fragments, {"5": "Доступ после входа."}) == report


def test_citation_to_an_existing_empty_block_is_rejected():
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(_report(), FRAGMENTS, {**SECTIONS, "1": "  "})
    assert caught.value.code == "INVALID_REQUIREMENT_COVERAGE"


@pytest.mark.parametrize("required,matched", [(["5.4"], ["5.5:0"]), (["5.4", "5.5"], ["5.4:0"])])
def test_present_verdict_cannot_bypass_required_section_placement(required, matched):
    fragments = requirement_fragments({"comment": "Отчёт содержит сотрудника и дату. Мониторинг проверяет сбои."})
    report = {"checks": [{"fragment_id": key, "status": "PRESENT", "matches": matched, "required_section_ids": required, "superseded_by": None} for key in fragments]}
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, fragments, {"5.4:0": "Бизнес-отчёт", "5.5:0": "Технический отчёт о сбоях"})
    assert caught.value.code == "REQUIREMENT_COVERAGE_FAILED"
    assert all(item["reason"] == "WRONG_SECTION" and item["required_section_ids"] == required for item in caught.value.details["requirements"])


def test_business_report_and_technical_report_use_their_respective_sections():
    fragments = requirement_fragments({"comment": "Отчёт содержит сотрудника и дату. Технический отчёт содержит время сбоя."})
    report = {
        "checks": [{"fragment_id": key, "status": "PRESENT", "matches": [section + ":0"], "required_section_ids": [section], "superseded_by": None} for key, section in zip(fragments, ["5.4", "5.5"])]
    }
    verified = validate_requirement_coverage(report, fragments, {"5.4:0": "Сотрудник и дата", "5.5:0": "Время сбоя"})
    assert [check["matches"][0]["block_id"] for check in verified["checks"]] == ["5.4:0", "5.5:0"]


def test_explicit_input_can_supersede_only_an_existing_requirement():
    fragments = requirement_fragments({"section:5": "Хранить 90 дней.", "comment": "Теперь хранить 120 дней."})
    report = {
        "checks": [
            {"fragment_id": "section:5:0", "status": "SUPERSEDED", "matches": [], "required_section_ids": [], "superseded_by": "comment:0"},
            {"fragment_id": "comment:0", "status": "PRESENT", "matches": ["5"], "required_section_ids": [], "superseded_by": None},
        ]
    }
    assert validate_requirement_coverage(report, fragments, {"5": "Хранить 120 дней."})["checks"][1]["matches"] == [{"block_id": "5", "quote": "Хранить 120 дней."}]
    report["checks"][1].update(status="NOT_REQUIREMENT", matches=[])
    with pytest.raises(RuleViolation) as caught:
        validate_requirement_coverage(report, fragments, {"5": "Нет срока хранения."})
    assert caught.value.code == "INVALID_REQUIREMENT_COVERAGE"
