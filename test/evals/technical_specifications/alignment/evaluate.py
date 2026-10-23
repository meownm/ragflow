"""In-runtime nightly/manual evaluator over the production alignment boundary."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from api.technical_specifications.alignment_ai import SYSTEM, AlignmentAI
from api.technical_specifications.alignment_budget import AlignmentBudget
from test.evals.technical_specifications.alignment.corpus import CASES, inputs, score
from test.evals.technical_specifications.alignment.metrics import semantic_metrics


def _unfinished_backend(result, budget):
    return bool(budget.reserved_tokens and result.get("audit", {}).get("backend_completion_known") is not True)


def case_observation(case, result):
    errors = score(case, result)
    complete = result.get("execution_status") == "COMPLETE" and result.get("report_status") == "COMPLETE"
    status = "FAIL" if result.get("quality_status") == "FAIL" and not complete else ("PASS" if not errors else "FAIL") if complete else "INCOMPLETE"
    return {"case_id": case[0], "status": status, "errors": errors, "result": result}


async def run_cases(ai, actor, *, case_id=None, checkpoint=None, budget=None, expected_identity=None):
    from test.evals.technical_specifications.alignment.real_limits import evaluation_policy

    chosen = [case for case in CASES if case_id is None or case[0] == case_id]
    if not chosen:
        raise ValueError("Unknown or empty alignment corpus")
    report = {
        "schema_version": 1,
        "document_type": "technical_specification",
        "suite_id": "bt-tz-alignment-v1",
        "scope": "DIAGNOSTIC" if case_id else "CORE",
        "expected_case_ids": [case[0] for case in chosen],
        "cases": [],
        "status": "INCOMPLETE",
    }
    policy = evaluation_policy(len(chosen), core_only=True)
    budget = budget or AlignmentBudget(seconds=policy["time_limit_ms"] / 1000, calls=policy["call_limit"], token_limit=policy["token_limit"])
    for case in chosen:
        document, basis = inputs(case)
        report["active_case_id"] = case[0]
        if checkpoint:
            checkpoint(report)

        def reserved(audit):
            report["active_audit"] = audit
            report["budget"] = budget.snapshot()
            if checkpoint:
                checkpoint(report)

        result = await ai.check(actor, document, basis, budget=budget, on_reserved=reserved)
        report["cases"].append(case_observation(case, result))
        report["active_case_id"] = None
        report.pop("active_audit", None)
        statuses = {item["status"] for item in report["cases"]}
        report["quality_status"] = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "INCOMPLETE" in statuses else "PASS"
        report["evidence_status"] = "VERIFIED" if all(item["result"].get("evidence_status") == "VERIFIED" for item in report["cases"]) else "INCOMPLETE"
        identities = {(item["result"].get("audit", {}).get("model_digest"), item["result"].get("audit", {}).get("profile_sha256")) for item in report["cases"]}
        if expected_identity is not None:
            identities.add(expected_identity)
        if len(identities) != 1 or any(not digest or not profile for digest, profile in identities):
            report["evidence_status"] = "INCOMPLETE"
        report["status"] = "INCOMPLETE"
        report["budget"] = budget.snapshot()
        if checkpoint:
            checkpoint(report)
        if _unfinished_backend(result, budget) or len(identities) > 1:
            if len(identities) > 1:
                report["reason_code"] = "MODEL_IDENTITY_CHANGED"
            break
    if case_id:
        report["status"] = "DIAGNOSTIC"
    elif report["quality_status"] == "FAIL":
        report["status"] = "FAIL"
    elif report["evidence_status"] == "VERIFIED" and len(report["cases"]) == len(chosen):
        report["status"] = "PASS"
    report["qualifies"] = report["status"] == "PASS"
    return report


async def run_evaluation(ai, actor, *, case_id=None, checkpoint=None, limits_only=False, core_only=False, expected_identity=None):
    from test.evals.technical_specifications.alignment.real_limits import IDS, evaluation_policy
    from test.evals.technical_specifications.alignment.real_limits import run as run_limits

    if core_only and (case_id or limits_only):
        raise ValueError("Core corpus requires all scenarios without limit probes")
    if case_id or core_only:
        return await run_cases(ai, actor, case_id=case_id, checkpoint=checkpoint, expected_identity=expected_identity)
    policy = evaluation_policy(len(CASES), limits_only=limits_only)
    budget = AlignmentBudget(seconds=policy["time_limit_ms"] / 1000, calls=policy["call_limit"], token_limit=policy["token_limit"])
    report = (
        {
            "schema_version": 1,
            "document_type": "technical_specification",
            "suite_id": "bt-tz-alignment-v1",
            "expected_case_ids": [],
            "cases": [],
            "quality_status": "UNKNOWN",
            "scope": "DIAGNOSTIC",
            "status": "INCOMPLETE",
        }
        if limits_only
        else await run_cases(ai, actor, checkpoint=checkpoint, budget=budget, expected_identity=expected_identity)
    )
    report.update(scope="DIAGNOSTIC" if limits_only else "FULL", qualifies=False, status="INCOMPLETE", real_limit_probes={"status": "INCOMPLETE", "expected_case_ids": list(IDS), "cases": []})

    def save(probes, ledger):
        report["real_limit_probes"]["cases"] = list(probes)
        report["budget"] = ledger
        if checkpoint:
            checkpoint(report)

    if checkpoint:
        checkpoint(report)
    interrupted = any(_unfinished_backend(item["result"], budget) for item in report["cases"])
    if interrupted or report.get("reason_code") == "MODEL_IDENTITY_CHANGED":
        report["real_limit_probes"]["reason_code"] = "MODEL_IDENTITY_CHANGED" if report.get("reason_code") == "MODEL_IDENTITY_CHANGED" else "BACKEND_USAGE_UNKNOWN_AFTER_INTERRUPTED_CASE"
    else:
        prior_ids = [item["result"].get("audit", {}).get("request_id") for item in report["cases"]]
        report["real_limit_probes"] = await run_limits(
            ai.open_model,
            actor,
            checkpoint=save,
            baseline=report["cases"][0]["result"] if report["cases"] else None,
            budget=budget,
            prior_request_ids=[identifier for identifier in prior_ids if identifier],
        )
    if report["real_limit_probes"]["status"] != "PASS":
        report["evidence_status"] = "INCOMPLETE"
    report["budget"] = budget.snapshot()
    if report["budget"]["elapsed_ms"] > report["budget"]["time_limit_ms"]:
        report.update(evidence_status="INCOMPLETE", reason_code="ALIGNMENT_DEADLINE")
    report["status"] = (
        "DIAGNOSTIC"
        if limits_only
        else "FAIL"
        if report["quality_status"] == "FAIL"
        else "PASS"
        if report.get("evidence_status") == "VERIFIED" and report["real_limit_probes"]["status"] == "PASS"
        else "INCOMPLETE"
    )
    report["qualifies"] = report["status"] == "PASS"
    return report


def public_summary(report):
    """No source text, generated content or exception messages in admin projections."""
    audits = [item.get("result", {}).get("audit", {}) for item in report.get("cases", [])]
    identity_audits = list(audits)
    experiments = report.get("nightly_experiments", {})
    if isinstance(experiments, dict):
        for stage in experiments.values():
            if isinstance(stage, dict):
                identity_audits.extend(item.get("result", {}).get("audit", {}) for item in stage.get("cases", []))
    signatures = {(audit.get("model"), audit.get("model_digest")) for audit in identity_audits if audit.get("model")}
    complete = (
        bool(report.get("expected_case_ids")) and len(report.get("cases", [])) == len(report["expected_case_ids"]) and all(item["status"] in ("PASS", "FAIL") for item in report.get("cases", []))
    )
    passed = sum(item["status"] == "PASS" for item in report.get("cases", []))
    durations = [audit.get("duration_ms") for audit in audits]
    tokens = [audit.get(key) for audit in audits for key in ("prompt_tokens", "completion_tokens")]
    metadata = [audit.get("generation_parameters") for audit in identity_audits]
    profiles = list({json.dumps(item, sort_keys=True) for item in metadata if isinstance(item, dict)})
    ledger = report.get("budget")
    total_tokens = sum(tokens) if tokens and all(type(value) is int and value >= 0 for value in tokens) else None
    duration = sum(durations) if durations and all(type(value) is int and value >= 0 for value in durations) else None
    if isinstance(ledger, dict):
        total_tokens = ledger.get("spent_tokens") if ledger.get("reserved_tokens") == 0 and type(ledger.get("spent_tokens")) is int else None
        duration = ledger.get("elapsed_ms") if type(ledger.get("elapsed_ms")) is int else None
    return {
        "document_type": "technical_specification",
        "status": report["status"],
        "quality_status": report.get("quality_status", "UNKNOWN"),
        "evidence_status": report.get("evidence_status", "INCOMPLETE"),
        "suite_id": report.get("suite_id"),
        "suite_version": "1",
        "expected_cases": len(report.get("expected_case_ids", [])),
        "executed_cases": len(report.get("cases", [])),
        "provider": "Ollama",
        "model": next(iter(signatures))[0] if len(signatures) == 1 else None,
        "model_digest": next(iter(signatures))[1] if len(signatures) == 1 else None,
        "duration_ms": duration,
        "total_tokens": total_tokens,
        "p0_case_pass_rate": passed / len(report["expected_case_ids"]) if complete else None,
        "all_case_pass_rate": passed / len(report["expected_case_ids"]) if complete else None,
        "suite_sha256": hashlib.sha256(Path(__file__).with_name("corpus.py").read_bytes()).hexdigest(),
        "prompt_hashes": {"alignment": hashlib.sha256(SYSTEM.encode()).hexdigest()},
        "parameter_profiles": [json.loads(item) for item in sorted(profiles)],
        "alignment_quality": semantic_metrics(report),
        "alignment_metrics": {
            "model_cases_passed": passed,
            "model_cases_failed": sum(item["status"] == "FAIL" for item in report.get("cases", [])),
            "model_cases_unknown": sum(item["status"] == "INCOMPLETE" for item in report.get("cases", [])),
            "model_cases_unexecuted": max(0, len(report.get("expected_case_ids", [])) - len(report.get("cases", []))),
            "reports_complete": sum(item.get("result", {}).get("report_status") == "COMPLETE" for item in report.get("cases", [])),
            "evidence_verified": sum(item.get("result", {}).get("evidence_status") == "VERIFIED" for item in report.get("cases", [])),
        },
        "cases": [
            {
                "case_id": item["case_id"],
                "status": item["status"],
                "failure_count": len(item["errors"]),
                "diagnostics": [{"code": "ASSERTION_FAILED"}] if item["errors"] else [],
                "metrics": item.get("result", {}).get("metrics", {}),
            }
            for item in report.get("cases", [])
        ],
    }


def frozen_model_opener(model_name=None):
    """One read-only profile boundary shared by nightly and frozen host runners."""
    from api.source_workbench.adapters import RAGFlowAnswerGateway, RAGFlowProcessModel

    frozen = None

    async def open_model(actor):
        nonlocal frozen
        model = await RAGFlowAnswerGateway.open_processor(actor)
        config = dict(model.model_config)
        if model_name:
            config["llm_name"] = model_name
        selected = model
        if model_name:
            selected = RAGFlowProcessModel(actor, config)
            await selected.resolve_context()
            selected.resolve_output_limit()
        profile = {"config": config, "context": selected.context_tokens, "output": selected.output_tokens}
        signature = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()
        if frozen is not None and signature != frozen:
            raise ValueError("QA configuration changed")
        frozen = signature
        return selected

    return open_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--model")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--nightly", action="store_true")
    mode.add_argument("--core-only", action="store_true")
    args = parser.parse_args()
    if args.nightly and args.case_id:
        parser.error("Nightly collection requires the complete corpus")
    if args.core_only and args.case_id:
        parser.error("Core collection requires the complete corpus")
    if os.environ.get("TZ_ALIGNMENT_LIVE") != "1":
        raise ValueError("Explicit live QA opt-in required")
    from common import settings

    settings.init_settings()
    from api.db.db_models import Tenant

    tenant = Tenant.get_or_none((Tenant.id == args.tenant) & (Tenant.name == "Business Documents nightly QA"))
    if tenant is None:
        raise ValueError("Existing dedicated QA tenant required")

    def checkpoint(report):
        pending = args.report.with_suffix(".tmp")
        pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(args.report)

    ai = AlignmentAI(frozen_model_opener(args.model))
    if args.nightly:
        from test.evals.technical_specifications.alignment.nightly import run_nightly

        report = asyncio.run(run_nightly(ai, tenant.id, checkpoint=checkpoint))
    else:
        report = asyncio.run(run_evaluation(ai, tenant.id, case_id=args.case_id, core_only=args.core_only, checkpoint=checkpoint))
    checkpoint(report)
    return 0 if report["qualifies"] else 1 if report.get("quality_status") == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
