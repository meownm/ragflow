"""Acquire release evidence by immutable GitHub identity, before extraction."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import zipfile


def api(path: str, *, raw: bool = False):
    result = subprocess.run(["gh", "api", path], capture_output=True, check=True, timeout=120)
    return result.stdout if raw else json.loads(result.stdout)


def validate_artifact(artifact: dict, run: dict, jobs: list[dict], expected_name: str, producer: str = "evidence") -> None:
    origin = artifact.get("workflow_run", {})
    if (
        artifact.get("name") != expected_name
        or artifact.get("expired") is not False
        or type(artifact.get("id")) is not int
        or artifact["id"] < 1
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
        or origin.get("id") != run.get("id")
        or origin.get("head_sha") != run.get("head_sha")
        or origin.get("repository_id") != run.get("repository", {}).get("id")
        or origin.get("head_repository_id") != run.get("head_repository", {}).get("id")
        or not expected_name.endswith(f"-{run['id']}-{run['run_attempt']}")
    ):
        raise ValueError("foreign or expired GitHub artifact")
    created = datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00"))
    # Artifact API has no attempt field. Its immutable name and producer interval bind it.
    if not any(
        (job.get("name") == producer or job.get("name", "").endswith(" / " + producer))
        and job.get("run_attempt") == run["run_attempt"]
        and job.get("head_sha") == run["head_sha"]
        and job.get("conclusion") == "success"
        and job.get("completed_at")
        and datetime.fromisoformat(job["started_at"].replace("Z", "+00:00")) <= created <= datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
        for job in jobs
    ):
        raise ValueError("artifact not produced by a successful current-attempt job")


def qualification_producers(run_id: str, attempt: int) -> dict[str, str]:
    suffix = f"-{run_id}-{attempt}"
    producers = {"image": "build_image", "browser-tools": "frontend", "python": "package_python", "preflight": "ragflow_preflight", "browser-results": "browser_regression"}
    producers.update({f"engine-{engine}": f"ragflow_tests_{engine}" for engine in ("infinity", "elasticsearch")})
    producers.update({f"cli-{system}-{arch}": f"build_cli ({system}-{arch})" for system in ("linux", "darwin", "windows") for arch in ("amd64", "arm64")})
    producers.update({f"browser-result-{browser}": f"browser ({browser})" for browser in ("chromium", "firefox", "webkit")})
    return {name + suffix: producer for name, producer in producers.items()}


def api_items(path: str, key: str) -> list[dict]:
    items = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        result = api(f"{path}{separator}per_page=100&page={page}")[key]
        items.extend(result)
        if len(result) < 100:
            return items
        page += 1


def trusted_run(repository: str, run_id: str, attempt: int, revision: str) -> dict:
    run = api(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}")
    if (
        run.get("repository", {}).get("full_name") != repository
        or str(run.get("id")) != run_id
        or run.get("run_attempt") != attempt
        or run.get("head_sha") != revision
        or run.get("event") not in {"schedule", "push", "workflow_dispatch"}
        or run.get("path") not in {".github/workflows/tests.yml", ".github/workflows/release.yml"}
    ):
        raise ValueError("untrusted qualification run")
    return run


def acquire_producers(repository: str, run_id: str, attempt: int, revision: str, destination: Path) -> list[dict]:
    run = trusted_run(repository, run_id, attempt, revision)
    jobs = api_items(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    artifacts = api_items(f"repos/{repository}/actions/runs/{run_id}/artifacts", "artifacts")
    expected = qualification_producers(run_id, attempt)
    selected = []
    for name, producer in expected.items():
        matches = [item for item in artifacts if item.get("name") == name]
        if len(matches) != 1:
            raise ValueError("missing or duplicate producer artifact: " + name)
        validate_artifact(matches[0], run, jobs, name, producer)
        selected.append(matches[0])
    # Validate the entire producer set before downloading any bytes.
    for artifact in selected:
        archive = api(f"repos/{repository}/actions/artifacts/{artifact['id']}/zip", raw=True)
        extract_verified(archive, artifact["digest"], destination / artifact["name"])
    return selected


def extract_verified(archive: bytes, digest: str, destination: Path) -> None:
    if "sha256:" + hashlib.sha256(archive).hexdigest() != digest:
        raise ValueError("GitHub artifact archive digest mismatch")
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        names = set()
        for item in source.infolist():
            original = item.orig_filename
            path = PurePosixPath(original)
            normalized = path.as_posix()
            if (
                not original
                or "\\" in original
                or "\x00" in original
                or path.is_absolute()
                or ":" in original
                or ".." in path.parts
                or normalized in {"", "."}
                or original.rstrip("/") != normalized
                or normalized in names
                or ((item.external_attr >> 16) & 0o170000) == 0o120000
            ):
                raise ValueError("unsafe artifact archive entry")
            names.add(normalized)
        destination.mkdir(parents=True, exist_ok=False)
        source.extractall(destination)


def acquire(repository: str, run_id: str, attempt: int, artifact_id: int, name: str, revision: str, destination: Path) -> dict:
    run = trusted_run(repository, run_id, attempt, revision)
    jobs = api_items(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    artifact = api(f"repos/{repository}/actions/artifacts/{artifact_id}")
    validate_artifact(artifact, run, jobs, name)
    archive = api(f"repos/{repository}/actions/artifacts/{artifact_id}/zip", raw=True)
    extract_verified(archive, artifact["digest"], destination)
    return artifact


def verify_release_download(directory: Path, proof: dict) -> None:
    """Local deploy rechecks GitHub ownership and every downloaded asset digest."""
    repository = os.environ.get("RAGFLOW_RELEASE_REPOSITORY", "meownm/ragflow")
    if proof.get("repository") != repository:
        raise ValueError("release repository is not trusted")
    release = api(f"repos/{repository}/releases/tags/{proof['release_tag']}")
    if release.get("draft") or release.get("tag_name") != proof["release_tag"]:
        raise ValueError("release is not published")
    manifest_path = directory / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("bundle_id") != proof["bundle_id"] or manifest.get("qualification_sha256") != proof["qualification_sha256"]:
        raise ValueError("release manifest identity mismatch")
    required = {"candidate-receipt.json", "publication-receipt.json", "release-manifest.json"}
    assets = {item["name"]: item for item in release["assets"]}
    for name in required:
        item = assets.get(name, {})
        local = directory / name
        if not local.is_file() or item.get("digest") != "sha256:" + hashlib.sha256(local.read_bytes()).hexdigest():
            raise ValueError("release asset does not match GitHub origin: " + name)


def source_bytes(repository: str, revision: str, path: str) -> bytes:
    metadata = api(f"repos/{repository}/contents/{path}?ref={revision}")
    if metadata.get("encoding") != "base64" or metadata.get("path") != path:
        raise ValueError("qualification source file unavailable")
    return base64.b64decode(metadata["content"].replace("\n", ""), validate=True)


def verify_source_inventories(receipt: dict, packet: Path) -> None:
    """Read policy as data from the qualified source, without executing candidate code."""
    from tools.quality.api_case_evidence import ENGINES, SUITES, verify_report
    from tools.quality.browser_inventory import expected_inventory, verify_junit
    from tools.quality.release_evidence import verify_blue_green, verify_runtime_upgrade

    ci = receipt["ci"]
    repository, revision = ci["repository"], receipt["source_revision"]
    transition = json.loads((packet / "blue-green/blue-green.json").read_text())
    verify_blue_green(transition, revision, receipt["image"]["staging_digest"])
    if transition["controller_sha256"] != hashlib.sha256(source_bytes(repository, revision, "deployment/blue_green.py")).hexdigest():
        raise ValueError("blue-green controller differs from qualified source")
    recovery = verify_runtime_upgrade(json.loads((packet / "upgrade/qualified-upgrade.json").read_text()), revision, receipt["image"]["staging_digest"])
    if receipt.get("recovery") != recovery:
        raise ValueError("qualification receipt differs from verified recovery scope")
    for source, name in (
        ("test/playwright/mandatory-browser-cases.json", "browser.json"),
        ("test/mandatory-api-cases.json", "api.json"),
        ("tools/quality/release-bootstrap-baseline.json", "bootstrap-baseline.json"),
    ):
        content = source_bytes(repository, revision, source)
        if content != (packet / "source-inventories" / name).read_bytes():
            raise ValueError("qualification inventory differs from verified source snapshot")
    if expected_inventory(packet / "source-inventories/browser.json")[1] != receipt["artifacts"]["browser_inventory_sha256"]:
        raise ValueError("browser inventory differs from qualification policy")
    for browser in ("chromium", "firefox", "webkit"):
        reports = list((packet / "browser-results").rglob(browser + ".xml"))
        records = list((packet / "browser-results").rglob(browser + ".json"))
        if len(reports) != 1 or len(records) != 1:
            raise ValueError("browser executed cases missing or duplicated")
        expected = {
            "browser": browser,
            "sha": revision,
            "result": "success",
            "manifest_sha256": hashlib.sha256((packet / "browser-build.json").read_bytes()).hexdigest(),
            **verify_junit(reports[0], packet / "source-inventories/browser.json"),
        }
        if json.loads(records[0].read_text()) != expected:
            raise ValueError("browser receipt differs from executed cases")
    identity = {"repository": repository, "sha": revision, "run_id": ci["run_id"], "run_attempt": str(ci["attempt"]), "image_digest": receipt["image"]["staging_digest"].split("@", 1)[1]}
    for engine in ENGINES:
        for suite in SUITES:
            root = packet / ("engine-" + engine)
            actual = verify_report(root / (suite + "-cases.json"), suite=suite, engine=engine, identity=identity, inventory=packet / "source-inventories/api.json")
            if actual != json.loads((root / (suite + "-qualified.json")).read_text()):
                raise ValueError("qualified API summary differs from verified execution")


def verify_producer_packet(receipt: dict, packet: Path) -> None:
    """Reassemble with trusted tooling; the collector cannot substitute producer bytes."""
    from tools.quality.release_packet import assemble

    ci = receipt["ci"]
    repository, revision = ci["repository"], receipt["source_revision"]
    run_id, attempt = str(ci["run_id"]), ci["attempt"]
    run = trusted_run(repository, run_id, attempt, revision)
    jobs = api_items(f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    expected = qualification_producers(run_id, attempt)
    origins = json.loads((packet / "producer-origins.json").read_text())
    if len(origins) != len(expected) or {item["name"] for item in origins} != set(expected) or len({item["id"] for item in origins}) != len(expected):
        raise ValueError("incomplete qualification producer origins")
    # Bind every supplied origin to current GitHub metadata before fetching any archive.
    for origin in origins:
        actual = api(f"repos/{repository}/actions/artifacts/{origin['id']}")
        validate_artifact(actual, run, jobs, origin["name"], expected[origin["name"]])
        if any(origin.get(key) != actual.get(key) for key in ("id", "name", "digest", "created_at", "workflow_run")):
            raise ValueError("qualification producer origin differs from GitHub")
    with tempfile.TemporaryDirectory(prefix="ragflow-producer-proof-") as temporary:
        root = Path(temporary)
        for origin in origins:
            archive = api(f"repos/{repository}/actions/artifacts/{origin['id']}/zip", raw=True)
            extract_verified(archive, origin["digest"], root / "incoming" / origin["name"])
        for path in (
            "test/playwright/mandatory-browser-cases.json",
            "test/mandatory-api-cases.json",
            "tools/scripts/install.sh",
            "tools/scripts/install.ps1",
            "tools/quality/release-bootstrap-baseline.json",
        ):
            target = root / "source" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_bytes(repository, revision, path))
        identity = {"repository": repository, "sha": revision, "run_id": run_id, "run_attempt": str(attempt)}
        assemble(root / "incoming", root / "packet", identity=identity, source_root=root / "source", origin_path=packet / "producer-origins.json")
        actual_files = {file.relative_to(packet): file for file in packet.rglob("*") if file.is_file()}
        trusted_files = {file.relative_to(root / "packet"): file for file in (root / "packet").rglob("*") if file.is_file()}
        if set(actual_files) != set(trusted_files) or any(actual_files[name].read_bytes() != file.read_bytes() for name, file in trusted_files.items()):
            raise ValueError("qualification packet differs from verified producer artifacts")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "run-id", "sha"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--name")
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--artifact-id", type=int)
    parser.add_argument("--producers", action="store_true")
    parser.add_argument("--origin-output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.producers:
        if args.origin_output is None or args.name or args.artifact_id:
            parser.error("--producers requires --origin-output, without --name/--artifact-id")
        artifacts = acquire_producers(args.repository, args.run_id, args.attempt, args.sha, args.output)
        args.origin_output.write_text(json.dumps(artifacts, indent=2) + "\n", encoding="utf-8")
    else:
        if not args.name or args.artifact_id is None:
            parser.error("qualification acquisition requires --name and --artifact-id")
        artifact = acquire(args.repository, args.run_id, args.attempt, args.artifact_id, args.name, args.sha, args.output)
        (args.output / "artifact-origin.json").write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
