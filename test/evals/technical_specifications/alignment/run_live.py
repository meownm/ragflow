"""Frozen live BT/TZ alignment in the existing dedicated QA model runtime."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FILES = {
    "document_exchange.identity": "document_exchange/identity.py",
    "api.document_exchange.merge": "api/document_exchange/merge.py",
    "api.source_workbench.processing": "api/source_workbench/processing.py",
    "api.technical_specifications.domain": "api/technical_specifications/domain.py",
    "api.technical_specifications.alignment": "api/technical_specifications/alignment.py",
    "api.technical_specifications.alignment_budget": "api/technical_specifications/alignment_budget.py",
    "api.technical_specifications.alignment_ai": "api/technical_specifications/alignment_ai.py",
    "test.evals.technical_specifications.alignment.corpus": "test/evals/technical_specifications/alignment/corpus.py",
    "test.evals.technical_specifications.alignment.metrics": "test/evals/technical_specifications/alignment/metrics.py",
    "test.evals.technical_specifications.alignment.real_limits": "test/evals/technical_specifications/alignment/real_limits.py",
    "test.evals.technical_specifications.alignment.evidence": "test/evals/technical_specifications/alignment/evidence.py",
    "test.evals.technical_specifications.alignment.evaluate": "test/evals/technical_specifications/alignment/evaluate.py",
    "test.evals.technical_specifications.alignment.experiments": "test/evals/technical_specifications/alignment/experiments.py",
    "test.evals.technical_specifications.alignment.holdout": "test/evals/technical_specifications/alignment/holdout.py",
    "test.evals.technical_specifications.alignment.proxy_evidence": "test/evals/technical_specifications/alignment/proxy_evidence.py",
}
CHILD = """
import asyncio, hashlib, json, logging, sys, types
logging.disable(logging.CRITICAL)
from common import settings
settings.init_settings()
from api.db.db_models import Tenant
tenant = Tenant.get_or_none(Tenant.name == "Business Documents nightly QA")
assert tenant is not None, "Dedicated QA tenant missing"
for name, item in PAYLOAD["modules"].items():
    assert hashlib.sha256(item["source"].encode()).hexdigest() == item["sha256"]
    if name.startswith("test."):
        parts = name.split(".")
        for index in range(1, len(parts)):
            package = ".".join(parts[:index])
            if package not in sys.modules:
                parent = types.ModuleType(package)
                parent.__path__ = []
                sys.modules[package] = parent
    module = types.ModuleType(name)
    module.__package__ = name.rpartition(".")[0]
    module.__file__ = "<frozen-source>/" + item["path"]
    sys.modules[name] = module
    exec(compile(item["source"], module.__file__, "exec"), module.__dict__)
