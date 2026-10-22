"""Release evidence must reject mismatched or incomplete candidate proof."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from tools.quality.release_evidence import alignment_evidence_identity, make_receipt, validate_receipt, verify_alignment_evidence, verify_release

REVISION = "a" * 40
IMAGE = f"registry.example:5443/ragflow:{REVISION}"
DIGEST = f"registry.example:5443/ragflow@sha256:{'b' * 64}"
JOBS = {name: "success" for name in ("ragflow_preflight", "ragflow_tests_infinity", "ragflow_tests_elasticsearch")}


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _alignment_report():
    from api.technical_specifications.alignment import fingerprint, materialize_report, report_input, validate_report
    from api.technical_specifications.alignment_ai import SYSTEM
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score

    report = {
        "schema_version": 1,
        "document_type": "technical_specification",
        "suite_id": "bt-tz-alignment-v1",
        "scope": "FULL",
        "source_revision": REVISION,
        "source_dirty": False,
        "image_digest": DIGEST,
        **alignment_evidence_identity(),
        "expected_case_ids": [case[0] for case in CASES],
        "cases": [],
        "status": "PASS",
        "quality_status": "PASS",
        "evidence_status": "VERIFIED",
        "qualifies": True,
    }
    for case in CASES:
        document, basis = inputs(case)
        oracle = case[5] if len(case) > 5 else {}
        expected_rows = oracle.get("rows", [(case[3], case[4])])
        evidence_sections = {"5", *oracle.get("sections", {})}
        refs = [{"section": section, "quote": document["sections"][section][:80]} for section in sorted(evidence_sections) if document["sections"][section]]
        value = {
            "candidate_sha256": report_input(document, basis)["candidate_sha256"],
            "basis_sha256": basis["sha256"],
            "requirements": [
                {"id": row["id"], "coverage": expected[0], "consistency": expected[1], "references": refs, "reason": "Expected fixture decision"}
                for row, expected in zip(basis["inventory"], expected_rows, strict=True)
            ],
            "additional_rules": [{"kind": "UNSUPPORTED_BUSINESS_RULE", "references": refs, "reason": "Fixture business action"}]
            if oracle.get("unsupported_business_rule", case[0] == "BTZ-23")
            else [],
            "joint": {"checked_ids": [row["id"] for row in basis["inventory"]], "consistency": oracle.get("joint", "CONSISTENT"), "reason": "Expected fixture decision"},
        }
        result = validate_report(value, document, basis)
        if oracle.get("row_quotes"):
            for row, quote in zip(value["requirements"], oracle["row_quotes"], strict=True):
                row["references"] = [{"section": next(section for section, text in document["sections"].items() if quote in text), "quote": quote}] if quote else []
            result = validate_report(value, document, basis)
        if quote := oracle.get("unsupported_quote"):
            value["additional_rules"][0]["references"] = [{"section": next(section for section, text in document["sections"].items() if quote in text), "quote": quote}]
        options = {"temperature": 0, "num_ctx": 32768, "num_predict": 8192}
        payload = report_input(document, basis)
        model_report = deepcopy(value)
        for group in ("requirements", "additional_rules"):
            for index, row in enumerate(model_report[group]):
                if group == "requirements" and oracle.get("row_quotes") and oracle["row_quotes"][index]:
                    row["references"] = [{"citation_id": next(item["id"] for item in payload["citations"] if oracle["row_quotes"][index] in item["quote"])}]
                else:
                    row["references"] = [{"citation_id": next(item["id"] for item in payload["citations"] if item["section"] == ref["section"])} for ref in row["references"]]
        result = validate_report(materialize_report(model_report, payload), document, basis)
        result["model_report"] = model_report
        wire = {
            "model": "fixture-model",
            "stream": False,
            "format": payload["response_schema"],
            "truncate": False,
            "shift": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "options": options,
        }
        result.update(
            evidence_status="VERIFIED",
            audit={
                "evidence_kind": "NATURAL_MODEL",
                "provider": "Ollama",
                "model": "fixture-model",
                "response_model": "fixture-model",
                "model_digest": "f" * 64,
                "profile_sha256": "e" * 64,
                "request_id": case[0],
                "finish_reason": "stop",
                "evidence_complete": True,
                "input_integrity": "VERIFIED",
                "input_tokenization": "SERVER_EXACT",
                "prompt_tokens": 1000,
                "completion_tokens": 1000,
                "generation_parameters": options,
                "input_sha256": fingerprint(payload),
                "wire_sha256": fingerprint(wire),
            },
        )
        errors = score(case, result)
        assert errors == [], (case[0], errors)
        report["cases"].append({"case_id": case[0], "status": "PASS", "errors": [], "result": result})
    from test.unit_test.tools.quality.alignment_evidence_fixture import add_probe_contract

    return add_probe_contract(report)


def _verify(receipt_path, *args):
    return verify_release(receipt_path, *args, technical_specifications_path=receipt_path.parent / "technical-specifications.json")


@pytest.mark.parametrize(
    "defect",
    [
        "truncation",
        "input-rejected",
        "cancel-stop",
        "cancel-usage",
        "tokenizer",
        "outstanding",
        "spent",
        "deadline",
        "input-tokenization",
        "response-model",
        "profile",
        "digest-after",
        "completion-known",
        "malformed-request-id",
    ],
)
def test_backend_limit_proof_cannot_be_replaced_by_pass_labels(defect):
    from tools.quality.release_evidence import AlignmentEvidenceIncomplete

    report = _alignment_report()
    probes = report["real_limit_probes"]["cases"]
    if defect == "truncation":
        probes[2]["qualification_rejected"] = False
    elif defect == "input-rejected":
        probes[2].update(input_integrity="REJECTED", guard_rejection_observed=True, status="PASS")
        probes[2]["audit"].update(input_integrity="REJECTED", finish_reason="input_rejected", prompt_tokens=None, completion_tokens=None, backend_completion_known=False)
    elif defect == "cancel-stop":
        probes[4]["backend_stop_verified"] = False
    elif defect == "cancel-usage":
        del probes[4]["backend_usage"]
    elif defect == "tokenizer":
        probes[7]["tokenizer_verified"] = False
    elif defect == "outstanding":
        report["budget"]["reserved_tokens"] = 1
    elif defect == "spent":
        report["budget"]["spent_tokens"] -= 1
    elif defect == "deadline":
        report["budget"]["elapsed_ms"] = report["budget"]["time_limit_ms"] + 1
    else:
        audit = report["cases"][0]["result"]["audit"]
        audit[
            {
                "input-tokenization": "input_tokenization",
                "response-model": "response_model",
                "profile": "profile_sha256",
                "digest-after": "model_digest_after",
                "completion-known": "backend_completion_known",
                "malformed-request-id": "request_id",
            }[defect]
        ] = "unverified"
    with pytest.raises(AlignmentEvidenceIncomplete):
        verify_alignment_evidence(report, REVISION, DIGEST)


@pytest.fixture()
def packet(tmp_path):
    receipt = make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, JOBS)
    receipt_path = _write(tmp_path / "candidate-receipt.json", receipt)
    backup = tmp_path / "postgres.dump"
    backup.write_bytes(b"verified backup bytes")
    deployment = {
        "schema": 1,
        "mode": "release",
        "candidate_revision": REVISION,
        "candidate_image": IMAGE,
        "candidate_image_id": "sha256:local-image-id",
        "candidate_repo_digests": [DIGEST],
        "candidate_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "candidate_receipt_status": "verified",
        "containers": [{"service": "ragflow-cpu", "image": "sha256:local-image-id", "status": "running", "health": "healthy"}],
        "backup": {"path": str(backup), "sha256": hashlib.sha256(backup.read_bytes()).hexdigest()},
        "health": {"status": "ok"},
    }
    deployment_path = _write(tmp_path / "deployment.json", deployment)
    workbench_path = _write(
        tmp_path / "source-workbench.json",
        {
            "source_revision": REVISION,
            "source_dirty": False,
            "status": "fail",
            "metrics": {},
            "cases": [{"id": "query-1"}],
            "index": {"state": "ready"},
            "model": {"embedding": "model", "weights": {"digest": "sha256:weights"}},
            "corpus_sha256": "a" * 64,
        },
    )
    business_path = _write(
        tmp_path / "business-documents.json",
        {
            "source_revision": REVISION,
            "source_dirty": False,
            "status": "FAIL",
            "case_results": [{"case_id": "M01"}],
            "ai": {"model": "model", "execution_count": 1},
            "model_weights_digest": "sha256:weights",
        },
    )
    _write(tmp_path / "technical-specifications.json", _alignment_report())
    return receipt_path, deployment_path, workbench_path, business_path, deployment


def test_quality_threshold_failure_is_recorded_without_replacing_release_identity_gate(packet):
    result = _verify(*packet[:4])
    assert result["status"] == "pass"
    assert result["evidence_status"] == "pass"
    assert result["quality_status"] == "fail"
    assert result["quality_baseline"] == {"source_workbench": "fail", "business_documents": "fail", "technical_specifications": "pass"}
    assert result["source_revision"] == REVISION
    assert result["image_digest"] == DIGEST


@pytest.mark.parametrize(
    "change,failed_check",
    [
        ({"candidate_revision": "c" * 40}, "source_revision"),
        ({"candidate_repo_digests": [f"registry.example:5443/ragflow@sha256:{'c' * 64}"]}, "image_digest"),
        ({"health": {"status": "error"}}, "api_health"),
        ({"candidate_receipt_status": "metadata_only"}, "receipt_verified"),
        ({"candidate_image_id": None}, "container_image"),
        ({"containers": [{"service": "ragflow-cpu", "image": "sha256:other", "status": "running", "health": "healthy"}]}, "container_image"),
    ],
)
def test_mismatched_revision_digest_and_health_fail(packet, change, failed_check):
    receipt_path, deployment_path, workbench_path, business_path, deployment = packet
    changed = deepcopy(deployment)
    changed.update(change)
    _write(deployment_path, changed)
    result = _verify(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "fail"
    assert result["checks"][failed_check] is False


def test_unavailable_backup_or_quality_report_is_incomplete(packet):
    receipt_path, deployment_path, workbench_path, business_path, deployment = packet
    changed = deepcopy(deployment)
    changed["backup"]["path"] = str(deployment_path.parent / "missing.dump")
    _write(deployment_path, changed)
    result = _verify(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert result["checks"]["backup"] is False
    _write(deployment_path, deployment)
    business_path.unlink()
    assert _verify(receipt_path, deployment_path, workbench_path, business_path)["status"] == "incomplete"


def test_dirty_quality_baseline_cannot_prove_clean_candidate(packet):
    receipt_path, deployment_path, workbench_path, business_path, _ = packet
    _write(
        workbench_path,
        {
            "source_revision": REVISION,
            "source_dirty": True,
            "status": "pass",
            "metrics": {},
            "cases": [{"id": "query-1"}],
            "index": {"state": "ready"},
            "model": {"embedding": "model", "weights": {"digest": "sha256:weights"}},
            "corpus_sha256": "a" * 64,
        },
    )
    result = _verify(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert "source_workbench" not in result["quality_baseline"]


def test_ci_job_failure_or_foreign_digest_cannot_form_receipt():
    failed = {**JOBS, "ragflow_tests_elasticsearch": "failure"}
    with pytest.raises(ValueError, match="required CI jobs"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, failed)
    with pytest.raises(ValueError, match="digest"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, f"other/ragflow@sha256:{'b' * 64}", JOBS)


def test_explicit_engine_skip_preserves_release_identity_verification(packet):
    receipt_path, deployment_path, workbench_path, business_path, deployment = packet
    jobs = {**JOBS, "ragflow_tests_infinity": "skipped", "ragflow_tests_elasticsearch": "skipped"}
    receipt = make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, jobs, engine_tests_required=False, engine_test_reason="no-engine-changes")
    _write(receipt_path, receipt)
    deployment["candidate_receipt_sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    _write(deployment_path, deployment)
    assert _verify(receipt_path, deployment_path, workbench_path, business_path)["status"] == "pass"
    assert receipt["ci"]["jobs"]["ragflow_tests_infinity"] == "skipped"


@pytest.mark.parametrize("result", ["failure", "cancelled", "success"])
def test_skip_policy_never_hides_a_failed_or_inconsistent_engine_run(result):
    jobs = {**JOBS, "ragflow_tests_infinity": result, "ragflow_tests_elasticsearch": "skipped"}
    with pytest.raises(ValueError, match="required CI jobs"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, jobs, engine_tests_required=False, engine_test_reason="no-engine-changes")


def test_old_receipts_require_both_engine_successes():
    receipt = make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, dict(JOBS))
    del receipt["ci"]["engine_tests_required"]
    del receipt["ci"]["engine_test_reason"]
    validate_receipt(receipt)
    receipt["ci"]["jobs"]["ragflow_tests_infinity"] = "skipped"
    with pytest.raises(ValueError, match="required CI jobs"):
        validate_receipt(receipt)


@pytest.mark.parametrize("required,reason", [(False, ""), (False, "baseline-unavailable"), ("false", "no-engine-changes"), (None, "no-engine-changes")])
def test_receipt_rejects_unproven_skip_selection(required, reason):
    with pytest.raises(ValueError, match="engine selection"):
        make_receipt(REVISION, "meownm/ragflow", "123", IMAGE, DIGEST, JOBS, engine_tests_required=required, engine_test_reason=reason)


def test_incomplete_quality_report_cannot_pass_release(packet):
    receipt_path, deployment_path, workbench_path, business_path, _ = packet
    _write(workbench_path, {"status": "incomplete", "failure": "index unavailable"})
    result = _verify(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert result["quality_status"] == "incomplete"
    assert "Source Workbench quality run is incomplete" in result["missing"]


def test_missing_model_evidence_is_incomplete_even_with_failed_threshold(packet):
    receipt_path, deployment_path, workbench_path, business_path, _ = packet
    workbench = json.loads(workbench_path.read_text(encoding="utf-8"))
    workbench["model"]["weights"] = None
    _write(workbench_path, workbench)
    business = json.loads(business_path.read_text(encoding="utf-8"))
    business["ai"] = None
    _write(business_path, business)
    result = _verify(receipt_path, deployment_path, workbench_path, business_path)
    assert result["status"] == "incomplete"
    assert "Source Workbench embedding model evidence is unavailable" in result["missing"]
    assert "Business Documents live model evidence is unavailable" in result["missing"]


def test_alignment_report_is_mandatory_for_existing_release_calls(packet):
    result = verify_release(*packet[:4])
    assert result["status"] == "incomplete"
    assert "Technical Specifications quality report is required" in result["missing"]


@pytest.mark.parametrize("field", ["source_revision", "source_dirty", "image_digest", "corpus_sha256", "implementation_sha256", "scope", "expected_case_ids", "cases", "evidence_status"])
def test_missing_alignment_proof_never_qualifies(packet, field):
    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data[field]
    _write(path, data)
    result = _verify(*packet[:4])
    assert result["status"] == "incomplete"
    assert "technical_specifications" not in result["quality_baseline"]


@pytest.mark.parametrize(
    "field",
    ["evidence_kind", "provider", "model", "model_digest", "request_id", "finish_reason", "evidence_complete", "input_integrity", "prompt_tokens", "completion_tokens", "generation_parameters"],
)
def test_missing_alignment_call_proof_never_qualifies(packet, field):
    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["cases"][0]["result"]["audit"][field]
    _write(path, data)
    assert _verify(*packet[:4])["status"] == "incomplete"


@pytest.mark.parametrize(
    "field,value",
    [("source_revision", "c" * 40), ("source_dirty", True), ("image_digest", "old@sha256:" + "c" * 64), ("scope", "DIAGNOSTIC"), ("corpus_sha256", "0" * 64), ("implementation_sha256", "0" * 64)],
)
def test_foreign_or_diagnostic_alignment_evidence_never_qualifies(packet, field, value):
    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = value
    _write(path, data)
    assert _verify(*packet[:4])["status"] == "incomplete"


@pytest.mark.parametrize("defect", ["metrics", "scores", "wire", "truncate", "shift", "usage", "aggregate", "missing_requirement"])
def test_alignment_verifier_recomputes_results_instead_of_trusting_pass(packet, defect):
    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    item = data["cases"][0]
    if defect == "metrics":
        item["result"]["metrics"]["implemented"] = 99
    elif defect == "scores":
        item["errors"] = ["fabricated"]
    elif defect == "wire":
        item["result"]["audit"]["wire_sha256"] = "0" * 64
    elif defect in {"truncate", "shift"}:
        from api.technical_specifications.alignment import fingerprint, report_input
        from api.technical_specifications.alignment_ai import SYSTEM, ollama_request
        from test.evals.technical_specifications.alignment.corpus import CASES, inputs

        document, basis = inputs(CASES[0])
        options = item["result"]["audit"]["generation_parameters"]
        wire = ollama_request("fixture-model", SYSTEM, report_input(document, basis), {"context_limit": options["num_ctx"], "output_limit": options["num_predict"]})
        wire[defect] = True
        item["result"]["audit"]["wire_sha256"] = fingerprint(wire)
    elif defect == "usage":
        item["result"]["audit"]["completion_tokens"] = 100_000
    elif defect == "aggregate":
        data["qualifies"] = False
    else:
        item["result"]["report"]["requirements"] = []
    _write(path, data)
    assert _verify(*packet[:4])["status"] == "fail"


def test_alignment_failure_is_a_mandatory_quality_gate(packet):
    from api.technical_specifications.alignment import validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score

    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    item = data["cases"][0]
    value = item["result"]["report"]
    value["requirements"][0].update(coverage="MISSING", references=[], reason="Injected model omission")
    item["result"]["model_report"]["requirements"][0].update(coverage="MISSING", references=[], reason="Injected model omission")
    document, basis = inputs(CASES[0])
    item["result"].update(validate_report(value, document, basis))
    item.update(status="FAIL", errors=score(CASES[0], item["result"]))
    data.update(status="FAIL", quality_status="FAIL", qualifies=False)
    _write(path, data)
    result = _verify(*packet[:4])
    assert result["status"] == "fail"
    assert result["quality_baseline"]["technical_specifications"] == "fail"
    assert "Technical Specifications quality gate failed" in result["failures"]


def test_duplicate_alignment_json_keys_do_not_silently_replace_evidence(packet):
    path = packet[0].parent / "technical-specifications.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw[:-1] + ', "scope": "FULL"}', encoding="utf-8")
    assert _verify(*packet[:4])["status"] == "incomplete"


def test_valid_full_alignment_proof_recomputes_all_current_cases():
    assert verify_alignment_evidence(_alignment_report(), REVISION, DIGEST) == "pass"


@pytest.mark.parametrize(
    "damage,message",
    [("missing_raw", "raw source selections"), ("unknown_id", "invalid requirement evidence"), ("changed_quote", "source selections differ")],
)
def test_full_gate_binds_original_model_selections_to_exact_current_source(damage, message):
    from test.evals.technical_specifications.alignment.evidence import verify_execution

    report = _alignment_report()
    assert verify_execution(report) == "pass"
    result = report["cases"][0]["result"]
    if damage == "missing_raw":
        del result["model_report"]
    elif damage == "unknown_id":
        result["model_report"]["requirements"][0]["references"][0]["citation_id"] = "C999999"
    else:
        # Still an exact source substring, but it was not the model's selection.
        result["report"]["requirements"][0]["references"][0]["quote"] = "Оператор"
    with pytest.raises(ValueError, match=message):
        verify_execution(report)


@pytest.mark.parametrize("damage", ["legacy_json", "wrong_schema_hash"])
def test_full_gate_recomputes_the_exact_native_generation_schema(damage):
    from api.technical_specifications.alignment import fingerprint, report_input
    from api.technical_specifications.alignment_ai import SYSTEM, ollama_request
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs
    from test.evals.technical_specifications.alignment.evidence import verify_execution

    report = _alignment_report()
    assert verify_execution(report) == "pass"
    document, basis = inputs(CASES[0])
    audit = report["cases"][0]["result"]["audit"]
    options = audit["generation_parameters"]
    wire = ollama_request("fixture-model", SYSTEM, report_input(document, basis), {"context_limit": options["num_ctx"], "output_limit": options["num_predict"]})
    if damage == "legacy_json":
        wire["format"] = "json"
    else:
        wire["format"]["properties"]["basis_sha256"]["enum"] = ["0" * 64]
    audit["wire_sha256"] = fingerprint(wire)
    with pytest.raises(ValueError, match="input audit is inconsistent"):
        verify_execution(report)


@pytest.mark.parametrize(
    "field,value",
    [("evidence_kind", "CONTROLLED_FAULT"), ("finish_reason", "length"), ("input_integrity", "NOT_ATTESTED"), ("evidence_complete", False), ("prompt_tokens", None), ("completion_tokens", True)],
)
def test_controlled_faults_or_unverified_completion_cannot_prove_model_quality(packet, field, value):
    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["cases"][0]["result"]["audit"][field] = value
    _write(path, data)
    assert _verify(*packet[:4])["status"] == "incomplete"


@pytest.mark.parametrize("defect", ["truncated", "duplicate_case", "duplicate_call", "different_model"])
def test_alignment_missing_or_mixed_inventory_is_incomplete(packet, defect):
    path = packet[0].parent / "technical-specifications.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if defect == "truncated":
        data["cases"][0]["result"]["report_status"] = "INCOMPLETE"
    elif defect == "duplicate_case":
        data["cases"][-1] = deepcopy(data["cases"][0])
    elif defect == "duplicate_call":
        data["cases"][1]["result"]["audit"]["request_id"] = data["cases"][0]["result"]["audit"]["request_id"]
    else:
        data["cases"][1]["result"]["audit"]["model_digest"] = "d" * 64
    _write(path, data)
    assert _verify(*packet[:4])["status"] == "incomplete"


@pytest.mark.parametrize(
    "damage",
    [
        "non-object-audit",
        "missing-model",
        "missing-digest-after",
        "missing-wire",
        "duplicate-real-id",
        "duplicate-natural-id",
        "foreign-digest",
        "foreign-response-model",
        "changed-options",
        "wrong-finish",
        "output-overrun",
        "client-close-only",
        "wrong-cancel-wire",
        "raised-token-policy",
        "raised-time-policy",
    ],
)
def test_real_probe_contract_cannot_be_replaced_by_status_labels(damage):
    from test.evals.technical_specifications.alignment.evidence import verify_execution

    report = _alignment_report()
    probes = report["real_limit_probes"]["cases"]
    audit = probes[1]["audit"]
    if damage == "non-object-audit":
        probes[1]["audit"] = None
    elif damage == "missing-model":
        del audit["model"]
    elif damage == "missing-digest-after":
        del audit["model_digest_after"]
    elif damage == "missing-wire":
        del audit["wire_sha256"]
    elif damage == "duplicate-real-id":
        probes[2]["audit"]["request_id"] = audit["request_id"]
        probes[2]["request_id"] = audit["request_id"]
    elif damage == "duplicate-natural-id":
        audit["request_id"] = report["cases"][0]["result"]["audit"]["request_id"]
        probes[1]["request_id"] = audit["request_id"]
    elif damage == "foreign-digest":
        audit["model_digest"] = "c" * 64
        audit["model_digest_after"] = "c" * 64
    elif damage == "foreign-response-model":
        audit["response_model"] = "another-model"
    elif damage == "changed-options":
        audit["generation_parameters"]["num_predict"] = 9
    elif damage == "wrong-finish":
        audit["finish_reason"] = "stop"
    elif damage == "output-overrun":
        audit["completion_tokens"] = 9
        report["budget"]["spent_tokens"] += 1
    elif damage == "client-close-only":
        del probes[4]["backend_observation"]
        probes[4]["client_task_closed"] = True
    elif damage == "wrong-cancel-wire":
        probes[4]["backend_observation"]["wire_sha256"] = "0" * 64
    elif damage == "raised-token-policy":
        report["budget"]["token_limit"] += 1
    else:
        report["budget"]["time_limit_ms"] += 1
    with pytest.raises(ValueError):
        verify_execution(report)


def test_approved_probe_budget_and_output_cap_are_fixed_independently():
    from test.evals.technical_specifications.alignment.corpus import CASES
    from test.evals.technical_specifications.alignment.real_limits import evaluation_policy, probe_spec

    assert evaluation_policy(len(CASES)) == {"call_limit": len(CASES) + 3, "token_limit": 4_000_000, "time_limit_ms": (len(CASES) * 300 + 180) * 1000}
    assert probe_spec(2)["plan"] == {"context_limit": 4096, "output_limit": 8}
