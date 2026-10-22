"""Operational unknowns must not turn into model-quality failures or successes."""

from types import SimpleNamespace

import pytest

from test.evals.technical_specifications.alignment import evaluate


@pytest.mark.parametrize(
    "identifier,coverage,consistency,expected_error",
    [
        ("BTZ-02", "PARTIAL", "CONTRADICTED", "row:1:coverage:PARTIAL expected:MISSING"),
        ("BTZ-03", "PARTIAL", "CONTRADICTED", "row:1:coverage:PARTIAL expected:MISSING"),
        ("BTZ-07", "MISSING", "CONTRADICTED", "row:1:coverage:MISSING expected:PARTIAL"),
        ("BTZ-08", "PARTIAL", "CONTRADICTED", "row:1:coverage:PARTIAL expected:MISSING"),
        ("BTZ-09", "PARTIAL", "CONTRADICTED", "row:1:coverage:PARTIAL expected:MISSING"),
        ("BTZ-11", "MISSING", "CONTRADICTED", "row:1:coverage:MISSING expected:PARTIAL"),
        ("BTZ-15", "MISSING", "CONSISTENT", "row:1:coverage:MISSING expected:PARTIAL"),
        ("BTZ-16", "PARTIAL", "CONTRADICTED", "row:1:consistency:CONTRADICTED expected:CONSISTENT"),
        ("BTZ-17", "PARTIAL", "CONTRADICTED", "row:1:coverage:PARTIAL expected:MISSING"),
        ("BTZ-28", "IMPLEMENTED", "CONSISTENT", "row:1:consistency:CONSISTENT expected:CONTRADICTED"),
        ("BTZ-31", "IMPLEMENTED", "CONTRADICTED", "row:1:coverage:IMPLEMENTED expected:PARTIAL"),
        ("BTZ-32", "PARTIAL", "CONTRADICTED", "row:1:coverage:PARTIAL expected:MISSING"),
        ("BTZ-33", "IMPLEMENTED", "CONTRADICTED", "row:1:coverage:IMPLEMENTED expected:PARTIAL"),
        ("BTZ-38", "MISSING", "CONTRADICTED", "row:1:coverage:MISSING expected:PARTIAL"),
        ("BTZ-40", "MISSING", "CONTRADICTED", "row:1:coverage:MISSING expected:PARTIAL"),
        ("BTZ-47", "PARTIAL", "CONSISTENT", "row:1:coverage:PARTIAL expected:IMPLEMENTED"),
    ],
)
def test_semantic_regressions_remain_failures(identifier, coverage, consistency, expected_error):
    """Real errors and boundary counterexamples; scorer, not model evidence."""
    from api.technical_specifications.alignment import report_input, validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    case = next(case for case in CASES if case[0] == identifier)
    document, basis = inputs(case)
    options = case[5] if len(case) > 5 else {}
    labels = options.get("rows", [(case[3], case[4])])
    report = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": [
            {
                "id": item["id"],
                "coverage": coverage if index == 0 else expected[0],
                "consistency": consistency if index == 0 else expected[1],
                "references": [{"section": "5", "quote": document["sections"]["5"]}],
                "reason": "Observed wrong semantic label",
            }
            for index, (item, expected) in enumerate(zip(basis["inventory"], labels, strict=True))
        ],
        "additional_rules": [],
        "joint": {"checked_ids": [item["id"] for item in basis["inventory"]], "consistency": options.get("joint", "CONSISTENT"), "reason": "Isolate the row labels"},
    }
    observed = evaluate.case_observation(case, validate_report(report, document, basis))
    assert observed["status"] == "FAIL" and expected_error in observed["errors"]
    if identifier == "BTZ-16":
        assert "row:1:coverage:PARTIAL expected:IMPLEMENTED" in observed["errors"] and "FALSE_BLOCK" in observed["errors"]


@pytest.mark.parametrize(
    "identifier,unsupported,errors,conformant",
    [
        ("BTZ-23", False, ["UNSUPPORTED_BUSINESS_RULE_MISSED"], False),
        ("BTZ-23", True, [], False),
        ("BTZ-40", False, [], False),
        ("BTZ-40", True, ["UNEXPECTED_UNSUPPORTED_BUSINESS_RULE"], False),
        ("BTZ-34", True, [], False),
        ("BTZ-22", False, [], True),
    ],
)
def test_explicit_additional_rule_oracle_preserves_unlabelled_axis(identifier, unsupported, errors, conformant):
    from test.evals.technical_specifications.alignment.corpus import CASES, is_conformant, score

    case = next(case for case in CASES if case[0] == identifier)
    report = {
        "requirements": [{"coverage": case[3], "consistency": case[4]}],
        "joint": {"consistency": "CONSISTENT"},
        "additional_rules": [{"kind": "UNSUPPORTED_BUSINESS_RULE"}] if unsupported else [],
    }
    result = {"report_status": "COMPLETE", "quality_status": "PASS" if conformant else "FAIL", "report": report}
    assert score(case, result) == errors
    assert is_conformant(case) is conformant