from test.evals.technical_specifications.alignment.corpus import CASES
from test.evals.technical_specifications.alignment.evaluate import run_evaluation, frozen_model_opener
from api.technical_specifications.alignment_ai import AlignmentAI
async def main():
    printed = 0
    last_probe = None
    last_active = None
    last_ledger = None
    def checkpoint(report):
        nonlocal printed, last_probe, last_active, last_ledger
        if report.get("active_audit"):
            active = json.dumps({"active_case_id":report["active_case_id"], "active_audit":report["active_audit"], "budget":report.get("budget")}, sort_keys=True)
            if active != last_active:
                print("ALIGNMENT_ACTIVE=" + active, flush=True)
                last_active = active
        if isinstance(report.get("budget"), dict):
            ledger = json.dumps(report["budget"], sort_keys=True)
            if ledger != last_ledger:
                print("ALIGNMENT_LEDGER=" + ledger, flush=True)
                last_ledger = ledger
        if len(report["cases"]) > printed:
            print("ALIGNMENT_CASE=" + json.dumps(report["cases"][-1], ensure_ascii=True), flush=True)
            printed = len(report["cases"])
        if "real_limit_probes" in report:
            snapshot = json.dumps({"real_limit_probes":report["real_limit_probes"], "budget":report.get("budget")}, sort_keys=True)
            if snapshot != last_probe:
                print("ALIGNMENT_PROBES=" + snapshot, flush=True)
                last_probe = snapshot
    if PAYLOAD["prepare_only"]:
        print("ALIGNMENT_FINAL=" + json.dumps({"scope":"PREPARATION", "quality_status":"UNKNOWN", "evidence_status":"INCOMPLETE", "cases":[], "expected_case_ids":[case[0] for case in CASES]}), flush=True)
        return
    if PAYLOAD.get("holdout_inputs"):
        from test.evals.technical_specifications.alignment.holdout import run_holdout
        report = await run_holdout(AlignmentAI(frozen_model_opener()), tenant.id, PAYLOAD["holdout_inputs"], checkpoint=checkpoint)
    elif PAYLOAD["experiment"]:
        from test.evals.technical_specifications.alignment.experiments import run_experiment
        report = await run_experiment(AlignmentAI(frozen_model_opener()), tenant.id, PAYLOAD["experiment"], checkpoint=checkpoint)
    else:
        report = await run_evaluation(AlignmentAI(frozen_model_opener()), tenant.id, case_id=PAYLOAD["case_id"], checkpoint=checkpoint, limits_only=PAYLOAD["limits_only"])
    print("ALIGNMENT_FINAL=" + json.dumps(report, ensure_ascii=True), flush=True)
