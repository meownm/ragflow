"""Negative controls for corpus integrity, exact oracles and real-call evidence."""

import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.technical_specifications.domain import SpecificationError
from test.evals.technical_specifications import run_live
from test.evals.technical_specifications.live_quality import changed, chat_input, run_case, run_suite, score_chat, score_content, score_verification, validate_corpus
from test.evals.technical_specifications.run_live import verify_upstream

CORPUS = json.loads(Path(__file__).with_name("golden.v2.json").read_text(encoding="utf-8"))


def case(identifier):
    return deepcopy(next(item for item in CORPUS["cases"] if item["id"] == identifier))


def test_frozen_inventory_retains_baseline_and_complex_negative_scenarios():
    expected = {*(f"C{n:02}" for n in range(1, 18)), *(f"V{n:02}" for n in range(1, 19)), *(f"M{n:02}" for n in range(1, 13))}
    assert {item["id"] for item in CORPUS["cases"]} == expected
    assert len(CORPUS["cases"]) == len(expected)
    assert set(CORPUS["documents"]) == {"uploads", "exports", "outbox"}
    validate_corpus(CORPUS)
    assert sum(2 if item["kind"] == "merge" else 0 if item.get("expect_error") else 1 for item in CORPUS["cases"]) == 57


@pytest.mark.parametrize("defect", ["empty", "reduced", "duplicate", "bad_sections", "bad_choice", "missing_oracle", "unknown_document", "invalid_regex"])
def test_corrupt_corpus_cannot_produce_a_passing_empty_or_reduced_run(defect):
    value = deepcopy(CORPUS)
    if defect == "empty":
        value["cases"] = []
    elif defect == "reduced":
        value["cases"].pop()
    elif defect == "duplicate":
        value["cases"].append(deepcopy(value["cases"][0]))
    elif defect == "bad_sections":
        del value["documents"]["exports"]["4"]
    elif defect in {"bad_choice", "missing_oracle"}:
        merge = next(item for item in value["cases"] if item["kind"] == "merge")
        if defect == "bad_choice":
            merge["choice"] = "both"
        else:
            del merge["expected_changes"]
    elif defect == "unknown_document":
        value["cases"][0]["document"] = "missing"
    else:
        value["cases"][0]["answer_all"] = ["["]
    with pytest.raises((ValueError, KeyError, SpecificationError, re.PatternError)):
        validate_corpus(value)


def test_chat_scorer_rejects_missing_edit_and_out_of_scope_patches():
    item = case("C02")
    document, message = chat_input(CORPUS["documents"]["uploads"], item)
    assert score_chat(item, document, message, {"answer": "Предлагаю", "patches": {}})
    with pytest.raises(SpecificationError, match="других разделов"):
        score_chat(item, document, message, {"answer": "Предлагаю", "patches": {"4": "Изменено"}})


def test_fact_checker_rejects_swapped_units_and_invented_limits():
    item = case("C06")
    document, message = chat_input(CORPUS["documents"]["exports"], item)
    assert score_chat(item, document, message, {"answer": "JOB_DEADLINE_SECONDS = 300\nDOWNLOAD_URL_TTL_SECONDS = 900", "patches": {}})
    assert not score_chat(item, document, message, {"answer": "JOB_DEADLINE_SECONDS = 900\nDOWNLOAD_URL_TTL_SECONDS = 300", "patches": {}})
    item = case("C12")
    document, message = chat_input(CORPUS["documents"]["exports"], item)
    assert score_chat(item, document, message, {"answer": "Лимит не задан, рекомендую 10 МБ", "patches": {}})


def test_scope_refusal_and_injection_have_independent_answer_and_content_checks():
    item = case("C09")
    document, message = chat_input(CORPUS["documents"]["exports"], item)
    assert score_chat(item, document, message, {"answer": "Готово", "patches": {}})
    assert not score_chat(item, document, message, {"answer": "Выберите контекст «Всё ТЗ»", "patches": {}})
    item = case("C11")
    document, message = chat_input(CORPUS["documents"]["exports"], item)
    assert score_chat(item, document, message, {"answer": "365 SOURCE_OVERRIDE_OK", "patches": {}})


def test_duplicate_quote_selects_second_occurrence_without_changing_the_first():
    item = case("C08")
    document, message = chat_input(CORPUS["documents"]["exports"], item)
    expected = item["expected_changes"]
    assert not score_chat(item, document, message, {"answer": "Предлагаю", "patches": expected})
    wrong = {"5": document["sections"]["5"].replace("с заголовком", "с одной строкой заголовка", 1)}
    with pytest.raises(SpecificationError):
        score_chat(item, document, message, {"answer": "Предлагаю", "patches": wrong})