@pytest.mark.parametrize("identifier", ["BTZ-56", "BTZ-57"])
@pytest.mark.parametrize("damaged", [False, True])
def test_exception_and_basis_only_conflict_counterexamples(identifier, damaged):
    from api.technical_specifications.alignment import report_input, validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    case = next(case for case in CASES if case[0] == identifier)
    document, basis = inputs(case)
    options = case[5]
    rows = []
    for index, (item, labels) in enumerate(zip(basis["inventory"], options["rows"], strict=True)):
        refs = [{"section": "5", "quote": options["row_quotes"][index]}] if options.get("row_quotes") else []
        rows.append({"id": item["id"], "coverage": labels[0], "consistency": labels[1], "references": refs, "reason": "Synthetic contract answer"})
    report = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": rows,
        "additional_rules": [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": rows[0]["references"], "reason": "Duplicate exception"}] if damaged and identifier == "BTZ-56" else [],
        "joint": {
            "checked_ids": [item["id"] for item in basis["inventory"]],
            "consistency": "CONSISTENT" if damaged and identifier == "BTZ-57" else "CONTRADICTED",
            "reason": "Joint constraint decision",
        },
    }
    observed = evaluate.case_observation(case, validate_report(report, document, basis))
    assert observed["status"] == ("FAIL" if damaged else "PASS")
    error = "UNEXPECTED_UNSUPPORTED_BUSINESS_RULE" if identifier == "BTZ-56" else "JOINT_CONSTRAINT_CONTRADICTION_MISSED"
    assert observed["errors"] == ([error] if damaged else [])


@pytest.mark.parametrize("identifier", ["BTZ-58", "BTZ-59"])
@pytest.mark.parametrize("mode", ["correct", "observed_error", "wrong_joint", "wrong_evidence"])
def test_partial_scope_and_joint_regressions(identifier, mode):
    from api.technical_specifications.alignment import report_input, validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    case = next(case for case in CASES if case[0] == identifier)
    document, basis = inputs(case)
    options = case[5]
    rows = []
    for item, labels, quote in zip(basis["inventory"], options["rows"], options["row_quotes"], strict=True):
        section = next((key for key, text in document["sections"].items() if quote and quote in text), None)
        refs = [{"section": section, "quote": quote}] if quote else []
        rows.append({"id": item["id"], "coverage": labels[0], "consistency": labels[1], "references": refs, "reason": "Synthetic contract answer"})
    joint = options["joint"]
    if mode in {"observed_error", "wrong_joint"}:
        joint = "CONTRADICTED" if joint == "CONSISTENT" else "CONSISTENT"
    if mode == "observed_error" and identifier == "BTZ-58":
        rows[-1]["consistency"] = "CONTRADICTED"
    if mode == "wrong_evidence":
        section = "6" if identifier == "BTZ-58" else "3"
        rows[-1]["references"] = [{"section": section, "quote": document["sections"][section]}]
    report = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": rows,
        "additional_rules": [],
        "joint": {"checked_ids": [item["id"] for item in basis["inventory"]], "consistency": joint, "reason": "Whole BT and TZ constraint decision"},
    }
    observed = evaluate.case_observation(case, validate_report(report, document, basis))
    assert observed["status"] == ("PASS" if mode == "correct" else "FAIL")
    expected_errors = []
    if mode == "observed_error" and identifier == "BTZ-58":
        expected_errors.append("row:3:consistency:CONTRADICTED expected:CONSISTENT")
    if mode in {"observed_error", "wrong_joint"}:
        expected_errors.append("JOINT_CONSTRAINT_CONTRADICTION_MISSED")
    if mode == "wrong_evidence":
        expected_errors.append(f"row:{len(rows)}:EVIDENCE_CLAUSE_MISSING")
    assert observed["errors"] == expected_errors
    if identifier == "BTZ-58":
        assert not rows[0]["references"] and not rows[1]["references"]