asyncio.run(main())
"""


def _timeout(args, case_count):
    if args.prepare_only:
        return 120
    if getattr(args, "holdout_dir", None):
        return case_count * 300 + 300
    if args.experiment == "REPEATS":
        return case_count * 600 + 300
    if args.experiment:
        return 720
    if args.limits_only:
        return 300
    return case_count * 300 + 300


def _recover(lines, expected, experiment, exit_code):
    """An interrupted runner preserves settled spend and any outstanding request."""

    def events(prefix):
        return [json.loads(line.split("=", 1)[1]) for line in lines if line.startswith(prefix + "=")]

    cases = events("ALIGNMENT_CASE")
    result = {
        "quality_status": "FAIL" if any(item["status"] == "FAIL" for item in cases) else "UNKNOWN",
        "evidence_status": "INCOMPLETE",
        "qualifies": False,
        "cases": cases,
        "expected_case_ids": expected,
        "reason_code": "RUN_TIMEOUT" if exit_code == 124 else "FINAL_REPORT_MISSING",
    }
    if experiment:
        result.update(scope="EXPERIMENT", experiment=experiment)
    active = events("ALIGNMENT_ACTIVE")
    if active and active[-1]["active_case_id"] not in {item["case_id"] for item in cases}:
        result.update(active[-1])
    probes = events("ALIGNMENT_PROBES")
    if probes:
        result.update(probes[-1])
    ledgers = events("ALIGNMENT_LEDGER")
    if ledgers:
        result["budget"] = ledgers[-1]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--proxy-audit-url", default="http://localhost:11435")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--limits-only", action="store_true")
    parser.add_argument("--experiment", choices=("REPEATS", "ADMISSION", "REASONING"))
    parser.add_argument("--holdout-dir", type=Path)
    args = parser.parse_args()
    from test.evals.technical_specifications.alignment.corpus import CASES
    from test.evals.technical_specifications.alignment.experiments import experiment_case_ids

    if args.experiment and (args.case_id or args.limits_only):
        parser.error("An experiment cannot be combined with a case or the limit-only lane")
    if args.holdout_dir and (args.case_id or args.limits_only or args.experiment or args.prepare_only):
        parser.error("Holdout is an independent inference series")
    holdout_value = holdout_manifest = None
    holdout_review_sha256 = None
    if args.holdout_dir:
        from test.evals.technical_specifications.alignment.holdout import load_bundle

        holdout_value, holdout_manifest = load_bundle(args.holdout_dir)
        holdout_review_sha256 = hashlib.sha256((args.holdout_dir / "approval.json").read_bytes()).hexdigest()

    chosen = [case for case in CASES if not args.case_id or case[0] == args.case_id]
    if not chosen:
        raise ValueError("Unknown alignment case")
    output = args.evidence_dir.resolve()
    if output.exists() or output.is_relative_to(ROOT):
        raise ValueError("Use a new evidence directory outside the checkout")
    container = "ragflow-local-ragflow-cpu-1"
    inspected = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]
    assert inspected["Config"]["Labels"]["com.docker.compose.project"] == "ragflow-local"
    modules = {}
    for name, path in FILES.items():
        source = (ROOT / path).read_text(encoding="utf-8")
        modules[name] = {"path": path, "source": source, "sha256": hashlib.sha256(source.encode()).hexdigest(), "source_bytes_sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}
    host_source = Path(__file__).read_bytes()
    payload = {
        "modules": modules,
        "case_id": args.case_id,
        "prepare_only": args.prepare_only,
        "limits_only": args.limits_only,
        "experiment": args.experiment,
        "harness_sha256": hashlib.sha256(host_source).hexdigest(),
        "holdout_inputs": holdout_value,
    }
    output.mkdir(parents=True)
    (output / "input.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script = "PAYLOAD=" + repr(payload) + "\n" + CHILD
    (output / "host-source.py").write_bytes(host_source)
    (output / "child-source.py").write_text(script, encoding="utf-8")
    with (output / "run.log").open("wb") as log:
        try:
            completed = subprocess.run(
                ["docker", "exec", "-i", container, "/ragflow/.venv/bin/python", "-B", "-"],
                input=script.encode(),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=_timeout(args, len(holdout_value["cases"]) if holdout_value else len(chosen)),
                check=False,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
    lines = (output / "run.log").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line.split("=", 1)[1]) for line in lines if line.startswith("ALIGNMENT_FINAL=")]
    stable = Path(__file__).read_bytes() == host_source and all(hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["source_bytes_sha256"] for item in modules.values())
    expected = experiment_case_ids(args.experiment) if args.experiment else [case[0] for case in chosen]
    result = events[-1] if events else _recover(lines, expected, args.experiment, exit_code)
    if holdout_value:
        from api.technical_specifications.alignment import fingerprint
        from test.evals.technical_specifications.alignment.holdout import load_bundle, score_holdout

        identifiers = [case["case_id"] for case in holdout_value["cases"]]
        if not events:
            result.update(expected_case_ids=identifiers, input_sha256=fingerprint(holdout_value))
        result.update(scope="HOLDOUT", suite_id="bt-tz-independent-holdout-v1")
        stable = stable and load_bundle(args.holdout_dir) == (holdout_value, holdout_manifest)
        stable = stable and hashlib.sha256((args.holdout_dir / "approval.json").read_bytes()).hexdigest() == holdout_review_sha256
        result = score_holdout(result, holdout_value, args.holdout_dir, holdout_manifest)
        result["annotation_review_sha256"] = holdout_review_sha256
    snapshot = {name: item["source_bytes_sha256"] for name, item in modules.items()}
    result.update(
        runtime_image=inspected["Image"],
        source_stable=stable,
        exit_code=exit_code,
        harness_sha256=payload["harness_sha256"],
        module_snapshot_sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
    )
    from test.evals.technical_specifications.alignment.proxy_evidence import collect

    proxy = collect(result, args.proxy_audit_url)
    (output / "proxy-evidence.json").write_text(json.dumps(proxy, ensure_ascii=False, indent=2), encoding="utf-8")
    result["proxy_evidence_status"] = proxy["status"]
    result["qualifies"] = (
        exit_code == 0 and stable and result["quality_status"] == "PASS" and result["evidence_status"] == "VERIFIED" and proxy["status"] == "VERIFIED" and result.get("scope") == "FULL"
    )
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("quality_status", "evidence_status", "qualifies", "source_stable", "exit_code")}))
    return 0 if result["qualifies"] else 1 if result["quality_status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