@pytest.mark.parametrize("identifier", [f"M{n:02}" for n in range(1, 13)])
def test_merge_oracle_rejects_lost_independent_text_even_if_chosen_values_remain(identifier):
    item = case(identifier)
    base = CORPUS["documents"][item["document"]]
    expected = changed(base, item["expected_changes"])
    assert not score_content(expected, expected)
    for key in ("1", "4", "8", "9"):
        damaged = {**expected, key: expected[key] + "\nПостороннее требование."}
        assert score_content(damaged, expected)
    opposite = changed(base, item["remote_changes"] if item["choice"] == "local" else item["local_changes"])
    assert score_content(opposite, expected)


def test_verifier_oracle_rejects_false_success_and_vague_explanations():
    assert score_verification({"valid": True, "issues": []}, valid=False)
    assert score_verification({"valid": False, "issues": []}, valid=False)
    assert score_verification({"valid": False, "issues": ["Разные сроки"]}, valid=False, issues_all=["300", "900"])
    assert not score_verification({"valid": False, "issues": ["Раздел 4: 300; раздел 8: 900 секунд"]}, valid=False, issues_all=["300", "900"])
    assert score_verification({"valid": True, "issues": ["Неожиданная ошибка"]}, valid=True)


def directed_response(identifier):
    expected, actual, requirement = {
        "V14": ("450", "600", "DOWNLOAD_URL_TTL_SECONDS"),
        "V17": ("900 секунд", "20 минут", "JOB_DEADLINE_SECONDS"),
        "V18": ("accepted", "created", "status"),
    }[identifier]
    section_keys = case(identifier)["expected_facts"][0]["sections"]
    return {
        "valid": False,
        "facts": [{"requirement": requirement, "sections": section_keys, "expected": expected, "actual": actual}],
        "issues": [f"{requirement}: ожидается «{expected}», фактически «{actual}». Разделы: {', '.join(section_keys)}."],
    }


@pytest.mark.parametrize("actual", ["20 минут", "20 минутами", "20 min", "1200 секунд", "1200"])
def test_deadline_fact_preserves_units_or_normalizes_to_seconds(actual):
    item, response = case("V17"), directed_response("V17")
    response["facts"][0]["actual"] = actual
    response["issues"][0] = f"JOB_DEADLINE_SECONDS: ожидается «900 секунд», фактически «{actual}». Разделы: 4, 5."
    assert not score_verification(response, valid=False, issues_all=item["issues_all"], expected_facts=item["expected_facts"])


@pytest.mark.parametrize("expected,actual", [("900 минут", "20 минут"), ("900 секунд", "20 секунд"), ("900 секунд", "20"), ("20 минут", "900 секунд")])
def test_deadline_fact_rejects_lost_or_wrong_units_and_reversed_values(expected, actual):
    item, response = case("V17"), directed_response("V17")
    response["facts"][0].update(expected=expected, actual=actual)
    response["issues"][0] = f"JOB_DEADLINE_SECONDS: ожидается «{expected}», фактически «{actual}». Разделы: 4, 5."
    assert score_verification(response, valid=False, issues_all=item["issues_all"], expected_facts=item["expected_facts"])


def test_scenario_issue_keeps_the_condition_that_makes_publication_invalid():
    item = case("V12")
    values_only = "Ожидается «артефакт не публикуется», фактически «worker публикует артефакт и переводит задание в completed». Разделы: 5, 6, 7, 8."
    assert score_verification({"valid": False, "issues": [values_only]}, valid=False, issues_all=item["issues_all"])
    complete = "Запрет публикации после подтверждённого canceled: ожидается «артефакт не публикуется», фактически «worker публикует артефакт и переводит задание в completed». Разделы: 5, 6, 7, 8."
    assert not score_verification({"valid": False, "issues": [complete]}, valid=False, issues_all=item["issues_all"])