@pytest.mark.parametrize("identifier", ["BTZ-30", "BTZ-60", "BTZ-61"])
@pytest.mark.parametrize("mode", ["correct", "observed_error", "wrong_joint", "wrong_evidence"])
def test_technical_detail_external_value_and_new_action_regressions(identifier, mode):
    from api.technical_specifications.alignment import report_input, validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    case = next(case for case in CASES if case[0] == identifier)
    document, basis = inputs(case)
    oracle = case[5]

    def reference(quote):
        return [{"section": next(section for section, text in document["sections"].items() if quote in text), "quote": quote}] if quote else []

    rows = [
        {"id": item["id"], "coverage": labels[0], "consistency": labels[1], "references": reference(quote), "reason": "Controlled regression answer"}
        for item, labels, quote in zip(basis["inventory"], oracle["rows"], oracle["row_quotes"], strict=True)
    ]
    rules = [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": reference(oracle["unsupported_quote"]), "reason": "Independent mandatory action"}] if oracle.get("unsupported_quote") else []
    joint = oracle["joint"]
    if mode == "observed_error":
        if identifier == "BTZ-30":
            rules = [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": reference("Таймаут формирования отчёта составляет 30 секунд."), "reason": "Observed false business rule"}]
        elif identifier == "BTZ-60":
            rows[0]["consistency"] = joint = "CONTRADICTED"
        else:
            rules = []
    elif mode == "wrong_joint":
        joint = "CONTRADICTED" if joint == "CONSISTENT" else "CONSISTENT"
    elif mode == "wrong_evidence":
        unrelated = {"BTZ-30": "Таймаут формирования отчёта составляет 30 секунд.", "BTZ-60": document["sections"]["5"], "BTZ-61": document["sections"]["3"]}[identifier]
        rows[0]["references"] = reference(unrelated)
    value = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": rows,
        "additional_rules": rules,
        "joint": {"checked_ids": [item["id"] for item in basis["inventory"]], "consistency": joint, "reason": "Controlled joint decision"},
    }
    observed = evaluate.case_observation(case, validate_report(value, document, basis))
    assert observed["status"] == ("PASS" if mode == "correct" else "FAIL")
    if mode == "observed_error":
        expected = {"BTZ-30": "UNEXPECTED_UNSUPPORTED_BUSINESS_RULE", "BTZ-60": "row:1:consistency:CONTRADICTED expected:UNDETERMINED", "BTZ-61": "UNSUPPORTED_BUSINESS_RULE_MISSED"}
        assert expected[identifier] in observed["errors"]
    elif mode == "wrong_joint":
        assert "JOINT_CONSTRAINT_CONTRADICTION_MISSED" in observed["errors"]
    elif mode == "wrong_evidence":
        assert "row:1:EVIDENCE_CLAUSE_MISSING" in observed["errors"]


def test_new_action_golden_requires_its_own_evidence():
    from test.evals.technical_specifications.alignment.corpus import CASES, score

    case = next(case for case in CASES if case[0] == "BTZ-61")
    oracle = case[5]
    report = {
        "requirements": [{"coverage": "PARTIAL", "consistency": "CONTRADICTED", "references": [{"section": "8", "quote": oracle["row_quotes"][0]}]}],
        "joint": {"consistency": "CONTRADICTED"},
        "additional_rules": [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": [{"section": "3", "quote": oracle["sections"]["3"]}]}],
    }
    assert score(case, {"report_status": "COMPLETE", "quality_status": "FAIL", "report": report}) == ["ADDITIONAL_RULE_EVIDENCE_CLAUSE_MISSING"]


@pytest.mark.parametrize("mode", ["correct", "observed_error", "false_implementation", "missing_coverage", "wrong_joint", "wrong_evidence"])
def test_bounded_mean_with_undefined_percentile_remains_partial_and_compatible(mode):
    from api.technical_specifications.alignment import report_input, validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    case = next(case for case in CASES if case[0] == "BTZ-62")
    document, basis = inputs(case)
    coverage, consistency, joint = "PARTIAL", "CONSISTENT", "CONSISTENT"
    quote = case[5]["row_quotes"][0]
    if mode == "observed_error":
        coverage, consistency, joint = "MISSING", "CONTRADICTED", "CONTRADICTED"
    elif mode == "false_implementation":
        coverage = "IMPLEMENTED"
    elif mode == "missing_coverage":
        coverage = "MISSING"
    elif mode == "wrong_joint":
        joint = "CONTRADICTED"
    elif mode == "wrong_evidence":
        quote = "Отдельная гарантия p95 в проекте не определена."
    report = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": [
            {
                "id": basis["inventory"][0]["id"],
                "coverage": coverage,
                "consistency": consistency,
                "references": [{"section": "8", "quote": quote}],
                "reason": "Bounded mean does not establish the percentile",
            }
        ],
        "additional_rules": [],
        "joint": {"checked_ids": [basis["inventory"][0]["id"]], "consistency": joint, "reason": "Mean and percentile bounds can coexist"},
    }
    result = validate_report(report, document, basis)
    observed = evaluate.case_observation(case, result)
    assert observed["status"] == ("PASS" if mode == "correct" else "FAIL")
    if mode == "correct":
        assert result["quality_status"] == "FAIL" and result["qualifies"] is False
    else:
        expected = {
            "observed_error": "row:1:consistency:CONTRADICTED expected:CONSISTENT",
            "false_implementation": "row:1:coverage:IMPLEMENTED expected:PARTIAL",
            "missing_coverage": "row:1:coverage:MISSING expected:PARTIAL",
            "wrong_joint": "JOINT_CONSTRAINT_CONTRADICTION_MISSED",
            "wrong_evidence": "row:1:EVIDENCE_CLAUSE_MISSING",
        }
        assert expected[mode] in observed["errors"]


