"""Exact source selections avoid free-form citation copying without repairing answers."""

from copy import deepcopy

import pytest

from api.technical_specifications.alignment import CONSISTENCY, COVERAGE, materialize_report, report_format, report_input, source_snapshot, validate_report
from api.technical_specifications.domain import SpecificationError, empty_sections


def packet(text="Оператор отменяет заявку."):
    document = {"id": "tz", "revision": 1, "sections": {**empty_sections(), "4": text}}
    basis = source_snapshot([{"document_id": "bt", "revision_id": "r", "agreement_event_id": "a", "content_hash": "h", "body_markdown": "Оператор отменяет заявку."}])
    payload = report_input(document, basis)
    raw = {
        "candidate_sha256": payload["candidate_sha256"],
        "basis_sha256": payload["basis_sha256"],
        "requirements": [{"id": "R001", "coverage": "IMPLEMENTED", "consistency": "CONSISTENT", "references": [{"citation_id": payload["citations"][0]["id"]}], "reason": "Selected source evidence"}],
        "additional_rules": [],
        "joint": {"checked_ids": ["R001"], "consistency": "CONSISTENT", "reason": ""},
    }
    return document, basis, payload, raw


def test_native_schema_pins_source_hashes_without_supplying_semantic_answers():
    _, _, payload, _ = packet()
    schema = report_format(payload)
    assert schema == payload["response_schema"]
    assert set(schema["required"]) == {"candidate_sha256", "basis_sha256", "requirements", "additional_rules", "joint"}
    assert schema["additionalProperties"] is False
    assert all(schema["properties"][key]["enum"] == [payload[key]] for key in ("candidate_sha256", "basis_sha256"))
    row = schema["properties"]["requirements"]["items"]["properties"]
    assert set(row["coverage"]["enum"]) == COVERAGE and set(row["consistency"]["enum"]) == CONSISTENCY
    assert row["id"] == row["reason"] == {"type": "string"}
    assert schema["properties"]["joint"]["properties"]["checked_ids"] == {"type": "array", "items": {"type": "string"}}
    schema["properties"]["candidate_sha256"]["enum"] = ["0" * 64]
    assert report_format(payload) == payload["response_schema"]


@pytest.mark.parametrize("damage", ["missing", "wrong_hash_enum", "forced_label", "bad_input_hash"])
def test_missing_or_tampered_native_schema_is_never_a_json_fallback(damage):
    _, _, payload, _ = packet()
    if damage == "missing":
        del payload["response_schema"]
    elif damage == "wrong_hash_enum":
        payload["response_schema"]["properties"]["basis_sha256"]["enum"] = ["0" * 64]
    elif damage == "forced_label":
        payload["response_schema"]["properties"]["requirements"]["items"]["properties"]["coverage"]["enum"] = ["IMPLEMENTED"]
    else:
        payload["basis_sha256"] = "invalid"
    with pytest.raises(SpecificationError) as error:
        report_format(payload)
    assert error.value.code == "ALIGNMENT_REPORT_INVALID"


def test_admission_counts_the_full_generation_schema_and_keeps_output_reserve():
    from types import SimpleNamespace

    from api.technical_specifications.alignment_budget import plan_request

    _, _, payload, _ = packet()
    model = SimpleNamespace(context_tokens=32000, output_tokens=None, count_tokens=lambda text: len(text.encode()))
    complete = plan_request("system", payload, model)
    without_schema = {key: value for key, value in payload.items() if key != "response_schema"}
    smaller = plan_request("system", without_schema, model)
    assert complete["input_estimate"] > smaller["input_estimate"]
    assert complete["output_limit"] < smaller["output_limit"]
    assert complete["required_output_tokens"] == smaller["required_output_tokens"]


