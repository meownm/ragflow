"""Independent golden expectations for inventory, verdict and resource guards."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from api.technical_specifications.alignment import candidate_identity, failure_result, source_snapshot, strict_json, validate_report
from api.technical_specifications.alignment_budget import AlignmentBudget, plan_request
from api.technical_specifications.domain import SpecificationError, empty_sections


@pytest.fixture
def inputs():
    document = {"id": "tz", "revision": 1, "sections": {**empty_sections(), "4": "Оператор принимает до 50 файлов.", "8": "До 50 файлов, размер каждого до 40 МБ."}}
    basis = source_snapshot(
        [
            {
                "document_id": "bt",
                "revision_id": "r1",
                "agreement_event_id": "agreed-1",
                "content_hash": "hash",
                "body_markdown": "Принимать любой пакет от 1 до 50 файлов.\n\nРазмер каждого файла до 40 МБ.",
            }
        ]
    )
    report = {
        "candidate_sha256": candidate_identity(document),
        "basis_sha256": basis["sha256"],
        "requirements": [
            {"id": item["id"], "coverage": "IMPLEMENTED", "consistency": "CONSISTENT", "references": [{"section": "8", "quote": "До 50 файлов, размер каждого до 40 МБ."}], "reason": ""}
            for item in basis["inventory"]
        ],
        "additional_rules": [],
        "joint": {"checked_ids": [item["id"] for item in basis["inventory"]], "consistency": "CONSISTENT", "reason": ""},
    }
    return document, basis, report


def test_full_valid_report_and_positive_citation_do_not_hide_negative_decision(inputs):
    document, basis, report = inputs
    assert validate_report(report, document, basis)["qualifies"]
    report["requirements"][0].update(consistency="CONTRADICTED", reason="Разделы 4 и 8 несовместимы")
    result = validate_report(report, document, basis)
    assert not result["qualifies"] and result["metrics"]["contradictions"] == 1


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown", "reordered", "wrong_candidate", "wrong_basis", "empty_ref", "false_quote", "missing_joint", "unknown_status", "wrong_type"])
def test_full_json_is_not_proof_of_complete_verification(inputs, damage):
    document, basis, report = inputs
    if damage == "missing":
        report["requirements"].pop()
    elif damage == "duplicate":
        report["requirements"][1] = deepcopy(report["requirements"][0])
    elif damage == "unknown":
        report["requirements"][0]["id"] = "other"
    elif damage == "reordered":
        report["requirements"].reverse()
    elif damage == "wrong_candidate":
        report["candidate_sha256"] = "other"
    elif damage == "wrong_basis":
        report["basis_sha256"] = "other"
    elif damage == "empty_ref":
        report["requirements"][0]["references"] = []
    elif damage == "false_quote":
        report["requirements"][0]["references"][0]["quote"] = "не существующее значение"
    elif damage == "missing_joint":
        report["joint"]["checked_ids"].pop()
    elif damage == "unknown_status":
        report["requirements"][0]["coverage"] = "NOT_APPLICABLE"
    else:
        report["joint"]["consistency"] = []
    with pytest.raises(SpecificationError) as error:
        validate_report(strict_json(json.dumps(report)), document, basis)
    assert error.value.code == "ALIGNMENT_REPORT_INVALID"


@pytest.mark.parametrize("coverage", ["MISSING", "PARTIAL", "UNDETERMINED"])
def test_absence_is_not_a_successful_noncontradiction_check(inputs, coverage):
    document, basis, report = inputs
    report["requirements"][0].update(coverage=coverage, references=[], reason="Не реализовано")
    result = validate_report(report, document, basis)
    assert result["report_status"] == "COMPLETE" and result["quality_status"] == "FAIL"


def test_unbased_rule_has_no_fake_bt_id_and_joint_limits_are_checked(inputs):
    document, basis, report = inputs
    report["additional_rules"] = [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": [{"section": "4", "quote": "Оператор"}], "reason": "Новое обязательное действие без основания"}]
    assert validate_report(report, document, basis)["metrics"]["unsupported_rules"] == 1
    report["additional_rules"] = []
    report["joint"].update(consistency="CONTRADICTED", reason="Пакет 50 × 40 МБ не помещается в 100 МБ")
    assert not validate_report(report, document, basis)["qualifies"]


def test_unknown_additional_rule_alone_blocks_an_otherwise_complete_report(inputs):
    document, basis, report = inputs
    assert validate_report(report, document, basis)["qualifies"]
    report["additional_rules"] = [{"kind": "UNDETERMINED", "references": [{"section": "4", "quote": document["sections"]["4"]}], "reason": "Independent condition depends on an unavailable source"}]
    result = validate_report(report, document, basis)
    assert result["execution_status"] == result["report_status"] == "COMPLETE"
    assert result["metrics"]["implemented"] == len(basis["inventory"]) and result["metrics"]["joint_consistency"] == "CONSISTENT"
    assert result["quality_status"] == "FAIL" and not result["qualifies"]
    assert result["report"]["additional_rules"][0]["kind"] == "UNDETERMINED"


def test_reason_byte_limit_is_not_repaired_or_truncated(inputs):
    document, basis, report = inputs
    report["requirements"][0]["reason"] = "я" * 256
    assert validate_report(report, document, basis)["report_status"] == "COMPLETE"
    report["requirements"][0]["reason"] += "a"
    with pytest.raises(SpecificationError) as error:
        validate_report(report, document, basis)
    assert error.value.code == "ALIGNMENT_REPORT_INVALID"
    assert len(report["requirements"][0]["reason"].encode()) == 513


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":{"a":1,"a":2}}', '{"x":NaN}', '{"x":Infinity}', "{}{}", "[]", "```json\n{}\n```", "\ud800"])
def test_ambiguous_or_nonfinite_json_is_rejected_before_normalization(raw):
    with pytest.raises(SpecificationError):
        strict_json(raw)


def test_heading_clauses_and_exact_spans_cannot_disappear():
    raw = "# Отмена запрещена\n\nПервая часть.\n\nВторая часть."
    basis = source_snapshot([{"document_id": "bt", "revision_id": "r", "agreement_event_id": "a", "content_hash": "h", "body_markdown": raw}])
    assert len(basis["inventory"]) == 3
    assert all(raw[item["start"] : item["end"]] == item["quote"] for item in basis["inventory"])
    assert basis["inventory"][0]["quote"] == "# Отмена запрещена"


@pytest.mark.parametrize(
    "body",
    ["Требования не определены.", "## 2. Нормативно-правовые акты (НПА)\nТребования не определены.", "## 5. Нефункциональные требования\nПараметры производительности и доступности не определены."],
)
def test_explicit_absence_is_retained_but_never_counted_as_implemented(inputs, body):
    document, _, report = inputs
    raw = body + "\n\nПринимать любой пакет от 1 до 50 файлов."
    basis = source_snapshot(
        [
            {
                "document_id": "bt",
                "revision_id": "r",
                "agreement_event_id": "a",
                "content_hash": "h",
                "body_markdown": raw,
                "section_headings": ["## 2. Нормативно-правовые акты (НПА)", "## 5. Нефункциональные требования"],
            }
        ]
    )
    report["basis_sha256"] = basis["sha256"]
    report["requirements"][0].update(coverage="NO_REQUIREMENT", references=[], reason="В этом полном фрагменте требование явно не задано.")
    result = validate_report(report, document, basis)
    assert result["qualifies"] and result["metrics"]["implemented"] == result["metrics"]["no_requirement"] == 1
    assert result["metrics"]["returned_fragments"] == result["metrics"]["expected_fragments"] == 2
    assert raw[basis["inventory"][0]["start"] : basis["inventory"][0]["end"]] == body
    report["requirements"][0].update(coverage="IMPLEMENTED")
    with pytest.raises(SpecificationError):
        validate_report(report, document, basis)


@pytest.mark.parametrize(
    "body",
    [
        "## Отмена запрещена\nТребования не определены.",
        "## 99. Отмена запрещена\nТребования не определены.",
        "## 2. Нормативно-правовые акты (НПА)\nТребования не определены. Отмена запрещена.",
        "Требования не определены.\nОтмена запрещена.",
        "Требования не определены, но отмена запрещена.",
        "```\nТребования не определены.\n```",
        "Отмена запрещена.",
    ],
)
def test_no_requirement_cannot_hide_a_heading_clause_or_mixed_source(inputs, body):
    document, _, report = inputs
    basis = source_snapshot(
        [{"document_id": "bt", "revision_id": "r", "agreement_event_id": "a", "content_hash": "h", "body_markdown": body, "section_headings": ["## 2. Нормативно-правовые акты (НПА)"]}]
    )
    report["basis_sha256"] = basis["sha256"]
    report["requirements"] = [{"id": "R001", "coverage": "NO_REQUIREMENT", "consistency": "CONSISTENT", "references": [], "reason": "Нет требований"}]
    report["joint"]["checked_ids"] = ["R001"]
    with pytest.raises(SpecificationError) as error:
        validate_report(report, document, basis)
    assert error.value.code == "ALIGNMENT_REPORT_INVALID"


def test_only_absence_markers_cannot_qualify_an_empty_business_basis(inputs):
    document, _, report = inputs
    basis = source_snapshot([{"document_id": "bt", "revision_id": "r", "agreement_event_id": "a", "content_hash": "h", "body_markdown": "Требования не определены."}])
    report["basis_sha256"] = basis["sha256"]
    report["requirements"] = [{"id": "R001", "coverage": "NO_REQUIREMENT", "consistency": "CONSISTENT", "references": [], "reason": "Требование отсутствует"}]
    report["joint"]["checked_ids"] = ["R001"]
    result = validate_report(report, document, basis)
    assert not result["qualifies"] and result["quality_status"] == "FAIL" and result["metrics"]["implemented"] == 0


@pytest.mark.parametrize("body", ["", " " * 20, "a" * 100_001, "\n\n".join(str(i) for i in range(129))], ids=["empty", "whitespace", "oversized", "too_many_fragments"])
def test_empty_or_oversized_inventory_is_never_a_vacuous_pass(body):
    with pytest.raises(SpecificationError):
        source_snapshot([{"document_id": "bt", "revision_id": "r", "agreement_event_id": "a", "content_hash": "h", "body_markdown": body}])


def test_report_budget_and_call_count_do_not_reset(inputs):
    document, basis, _ = inputs
    from api.technical_specifications.alignment import report_input

    payload = report_input(document, basis)
    model = SimpleNamespace(context_tokens=32000, output_tokens=1, count_tokens=lambda value: len(value) // 4 + 1)
    with pytest.raises(SpecificationError) as error:
        plan_request("system", payload, model)
    assert error.value.code == "ALIGNMENT_CONTEXT_EXCEEDED"
    assert error.value.details["required_output_tokens"] > 1
    budget = AlignmentBudget(calls=1)
    budget.claim()
    with pytest.raises(SpecificationError) as error:
        budget.claim()
    assert error.value.code == "ALIGNMENT_CALL_LIMIT"


def test_deadline_uses_elapsed_time_not_wall_clock(monkeypatch):
    import api.technical_specifications.alignment_budget as module

    now = [10.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    budget = AlignmentBudget(seconds=3, started=10)
    now[0] = 14
    with pytest.raises(SpecificationError):
        budget.claim()
    assert budget.spent_calls == 0


def test_completed_bad_report_is_model_failure_not_infrastructure_unknown():
    assert failure_result("ALIGNMENT_REPORT_INVALID", completed=True)["quality_status"] == "FAIL"
    assert failure_result("MODEL_TRANSPORT_ERROR")["quality_status"] == "UNKNOWN"


@pytest.mark.parametrize("options", [{"seconds": True}, {"seconds": float("nan")}, {"seconds": float("inf")}, {"calls": True}, {"calls": 0}, {"token_limit": False}, {"token_limit": -1}])
def test_invalid_budget_cannot_disable_guards(options):
    with pytest.raises(SpecificationError) as error:
        AlignmentBudget(**options)
    assert error.value.code == "INVALID_ALIGNMENT_BUDGET"


def test_unknown_usage_retains_reservation_and_retry_cannot_spend_it_again():
    budget = AlignmentBudget(calls=2, token_limit=10)
    budget.claim(6)
    budget.settle(None, None)
    assert budget.reserved_tokens == 6 and budget.spent_tokens == 0
    with pytest.raises(SpecificationError) as error:
        budget.claim(5)
    assert error.value.code == "ALIGNMENT_TOKEN_LIMIT" and budget.spent_calls == 1
    budget.settle(2, 2)
    budget.claim(6)
    assert budget.spent_tokens + budget.reserved_tokens == budget.token_limit


def test_later_known_usage_does_not_erase_an_earlier_unknown_reservation():
    budget = AlignmentBudget(calls=3, token_limit=100)
    first = budget.claim(30)
    budget.settle(None, None, claim_id=first)
    second = budget.claim(40)
    budget.settle(10, 10, claim_id=second)
    assert budget.reserved_tokens == 30 and budget.spent_tokens == 20
    with pytest.raises(SpecificationError) as error:
        budget.settle(10, 10, claim_id=second)
    assert error.value.code == "ALIGNMENT_USAGE_ALREADY_RECORDED"
    assert budget.spent_tokens == 20


def test_actual_overrun_includes_other_unknown_reservations():
    budget = AlignmentBudget(calls=2, token_limit=100)
    first = budget.claim(80)
    budget.settle(None, None, claim_id=first)
    second = budget.claim(10)
    with pytest.raises(SpecificationError) as error:
        budget.settle(20, 20, claim_id=second)
    assert error.value.code == "ALIGNMENT_TOKEN_LIMIT"
    assert budget.spent_tokens == 40 and budget.reserved_tokens == 80


@pytest.mark.parametrize("rows", [[None], [{}], [{"document_id": []}], [{"document_id": ""}]])
def test_malformed_basis_fails_before_inventory_sort(rows):
    with pytest.raises(SpecificationError) as error:
        source_snapshot(rows)
    assert error.value.code == "ALIGNMENT_SOURCE_INVALID"


def test_complete_negative_report_reserve_has_exact_admission_boundary(inputs):
    from api.technical_specifications.alignment import MAX_REPORT_BYTES, report_input

    document, basis, _ = inputs
    payload = report_input(document, basis)
    model = SimpleNamespace(context_tokens=32000, output_tokens=16000, count_tokens=lambda value: len(value.encode()) // 3 + 1)
    plan = plan_request("system", payload, model)
    assert plan["required_output_tokens"] > 1024
    assert plan["required_output_tokens"] <= plan["output_limit"] <= plan["schema_output_token_ceiling"]
    assert plan["schema_max_report_bytes"] <= MAX_REPORT_BYTES
    required = plan["required_output_tokens"]
    model.output_tokens = required - 1
    with pytest.raises(SpecificationError) as error:
        plan_request("system", payload, model)
    assert error.value.code == "ALIGNMENT_CONTEXT_EXCEEDED"
    for limit in (required, required + 1):
        model.output_tokens = limit
        assert plan_request("system", payload, model)["output_limit"] == limit
