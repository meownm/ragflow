"""Record a CI image receipt and verify one local release's evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
JOBS = ("ragflow_preflight", "ragflow_tests_infinity", "ragflow_tests_elasticsearch")
RELEASE_LANES = (
    "ragflow_preflight",
    "frontend",
    "build_image",
    "ragflow_tests_infinity",
    "ragflow_tests_elasticsearch",
    "browser_regression",
    "package_python",
    "build_cli",
)
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
CLI_PLATFORMS = {f"{os}-{arch}" for os in ("linux", "darwin", "windows") for arch in ("amd64", "arm64")}
REQUIRED_MODELS = {"qwen3.8:latest", "qwen3.6:27b", "bge-m3:latest"}
BOOTSTRAP_BASELINE = Path(__file__).with_name("release-bootstrap-baseline.json")
ALIGNMENT_ROOT = Path(__file__).resolve().parents[2]
ALIGNMENT_IMPLEMENTATION = (
    "api/technical_specifications/alignment.py",
    "api/technical_specifications/alignment_ai.py",
    "api/technical_specifications/alignment_budget.py",
    "api/technical_specifications/domain.py",
    "test/evals/technical_specifications/alignment/evaluate.py",
    "test/evals/technical_specifications/alignment/metrics.py",
    "test/evals/technical_specifications/alignment/evidence.py",
    "test/evals/technical_specifications/alignment/real_limits.py",
    "test/evals/technical_specifications/alignment/experiments.py",
    "test/evals/technical_specifications/alignment/holdout.py",
    "test/evals/technical_specifications/alignment/nightly.py",
    "test/evals/technical_specifications/alignment/proxy_evidence.py",
    "test/evals/technical_specifications/alignment/run_live.py",
)


class AlignmentEvidenceIncomplete(ValueError):
    """A report cannot establish qualification of the candidate bytes."""


def alignment_evidence_identity(root: Path = ALIGNMENT_ROOT) -> dict[str, str]:
    """Hash the actual corpus and bounded evaluator implementation, not case counts."""
    files = {name: _sha256(root / name) for name in ALIGNMENT_IMPLEMENTATION}
    return {
        "corpus_sha256": _sha256(root / "test/evals/technical_specifications/alignment/corpus.py"),
        "implementation_sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }


def verify_alignment_evidence(report: dict, revision: str, image_digest: str) -> str:
    """Recompute the full live corpus; summaries and diagnostic runs cannot qualify."""
    if (
        report.get("source_revision") != revision
        or report.get("source_dirty") is not False
        or report.get("image_digest") != image_digest
        or any(report.get(key) != value for key, value in alignment_evidence_identity().items())
    ):
        raise AlignmentEvidenceIncomplete("Technical Specifications evidence does not identify the clean candidate bytes")
    from test.evals.technical_specifications.alignment.evidence import EvidenceIncomplete, verify_execution

    try:
        return verify_execution(report)
    except EvidenceIncomplete as error:
        raise AlignmentEvidenceIncomplete(str(error)) from error


def verify_branch_coverage(report: Path, minimum: int = 79) -> dict:
    coverage = ET.parse(report).getroot()
    valid = int(coverage.get("branches-valid", "0"))
    covered = int(coverage.get("branches-covered", "-1"))
    if valid < 1 or not 0 <= covered <= valid or covered * 100 < minimum * valid:
        raise ValueError("Business Documents branch coverage below mandatory 79%")
    return {"branches_valid": valid, "branches_covered": covered, "minimum": minimum}


def verify_blue_green(report: dict, revision: str, image_digest: str) -> None:
    """Reject missing, failed, foreign or partial HTTP transition qualification."""
    if (
        report.get("schema") != 1
        or report.get("purpose") != "blue-green-http-transition"
        or report.get("status") != "PASS"
        or report.get("cleanup") != "PASS"
        or report.get("source_sha") != revision
        or report.get("image_digest") != image_digest
        or type(report.get("requests")) is not int
        or report["requests"] < 2
        or type(report.get("failed_requests")) is not int
        or report["failed_requests"] != 0
        or any(report.get(key) is not True for key in ("sse_drain", "old_assets", "unready_rejected", "gateway_unchanged", "post_switch_rollback", "interrupted_recovery"))
        or not isinstance(report.get("controller_sha256"), str)
        or not HEX_DIGEST.fullmatch(report["controller_sha256"])
    ):
        raise ValueError("blue-green HTTP transition qualification missing, incomplete or foreign")


def verify_runtime_upgrade(report: dict, revision: str, image_digest: str) -> dict:
    if (
        report.get("status") != "PASS"
        or report.get("cleanup") != "PASS"
        or report.get("source_sha") != revision
        or report.get("image_digest") != image_digest
        or not isinstance(report.get("previous_source_sha"), str)
        or not SHA.fullmatch(report["previous_source_sha"])
        or not isinstance(report.get("previous_image_digest"), str)
        or not DIGEST.fullmatch(report["previous_image_digest"])
        or any(report.get(check) is not True for check in ("repeated_initializer", "candidate_restore", "previous_restore", "previous_reads_upgraded"))
    ):
        raise ValueError("previous qualified runtime migration/restore incomplete")
    if report.get("purpose") == "initial-runtime-baseline-upgrade":
        content = BOOTSTRAP_BASELINE.read_bytes()
        baseline = json.loads(content)
        historical = report.get("historical_baseline", {})
        if (
            report.get("bootstrap_baseline_sha256") != hashlib.sha256(content).hexdigest()
            or report.get("previous_image_digest") != baseline["image_digest"]
            or report.get("previous_source_sha") != baseline["source_sha"]
            or historical.get("repository") != baseline["repository"]
            or historical.get("source_sha") != baseline["source_sha"]
            or historical.get("image_digest") != baseline["image_digest"]
            or historical.get("qualified") is not False
            or historical.get("github_commit_verified") is not True
            or historical.get("candidate_ancestor_verified") is not True
            or "previous_receipt_sha256" in report
        ):
            raise ValueError("unapproved initial runtime baseline")
    elif report.get("purpose") != "previous-qualified-image-upgrade" or not isinstance(report.get("previous_receipt_sha256"), str) or not HEX_DIGEST.fullmatch(report["previous_receipt_sha256"]):
        raise ValueError("previous qualified runtime migration/restore incomplete")
    summary = {
        "kind": "initial-bootstrap" if report["purpose"] == "initial-runtime-baseline-upgrade" else "previous-qualified-release",
        "previous_source_sha": report["previous_source_sha"],
        "previous_image_digest": report["previous_image_digest"],
        "previous_qualification": "historical-unqualified" if report["purpose"] == "initial-runtime-baseline-upgrade" else "qualified",
        "scope": "Synthetic PostgreSQL rows and backup restoration using actual image model initializers; no object store or runtime API",
    }
    if "bootstrap_baseline_sha256" in report:
        summary["baseline_policy_sha256"] = report["bootstrap_baseline_sha256"]
    return summary


def bundle_identity(receipt: dict) -> str:
    """Product identity is independent of qualification and publication retries."""
    data = {
        "source": receipt["source_revision"],
        "image": receipt["image"]["staging_digest"].split("@", 1)[1],
        "spa": receipt["artifacts"]["spa_sha256"],
        "packages": {name: digest for name, digest in receipt["artifacts"]["files"].items() if name.startswith(("cli/", "python/", "installers/"))},
        "toolchain": receipt["toolchain"],
        "models": sorted(receipt["models"], key=lambda item: item["name"]),
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_publication(receipt: dict, proof: dict, receipt_sha256: str) -> None:
    require_publishable(receipt)
    if (
        not isinstance(proof, dict)
        or proof.get("schema") != 2
        or proof.get("status") != "PASS"
        or proof.get("qualification_sha256") != receipt_sha256
        or proof.get("bundle_id") != bundle_identity(receipt)
        or proof.get("repository") != receipt["ci"]["repository"]
        or proof.get("image_digest") != receipt["image"]["digest"]
        or not isinstance(proof.get("release_tag"), str)
    ):
        raise ValueError("publication proof does not identify qualified bundle")
    origin = proof.get("qualification_artifact", {})
    ci = receipt["ci"]
    if (
        type(origin.get("id")) is not int
        or origin["id"] < 1
        or origin.get("name") != f"qualification-{ci['run_id']}-{ci['attempt']}"
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", origin.get("digest", ""))
        or str(origin.get("workflow_run", {}).get("id")) != ci["run_id"]
        or origin.get("workflow_run", {}).get("head_sha") != receipt["source_revision"]
    ):
        raise ValueError("publication proof lacks qualification artifact origin")


def file_manifest(root: Path) -> dict[str, str]:
    """Hash a closed artifact tree; links must never escape the evidence packet."""
    if not root.is_dir():
        raise ValueError(f"artifact directory unavailable: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in artifact packet")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = _sha256(path)
    if not files:
        raise ValueError("artifact packet is empty")
    return files


def verify_files(root: Path, expected: dict[str, str]) -> None:
    if file_manifest(root) != expected:
        raise ValueError("artifact file set or digest mismatch")


def qualification_status(jobs: dict[str, str], required=RELEASE_LANES) -> str:
    results = [jobs.get(name) for name in required]
    if "failure" in results:
        return "FAIL"
    if "cancelled" in results:
        return "CANCELLED"
    return "PASS" if results and all(result == "success" for result in results) else "INCOMPLETE"


def make_release_receipt(revision: str, repository: str, run_id: str, attempt: int, image: dict, jobs: dict, artifacts: dict, toolchain: dict, models: list[dict], *, profile: str = "release") -> dict:
    receipt = {
        "schema": 2,
        "source_revision": revision,
        "status": qualification_status(jobs) if profile == "release" else "INCOMPLETE",
        "ci": {"repository": repository, "run_id": run_id, "attempt": attempt, "profile": profile, "required_lanes": list(RELEASE_LANES), "jobs": jobs},
        "image": image,
        "artifacts": artifacts,
        "toolchain": toolchain,
        "models": models,
    }
    validate_receipt(receipt)
    return receipt


def _validate_release_receipt(receipt: dict) -> None:
    ci, image = receipt.get("ci"), receipt.get("image")
    if not isinstance(receipt.get("source_revision"), str) or not SHA.fullmatch(receipt["source_revision"]):
        raise ValueError("receipt needs a full source revision")
    if not isinstance(ci, dict) or not isinstance(ci.get("repository"), str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", ci["repository"]):
        raise ValueError("receipt needs repository identity")
    if not isinstance(ci.get("run_id"), str) or not ci["run_id"].isdigit() or not ci["run_id"].strip("0"):
        raise ValueError("receipt needs a CI run ID")
    if type(ci.get("attempt")) is not int or ci["attempt"] < 1 or ci.get("profile") not in {"targeted", "release"}:
        raise ValueError("invalid qualification attempt or profile")
    if ci.get("required_lanes") != list(RELEASE_LANES) or not isinstance(ci.get("jobs"), dict):
        raise ValueError("qualification policy mismatch")
    if set(ci["jobs"]) != set(RELEASE_LANES) or any(not isinstance(result, str) or result not in {"success", "failure", "cancelled", "skipped"} for result in ci["jobs"].values()):
        raise ValueError("invalid qualification lane set")
    expected = qualification_status(ci["jobs"]) if ci["profile"] == "release" else "INCOMPLETE"
    if receipt.get("status") != expected:
        raise ValueError("qualification status mismatch")
    if not isinstance(image, dict):
        raise ValueError("missing image identity")
    if "published" in image:
        raise ValueError("qualification receipt must not contain mutable publication state")
    for key in ("digest", "staging_digest"):
        if not isinstance(image.get(key), str) or not DIGEST.fullmatch(image[key]):
            raise ValueError("invalid image digest")
    if image["digest"].split("@", 1)[1] != image["staging_digest"].split("@", 1)[1]:
        raise ValueError("promotion changed image digest")
    tag = receipt["source_revision"] + "-" + ci["run_id"] + "-" + str(ci["attempt"])
    if not isinstance(image.get("reference"), str) or not image["reference"].endswith(":" + tag):
        raise ValueError("image reference does not identify source revision")
    if image["digest"].split("@", 1)[0] != image["reference"].rsplit(":", 1)[0]:
        raise ValueError("published image repository mismatch")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"files", "spa_sha256", "browser_inventory_sha256", "cli_platforms", "python_packages"}:
        raise ValueError("incomplete artifact inventory")
    if (
        not isinstance(artifacts["cli_platforms"], list)
        or not all(isinstance(platform, str) for platform in artifacts["cli_platforms"])
        or set(artifacts["cli_platforms"]) != CLI_PLATFORMS
        or len(artifacts["cli_platforms"]) != 6
    ):
        raise ValueError("incomplete CLI platform matrix")
    if (
        not isinstance(artifacts["python_packages"], list)
        or not all(isinstance(package, str) for package in artifacts["python_packages"])
        or set(artifacts["python_packages"]) != {"ragflow-sdk", "ragflow-cli"}
        or len(artifacts["python_packages"]) != 2
    ):
        raise ValueError("incomplete Python package matrix")
    for key in ("spa_sha256", "browser_inventory_sha256"):
        if not isinstance(artifacts.get(key), str) or not HEX_DIGEST.fullmatch(artifacts[key]):
            raise ValueError("invalid browser evidence digest")
    files = artifacts.get("files")
    if (
        not isinstance(files, dict)
        or not files
        or any(
            not isinstance(name, str) or not name or "\\" in name or name.startswith("/") or ".." in name.split("/") or not isinstance(digest, str) or not HEX_DIGEST.fullmatch(digest)
            for name, digest in files.items()
        )
    ):
        raise ValueError("invalid artifact file manifest")
    toolchain = receipt.get("toolchain")
    if not isinstance(toolchain, dict) or any(not isinstance(toolchain.get(key), str) or not toolchain[key] for key in ("python", "go", "node", "native", "runtime")):
        raise ValueError("missing toolchain provenance")
    models = receipt.get("models")
    if (
        not isinstance(models, list)
        or not models
        or any(
            not isinstance(model, dict)
            or not isinstance(model.get("name"), str)
            or not model["name"]
            or not isinstance(model.get("digest"), str)
            or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", model["digest"])
            for model in models
        )
    ):
        raise ValueError("missing model weight identity")
    if len({model["name"] for model in models}) != len(models):
        raise ValueError("duplicate model weight identity")
    if {model["name"] for model in models} != REQUIRED_MODELS:
        raise ValueError("required model inventory mismatch")


def require_publishable(receipt: dict) -> None:
    validate_receipt(receipt)
    if receipt.get("schema") != 2 or receipt.get("status") != "PASS" or receipt["ci"]["profile"] != "release":
        raise ValueError("publication requires complete schema 2 release qualification")


def verify_ci_origin(receipt: dict, run: dict, jobs: list[dict]) -> None:
    """Check live GitHub metadata fetched by the trusted publisher."""
    require_publishable(receipt)
    ci = receipt["ci"]
    if (
        str(run.get("id")) != ci["run_id"]
        or run.get("run_attempt") != ci["attempt"]
        or run.get("head_sha") != receipt["source_revision"]
        or run.get("repository", {}).get("full_name") != ci["repository"]
        or run.get("path") not in {".github/workflows/release.yml", ".github/workflows/tests.yml"}
        or run.get("event") not in {"schedule", "push", "workflow_dispatch"}
    ):
        raise ValueError("foreign CI run, attempt, source, repository or event")
    for lane in RELEASE_LANES:
        matching = [job for job in jobs if job.get("name", "").split(" / ")[-1].split(" (")[0] == lane]
        expected_count = 6 if lane == "build_cli" else 1
        if lane == "build_cli" and {job.get("name", "").split(" / ")[-1] for job in matching} != {f"build_cli ({platform})" for platform in CLI_PLATFORMS}:
            raise ValueError("CI CLI platform identity mismatch")
        if len(matching) != expected_count or any(
            job.get("conclusion") != "success" or job.get("head_sha") != receipt["source_revision"] or job.get("run_attempt") != ci["attempt"] for job in matching
        ):
            raise ValueError(f"CI lane not verified: {lane}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def make_receipt(
    revision: str, repository: str, run_id: str, image: str, digest: str, jobs: dict[str, str], *, engine_tests_required: bool = True, engine_test_reason: str = "engine-boundary-changed"
) -> dict:
    receipt = {
        "schema": 1,
        "source_revision": revision,
        "ci": {"repository": repository, "run_id": run_id, "jobs": jobs, "engine_tests_required": engine_tests_required, "engine_test_reason": engine_test_reason},
        "image": {"reference": image, "digest": digest},
    }
    validate_receipt(receipt)
    return receipt


def validate_receipt(receipt: dict) -> None:
    if isinstance(receipt, dict) and receipt.get("schema") == 2:
        _validate_release_receipt(receipt)
        return
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
    required = ci.get("engine_tests_required", True)
    if type(required) is not bool or (not required and ci.get("engine_test_reason") != "no-engine-changes"):
        raise ValueError("candidate receipt needs an explicit engine selection")
    expected = {JOBS[0]: "success", **{job: "success" if required else "skipped" for job in JOBS[1:]}}
    if ci.get("jobs") != expected:
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
    *,
    technical_specifications_path: Path | None = None,
) -> dict:
    """Return pass/fail/incomplete; quality thresholds remain baseline observations."""
    report: dict = {"schema": 1, "status": "incomplete", "checks": {}, "artifacts": {}, "quality_baseline": {}}
    failures: list[str] = []
    missing: list[str] = []

    def read(name: str, path: Path) -> dict | None:
        try:

            def unique_pairs(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise ValueError("duplicate quality evidence key")
                    value[key] = item
                return value

            def invalid_constant(_value):
                raise ValueError("non-finite quality evidence value")

            options = {"object_pairs_hook": unique_pairs, "parse_constant": invalid_constant} if name == "technical_specifications" else {}
            data = json.loads(path.read_text(encoding="utf-8-sig"), **options)
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
    technical = read("technical_specifications", technical_specifications_path) if technical_specifications_path is not None else None
    if technical_specifications_path is None:
        missing.append("Technical Specifications quality report is required")
    if receipt is not None:
        try:
            validate_receipt(receipt)
            if receipt.get("schema") == 2:
                require_publishable(receipt)
                publication = read("publication", receipt_path.with_name("publication-receipt.json"))
                if publication is None:
                    raise ValueError("publication proof unavailable")
                validate_publication(receipt, publication, _sha256(receipt_path))
        except ValueError as error:
            failures.append(str(error))
        else:
            report["source_revision"] = receipt["source_revision"]
            report["image_digest"] = receipt["image"]["digest"]

    if receipt is not None and deployment is not None and not failures:
        checks = {
            "release_mode": deployment.get("mode") == "release",
            "source_revision": deployment.get("candidate_revision") == receipt["source_revision"],
            "image_reference": deployment.get("candidate_image") == receipt["image"]["reference"] and (receipt.get("schema") != 2 or deployment.get("deployment_image") == receipt["image"]["digest"]),
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
        if technical is not None:
            try:
                quality = verify_alignment_evidence(technical, revision, receipt["image"]["digest"])
            except AlignmentEvidenceIncomplete as error:
                missing.append(str(error))
            except (ValueError, KeyError, TypeError, OSError):
                failures.append("Technical Specifications quality report is invalid")
            else:
                report["quality_baseline"]["technical_specifications"] = quality
                if quality != "pass":
                    failures.append("Technical Specifications quality gate failed")

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
    report["evidence_status"] = report["status"]
    observed_quality = report["quality_baseline"]
    report["quality_status"] = (
        "incomplete" if set(observed_quality) != {"source_workbench", "business_documents", "technical_specifications"} else "fail" if "fail" in observed_quality.values() else "pass"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    receipt = sub.add_parser("receipt")
    for name in ("revision", "repository", "run-id", "image", "digest", "preflight", "infinity", "elasticsearch", "output"):
        receipt.add_argument(f"--{name}", required=True)
    receipt.add_argument("--engine-tests-required", choices=("true", "false"), default="true")
    receipt.add_argument("--engine-test-reason", default="engine-boundary-changed")
    verify = sub.add_parser("verify")
    for name in ("receipt", "deployment", "source-workbench-report", "business-documents-report", "technical-specifications-report", "output"):
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
                engine_tests_required=args.engine_tests_required == "true",
                engine_test_reason=args.engine_test_reason,
            )
        except ValueError as error:
            parser.error(str(error))
        destination = Path(args.output)
        status = 0
    else:
        data = verify_release(
            args.receipt, args.deployment, args.source_workbench_report, args.business_documents_report, args.source_candidate, technical_specifications_path=args.technical_specifications_report
        )
        destination = args.output
        status = {"pass": 0, "fail": 1, "incomplete": 2}[data["status"]]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": data.get("status", "created"), "output": str(destination)}))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
