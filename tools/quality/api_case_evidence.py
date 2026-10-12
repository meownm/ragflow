"""Validate API results against reviewed, explicit release case inventories.

The pytest collector records execution, never the expectation. The checked-in
inventory owns applicability; a bug skip or an unknown skip blocks qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re


INVENTORY = Path(__file__).resolve().parents[2] / "test/mandatory-api-cases.json"
SUITES = ("test_sdk_api", "restful_api", "test_web_api", "test_admin_api")
ENGINES = ("elasticsearch", "infinity")
IDENTITY_FIELDS = ("repository", "sha", "run_id", "run_attempt", "image_digest")


def expected_cases(suite: str, engine: str, inventory: Path = INVENTORY) -> tuple[set[str], dict, str]:
    raw = inventory.read_bytes()
    policy = json.loads(raw)
    if type(policy.get("schema")) is not int or policy["schema"] != 1 or policy.get("purpose") != "mandatory-release-api-cases" or policy.get("model_profile") != "local":
        raise ValueError("invalid API inventory header")
    if set(policy.get("suites", {})) != set(SUITES) or suite not in SUITES or engine not in ENGINES:
        raise ValueError("invalid API inventory suite or engine")
    if set(policy["suites"][suite]) != set(ENGINES):
        raise ValueError("API inventory lacks an engine")
    lane = policy["suites"][suite][engine]
    required, exclusions = lane.get("required"), lane.get("not_applicable")
    if not isinstance(required, list) or not required or not isinstance(exclusions, dict):
        raise ValueError("invalid API inventory cases")
    nodes = required + list(exclusions)
    prefix = f"test/testcases/{suite}/"
    if any(not isinstance(node, str) or not node.startswith(prefix) or ".py::" not in node for node in nodes):
        raise ValueError("invalid API case identity")
    if len(nodes) != len(set(nodes)):
        raise ValueError("duplicate API inventory case")
    for exclusion in exclusions.values():
        if not isinstance(exclusion, dict) or exclusion.get("kind") not in {"engine_contract", "external_provider"}:
            raise ValueError("invalid API applicability exception")
        if not all(isinstance(exclusion.get(key), str) and exclusion[key].strip() for key in ("reason", "skip_reason", "contract")):
            raise ValueError("API applicability lacks a reason or contract")
    return set(required), exclusions, hashlib.sha256(raw).hexdigest()


def verify_report(report: Path, *, suite: str, engine: str, identity: dict, inventory: Path = INVENTORY) -> dict:
    required, exclusions, inventory_hash = expected_cases(suite, engine, inventory)
    raw_report = report.read_bytes()
    data = json.loads(raw_report)
    if type(data.get("schema")) is not int or data["schema"] != 1 or data.get("purpose") != "api-case-execution":
        raise ValueError("invalid API execution report")
    if set(identity) != set(IDENTITY_FIELDS) or any(not isinstance(identity[key], str) or not identity[key] for key in IDENTITY_FIELDS):
        raise ValueError("missing trusted API identity")
    if not re.fullmatch(r"[0-9a-f]{40}", identity["sha"]) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity["image_digest"]):
        raise ValueError("invalid API source or image identity")
    if not all(re.fullmatch(r"[1-9][0-9]*", identity[key]) for key in ("run_id", "run_attempt")):
        raise ValueError("invalid API run identity")
    expected_identity = {**identity, "suite": suite, "engine": engine, "model_profile": "local"}
    if data.get("identity") != expected_identity:
        raise ValueError("API execution origin mismatch")
    if type(data.get("exit_status")) is not int or data["exit_status"] != 0 or data.get("collection_errors") != []:
        raise ValueError("API execution or collection did not succeed")
    expected = required | set(exclusions)
    collected = data.get("collected")
    cases = data.get("cases")
    if not isinstance(collected, list) or any(not isinstance(node, str) for node in collected) or len(collected) != len(set(collected)):
        raise ValueError("invalid or duplicate API collection")
    if not isinstance(cases, list) or any(not isinstance(case, dict) or not isinstance(case.get("nodeid"), str) for case in cases):
        raise ValueError("invalid API execution cases")
    actual = [case["nodeid"] for case in cases]
    if len(actual) != len(set(actual)):
        raise ValueError("duplicate API execution case")
    if set(collected) != expected or set(actual) != expected:
        raise ValueError(f"API inventory mismatch: missing={len(expected - set(actual))}, extra={len(set(actual) - expected)}")
    passed, not_applicable = 0, []
    for case in cases:
        phases = case.get("phases")
        if not isinstance(phases, list) or any(not isinstance(phase, dict) for phase in phases):
            raise ValueError("invalid API execution phases")
        phase_names = [phase.get("when") for phase in phases]
        if len(phase_names) != len(set(phase_names)) or set(phase_names) - {"setup", "call", "teardown"}:
            raise ValueError("duplicate or unknown API execution phase")
        if any(phase.get("wasxfail") or phase.get("outcome") not in {"passed", "skipped"} for phase in phases):
            raise ValueError(f"API case failed, errored or xfailed: {case['nodeid']}")
        skipped = [phase for phase in phases if phase["outcome"] == "skipped"]
        if skipped:
            exclusion = exclusions.get(case["nodeid"])
            if exclusion is None or len(skipped) != 1 or skipped[0].get("skip_reason") != exclusion["skip_reason"]:
                raise ValueError(f"API case has an unapproved skip: {case['nodeid']}")
            if "setup" not in phase_names or "teardown" not in phase_names or skipped[0]["when"] not in {"setup", "call"}:
                raise ValueError("API applicability lacks completed setup/teardown evidence")
            not_applicable.append({"nodeid": case["nodeid"], **exclusion})
        else:
            if set(phase_names) != {"setup", "call", "teardown"}:
                raise ValueError(f"API case has incomplete execution: {case['nodeid']}")
            passed += 1
    return {
        "status": "PASS",
        "suite": suite,
        "engine": engine,
        "tests": len(actual),
        "passed": passed,
        "not_applicable": not_applicable,
        "inventory_sha256": inventory_hash,
        "report_sha256": hashlib.sha256(raw_report).hexdigest(),
        "identity": expected_identity,
    }


class CaseEvidencePlugin:
    """Record exact pytest nodeids and all phases without response bodies or secrets."""

    def __init__(self, output: Path, suite: str, config):
        self.output = output
        self.identity = {
            "repository": os.getenv("GITHUB_REPOSITORY", ""),
            "sha": os.getenv("GITHUB_SHA", ""),
            "run_id": os.getenv("GITHUB_RUN_ID", ""),
            "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
            "image_digest": os.getenv("RAGFLOW_TEST_IMAGE_DIGEST", ""),
            "suite": suite,
            "engine": os.getenv("DOC_ENGINE", ""),
            "model_profile": config.getoption("--model-profile"),
        }
        self.collected = []
        self.collection_errors = []
        self.cases = {}

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        phase = {"when": report.when, "outcome": report.outcome, "wasxfail": bool(getattr(report, "wasxfail", False))}
        if report.skipped:
            phase["skip_reason"] = report.longrepr[2] if isinstance(report.longrepr, tuple) else "unknown skip"
            if phase["skip_reason"].startswith("Skipped: "):
                phase["skip_reason"] = phase["skip_reason"][len("Skipped: ") :]
        self.cases.setdefault(report.nodeid, []).append(phase)

    def pytest_sessionfinish(self, session, exitstatus):
        data = {
            "schema": 1,
            "purpose": "api-case-execution",
            "identity": self.identity,
            "exit_status": int(exitstatus),
            "collection_errors": self.collection_errors,
            "collected": self.collected,
            "cases": [{"nodeid": node, "phases": phases} for node, phases in self.cases.items()],
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(self.output.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self.output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--engine", choices=ENGINES, required=True)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--output", type=Path)
    for field in IDENTITY_FIELDS:
        parser.add_argument("--" + field.replace("_", "-"), required=True)
    args = parser.parse_args()
    try:
        result = verify_report(args.report, suite=args.suite, engine=args.engine, inventory=args.inventory, identity={field: getattr(args, field) for field in IDENTITY_FIELDS})
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if args.output:
            args.output.unlink(missing_ok=True)
        print(f"INCOMPLETE: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
