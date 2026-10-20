"""Nightly collection of bounded experiments before cancellation fault probes.

Each experiment is an independent, fixed-budget series. A retained reservation
or changed model stops later series; a new ledger never erases unknown debt.
"""

from copy import deepcopy

import aiohttp

from api.technical_specifications.domain import SpecificationError
from test.evals.technical_specifications.alignment.corpus import CASES
from test.evals.technical_specifications.alignment.evaluate import run_evaluation
from test.evals.technical_specifications.alignment.experiments import KINDS, experiment_case_ids, experiment_case_status, run_experiment

# Full evaluation contains a cancellation probe, so it must be the last series.
ORDER = KINDS


def execution_seconds():
    return len(CASES) * 900 + 1740


def experiment_summary(report):
    """Recompute observed quality; current experiments cannot attest qualification."""
    result = []
    experiments = report.get("nightly_experiments", {})
    for kind in ORDER:
        stage = experiments.get(kind, {}) if isinstance(experiments, dict) else {}
        stage = stage if isinstance(stage, dict) else {}
        expected = experiment_case_ids(kind)
        cases = stage.get("cases", [])
        if not isinstance(cases, list):
            cases = []
        prefix = all(isinstance(item, dict) for item in cases) and [item.get("case_id") for item in cases] == expected[: len(cases)]
        statuses = [experiment_case_status(kind, item) for item in cases] if prefix else []
        known = prefix and len(cases) == len(expected) and all(status in {"PASS", "FAIL"} for status in statuses)
        failed = statuses.count("FAIL")
        ledger = stage.get("budget", {})
        ledger = ledger if isinstance(ledger, dict) else {}
        tokens = ledger.get("spent_tokens")
        spent = tokens if type(tokens) is int and tokens >= 0 and type(ledger.get("reserved_tokens")) is int and ledger["reserved_tokens"] == 0 else None
        elapsed = ledger.get("elapsed_ms")
        result.append(
            {
                "experiment": kind,
                "expected_cases": len(expected),
                "executed_cases": len(cases) if prefix else 0,
                "quality_status": "FAIL" if failed else "PASS" if known else "UNKNOWN",
                "evidence_status": "INCOMPLETE",
                "total_tokens": spent,
                "duration_ms": elapsed if type(elapsed) is int and elapsed >= 0 else None,
            }
        )
    return result


def nightly_status(report):
    stages = experiment_summary(report)
    # Admission uses estimated tokens and thinking usage has no attestation.
    # No collection flag or successful labels may promote these facts to VERIFIED.
    return "FAIL" if any(stage["quality_status"] == "FAIL" for stage in stages) else "INCOMPLETE"


async def run_nightly(ai, actor, *, checkpoint=None):
    report = {
        "schema_version": 1,
        "document_type": "technical_specification",
        "suite_id": "bt-tz-alignment-v1",
        "scope": "FULL",
        "expected_case_ids": [case[0] for case in CASES],
        "cases": [],
        "status": "INCOMPLETE",
        "quality_status": "UNKNOWN",
        "evidence_status": "INCOMPLETE",
        "qualifies": False,
        "nightly_experiments": {kind: {"expected_case_ids": experiment_case_ids(kind), "cases": []} for kind in ORDER},
    }
    identities, request_ids = set(), set()

    def save():
        failed = any(item.get("status") == "FAIL" for stage in report["nightly_experiments"].values() for item in stage["cases"])
        report.update(status="FAIL" if report.get("quality_status") == "FAIL" or failed else "INCOMPLETE", qualifies=False)
        if checkpoint:
            checkpoint(deepcopy(report))

    save()
    for kind in ORDER:

        def progress(partial, kind=kind):
            report["nightly_experiments"][kind] = deepcopy(partial)
            save()

        try:
            stage = await run_experiment(ai, actor, kind, checkpoint=progress)
        except (SpecificationError, TimeoutError, ValueError, aiohttp.ClientError):
            # Partial report and pending reservation remain in the same artifact.
            report["reason_code"] = "NIGHTLY_EXPERIMENT_INTERRUPTED"
            save()
            return report
        progress(stage)
        items = stage.get("cases", [])
        for item in items:
            if item.get("inference_calls") == 0 and item.get("evidence_kind") == "DETERMINISTIC":
                continue
            audit = item.get("result", {}).get("audit", {})
            identities.add((audit.get("model"), audit.get("model_digest")))
            request_id = audit.get("request_id")
            if not request_id or request_id in request_ids:
                report["reason_code"] = "NIGHTLY_REQUEST_ID_INVALID"
            request_ids.add(request_id)
        complete = [item.get("case_id") for item in items] == experiment_case_ids(kind) and all(item.get("status") in {"PASS", "FAIL"} for item in items)
        if (
            not complete
            or stage.get("reason_code")
            or stage.get("budget", {}).get("reserved_tokens") != 0
            or len(identities) != 1
            or any(not model or not digest for model, digest in identities)
            or report.get("reason_code")
        ):
            report.setdefault("reason_code", "NIGHTLY_SERIES_INCOMPLETE")
            save()
            return report

    first = report["nightly_experiments"]["REPEATS"]["cases"][0]["result"]["audit"]

    def full_progress(partial):
        report.update(deepcopy(partial))
        save()

    try:
        full = await run_evaluation(ai, actor, checkpoint=full_progress, expected_identity=(first["model_digest"], first["profile_sha256"]))
    except (SpecificationError, TimeoutError, ValueError, aiohttp.ClientError):
        report["reason_code"] = "NIGHTLY_FULL_INTERRUPTED"
        save()
        return report
    report.update(full)
    report["status"] = "FAIL" if full.get("quality_status") == "FAIL" or nightly_status(report) == "FAIL" else "INCOMPLETE"
    report.update(qualifies=False, evidence_status="INCOMPLETE")
    if checkpoint:
        checkpoint(deepcopy(report))
    return report
