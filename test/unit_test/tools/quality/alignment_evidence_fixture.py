"""Synthetic proof fabrication for verifier tests ONLY; never model evidence."""

from hashlib import sha256


def add_probe_contract(report):
    from api.technical_specifications.alignment import fingerprint
    from api.technical_specifications.alignment_ai import ollama_request
    from test.evals.technical_specifications.alignment.corpus import CASES
    from test.evals.technical_specifications.alignment.real_limits import IDS, evaluation_policy, probe_kind, probe_spec

    # Preserve the semantic oracle and every document verdict of the input fixture.
    first = report["cases"][0]["result"]["audit"]
    model, digest = first["model"], first["model_digest"]
    for item in report["cases"]:
        audit = item["result"]["audit"]
        audit.update(request_id=sha256(("NATURAL:" + item["case_id"]).encode()).hexdigest()[:32], model_digest_after=audit["model_digest"], backend_completion_known=True)
    probes = [{"case_id": identifier, "status": "PASS", "evidence_kind": probe_kind(index)} for index, identifier in enumerate(IDS, start=1)]
    for index in (2, 3, 5):
        spec = probe_spec(index)
        identifier = sha256(("SYNTHETIC-PROBE:" + str(index)).encode()).hexdigest()[:32]
        request = ollama_request(model, spec["system"], spec["payload"], spec["plan"])
        audit = {
            "provider": "Ollama",
            "model": model,
            "model_digest": digest,
            "request_id": identifier,
            "wire_sha256": fingerprint(request),
            "generation_parameters": request["options"],
            "evidence_kind": probe_kind(index),
            "backend_completion_known": index != 5,
            "model_digest_after": digest if index != 5 else None,
            "response_model": model if index != 5 else None,
            "finish_reason": "length" if index == 2 else "stop" if index == 3 else None,
        }
        if index != 5:
            audit.update(prompt_tokens=100, completion_tokens=8, input_integrity="TRUNCATED" if index == 3 else "NOT_ATTESTED")
        probes[index - 1].update(request_id=identifier, audit=audit)
    probes[0]["reason_code"] = "NATURAL_BASELINE_OBSERVED"
    probes[1]["reason_code"] = "OUTPUT_LIMIT_OBSERVED"
    probes[2].update(input_integrity="TRUNCATED", qualification_rejected=True)
    probes[3].update(reason_code="LOADED_CONTEXT_OBSERVED", loaded_context=probe_spec(3)["plan"]["context_limit"], model=model, model_digest=digest)
    cancel = probes[4]
    usage = {"prompt_tokens": 20, "completion_tokens": 0}
    cancel.update(
        upstream_request_started=True,
        backend_stop_verified=True,
        backend_usage_known=True,
        backend_usage=usage,
        backend_observation={
            "boundary": "BACKEND",
            "terminal_state": "CANCELLED",
            "cancel_acknowledged": True,
            "request_id": cancel["request_id"],
            "wire_sha256": cancel["audit"]["wire_sha256"],
            "model": model,
            "model_digest": digest,
            "model_digest_after": digest,
            "usage": dict(usage),
        },
    )
    probes[5]["reason_code"] = "ALIGNMENT_CALL_LIMIT"
    probes[6]["reason_code"] = "ALL_USAGE_ACCOUNTED"
    probes[7].update(tokenizer_verified=True, input_integrity="VERIFIED")
    report["real_limit_probes"] = {"status": "PASS", "expected_case_ids": list(IDS), "cases": probes}
    policy = evaluation_policy(len(CASES))
    spent = sum(item["result"]["audit"][key] for item in report["cases"] for key in ("prompt_tokens", "completion_tokens")) + 236
    report["budget"] = {**policy, "calls": policy["call_limit"], "spent_tokens": spent, "reserved_tokens": 0, "elapsed_ms": 1000}
    return report
