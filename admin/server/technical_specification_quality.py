"""Technical-specification lane in the existing durable document-quality queue."""

import json
import os
import subprocess
import sys
from math import isfinite

from document_quality_evidence import record_quality_evidence
from document_quality_models import quality_model_digest


def execution_timeout(case_id=None, *, nightly=False):
    from test.evals.technical_specifications.alignment.corpus import CASES

    if nightly:
        from test.evals.technical_specifications.alignment.nightly import execution_seconds

        return execution_seconds()
    return 420 if case_id else len(CASES) * 300 + 300


def public_report(report):
    from test.evals.technical_specifications.alignment.metrics import public_metrics

    if not isinstance(report, dict):
        return None
    result = {}
    for key in ("document_type", "status", "quality_status", "evidence_status", "suite_id", "suite_version", "provider", "model", "model_digest"):
        value = report.get(key)
        if isinstance(value, str) and len(value) <= 256:
            result[key] = value
    if type(report.get("source_dirty")) is bool:
        result["source_dirty"] = report["source_dirty"]
    source = report.get("source_fingerprint")
    if isinstance(source, str) and len(source) == 64 and all(value in "0123456789abcdef" for value in source):
        result["source_fingerprint"] = source
    for key in ("expected_cases", "executed_cases", "duration_ms", "total_tokens", "p0_case_pass_rate", "all_case_pass_rate"):
        value = report.get(key)
        if type(value) in (int, float) and isfinite(value) and value >= 0:
            result[key] = value
    metrics = report.get("alignment_metrics") if isinstance(report.get("alignment_metrics"), dict) else {}
    result["alignment_metrics"] = {
        key: metrics[key]
        for key in ("model_cases_passed", "model_cases_failed", "model_cases_unknown", "model_cases_unexecuted", "reports_complete", "evidence_verified")
        if type(metrics.get(key)) is int and metrics[key] >= 0
    }
    result["alignment_quality"] = public_metrics(report.get("alignment_quality"))
    if isinstance(report.get("nightly_experiments"), list):
        from test.evals.technical_specifications.alignment.nightly import ORDER

        result["nightly_experiments"] = [
            {
                "experiment": item["experiment"],
                "quality_status": item["quality_status"],
                "evidence_status": "INCOMPLETE",
                **{key: item.get(key) if type(item.get(key)) is int and item[key] >= 0 else None for key in ("expected_cases", "executed_cases", "total_tokens")},
            }
            for item in report["nightly_experiments"]
            if isinstance(item, dict) and item.get("experiment") in ORDER and item.get("quality_status") in {"PASS", "FAIL", "UNKNOWN"}
        ]
    digest = report.get("suite_sha256")
    if isinstance(digest, str) and len(digest) == 64 and all(value in "0123456789abcdef" for value in digest):
        result["suite_sha256"] = digest
    prompt = report.get("prompt_hashes", {}).get("alignment") if isinstance(report.get("prompt_hashes"), dict) else None
    if isinstance(prompt, str) and len(prompt) == 64 and all(value in "0123456789abcdef" for value in prompt):
        result["prompt_hashes"] = {"alignment": prompt}
    profiles = report.get("parameter_profiles") if isinstance(report.get("parameter_profiles"), list) else []
    result["parameter_profiles"] = [
        {key: profile[key] for key in ("temperature", "num_ctx", "num_predict") if type(profile.get(key)) in (int, float) and isfinite(profile[key])}
        for profile in profiles
        if isinstance(profile, dict)
    ]
    result["cases"] = []
    for item in report.get("cases", []):
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str) or len(item["case_id"]) > 80:
            continue
        if item.get("status") not in ("PASS", "FAIL", "INCOMPLETE"):
            continue
        result["cases"].append({"case_id": item["case_id"], "status": item["status"], "failure_count": item.get("failure_count", 0) if type(item.get("failure_count", 0)) is int else 0})
    return result


