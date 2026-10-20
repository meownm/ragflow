"""Independent proxy archive evidence cannot be replaced by client assertions."""

from copy import deepcopy
import json

import pytest

from api.technical_specifications.alignment import fingerprint
from test.evals.technical_specifications.alignment.proxy_evidence import collect


@pytest.fixture
def packet():
    identifier = "a" * 32
    request = {"model": "qa", "messages": [{"role": "user", "content": "synthetic"}]}
    response = {"model": "qa", "done": True, "done_reason": "stop", "prompt_eval_count": 100, "eval_count": 50}
    transaction = {"request_id": identifier, "status": "done", "dedup_hit": False, "path": "/api/chat", "method": "POST", "response_status_code": 200}
    report = {
        "cases": [
            {
                "case_id": "synthetic",
                "result": {
                    "report_status": "INCOMPLETE",
                    "audit": {"request_id": identifier, "wire_sha256": fingerprint(request), "model": "qa", "finish_reason": "stop", "prompt_tokens": 100, "completion_tokens": 50},
                },
            }
        ]
    }
    archive = {"": transaction, "/request-body": request, "/response-body": response}
    paths = []

    def fetch(path):
        paths.append(path)
        suffix = path.removeprefix("/_proxy/admin/transactions/" + identifier)
        return archive[suffix]

    return report, archive, fetch, paths


def test_independent_matching_archives_prove_only_the_stated_boundary(packet):
    report, _, fetch, paths = packet
    proof = collect(report, "unused", fetch)
    assert proof["status"] == "VERIFIED"
    assert proof["boundary"] == "PROXY_WIRE_ONLY_NOT_BACKEND_CONTEXT_OR_CANCELLATION"
    assert len(paths) == 3 and all("/" + "a" * 32 in path for path in paths)
    assert 'synthetic"' not in str(proof)


@pytest.mark.parametrize("damage", ["cached", "unfinished", "input", "usage", "finish", "model", "request_id"])
def test_proxy_disagreement_never_proves_natural_inference(packet, damage):
    report, archive, fetch, _ = packet
    if damage == "cached":
        archive[""]["dedup_hit"] = True
    elif damage == "unfinished":
        archive[""]["status"] = "processing"
    elif damage == "input":
        archive["/request-body"]["messages"] = []
    elif damage == "usage":
        archive["/response-body"]["eval_count"] = 49
    elif damage == "finish":
        archive["/response-body"]["done_reason"] = "length"
    elif damage == "model":
        archive["/response-body"]["model"] = "foreign"
    else:
        archive[""]["request_id"] = "b" * 32
    assert collect(report, "unused", fetch)["status"] == "INCOMPLETE"


def test_missing_proxy_is_incomplete_and_never_leaks_exception_text(packet):
    report, _, _, _ = packet

    def broken(_):
        raise OSError("private credential")

    proof = collect(report, "unused", broken)
    assert proof["status"] == "INCOMPLETE" and "private" not in str(proof)
    untrusted = deepcopy(report)
    untrusted["cases"][0]["result"]["audit"]["request_id"] = "../other"
    assert collect(untrusted, "unused", lambda _: pytest.fail("Must not read untrusted id"))["status"] == "INCOMPLETE"


def test_admission_guard_is_not_fabricated_as_inference_or_a_standalone_proxy_pass(packet):
    report, _, fetch, _ = packet
    guard = {"case_id": "ADMISSION-CONTEXT-LOW", "evidence_kind": "DETERMINISTIC", "inference_calls": 0, "result": {"reason_code": "ALIGNMENT_CONTEXT_EXCEEDED"}}
    report["cases"].insert(0, guard)
    proof = collect(report, "unused", fetch)
    assert proof["status"] == "VERIFIED" and proof["cases"][0]["status"] == "NOT_APPLICABLE"
    assert collect({"cases": [guard]}, "unused", lambda _: pytest.fail("No inference"))["status"] == "INCOMPLETE"


@pytest.mark.parametrize("damage", [None, "missing_model_report", "changed_resolved_quote", "unknown_selection", "forged_catalog", "legacy_format", "changed_format_hash"])
def test_complete_source_selection_is_recomputed_from_the_archived_input(packet, damage):
    from api.technical_specifications.alignment import materialize_report, report_input
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs

    report, archive, fetch, _ = packet
    document, basis = inputs(CASES[0])
    payload = report_input(document, basis)
    raw = {
        "candidate_sha256": payload["candidate_sha256"],
        "basis_sha256": payload["basis_sha256"],
        "requirements": [{"id": "R001", "coverage": "IMPLEMENTED", "consistency": "CONSISTENT", "references": [{"citation_id": payload["citations"][0]["id"]}], "reason": "Synthetic"}],
        "additional_rules": [],
        "joint": {"checked_ids": ["R001"], "consistency": "CONSISTENT", "reason": ""},
    }
    result = report["cases"][0]["result"]
    result.update(report_status="COMPLETE", model_report=deepcopy(raw), report=materialize_report(raw, payload))
    if damage == "missing_model_report":
        del result["model_report"]
    elif damage == "changed_resolved_quote":
        result["report"]["requirements"][0]["references"][0]["quote"] = "Fabricated"
    elif damage == "unknown_selection":
        raw["requirements"][0]["references"][0]["citation_id"] = "C999999"
        result["model_report"] = deepcopy(raw)
    elif damage == "forged_catalog":
        payload["citations"][0]["quote"] = "Fabricated"
    archive["/request-body"]["messages"] = [{"role": "system", "content": "Synthetic"}, {"role": "user", "content": json.dumps(payload)}]
    archive["/request-body"]["format"] = deepcopy(payload["response_schema"])
    if damage == "legacy_format":
        archive["/request-body"]["format"] = "json"
    elif damage == "changed_format_hash":
        archive["/request-body"]["format"]["properties"]["basis_sha256"]["enum"] = ["0" * 64]
    result["audit"]["wire_sha256"] = fingerprint(archive["/request-body"])
    archive["/response-body"]["message"] = {"content": json.dumps(raw)}
    proof = collect(report, "unused", fetch)
    assert proof["status"] == ("VERIFIED" if damage is None else "INCOMPLETE")
    assert proof["boundary"] == "PROXY_WIRE_ONLY_NOT_BACKEND_CONTEXT_OR_CANCELLATION"