@pytest.mark.parametrize(
    "text",
    [
        "Оператору отмена запрещена. Журналирование обязательно.",
        "Я🙂" * 1000,
        "Я" * 255 + "a",
        "Я" * 256,
        "Я" * 256 + "a",
        "Начало. " + "Ж" * 1000 + ". Конец.",
        "| Роль | Право |\n| Оператор | Отмена запрещена |",
        "```mermaid\nstateDiagram-v2\nA --> B\n```",
    ],
)
def test_catalog_preserves_complete_input_and_every_nonwhitespace_character(text):
    document, _, payload, _ = packet(text)
    assert payload["sections"] == document["sections"]
    identifiers = [item["id"] for item in payload["citations"]]
    assert len(identifiers) == len(set(identifiers))
    for section, original in payload["sections"].items():
        quotes = [item["quote"] for item in payload["citations"] if item["section"] == section]
        assert all(0 < len(quote.encode()) <= 512 and quote in original for quote in quotes)
        assert "".join("".join(quotes).split()) == "".join(original.split())


def test_source_selection_is_exact_and_does_not_mutate_the_model_answer():
    document, basis, payload, raw = packet()
    original = deepcopy(raw)
    resolved = materialize_report(raw, payload)
    assert resolved["requirements"][0]["references"] == [{"section": "4", "quote": document["sections"]["4"]}]
    assert validate_report(resolved, document, basis)["report_status"] == "COMPLETE"
    resolved["joint"]["checked_ids"].clear()
    resolved["requirements"][0]["references"][0]["quote"] = "Changed after materialization"
    assert raw == original


@pytest.mark.parametrize("damage", ["unknown_id", "literal_quote", "extra_field", "boolean_id", "too_many", "changed_catalog", "wrong_section"])
def test_no_fallback_or_quote_repair_for_invalid_selections(damage):
    _, _, payload, raw = packet()
    ref = raw["requirements"][0]["references"][0]
    if damage == "unknown_id":
        ref["citation_id"] = "C999999"
    elif damage == "literal_quote":
        raw["requirements"][0]["references"] = [{"section": "4", "quote": "Оператор отменяет заявку."}]
    elif damage == "extra_field":
        ref["quote"] = "Оператор отменяет заявку."
    elif damage == "boolean_id":
        ref["citation_id"] = True
    elif damage == "too_many":
        raw["requirements"][0]["references"] *= 10
    elif damage == "changed_catalog":
        payload["citations"][0]["quote"] = "Fabricated quote"
    else:
        payload["citations"][0]["section"] = "9"
    with pytest.raises(SpecificationError) as error:
        materialize_report(raw, payload)
    assert error.value.code == "ALIGNMENT_REPORT_INVALID"


def test_selection_ids_cannot_rebind_a_report_to_another_revision():
    document, basis, _, raw = packet()
    document["revision"] = 2
    document["sections"]["4"] = "Оператору отмена запрещена."
    payload = report_input(document, basis)
    with pytest.raises(SpecificationError) as error:
        validate_report(materialize_report(raw, payload), document, basis)
    assert error.value.code == "ALIGNMENT_REPORT_INVALID"


def test_multiline_condition_does_not_exhaust_the_nine_reference_limit():
    text = "Оператор\nможет\nотменить\nтолько\nсобственную\nещё\nне\nисполненную\nзаявку\nбесплатно."
    document, basis, payload, raw = packet(text)
    assert len(text.encode()) < 512 and len(text.splitlines()) > 9
    assert payload["citations"][0]["quote"] == text
    report = materialize_report(raw, payload)
    assert len(report["requirements"][0]["references"]) == 1
    assert validate_report(report, document, basis)["report_status"] == "COMPLETE"


def test_valid_but_irrelevant_selection_still_fails_the_semantic_oracle():
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score
    from test.unit_test.technical_specifications.test_alignment_metrics import case_result

    case = next(case for case in CASES if case[0] == "BTZ-53")
    document, basis = inputs(case)
    payload = report_input(document, basis)
    raw = case_result("BTZ-53")["result"]["report"]
    for row, quote in zip(raw["requirements"], case[5]["row_quotes"], strict=True):
        row["references"] = [{"citation_id": next(item["id"] for item in payload["citations"] if quote in item["quote"])}]
    row = raw["requirements"][0]
    row["references"] = [{"citation_id": next(item["id"] for item in payload["citations"] if "Технический узел 2:" in item["quote"] and case[5]["row_quotes"][0] not in item["quote"])}]
    result = validate_report(materialize_report(raw, payload), document, basis)
    assert result["report_status"] == "COMPLETE"
    assert "row:1:EVIDENCE_CLAUSE_MISSING" in score(case, result)
