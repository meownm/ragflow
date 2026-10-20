"""Synthetic contracts; never open the independently sealed live holdout."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.technical_specifications.alignment import report_input, validate_report
from test.evals.technical_specifications.alignment import holdout


def bundle(tmp_path):
    quote = "Рецензент читает отчёт."
    value = {
        "schema_version": 1,
        "suite_id": holdout.SUITE,
        "cases": [
            {"case_id": f"HBTZ-{index:02}", "title": "Synthetic contract", "business_requirements": [quote], "sections": {str(section): quote if section == 5 else "" for section in range(1, 10)}}
            for index in range(1, 9)
        ],
    }
    oracle = {
        "schema_version": 1,
        "suite_id": holdout.SUITE,
        "annotation_owner": "unit-test",
        "annotation_method": "SYNTHETIC_UNIT_FIXTURE",
        "cases": [
            {"case_id": case["case_id"], "rows": [{"coverage": "IMPLEMENTED", "consistency": "CONSISTENT", "required_quote": quote}], "joint": "CONSISTENT", "unsupported_business_rule": False}
            for case in value["cases"]
        ],
    }
    manifest = {"case_ids": [case["case_id"] for case in value["cases"]], "case_count": len(value["cases"]), "files": {}}
    for name, content in (("inputs.json", value), ("oracle.json", oracle)):
        raw = json.dumps(content, ensure_ascii=False).encode()
        (tmp_path / name).write_bytes(raw)
        manifest["files"][name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    review = {"status": "PASS", "case_count": len(value["cases"]), "reviewer": "unit-reviewer", "manifest_sha256": hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest()}
    (tmp_path / "approval.json").write_text(json.dumps(review), encoding="utf-8")
    return value, manifest


def answer(document, basis):
    payload = report_input(document, basis)
    raw = {
        "candidate_sha256": payload["candidate_sha256"],
        "basis_sha256": payload["basis_sha256"],
        "requirements": [
            {"id": row["id"], "coverage": "IMPLEMENTED", "consistency": "CONSISTENT", "references": [{"section": "5", "quote": row["quote"]}], "reason": "Synthetic"} for row in basis["inventory"]
        ],
        "additional_rules": [],
        "joint": {"checked_ids": [row["id"] for row in basis["inventory"]], "consistency": "CONSISTENT", "reason": "Synthetic"},
    }
    return validate_report(raw, document, basis)


def test_input_loader_never_opens_oracle_before_inference(tmp_path):
    value, manifest = bundle(tmp_path)
    (tmp_path / "oracle.json").unlink()
    assert holdout.load_bundle(tmp_path) == (value, manifest)
    contaminated = deepcopy(value)
    contaminated["cases"][0]["expected"] = "PASS"
    with pytest.raises(ValueError, match="annotations"):
        holdout.holdout_inputs(contaminated)
    (tmp_path / "inputs.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot"):
        holdout.load_bundle(tmp_path)


@pytest.mark.parametrize("defect", ["missing", "rejected", "stale", "owner", "count"])
def test_unreviewed_or_rejected_holdout_is_blocked_before_inference(tmp_path, defect):
    bundle(tmp_path)
    path = tmp_path / "approval.json"
    if defect == "missing":
        path.unlink()
    else:
        review = json.loads(path.read_text(encoding="utf-8"))
        if defect == "rejected":
            review["status"] = "FAIL"
        if defect == "stale":
            review["manifest_sha256"] = "0" * 64
        if defect == "owner":
            review["reviewer"] = ""
        if defect == "count":
            review["case_count"] -= 1
        path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="review"):
        holdout.load_bundle(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pass", "failure", "unknown", "identity", "duplicate"])
async def test_holdout_scores_only_after_generation_and_retains_fixed_inventory(tmp_path, mode):
    value, manifest = bundle(tmp_path)
    calls = []
    prior = uuid4().hex

    async def check(_actor, document, basis, *, budget, on_reserved):
        calls.append(document)
        claim = budget.claim(30)
        audit = {
            "model": "synthetic",
            "model_digest": "b" * 64 if mode == "identity" and len(calls) == 2 else "a" * 64,
            "profile_sha256": "c" * 64,
            "request_id": prior if mode == "duplicate" else uuid4().hex,
        }
        on_reserved(audit)
        if mode == "unknown":
            return {"execution_status": "INCOMPLETE", "report_status": "INCOMPLETE", "quality_status": "UNKNOWN", "audit": audit}
        budget.settle(10, 10, claim_id=claim)
        result = answer(document, basis)
        if mode == "failure" and len(calls) == 1:
            result["report"]["requirements"][0]["coverage"] = "PARTIAL"
        return {**result, "audit": audit}

    report = await holdout.run_holdout(SimpleNamespace(check=check), "synthetic", value)
    assert report["quality_status"] == "UNKNOWN" and all(item["status"] == "INCOMPLETE" for item in report["cases"])
    report = holdout.score_holdout(report, value, tmp_path, manifest)
    assert report["expected_case_ids"] == [case["case_id"] for case in value["cases"]]
    assert not report["qualifies"] and report["scope"] == "HOLDOUT"
    assert report["quality_status"] == ("PASS" if mode == "pass" else "FAIL" if mode == "failure" else "UNKNOWN")
    assert len(calls) == (1 if mode == "unknown" else 2 if mode in {"identity", "duplicate"} else 8)
    if mode == "unknown":
        assert report["budget"]["reserved_tokens"] == 30
    (tmp_path / "oracle.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="oracle snapshot"):
        holdout.score_holdout(report, value, tmp_path, manifest)