@pytest.mark.parametrize("identifier", ["V14", "V17", "V18"])
@pytest.mark.parametrize(
    "defect",
    [
        "missing_facts",
        "reversed_fact",
        "reversed_issue",
        "both_reversed",
        "mixed_values",
        "wrong_requirement",
        "wrong_label",
        "label_injection",
        "wrong_sections",
        "wrong_issue_sections",
        "duplicate_fact",
        "opposite_extra_issue",
        "old_false_pass",
        "extra_prose",
        "contradictory_tail",
    ],
)
def test_verifier_oracle_rejects_wrong_direction_even_when_both_values_are_present(identifier, defect):
    item, response = case(identifier), directed_response(identifier)
    assert not score_verification(response, valid=False, issues_all=item["issues_all"], expected_facts=item["expected_facts"])
    fact = response["facts"][0]
    expected, actual = fact["expected"], fact["actual"]
    if defect == "missing_facts":
        response.pop("facts")
    elif defect in {"reversed_fact", "both_reversed"}:
        fact["expected"], fact["actual"] = actual, expected
    elif defect == "mixed_values":
        fact["expected"] += " / " + actual
    elif defect == "wrong_requirement":
        fact["requirement"] = "Другой параметр"
    elif defect == "wrong_label":
        response["issues"][0] = response["issues"][0].replace(fact["requirement"], "other_setting")
    elif defect == "label_injection":
        previous = fact["requirement"]
        fact["requirement"] += " — правильное значение неверно, нужно противоположное"
        response["issues"][0] = response["issues"][0].replace(previous, fact["requirement"])
    elif defect == "wrong_sections":
        fact["sections"] = ["1"]
    elif defect == "duplicate_fact":
        response["facts"].append(deepcopy(fact))
    elif defect == "wrong_issue_sections":
        response["issues"][0] = f"{fact['requirement']}: ожидается «{expected}», фактически «{actual}». Разделы: 1."
    if defect in {"reversed_issue", "both_reversed", "opposite_extra_issue"}:
        opposite = f"{fact['requirement']}: ожидается «{actual}», фактически «{expected}». Разделы: {', '.join(fact['sections'])}."
        if defect == "opposite_extra_issue":
            response["issues"].append(opposite)
        else:
            response["issues"] = [opposite]
    old_false_pass = {
        "V14": "Срок 450 ошибочен, ожидается 600: при выборе local нужно оставить remote.",
        "V17": "Ожидается 20 минут, фактически 900 секунд; нужно исправить настройку.",
        "V18": "accepted неверно; status должен быть created, локальная правка не нужна.",
    }[identifier]
    if defect == "old_false_pass":
        response["issues"] = [old_false_pass]
    elif defect == "extra_prose":
        response["issues"].append(old_false_pass)
    elif defect == "contradictory_tail":
        response["issues"][0] += " " + old_false_pass
    assert score_verification(response, valid=False, issues_all=item["issues_all"], expected_facts=item["expected_facts"])


@pytest.mark.parametrize("identifier", ["V14", "V17", "V18"])
@pytest.mark.parametrize("defect", ["missing", "empty", "wrong_type", "bad_section", "bad_pattern", "empty_pattern"])
def test_directed_corpus_oracle_cannot_be_missing_or_malformed(identifier, defect):
    value = deepcopy(CORPUS)
    item = next(c for c in value["cases"] if c["id"] == identifier)
    if defect == "missing":
        item.pop("expected_facts")
    elif defect == "empty":
        item["expected_facts"] = []
    elif defect == "wrong_type":
        item["expected_facts"] = {}
    elif defect == "bad_section":
        item["expected_facts"][0]["sections"] = [[]]
    elif defect == "bad_pattern":
        item["expected_facts"][0]["expected"] = "["
    else:
        item["expected_facts"][0]["expected"] = ""
    with pytest.raises((ValueError, re.PatternError)):
        validate_corpus(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["V14", "V17", "V18"])