@pytest.mark.parametrize(
    "identifier,mode",
    [(identifier, mode) for identifier in ("BTZ-63", "BTZ-64") for mode in ("correct", "observed_error", "wrong_coverage", "wrong_joint", "wrong_evidence", "incomplete_report")]
    + [("BTZ-63", "wrong_rule_evidence")],
)
def test_personal_visit_and_changed_tariff_regressions(identifier, mode):
    from api.technical_specifications.alignment import report_input, validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    case = next(case for case in CASES if case[0] == identifier)
    document, basis = inputs(case)
    options = case[5]

    def reference(quote):
        return [{"section": key, "quote": quote} for key, text in document["sections"].items() if quote in text][:1]

    rows = [
        {"id": item["id"], "coverage": labels[0], "consistency": labels[1], "references": reference(quote), "reason": "Controlled row decision"}
        for item, labels, quote in zip(basis["inventory"], options["rows"], options["row_quotes"], strict=True)
    ]
    rules = [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": reference(options["unsupported_quote"]), "reason": "New mandatory participant action"}] if identifier == "BTZ-63" else []
    joint = options["joint"]
    if mode == "observed_error":
        rules = [] if identifier == "BTZ-63" else [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": rows[0]["references"], "reason": "Duplicate changed tariff"}]
    elif mode == "wrong_coverage":
        rows[0]["coverage"] = "IMPLEMENTED" if identifier == "BTZ-63" else "PARTIAL"
    elif mode == "wrong_joint":
        joint = "CONSISTENT"
    elif mode == "wrong_evidence":
        quote = document["sections"]["2"] if identifier == "BTZ-63" else "включая случаи утраты исходного пропуска во время эвакуации."
        rows[0]["references"] = reference(quote)
    elif mode == "wrong_rule_evidence":
        rules[0]["references"] = reference(document["sections"]["8"])
    value = {
        "candidate_sha256": report_input(document, basis)["candidate_sha256"],
        "basis_sha256": basis["sha256"],
        "requirements": rows,
        "additional_rules": rules,
        "joint": {"checked_ids": [item["id"] for item in basis["inventory"]], "consistency": joint, "reason": "Controlled joint decision"},
    }
    result = validate_report(value, document, basis)
    assert result["quality_status"] == "FAIL" and result["qualifies"] is False
    if mode == "incomplete_report":
        result.update(report_status="INCOMPLETE", quality_status="UNKNOWN")
    observed = evaluate.case_observation(case, result)
    expected_status = "INCOMPLETE" if mode == "incomplete_report" else "PASS" if mode == "correct" else "FAIL"
    assert observed["status"] == expected_status
    if mode != "correct":
        expected_error = {
            "observed_error": "UNSUPPORTED_BUSINESS_RULE_MISSED" if identifier == "BTZ-63" else "UNEXPECTED_UNSUPPORTED_BUSINESS_RULE",
            "wrong_coverage": "row:1:coverage:IMPLEMENTED expected:PARTIAL" if identifier == "BTZ-63" else "row:1:coverage:PARTIAL expected:MISSING",
            "wrong_joint": "JOINT_CONSTRAINT_CONTRADICTION_MISSED",
            "wrong_evidence": "row:1:EVIDENCE_CLAUSE_MISSING",
            "wrong_rule_evidence": "ADDITIONAL_RULE_EVIDENCE_CLAUSE_MISSING",
            "incomplete_report": "REPORT_INCOMPLETE",
        }
        assert expected_error[mode] in observed["errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,status,quality",
    [
        ({"execution_status": "INCOMPLETE", "report_status": "INCOMPLETE", "quality_status": "UNKNOWN", "reason_code": "MODEL_TRANSPORT_ERROR"}, "INCOMPLETE", "UNKNOWN"),
        ({"execution_status": "COMPLETE", "report_status": "INCOMPLETE", "quality_status": "FAIL", "reason_code": "ALIGNMENT_REPORT_INVALID"}, "FAIL", "FAIL"),
        ({"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "evidence_status": "INCOMPLETE", "oracle_errors": []}, "INCOMPLETE", "PASS"),
        ({"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "FAIL", "evidence_status": "VERIFIED", "oracle_errors": []}, "PASS", "PASS"),
        ({"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "evidence_status": "VERIFIED", "oracle_errors": ["MISSED_CONTRADICTION"]}, "FAIL", "FAIL"),
    ],
)
async def test_execution_semantics_and_qualification_are_separate(monkeypatch, result, status, quality):
    result["audit"] = {"model_digest": "synthetic", "profile_sha256": "synthetic"}
    monkeypatch.setattr(evaluate, "CASES", [("test",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda _case, value: value.get("oracle_errors", [value.get("reason_code")]))

    async def check(*args, **kwargs):
        return result

    report = await evaluate.run_cases(SimpleNamespace(check=check), "synthetic")
    assert report["status"] == status and report["quality_status"] == quality
    assert report["qualifies"] is (status == "PASS")


@pytest.mark.asyncio
async def test_model_or_profile_switch_inside_full_run_invalidates_qualification(monkeypatch):
    monkeypatch.setattr(evaluate, "CASES", [("first",), ("second",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda *_: [])
    index = 0

    async def check(*args, **kwargs):
        nonlocal index
        index += 1
        return {
            "execution_status": "COMPLETE",
            "report_status": "COMPLETE",
            "quality_status": "PASS",
            "evidence_status": "VERIFIED",
            "audit": {"model_digest": f"weights-{index}", "profile_sha256": "same"},
        }

    report = await evaluate.run_cases(SimpleNamespace(check=check), "synthetic")
    assert report["quality_status"] == "PASS"
    assert report["evidence_status"] == report["status"] == "INCOMPLETE"
    assert not report["qualifies"]


@pytest.mark.asyncio
async def test_expected_identity_mismatch_stops_paid_probes_and_keeps_response(monkeypatch):
    from test.evals.technical_specifications.alignment import real_limits

    monkeypatch.setattr(evaluate, "CASES", [("first",), ("second",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda *_: [])
    calls = []

    async def check(*args, **kwargs):
        calls.append("natural")
        return {"execution_status": "COMPLETE", "report_status": "COMPLETE", "quality_status": "PASS", "evidence_status": "VERIFIED", "audit": {"model_digest": "changed", "profile_sha256": "profile"}}

    async def forbidden(*args, **kwargs):
        raise AssertionError("No paid probes after identity change")

    monkeypatch.setattr(real_limits, "run", forbidden)
    report = await evaluate.run_evaluation(SimpleNamespace(check=check), "synthetic", expected_identity=("expected", "profile"))
    assert calls == ["natural"] and len(report["cases"]) == 1
    assert report["cases"][0]["result"]["audit"]["model_digest"] == "changed"
    assert report["real_limit_probes"]["reason_code"] == "MODEL_IDENTITY_CHANGED"
    assert not report["qualifies"]


def test_unknown_usage_is_not_reported_as_zero():
    report = {"status": "INCOMPLETE", "expected_case_ids": ["one"], "cases": [{"case_id": "one", "status": "INCOMPLETE", "errors": ["TIMEOUT"], "result": {"audit": {}}}]}
    summary = evaluate.public_summary(report)
    assert summary["duration_ms"] is None and summary["total_tokens"] is None
    assert summary["p0_case_pass_rate"] is None
    assert summary["alignment_metrics"]["model_cases_unknown"] == 1


@pytest.mark.parametrize("changed_model", [False, True])
def test_interrupted_night_keeps_observed_identity_without_promoting_experiment_scores(changed_model):
    audit = {"model": "observed", "model_digest": "a" * 64, "generation_parameters": {"num_ctx": 262144, "num_predict": 8192}}
    second = {**audit, "model_digest": "b" * 64} if changed_model else audit
    report = {
        "status": "FAIL",
        "expected_case_ids": ["unexecuted-full"],
        "cases": [],
        "nightly_experiments": {
            "REPEATS": {"cases": [{"result": {"audit": audit}}, {"result": {"audit": {"backend_completion_known": False}}}]},
            "ADMISSION": {"cases": [{"result": {"audit": second}}]},
        },
    }
    summary = evaluate.public_summary(report)
    assert summary["model"] == (None if changed_model else "observed")
    assert summary["model_digest"] == (None if changed_model else "a" * 64)
    assert summary["parameter_profiles"] == [audit["generation_parameters"]]
    assert summary["executed_cases"] == 0 and summary["alignment_metrics"]["model_cases_unexecuted"] == 1
    assert summary["total_tokens"] is None and summary["all_case_pass_rate"] is None
    assert summary["evidence_status"] == "INCOMPLETE"


@pytest.mark.parametrize("reserved,total", [(0, 120), (20, None)])
def test_public_totals_cover_real_probes_and_retain_unknown_usage(reserved, total):
    report = {
        "status": "INCOMPLETE",
        "expected_case_ids": ["one"],
        "cases": [{"case_id": "one", "status": "INCOMPLETE", "errors": [], "result": {"audit": {"prompt_tokens": 10, "completion_tokens": 10, "duration_ms": 10}}}],
        "budget": {"spent_tokens": 120, "reserved_tokens": reserved, "elapsed_ms": 2000},
    }
    summary = evaluate.public_summary(report)
    assert summary["total_tokens"] == total and summary["duration_ms"] == 2000


def test_long_semantic_cases_cover_distinct_positions_and_require_relevant_quotes():
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score

    selected = [case for case in CASES if case[0] in {"BTZ-53", "BTZ-54", "BTZ-55"}]
    assert len(selected) == 3
    for index, case in enumerate(selected):
        document, basis = inputs(case)
        assert len(document["sections"]["5"]) > 40_000 and len(basis["inventory"]) == 3
        oracle = case[5]
        rows = [{"coverage": labels[0], "consistency": labels[1], "references": [{"quote": oracle["row_quotes"][position]}]} for position, labels in enumerate(oracle["rows"])]
        result = {"report_status": "COMPLETE", "quality_status": "FAIL", "report": {"requirements": rows, "joint": {"consistency": "CONTRADICTED"}, "additional_rules": []}}
        assert score(case, result) == []
        position = document["sections"]["5"].index(oracle["row_quotes"][index]) / len(document["sections"]["5"])
        assert abs(position - index / 2) < 0.01
        rows[index]["references"] = [{"quote": "адаптер передаёт идентификатор трассы"}]
        assert f"row:{index + 1}:EVIDENCE_CLAUSE_MISSING" in score(case, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["MODEL_FINISH_UNKNOWN", "MODEL_INPUT_TRUNCATED", "MODEL_IDENTITY_MISMATCH", "ALIGNMENT_REPORT_INVALID"])
async def test_unknown_backend_stops_semantics_and_real_probes_independent_of_error_label(monkeypatch, reason):
    from test.evals.technical_specifications.alignment import real_limits

    monkeypatch.setattr(evaluate, "CASES", [("first",), ("second",)])
    monkeypatch.setattr(evaluate, "inputs", lambda _: ({}, {}))
    monkeypatch.setattr(evaluate, "score", lambda *_: [reason])
    calls = []

    async def check(*args, budget, **kwargs):
        calls.append(1)
        budget.claim(30)
        return {
            "execution_status": "INCOMPLETE",
            "report_status": "INCOMPLETE",
            "quality_status": "UNKNOWN",
            "reason_code": reason,
            "audit": {"finish_reason": None, "backend_completion_known": False},
        }

    async def no_probes(*args, **kwargs):
        raise AssertionError("Unknown backend must prevent new paid probes")

    monkeypatch.setattr(real_limits, "run", no_probes)
    report = await evaluate.run_evaluation(SimpleNamespace(check=check), "synthetic")
    assert calls == [1] and report["status"] == "INCOMPLETE"
    assert len(report["cases"]) == 1 and report["budget"]["reserved_tokens"] == 30
    assert report["real_limit_probes"]["reason_code"] == "BACKEND_USAGE_UNKNOWN_AFTER_INTERRUPTED_CASE"
