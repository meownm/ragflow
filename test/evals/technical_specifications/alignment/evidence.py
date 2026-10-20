"""Recompute execution evidence for nightly and release consumers alike."""

import re

_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class EvidenceIncomplete(ValueError):
    """The invocation cannot establish complete model evidence."""


def verify_execution(report):
    from api.technical_specifications.alignment import fingerprint, report_input, validate_report
    from api.technical_specifications.alignment_ai import SYSTEM, ollama_request
    from api.technical_specifications.domain import SpecificationError
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score

    if type(report.get("schema_version")) is not int or report["schema_version"] != 1 or report.get("document_type") != "technical_specification" or report.get("suite_id") != "bt-tz-alignment-v1":
        raise ValueError("Technical Specifications quality report has an invalid identity")
    if report.get("scope") != "FULL":
        raise EvidenceIncomplete("Technical Specifications diagnostic run cannot qualify a release")
    expected = [case[0] for case in CASES]
    cases = report.get("cases")
    if (
        not expected
        or report.get("expected_case_ids") != expected
        or not isinstance(cases, list)
        or any(not isinstance(item, dict) for item in cases)
        or [item.get("case_id") for item in cases] != expected
    ):
        raise EvidenceIncomplete("Technical Specifications quality report has an incomplete corpus")
    signatures, requests = set(), set()
    spent_tokens = 0
    failed = False
    for case, item in zip(CASES, cases, strict=True):
        result = item.get("result")
        if not isinstance(result, dict) or result.get("execution_status") != "COMPLETE" or result.get("report_status") != "COMPLETE":
            raise EvidenceIncomplete("Technical Specifications execution or report is incomplete")
        document, basis = inputs(case)
        try:
            checked = validate_report(result.get("report"), document, basis)
        except SpecificationError as error:
            raise ValueError("Technical Specifications report contains invalid requirement evidence") from error
        if any(result.get(key) != checked[key] for key in ("execution_status", "report_status", "quality_status", "qualifies", "metrics")):
            raise ValueError("Technical Specifications derived results are inconsistent")
        errors = score(case, result)
        if item.get("errors") != errors or item.get("status") != ("FAIL" if errors else "PASS"):
            raise ValueError("Technical Specifications golden scores are inconsistent")
        failed |= bool(errors)
        audit = result.get("audit")
        if not isinstance(audit, dict):
            raise EvidenceIncomplete("Technical Specifications live model audit is unavailable")
        model, digest, request_id = audit.get("model"), audit.get("model_digest"), audit.get("request_id")
        options = audit.get("generation_parameters")
        if (
            result.get("evidence_status") != "VERIFIED"
            or audit.get("evidence_kind") != "NATURAL_MODEL"
            or audit.get("provider") != "Ollama"
            or not isinstance(model, str)
            or not model
            or not isinstance(digest, str)
            or not _HEX_DIGEST.fullmatch(digest)
            or audit.get("model_digest_after") != digest
            or not isinstance(request_id, str)
            or not re.fullmatch(r"[a-f0-9]{32}", request_id)
            or request_id in requests
            or audit.get("finish_reason") != "stop"
            or audit.get("backend_completion_known") is not True
            or audit.get("evidence_complete") is not True
            or audit.get("input_integrity") != "VERIFIED"
            or audit.get("input_tokenization") not in {"SERVER_EXACT", "MODEL_TOKEN_IDS_MATCHED"}
            or audit.get("response_model") != model
            or not isinstance(audit.get("profile_sha256"), str)
            or not _HEX_DIGEST.fullmatch(audit["profile_sha256"])
            or any(type(audit.get(key)) is not int or audit[key] < 1 for key in ("prompt_tokens", "completion_tokens"))
            or not isinstance(options, dict)
            or any(type(options.get(key)) is not int or options[key] < 1 for key in ("num_ctx", "num_predict"))
        ):
            raise EvidenceIncomplete("Technical Specifications model completion or input integrity is unverified")
        payload = report_input(document, basis)
        wire = ollama_request(model, SYSTEM, payload, {"context_limit": options["num_ctx"], "output_limit": options["num_predict"]})
        if options != wire["options"]:
            raise ValueError("Technical Specifications natural call options differ from its protocol")
        if audit.get("input_sha256") != fingerprint(payload) or audit.get("wire_sha256") != fingerprint(wire):
            raise ValueError("Technical Specifications model input audit is inconsistent")
        if audit["prompt_tokens"] + audit["completion_tokens"] > options["num_ctx"] or audit["completion_tokens"] > options["num_predict"]:
            raise ValueError("Technical Specifications model usage exceeds recorded limits")
        requests.add(request_id)
        signatures.add((model, digest, audit["profile_sha256"]))
        spent_tokens += audit["prompt_tokens"] + audit["completion_tokens"]
    from test.evals.technical_specifications.alignment.real_limits import IDS, evaluation_policy, probe_kind, probe_spec, validate_probe_audit

    limits = report.get("real_limit_probes")
    if (
        not isinstance(limits, dict)
        or limits.get("status") != "PASS"
        or limits.get("expected_case_ids") != list(IDS)
        or not isinstance(limits.get("cases"), list)
        or any(not isinstance(item, dict) for item in limits["cases"])
        or [item.get("case_id") for item in limits["cases"]] != list(IDS)
        or any(item.get("status") != "PASS" for item in limits["cases"])
    ):
        raise EvidenceIncomplete("Technical Specifications real limit probes are incomplete")
    probes = limits["cases"]
    if len(signatures) != 1:
        raise EvidenceIncomplete("Technical Specifications natural model identity changed")
    model, digest, _profile = next(iter(signatures))
    identity = (model, digest)
    if any(probe.get("evidence_kind") != probe_kind(index) for index, probe in enumerate(probes, start=1)):
        raise EvidenceIncomplete("Technical Specifications probe evidence boundaries are inconsistent")
    if (
        probes[0].get("reason_code") != "NATURAL_BASELINE_OBSERVED"
        or probes[2].get("input_integrity") != "TRUNCATED"
        or probes[2].get("qualification_rejected") is not True
        or type(probes[3].get("loaded_context")) is not int
        or probes[3]["loaded_context"] != probe_spec(3)["plan"]["context_limit"]
        or (probes[3].get("model"), probes[3].get("model_digest")) != identity
        or probes[5].get("reason_code") != "ALIGNMENT_CALL_LIMIT"
        or probes[6].get("reason_code") != "ALL_USAGE_ACCOUNTED"
        or probes[7].get("tokenizer_verified") is not True
        or probes[7].get("input_integrity") != "VERIFIED"
    ):
        raise EvidenceIncomplete("Technical Specifications backend limit observations are unverified")
    for index in (2, 3):
        probe = probes[index - 1]
        try:
            audit = validate_probe_audit(index, probe.get("audit"), identity=identity, request_ids=requests)
        except (TypeError, ValueError) as error:
            raise EvidenceIncomplete("Technical Specifications real probe dispatch or completion is unverified") from error
        if probe.get("request_id") != audit["request_id"]:
            raise EvidenceIncomplete("Technical Specifications probe request identity changed")
        if index == 3 and audit.get("input_integrity") != "TRUNCATED":
            raise EvidenceIncomplete("Technical Specifications reported truncation lacks matching call evidence")
        spent_tokens += audit["prompt_tokens"] + audit["completion_tokens"]
    cancel = probes[4]
    try:
        audit = validate_probe_audit(5, cancel.get("audit"), identity=identity, request_ids=requests, terminal=False)
    except (TypeError, ValueError) as error:
        raise EvidenceIncomplete("Technical Specifications canceled request wire is unverified") from error
    observation, usage = cancel.get("backend_observation"), cancel.get("backend_usage")
    if (
        cancel.get("request_id") != audit["request_id"]
        or cancel.get("upstream_request_started") is not True
        or cancel.get("backend_stop_verified") is not True
        or cancel.get("backend_usage_known") is not True
        or not isinstance(observation, dict)
        or observation.get("boundary") != "BACKEND"
        or observation.get("terminal_state") != "CANCELLED"
        or observation.get("cancel_acknowledged") is not True
        or observation.get("request_id") != audit["request_id"]
        or observation.get("wire_sha256") != audit["wire_sha256"]
        or (observation.get("model"), observation.get("model_digest")) != identity
        or observation.get("model_digest_after") != digest
        or not isinstance(usage, dict)
        or any(type(usage.get(key)) is not int or usage[key] < 0 for key in ("prompt_tokens", "completion_tokens"))
        or observation.get("usage") != usage
    ):
        raise EvidenceIncomplete("Technical Specifications backend cancellation and usage are unverified")
    plan = probe_spec(5)["plan"]
    if usage["completion_tokens"] > plan["output_limit"] or usage["prompt_tokens"] + usage["completion_tokens"] > plan["context_limit"]:
        raise ValueError("Technical Specifications canceled request exceeded its limits")
    spent_tokens += usage["prompt_tokens"] + usage["completion_tokens"]
    policy = evaluation_policy(len(CASES))
    ledger = report.get("budget")
    if (
        not isinstance(ledger, dict)
        or any(type(ledger.get(key)) is not int or ledger[key] < 0 for key in ("calls", "call_limit", "spent_tokens", "reserved_tokens", "token_limit", "elapsed_ms", "time_limit_ms"))
        or ledger["calls"] != policy["call_limit"]
        or ledger["call_limit"] != policy["call_limit"]
        or ledger["token_limit"] != policy["token_limit"]
        or ledger["time_limit_ms"] != policy["time_limit_ms"]
        or ledger["reserved_tokens"] != 0
        or ledger["spent_tokens"] != spent_tokens
        or ledger["spent_tokens"] > policy["token_limit"]
        or ledger["elapsed_ms"] > policy["time_limit_ms"]
    ):
        raise EvidenceIncomplete("Technical Specifications total execution budget is unverified")
    quality = "FAIL" if failed else "PASS"
    if len(signatures) != 1 or report.get("evidence_status") != "VERIFIED":
        raise EvidenceIncomplete("Technical Specifications model identity or evidence changed")
    if report.get("quality_status") != quality or report.get("status") != quality or report.get("qualifies") is not (quality == "PASS"):
        raise ValueError("Technical Specifications aggregate result is inconsistent")
    return quality.lower()
