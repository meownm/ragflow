"""GitHub and artifact adapters for the release evidence policy."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

from tools.quality.browser_inventory import expected_inventory
from tools.quality.release_origin import api_items
from tools.quality.release_evidence import (
    RELEASE_LANES,
    bundle_identity,
    file_manifest,
    make_release_receipt,
    require_publishable,
    validate_receipt,
    validate_publication,
    verify_branch_coverage,
    verify_ci_origin,
    verify_files,
    verify_runtime_upgrade,
)


def github(path: str, method: str = "GET"):
    result = subprocess.run(["gh", "api", "--method", method, path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def runners_ready(runners: list[dict], required: list[str]) -> None:
    for label in required:
        expected = {"self-hosted", "Linux", "ragflow-isolated-vm", label}
        if not any(runner.get("status") == "online" and expected <= {item["name"] for item in runner["labels"]} for runner in runners):
            raise ValueError(f"INCOMPLETE: no online runner with label {label}")


def queue_reason(job: dict, runners: list[dict], now: datetime) -> str | None:
    if job.get("status") != "queued" or not job.get("created_at"):
        return None
    labels = set(job.get("labels", []))
    if "self-hosted" not in labels:
        return None
    # Environment approval and concurrency use waiting/pending, respectively.
    # Only jobs queued for runner assignment are subject to this deadline.
    matching = [runner for runner in runners if labels <= {item["name"] for item in runner["labels"]}]
    online = [runner for runner in matching if runner.get("status") == "online"]
    elapsed = (now - datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))).total_seconds()
    if not online and elapsed > 900:
        return "runner-offline-over-15-minutes"
    if online and all(runner.get("busy") is True for runner in online) and elapsed > 7200:
        return "runner-queue-over-two-hours"
    return None


def watchdog(repository: str) -> None:
    runners = api_items(f"repos/{repository}/actions/runners", "runners")
    runs = {run["id"]: run for status in ("queued", "in_progress") for run in api_items(f"repos/{repository}/actions/runs?status={status}", "workflow_runs")}
    for run in runs.values():
        if run["path"] not in {".github/workflows/tests.yml", ".github/workflows/release.yml"} or run["status"] not in {"queued", "in_progress"}:
            continue
        jobs = api_items(f"repos/{repository}/actions/runs/{run['id']}/jobs", "jobs")
        reasons = [reason for job in jobs if (reason := queue_reason(job, runners, datetime.now(timezone.utc)))]
        if reasons:
            print(json.dumps({"run_id": run["id"], "status": "INCOMPLETE", "reasons": sorted(set(reasons))}))
            github(f"repos/{repository}/actions/runs/{run['id']}/cancel", "POST")


def read_models(url: str) -> list[dict]:
    request = urllib.request.Request(url.rstrip("/") + "/api/tags")
    with urllib.request.urlopen(request, timeout=20) as response:
        tags = json.load(response)["models"]
    models = []
    for name in ("qwen3.8:latest", "qwen3.6:27b", "bge-m3:latest"):
        found = [model for model in tags if model.get("name") == name]
        if len(found) != 1 or not found[0].get("digest"):
            raise ValueError(f"required model unavailable: {name}")
        models.append({"name": name, "digest": found[0]["digest"]})
    return models


def spa_manifest(root: Path, revision: str, image_digest: str) -> dict:
    files = file_manifest(root)
    if "index.html" not in files:
        raise ValueError("SPA lacks index.html")
    return {"schema": 2, "purpose": "release-image-spa", "sha": revision, "image_digest": image_digest, "files": files}


def validate_spa(manifest: dict, root: Path, revision: str, image_digest: str) -> None:
    if manifest != spa_manifest(root, revision, image_digest):
        raise ValueError("browser SPA is not from the qualified image")


def record_browser(report: Path, manifest: Path, browser: str, revision: str) -> dict:
    from tools.quality.browser_inventory import verify_junit

    return {"browser": browser, "sha": revision, "result": "success", "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), **verify_junit(report)}


def validate_browsers(root: Path, manifest: Path, revision: str) -> None:
    expected, digest = expected_inventory()
    actual = list(root.rglob("*.json"))
    if len(actual) != 3:
        raise ValueError("missing or extra browser receipts")
    seen = set()
    for path in actual:
        receipt = json.loads(path.read_text())
        browser = receipt.get("browser")
        if browser not in {"chromium", "firefox", "webkit"} or browser in seen:
            raise ValueError("invalid browser receipt set")
        seen.add(browser)
        if receipt != {
            "browser": browser,
            "sha": revision,
            "result": "success",
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "inventory_sha256": digest,
            "tests": len(expected),
        }:
            raise ValueError("browser receipt identity mismatch")


def write_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_qualification_origin(receipt: dict, packet: Path) -> None:
    from tools.quality.release_origin import verify_producer_packet, verify_source_inventories

    require_publishable(receipt)
    verify_files(packet, receipt["artifacts"]["files"])
    verify_source_inventories(receipt, packet)
    verify_producer_packet(receipt, packet)
    ci = receipt["ci"]
    run = github(f"repos/{ci['repository']}/actions/runs/{ci['run_id']}/attempts/{ci['attempt']}")
    jobs = api_items(f"repos/{ci['repository']}/actions/runs/{ci['run_id']}/attempts/{ci['attempt']}/jobs", "jobs")
    verify_ci_origin(receipt, run, jobs)


def create_receipt(packet: Path, needs: dict, profile: str) -> dict:
    if profile == "release":
        verify_branch_coverage(packet / "preflight/coverage-business-documents.xml")
    build = json.loads((packet / "image-build.json").read_text())
    revision = os.environ["SOURCE_SHA"]
    if build["sha"] != revision:
        raise ValueError("image build source mismatch")
    from tools.quality.runtime_image_lock import verify_lock

    content = (packet / "runtime-images.json").read_bytes()
    runtime = verify_lock(
        content,
        hashlib.sha256(content).hexdigest(),
        {"repository": os.environ["GITHUB_REPOSITORY"], "sha": revision, "run_id": os.environ["GITHUB_RUN_ID"], "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])},
        build["digest"],
    )
    build["toolchain"]["runtime"] = hashlib.sha256(json.dumps(runtime["services"], sort_keys=True).encode()).hexdigest()
    upgrade = json.loads((packet / "upgrade/qualified-upgrade.json").read_text())
    recovery = verify_runtime_upgrade(upgrade, revision, build["digest"])
    native = json.loads((packet / "native/native-image.json").read_text())
    if native.get("status") != "PASS" or native.get("source_sha") != revision or native.get("image_digest") != build["digest"]:
        raise ValueError("native final-image qualification missing or foreign")
    spa = packet / "browser-build.json"
    spa_data = json.loads(spa.read_text())
    if spa_data.get("purpose") != "release-image-spa" or spa_data.get("sha") != revision or spa_data.get("image_digest") != build["digest"]:
        raise ValueError("SPA manifest source/image mismatch")
    inventory_digest = expected_inventory()[1]
    platform_files = list((packet / "cli").glob("ragflow-cli-*"))
    platforms = [path.name.removeprefix("ragflow-cli-").removesuffix(".exe") for path in platform_files]
    wheels = list((packet / "python").glob("*.whl"))
    packages = [path.name.split("-", 1)[0].replace("_", "-") for path in wheels]
    models = []
    for engine in ("infinity", "elasticsearch"):
        result = json.loads((packet / f"engine-{engine}.json").read_text())
        if result["sha"] != revision or result["image_digest"] != build["digest"] or result["engine"] != engine:
            raise ValueError("engine evidence source/image mismatch")
        if not models:
            models = result["models"]
        elif models != result["models"]:
            raise ValueError("model weights changed between qualification lanes")
    validate_browsers(packet / "browser-results", spa, revision)
    jobs = {name: needs.get(name, {}).get("result", "skipped") for name in RELEASE_LANES}
    target = os.environ["RELEASE_IMAGE_REPOSITORY"]
    tag = revision + "-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    receipt = make_release_receipt(
        revision,
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_RUN_ID"],
        int(os.environ["GITHUB_RUN_ATTEMPT"]),
        {"reference": f"{target}:{tag}", "digest": target + "@" + build["digest"].split("@", 1)[1], "staging_digest": build["digest"]},
        jobs,
        {
            "files": file_manifest(packet),
            "spa_sha256": hashlib.sha256(spa.read_bytes()).hexdigest(),
            "browser_inventory_sha256": inventory_digest,
            "cli_platforms": platforms,
            "python_packages": packages,
        },
        build["toolchain"],
        models,
        profile=profile,
    )
    receipt["recovery"] = recovery
    if os.environ.get("EXPECTED_BUNDLE_ID") and bundle_identity(receipt) != os.environ["EXPECTED_BUNDLE_ID"]:
        raise ValueError("replay changed qualified product bytes, tools or models")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ready", "watchdog", "models", "spa", "verify-spa", "browser", "verify-browsers", "receipt", "validate", "verify-origin"))
    parser.add_argument("--path", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sha", default=os.getenv("SOURCE_SHA"))
    parser.add_argument("--image-digest", default=os.getenv("IMAGE_DIGEST"))
    parser.add_argument("--browser")
    parser.add_argument("--profile", default="release")
    parser.add_argument("--roles", nargs="+", choices=("ragflow-test", "ragflow-release"), default=["ragflow-test"])
    parser.add_argument("--published", action="store_true")
    args = parser.parse_args()
    repository = os.getenv("GITHUB_REPOSITORY", "meownm/ragflow")
    data = None
    if args.action == "ready":
        runners = api_items(f"repos/{repository}/actions/runners", "runners")
        runners_ready(runners, args.roles)
    elif args.action == "watchdog":
        watchdog(repository)
    elif args.action == "models":
        data = read_models(os.environ["RAGFLOW_TEST_OLLAMA_URL"])
    elif args.action == "spa":
        data = spa_manifest(args.path, args.sha, args.image_digest)
    elif args.action == "verify-spa":
        validate_spa(json.loads(args.manifest.read_text()), args.path, args.sha, args.image_digest)
    elif args.action == "browser":
        data = record_browser(args.path, args.manifest, args.browser, args.sha)
    elif args.action == "verify-browsers":
        validate_browsers(args.path, args.manifest, args.sha)
    elif args.action == "receipt":
        data = create_receipt(args.path, json.loads(os.environ["NEEDS_JSON"]), args.profile)
        if args.profile == "release":
            require_publishable(data)
    else:
        data = json.loads(args.path.read_text(encoding="utf-8-sig"))
        validate_receipt(data)
        if args.published:
            proof = json.loads(args.path.with_name("publication-receipt.json").read_text())
            validate_publication(data, proof, hashlib.sha256(args.path.read_bytes()).hexdigest())
            from tools.quality.release_origin import verify_release_download

            verify_release_download(args.path.parent, proof)
        if args.action == "verify-origin":
            verify_qualification_origin(data, args.manifest)
    if args.output and data is not None:
        write_json(args.output, data)
    print(json.dumps({"status": "verified", "action": args.action}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
