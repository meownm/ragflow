"""Record a CI image receipt and verify one local release's evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
JOBS = ("ragflow_preflight", "ragflow_tests_infinity", "ragflow_tests_elasticsearch")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def make_receipt(revision: str, repository: str, run_id: str, image: str, digest: str, jobs: dict[str, str]) -> dict:
    receipt = {
        "schema": 1,
        "source_revision": revision,
        "ci": {"repository": repository, "run_id": run_id, "jobs": jobs},
        "image": {"reference": image, "digest": digest},
    }
    validate_receipt(receipt)
    return receipt


def validate_receipt(receipt: dict) -> None:
    if not isinstance(receipt, dict) or receipt.get("schema") != 1:
        raise ValueError("candidate receipt schema must be 1")
    revision = receipt.get("source_revision")
    ci = receipt.get("ci")
    image = receipt.get("image")
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError("candidate receipt needs a full source revision")
    if not isinstance(ci, dict) or not isinstance(ci.get("repository"), str) or not ci["repository"]:
        raise ValueError("candidate receipt needs a repository")
    if not isinstance(ci.get("run_id"), str) or not ci["run_id"].isdigit():
        raise ValueError("candidate receipt needs a CI run ID")
    if not isinstance(ci.get("jobs"), dict) or set(ci["jobs"]) != set(JOBS) or any(ci["jobs"][job] != "success" for job in JOBS):
        raise ValueError("all required CI jobs must have succeeded")
    if not isinstance(image, dict) or not isinstance(image.get("reference"), str) or not isinstance(image.get("digest"), str):
        raise ValueError("candidate receipt needs image identity")
    if not image["reference"].endswith(f":{revision}"):
        raise ValueError("candidate image tag must match the tested revision")
    repository_name = image["reference"].rsplit(":", 1)[0]
    if not DIGEST.fullmatch(image["digest"]) or not image["digest"].startswith(f"{repository_name}@sha256:"):
        raise ValueError("candidate digest must identify the recorded image repository")


def verify_release(
    receipt_path: Path,
    deployment_path: Path,
    source_workbench_path: Path,
    business_documents_path: Path,
    source_candidate: Path | None = None,
) -> dict:
    """Return pass/fail/incomplete; quality thresholds remain baseline observations."""
    report: dict = {"schema": 1, "status": "incomplete", "checks": {}, "artifacts": {}, "quality_baseline": {}}
    failures: list[str] = []
    missing: list[str] = []

    def read(name: str, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("expected JSON object")
            report["artifacts"][name] = _artifact(path)
            return data
        except (OSError, ValueError) as error:
            missing.append(f"{name}: {type(error).__name__}")
            return None

    receipt = read("candidate_receipt", receipt_path)
    deployment = read("deployment", deployment_path)
    workbench = read("source_workbench", source_workbench_path)
    business = read("business_documents", business_documents_path)
    if receipt is not None:
        try:
            validate_receipt(receipt)
        except ValueError as error:
            failures.append(str(error))
        else:
            report["source_revision"] = receipt["source_revision"]
            report["image_digest"] = receipt["image"]["digest"]

    if receipt is not None and deployment is not None and not failures:
        checks = {
            "release_mode": deployment.get("mode") == "release",
            "source_revision": deployment.get("candidate_revision") == receipt["source_revision"],
            "image_reference": deployment.get("candidate_image") == receipt["image"]["reference"],
            "image_digest": receipt["image"]["digest"] in (deployment.get("candidate_repo_digests") or []),
            "receipt_hash": deployment.get("candidate_receipt_sha256", "").lower() == _sha256(receipt_path),
            "receipt_verified": deployment.get("candidate_receipt_status") == "verified",
            "api_health": isinstance(deployment.get("health"), dict) and deployment["health"].get("status") == "ok",
        }
        containers = deployment.get("containers")
        app = next((item for item in containers if isinstance(item, dict) and item.get("service") == "ragflow-cpu"), None) if isinstance(containers, list) else None
        checks["container_image"] = bool(
            app
            and isinstance(deployment.get("candidate_image_id"), str)
            and deployment["candidate_image_id"].startswith("sha256:")
            and app.get("image") == deployment["candidate_image_id"]
            and app.get("status") == "running"
            and app.get("health") in {"healthy", "none"}
        )
        backup = deployment.get("backup")
        if isinstance(backup, dict) and isinstance(backup.get("path"), str) and isinstance(backup.get("sha256"), str):
            backup_path = Path(backup["path"])
            if backup_path.is_file():
                report["artifacts"]["backup"] = _artifact(backup_path)
                checks["backup"] = _sha256(backup_path).lower() == backup["sha256"].lower()
            else:
                checks["backup"] = False
                missing.append("backup file is unavailable")
        else:
            checks["backup"] = False
            missing.append("backup evidence is unavailable")
        report["checks"] = checks
        failures.extend(name for name, passed in checks.items() if not passed and name != "backup")
        if not checks["backup"] and not any("backup" in item for item in missing):
            failures.append("backup hash mismatch")

    if receipt is not None and not failures:
        revision = receipt["source_revision"]
        if workbench is not None:
            if workbench.get("status") == "incomplete":
                missing.append("Source Workbench quality run is incomplete")
            elif (
                workbench.get("status") not in {"pass", "fail"}
                or not isinstance(workbench.get("metrics"), dict)
                or not isinstance(workbench.get("cases"), list)
                or not workbench["cases"]
                or not isinstance(workbench.get("index"), dict)
                or workbench["index"].get("state") != "ready"
                or not isinstance(workbench.get("model"), dict)
                or not isinstance(workbench.get("corpus_sha256"), str)
            ):
                failures.append("Source Workbench quality report is invalid")
            elif not isinstance(workbench["model"].get("weights"), dict) or not workbench["model"]["weights"].get("digest"):
                missing.append("Source Workbench embedding model evidence is unavailable")
            elif workbench.get("source_revision") != revision or workbench.get("source_dirty") is not False:
                missing.append("Source Workbench baseline is not from the clean candidate revision")
            else:
                report["quality_baseline"]["source_workbench"] = workbench["status"]
        if business is not None:
            if business.get("status") == "INCOMPLETE":
                missing.append("Business Documents quality run is incomplete")
            elif business.get("status") not in {"PASS", "FAIL"} or not isinstance(business.get("case_results"), list) or not business["case_results"]:
                failures.append("Business Documents quality report is invalid")
            elif not isinstance(business.get("ai"), dict) or not business["ai"].get("model") or not business["ai"].get("execution_count") or not business.get("model_weights_digest"):
                missing.append("Business Documents live model evidence is unavailable")
            elif business.get("source_revision") != revision or business.get("source_dirty") is not False:
                missing.append("Business Documents baseline is not from the candidate revision")
            else:
                report["quality_baseline"]["business_documents"] = business["status"].lower()

    if source_candidate is not None:
        try:
            from tools.quality.candidate import verify

            manifest = verify(source_candidate)
            report["artifacts"]["source_candidate"] = _artifact(source_candidate / "candidate.json")
            if receipt is not None and manifest["identity"]["head"] == receipt["source_revision"] and not manifest["identity"]["dirty"]:
                report["source_id"] = manifest["source_id"]
            else:
                missing.append("source snapshot does not represent the clean candidate revision")
        except (OSError, ValueError, KeyError) as error:
            missing.append(f"source snapshot: {type(error).__name__}")

    report["failures"] = failures
    report["missing"] = missing
    report["status"] = "fail" if failures else "incomplete" if missing else "pass"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    receipt = sub.add_parser("receipt")
    for name in ("revision", "repository", "run-id", "image", "digest", "preflight", "infinity", "elasticsearch", "output"):
        receipt.add_argument(f"--{name}", required=True)
    verify = sub.add_parser("verify")
    for name in ("receipt", "deployment", "source-workbench-report", "business-documents-report", "output"):
        verify.add_argument(f"--{name}", type=Path, required=True)
    verify.add_argument("--source-candidate", type=Path)
    args = parser.parse_args()
    if args.action == "receipt":
        try:
            data = make_receipt(
                args.revision,
                args.repository,
                args.run_id,
                args.image,
                args.digest,
                {
                    "ragflow_preflight": args.preflight,
                    "ragflow_tests_infinity": args.infinity,
                    "ragflow_tests_elasticsearch": args.elasticsearch,
                },
            )
        except ValueError as error:
            parser.error(str(error))
        destination = Path(args.output)
        status = 0
    else:
        data = verify_release(args.receipt, args.deployment, args.source_workbench_report, args.business_documents_report, args.source_candidate)
        destination = args.output
        status = {"pass": 0, "fail": 1, "incomplete": 2}[data["status"]]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": data.get("status", "created"), "output": str(destination)}))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