def execute(row, root, tenant, source_revision, source_fingerprint):
    # Imports stay within the operational adapter, outside the value-only owner.
    from document_quality_runs import _campaign_source_matches, _source_fingerprint, _source_revision

    from api.db.db_models import BusinessDocumentQualityCampaign
    from api.technical_specifications.alignment import validate_report
    from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score
    from test.evals.technical_specifications.alignment.evaluate import public_summary
    from test.evals.technical_specifications.alignment.evidence import verify_execution

    if not tenant or source_revision == "unknown" or row.source_revision != source_revision:
        return "INCOMPLETE", None, "Queued source revision or dedicated QA tenant is unavailable"
    if row.campaign_id:
        campaign = BusinessDocumentQualityCampaign.get_or_none(BusinessDocumentQualityCampaign.id == row.campaign_id)
        if campaign is None or campaign.baseline_status != "PASS" or not _campaign_source_matches(campaign):
            return "INCOMPLETE", None, "Nightly baseline did not pass"
    if row.model_name and row.model_digest and quality_model_digest(tenant, row.model_name) != row.model_digest:
        return "INCOMPLETE", None, "Model digest changed before execution"
    metadata = {"document_type": "technical_specification", "source_revision": source_revision, "source_fingerprint": source_fingerprint, "campaign_id": row.campaign_id}
    nightly = getattr(row, "trigger", None) in {"NIGHTLY", "MONTHLY"} and row.scope == "FULL"
    with record_quality_evidence("run", row.id, metadata) as directory:
        target = directory / "report.json"
        command = [sys.executable, "-B", "-m", "test.evals.technical_specifications.alignment.evaluate", "--tenant", tenant, "--report", str(target)]
        if row.model_name:
            command.extend(["--model", row.model_name])
        if row.case_id:
            command.extend(["--case-id", row.case_id])
        if nightly:
            command.append("--nightly")
        env = {**os.environ, "TZ_ALIGNMENT_LIVE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        with (directory / "pytest.log").open("w", encoding="utf-8") as log:
            try:
                process = subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=execution_timeout(row.case_id, nightly=nightly), check=False)
            except subprocess.TimeoutExpired:
                return "INCOMPLETE", None, "Live model suite timed out"
        if not target.is_file():
            return "INCOMPLETE", None, "Live model suite produced no report"
        report = json.loads(target.read_text(encoding="utf-8"))
        expected = [case for case in CASES if not row.case_id or case[0] == row.case_id]
        identifiers = [case.get("case_id") for case in report.get("cases", [])]
        if report.get("expected_case_ids") != [case[0] for case in expected] or identifiers != report["expected_case_ids"][: len(identifiers)] or (not nightly and len(identifiers) != len(expected)):
            return "INCOMPLETE", None, "Live model report has incomplete inventory"
        for case, item in zip(expected[: len(identifiers)], report["cases"], strict=True):
            result = item["result"]
            if result.get("report_status") == "COMPLETE":
                document, basis = inputs(case)
                recomputed = validate_report(result["report"], document, basis)
                if recomputed["metrics"] != result.get("metrics"):
                    return "INCOMPLETE", None, "Live model report metrics are inconsistent"
            if item["errors"] != score(case, result):
                return "INCOMPLETE", None, "Live model report scores are inconsistent"
        summary = public_summary(report)
        if nightly:
            from test.evals.technical_specifications.alignment.nightly import experiment_summary

            summary["nightly_experiments"] = experiment_summary(report)
            for key in ("total_tokens", "duration_ms"):
                values = [summary.get(key), *(item[key] for item in summary["nightly_experiments"])]
                summary[key] = sum(values) if all(type(value) is int and value >= 0 for value in values) else None
        summary.update(source_dirty=source_fingerprint is not None, source_fingerprint=source_fingerprint)
        if _source_revision() != source_revision or _source_fingerprint() != source_fingerprint:
            return "INCOMPLETE", summary, "Evaluation source changed during execution"
        if row.model_name and row.model_digest and quality_model_digest(tenant, row.model_name) != row.model_digest:
            return "INCOMPLETE", summary, "Model digest changed during execution"
        if process.returncode not in (0, 1, 2):
            return "INCOMPLETE", summary, "Live model execution failed"
        if row.scope == "CASE":
            return "DIAGNOSTIC", summary, None
        if nightly:
            from test.evals.technical_specifications.alignment.nightly import nightly_status

            status = "FAIL" if report.get("quality_status") == "FAIL" or nightly_status(report) == "FAIL" else "INCOMPLETE"
            summary.update(status=status, evidence_status="INCOMPLETE")
            return status, summary, "Nightly experiment qualification is incomplete"
        try:
            quality = verify_execution(report)
        except (ValueError, TypeError, KeyError):
            status = "FAIL" if report.get("quality_status") == "FAIL" else "INCOMPLETE"
        else:
            status = "PASS" if quality == "pass" and process.returncode == 0 else "FAIL" if quality == "fail" else "INCOMPLETE"
        if status == "INCOMPLETE":
            summary.update(status="INCOMPLETE", evidence_status="INCOMPLETE")
        return status, summary, None if status in ("PASS", "DIAGNOSTIC") else "Live model qualification did not pass"