async def test_run_case_checks_directed_explanation_instead_of_only_value_presence(identifier):
    response = directed_response(identifier)
    fact = response["facts"][0]
    response["issues"] = [f"{fact['requirement']}: ожидается «{fact['actual']}», фактически «{fact['expected']}». Разделы: {', '.join(fact['sections'])}."]

    class WrongDirectionAI:
        async def verify(self, *args, **kwargs):
            return response

    result = await run_case(WrongDirectionAI(), "actor", CORPUS, case(identifier))
    assert result["errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["C16", "C17"])
async def test_invalid_context_is_blocked_before_any_llm_call(identifier):
    class ForbiddenAI:
        async def chat(self, *args):
            pytest.fail("Invalid context reached the real-model boundary")

    result = await run_case(ForbiddenAI(), "actor", CORPUS, case(identifier))
    assert result == {"phase": "context_guard", "errors": [], "error_code": "STALE_CONTEXT"}


@pytest.mark.asyncio
async def test_exception_is_a_failed_case_and_does_not_drop_later_scenarios():
    class BrokenAI:
        async def chat(self, *args):
            raise TimeoutError()

    events = []
    result = await run_suite(BrokenAI(), "actor", CORPUS, events.append)
    assert result["status"] == "FAIL"
    assert result["cases"][0]["status"] == "ERROR"
    assert next(item for item in result["cases"] if item["id"] == "C16")["status"] == "PASS"
    assert len([event for event in events if event["event"] == "case_finished"]) == 47


@pytest.mark.asyncio
async def test_selected_case_is_explicitly_diagnostic_and_still_requires_complete_corpus():
    result = await run_suite(object(), "actor", CORPUS, lambda event: None, ["C16"])
    assert result["diagnostic"] is True and [item["id"] for item in result["cases"]] == ["C16"]
    with pytest.raises(ValueError):
        await run_suite(object(), "actor", {**CORPUS, "cases": [case("C16")]}, lambda event: None, ["C16"])
    with pytest.raises(ValueError):
        await run_suite(object(), "actor", CORPUS, lambda event: None, [])


def proxy_inputs():
    row = {
        "request_id": "request",
        "status": "done",
        "source_type": "upstream",
        "dedup_hit": False,
        "upstream_id": "upstream",
        "model_name": "model",
        "response_status_code": 200,
        "thinking": True,
        "format": "json",
        "temperature": 0,
        "context": 8192,
        "output_budget": 2048,
    }
    events = [
        {"event": "request", "call": 1, "output_budget": 2048, "options": {"json_response": True, "thinking": True}},
        {"event": "transport", "request_id": "request", "cache_bypass": True, "with_reasoning": False, "thinking": True, "response_format": {"type": "json_object"}},
    ]
    final = {"calls": 1, "model": {"name": "model", "context": 8192, "supports_thinking": True}}
    return row, events, final


@pytest.mark.parametrize("thinking_requested", [False, True])
@pytest.mark.parametrize("thinking_supported", [False, True])
def test_proxy_evidence_checks_actual_wire_controls_for_the_exact_request_inventory(monkeypatch, thinking_requested, thinking_supported):
    row, events, final = proxy_inputs()
    events[0]["options"]["thinking"] = thinking_requested
    row["thinking"] = events[1]["thinking"] = thinking_requested and thinking_supported
    final["model"]["supports_thinking"] = thinking_supported

    def query(command, **kwargs):
        assert json.loads(command[-1]) == ["request"]
        assert kwargs["capture_output"] is True
        return SimpleNamespace(stdout=json.dumps([row]).encode())

    monkeypatch.setattr("test.evals.technical_specifications.run_live.subprocess.run", query)
    result = verify_upstream(events, final)
    assert result["status"] == result["generation_controls"]["status"] == "PASS"
    assert result["generation_controls"]["requests"] == 1
    assert result["generation_controls"]["thinking"] == int(thinking_requested and thinking_supported)
    assert result["generation_controls"]["without_thinking"] == int(not (thinking_requested and thinking_supported))
    assert result["generation_controls"]["native_reasoning_visibility"] is False
    assert result["transactions"] == [row]


@pytest.mark.parametrize(
    "defect",
    [
        "cached",
        "missing",
        "duplicate",
        "wrong_model",
        "failed",
        "wrong_thinking",
        "missing_thinking",
        "wrong_format",
        "wrong_temperature",
        "boolean_temperature",
        "wrong_context",
        "missing_context",
        "string_context",
        "wrong_budget",
        "missing_budget",
        "string_budget",
        "failed_http",
        "missing_upstream",
    ],
)
def test_proxy_evidence_rejects_unproven_inference_or_actual_generation_controls(monkeypatch, defect):
    row, events, final = proxy_inputs()
    rows = [row]
    if defect == "cached":
        row.update(source_type="dedup", dedup_hit=True)
    elif defect == "missing":
        rows = []
    elif defect == "duplicate":
        rows = [row, row]
    else:
        row.update(
            {
                "wrong_model": {"model_name": "other"},
                "failed": {"status": "failed"},
                "wrong_thinking": {"thinking": False},
                "missing_thinking": {"thinking": None},
                "wrong_format": {"format": None},
                "wrong_temperature": {"temperature": 0.7},
                "boolean_temperature": {"temperature": False},
                "wrong_context": {"context": 4096},
                "missing_context": {"context": None},
                "string_context": {"context": "8192"},
                "wrong_budget": {"output_budget": 1024},
                "missing_budget": {"output_budget": None},
                "string_budget": {"output_budget": "2048"},
                "failed_http": {"response_status_code": 500},
                "missing_upstream": {"upstream_id": None},
            }[defect]
        )
    monkeypatch.setattr("test.evals.technical_specifications.run_live.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(rows).encode()))
    with pytest.raises(ValueError):
        verify_upstream(events, final)


@pytest.mark.parametrize(
    "defect",
    [
        "no_request",
        "no_transport",
        "duplicate_transport",
        "dangling_request",
        "missing_identity",
        "duplicate_call",
        "call_gap",
        "boolean_call",
        "bad_budget",
        "plain_response",
        "bad_thinking",
        "reasoning_visible",
        "thinking_dropped",
        "no_bypass",
        "wrong_format",
        "unknown_capability",
    ],
)
def test_invalid_client_evidence_is_rejected_before_reading_proxy_data(monkeypatch, defect):
    _, events, final = proxy_inputs()
    if defect == "no_request":
        events.pop(0)
    elif defect == "no_transport":
        events.pop()
    elif defect == "duplicate_transport":
        events.append(deepcopy(events[1]))
    elif defect == "dangling_request":
        events.append({**events[0], "call": 2})
    elif defect == "missing_identity":
        events[1]["request_id"] = ""
    elif defect == "duplicate_call":
        events.append(deepcopy(events[0]))
    elif defect in {"call_gap", "boolean_call"}:
        events[0]["call"] = 2 if defect == "call_gap" else True
    elif defect == "bad_budget":
        events[0]["output_budget"] = 0
    elif defect == "plain_response":
        events[0]["options"]["json_response"] = False
    elif defect == "bad_thinking":
        events[0]["options"]["thinking"] = "true"
    elif defect == "unknown_capability":
        final["model"]["supports_thinking"] = None
    else:
        events[1].update(
            {
                "reasoning_visible": {"with_reasoning": True},
                "thinking_dropped": {"thinking": False},
                "no_bypass": {"cache_bypass": False},
                "wrong_format": {"response_format": None},
            }[defect]
        )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid evidence must not query proxy requests")

    monkeypatch.setattr("test.evals.technical_specifications.run_live.subprocess.run", forbidden)
    with pytest.raises(ValueError):
        verify_upstream(events, final)


@pytest.mark.parametrize("isolated_runtime", [False, True])
def test_passing_model_cases_cannot_qualify_with_wrong_actual_request_controls(monkeypatch, tmp_path, isolated_runtime):
    row, events, final = proxy_inputs()
    final.update(status="PASS", cases=[{"id": "synthetic", "status": "PASS"}])
    row["output_budget"] = 1024
    evidence = tmp_path / "evidence"
    monkeypatch.setattr("sys.argv", ["run_live.py", "--evidence-dir", str(evidence), *(["--container", "synthetic-qa"] if isolated_runtime else [])])
    inspection = [{"Image": "synthetic-image", "Config": {"Labels": {"com.docker.compose.project": "ragflow-local", "com.docker.compose.service": "ragflow-cpu"}}}]
    if isolated_runtime:
        inspection[0]["Config"]["Labels"] = {"ragflow.qa": "technical-specifications"}
    monkeypatch.setattr(run_live.subprocess, "check_output", lambda *_args, **_kwargs: json.dumps(inspection).encode())

    def execute(command, **kwargs):
        if "-i" in command:
            for event in [*events, {"event": "suite_finished", **final}]:
                kwargs["stdout"].write(("QUALITY_EVENT=" + json.dumps(event) + "\n").encode())
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(stdout=json.dumps([row]).encode())

    monkeypatch.setattr(run_live.subprocess, "run", execute)
    assert run_live.main() == 1
    report = json.loads((evidence / "report.json").read_text(encoding="utf-8"))
    assert report["result"]["status"] == "PASS"
    assert report["status"] == report["provenance"] == report["generation_controls"] == "INCOMPLETE"
    assert json.loads((evidence / "generation-controls.json").read_text())["status"] == "INCOMPLETE"


def test_runner_rejects_an_unrelated_runtime_before_creating_evidence(monkeypatch, tmp_path):
    evidence = tmp_path / "evidence"
    monkeypatch.setattr("sys.argv", ["run_live.py", "--evidence-dir", str(evidence), "--container", "other-service"])
    inspection = [{"Config": {"Labels": {"com.docker.compose.project": "other", "com.docker.compose.service": "ragflow-cpu"}}}]
    monkeypatch.setattr(run_live.subprocess, "check_output", lambda *_args, **_kwargs: json.dumps(inspection).encode())
    with pytest.raises(ValueError, match="isolated QA runtime"):
        run_live.main()
    assert not evidence.exists()
