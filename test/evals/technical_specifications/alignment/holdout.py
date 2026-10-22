"""Sealed, separately labelled inputs; the oracle is read only after inference."""

import hashlib
import re
from pathlib import Path

from api.technical_specifications.alignment import CONSISTENCY, COVERAGE, fingerprint, source_snapshot, strict_json, validate_report
from api.technical_specifications.alignment_budget import AlignmentBudget

SUITE = "bt-tz-independent-holdout-v1"


def holdout_inputs(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("suite_id") != SUITE or not isinstance(value.get("cases"), list) or not 8 <= len(value["cases"]) <= 32:
        raise ValueError("Invalid holdout inventory")
    identifiers = []
    for case in value["cases"]:
        if not isinstance(case, dict) or set(case) != {"case_id", "title", "business_requirements", "sections"}:
            raise ValueError("Holdout inputs must not contain annotations")
        identifier = case["case_id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"HBTZ-\d{2}", identifier) or identifier in identifiers:
            raise ValueError("Invalid holdout case identity")
        identifiers.append(identifier)
        if not isinstance(case["title"], str) or not 1 <= len(case["title"]) <= 256:
            raise ValueError("Invalid holdout title")
        business = case["business_requirements"]
        if not isinstance(business, list) or not 1 <= len(business) <= 3 or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 or "\n" in text for text in business):
            raise ValueError("Holdout requires explicit atomic paragraphs")
        sections = case["sections"]
        if not isinstance(sections, dict) or set(sections) != {str(index) for index in range(1, 10)} or any(not isinstance(text, str) or len(text) > 10000 for text in sections.values()):
            raise ValueError("Invalid holdout sections")
    return value


def load_bundle(directory):
    directory = Path(directory)
    manifest_bytes = (directory / "manifest.json").read_bytes()
    manifest = strict_json(manifest_bytes.decode("utf-8"))
    try:
        review = strict_json((directory / "approval.json").read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError("Independent holdout label review is required") from error
    try:
        contract_sha256 = hashlib.sha256((directory / "annotation-contract.md").read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError("Frozen public annotation contract is required") from error
    if manifest.get("files", {}).get("annotation-contract.md", {}).get("sha256") != contract_sha256 or review.get("annotation_contract_sha256") != contract_sha256:
        raise ValueError("Independent review must bind the frozen public annotation contract")
    if (
        review.get("status") != "PASS"
        or review.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
        or review.get("case_count") != manifest.get("case_count")
        or not isinstance(review.get("reviewer"), str)
        or not 1 <= len(review["reviewer"]) <= 80
        or review["reviewer"] == manifest.get("annotation_owner")
    ):
        raise ValueError("Independent holdout label review is missing, rejected or stale")
    raw = (directory / "inputs.json").read_bytes()
    for name in ("inputs.json", "oracle.json"):
        item = manifest.get("files", {}).get(name, {})
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise ValueError("Holdout must be frozen before inference")
    if hashlib.sha256(raw).hexdigest() != manifest["files"]["inputs.json"]["sha256"]:
        raise ValueError("Holdout input snapshot changed")
    value = holdout_inputs(strict_json(raw.decode("utf-8")))
    if manifest.get("case_ids") != [case["case_id"] for case in value["cases"]] or manifest.get("case_count") != len(value["cases"]):
        raise ValueError("Holdout manifest inventory mismatch")
    # No oracle file is opened here, including for validation.
    return value, manifest


def model_inputs(case):
    body = "\n\n".join(case["business_requirements"])
    document = {"id": "holdout-tz-" + case["case_id"], "revision": 1, "sections": case["sections"]}
    basis = source_snapshot(
        [
            {
                "document_id": "holdout-bt-" + case["case_id"],
                "revision_id": "agreed-r1",
                "agreement_event_id": "holdout-agreement-1",
                "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                "body_markdown": body,
            }
        ]
    )
    return document, basis


async def run_holdout(ai, actor, value, *, checkpoint=None):
    value = holdout_inputs(value)
    budget = AlignmentBudget(seconds=len(value["cases"]) * 300, calls=len(value["cases"]), token_limit=4_000_000)
    report = {
        "schema_version": 1,
        "document_type": "technical_specification",
        "suite_id": SUITE,
        "scope": "HOLDOUT",
        "expected_case_ids": [case["case_id"] for case in value["cases"]],
        "cases": [],
        "input_sha256": fingerprint(value),
        "quality_status": "UNKNOWN",
        "evidence_status": "INCOMPLETE",
        "qualifies": False,
    }
    identities, requests = set(), set()
    for case in value["cases"]:
        document, basis = model_inputs(case)
        report["active_case_id"] = case["case_id"]

        def reserved(audit):
            report.update(active_audit=audit, budget=budget.snapshot())
            if checkpoint:
                checkpoint(report)

        result = await ai.check(actor, document, basis, budget=budget, on_reserved=reserved)
        report["cases"].append({"case_id": case["case_id"], "status": "INCOMPLETE", "errors": [], "result": result})
        report.update(active_case_id=None, budget=budget.snapshot())
        report.pop("active_audit", None)
        if checkpoint:
            checkpoint(report)
        audit = result.get("audit", {})
        identities.add((audit.get("model"), audit.get("model_digest"), audit.get("profile_sha256")))
        request_id = audit.get("request_id")
        invalid_id = not request_id or request_id in requests
        requests.add(request_id)
        if budget.reserved_tokens or invalid_id or len(identities) != 1 or any(not all(identity) for identity in identities):
            report["reason_code"] = "HOLDOUT_EXECUTION_INTERRUPTED"
            break
    report["budget"] = budget.snapshot()
    return report


def score_holdout(report, value, directory, manifest):
    """Read the sealed oracle only after the child has finished all model calls."""
    raw = (Path(directory) / "oracle.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["files"]["oracle.json"]["sha256"]:
        raise ValueError("Holdout oracle snapshot changed")
    oracle = strict_json(raw.decode("utf-8"))
    if (
        oracle.get("schema_version") != 1
        or oracle.get("suite_id") != SUITE
        or not isinstance(oracle.get("cases"), list)
        or [row.get("case_id") for row in oracle["cases"]] != report["expected_case_ids"]
        or report.get("input_sha256") != fingerprint(value)
    ):
        raise ValueError("Holdout scoring identity mismatch")
    for case, expected in zip(value["cases"], oracle["cases"], strict=True):
        rows = expected.get("rows")
        if (
            not isinstance(rows, list)
            or len(rows) != len(case["business_requirements"])
            or any(
                not isinstance(row, dict)
                or row.get("coverage") not in COVERAGE
                or row.get("consistency") not in CONSISTENCY
                or (
                    row.get("required_quote") is not None
                    and (not isinstance(row["required_quote"], str) or not row["required_quote"] or not any(row["required_quote"] in text for text in case["sections"].values()))
                )
                for row in rows
            )
            or expected.get("joint") not in CONSISTENCY
            or type(expected.get("unsupported_business_rule")) is not bool
        ):
            raise ValueError("Invalid holdout annotation")
    observed = report.get("cases", [])
    if [item["case_id"] for item in observed] != report["expected_case_ids"][: len(observed)]:
        raise ValueError("Holdout execution inventory mismatch")
    for case, expected, item in zip(value["cases"], oracle["cases"], observed):
        result = item["result"]
        if result.get("audit", {}).get("input_integrity") == "TRUNCATED":
            item.update(status="INCOMPLETE", errors=["MODEL_INPUT_TRUNCATED"])
            continue
        if result.get("report_status") != "COMPLETE" or result.get("execution_status") != "COMPLETE":
            item.update(status="FAIL" if result.get("quality_status") == "FAIL" else "INCOMPLETE", errors=["REPORT_INCOMPLETE"])
            continue
        document, basis = model_inputs(case)
        validate_report(result["report"], document, basis)
        errors = []
        answer = result["report"]
        for index, (actual, row) in enumerate(zip(answer["requirements"], expected["rows"], strict=True)):
            for key in ("coverage", "consistency"):
                if actual[key] != row[key]:
                    errors.append(f"row:{index + 1}:{key}")
            quote = row["required_quote"]
            if quote and not any(quote in ref["quote"] for ref in actual["references"]):
                errors.append(f"row:{index + 1}:EVIDENCE_CLAUSE_MISSING")
        if answer["joint"]["consistency"] != expected["joint"]:
            errors.append("JOINT_CONSISTENCY")
        unsupported = any(row["kind"] == "UNSUPPORTED_BUSINESS_RULE" for row in answer["additional_rules"])
        if unsupported != expected["unsupported_business_rule"]:
            errors.append("UNSUPPORTED_BUSINESS_RULE")
        item.update(status="FAIL" if errors else "PASS", errors=errors)
    quality = "FAIL" if any(item["status"] == "FAIL" for item in observed) else "PASS" if len(observed) == len(value["cases"]) and all(item["status"] == "PASS" for item in observed) else "UNKNOWN"
    report.update(
        quality_status=quality,
        evidence_status="INCOMPLETE",
        qualifies=False,
        annotation_method=oracle.get("annotation_method"),
        annotation_owner=oracle.get("annotation_owner"),
        oracle_sha256=manifest["files"]["oracle.json"]["sha256"],
        annotation_contract_sha256=manifest["files"]["annotation-contract.md"]["sha256"],
        status="FAIL" if quality == "FAIL" else "INCOMPLETE",
    )
    return report
