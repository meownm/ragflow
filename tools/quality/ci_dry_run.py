"""Exercise CI artifact transport and the deployment controller without product suites.

The packet is deliberately not a release receipt. Its image contains startup/schema
source for the controller protocol, Python and nginx; application dependencies,
databases, workers and models are not installed or started.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
from uuid import uuid4

from tools.quality.artifact_lifecycle import current, managed, note

ROOT = Path(__file__).resolve().parents[2]
PURPOSE = "ci-infrastructure-dry-run"
LABEL = "org.ragflow.ci-dry-run"
STARTUP = ("docker/entrypoint.sh", "api/ragflow_server.py", "admin/server/admin_server.py")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity() -> dict:
    revision = os.environ.get("SOURCE_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full source SHA")
    if os.environ.get("GITHUB_SHA", revision) != revision:
        raise ValueError("Source SHA differs from workflow SHA")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if checkout != revision:
            raise ValueError("Checkout differs from workflow SHA")
    return {
        "sha": revision,
        "repository": os.environ.get("GITHUB_REPOSITORY", "local"),
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
    }


def schema_paths(root: Path) -> set[str]:
    paths = {"api/db/init_data.py", "api/db/db_utils.py", "api/db/__init__.py"}
    for base in ("api", "admin"):
        paths.update(p.relative_to(root).as_posix() for p in (root / base).rglob("*.py") if "models" in p.stem or "migration" in p.relative_to(root).as_posix())
    return paths


def source_files(root: Path) -> dict:
    paths = {
        *STARTUP,
        *schema_paths(root),
        "deployment/blue_green.py",
        "deployment/runner/test-blue-green.py",
        "deployment/runner/ci-dry-run.Dockerfile",
        "test/integration/fixtures/blue_green_http.py",
        "tools/quality/ci_dry_run.py",
        "tools/quality/check_release_workflows.py",
        "tools/quality/artifact_lifecycle.py",
        "tools/quality/artifact_docker.py",
        "tools/quality/artifact_paths.py",
        "tools/quality/artifact_blobs.py",
        "tools/quality/artifact_registry.py",
        "tools/quality/artifact_retention.py",
        "tools/quality/artifact_policy.py",
        "tools/quality/check_cache.py",
        "tools/quality/candidate.py",
        ".github/actionlint.yaml",
    }
    paths.update(p.relative_to(root).as_posix() for p in (root / ".github/workflows").glob("*.yml"))
    for name in paths:
        if (root / name).is_symlink() or not (root / name).is_file():
            raise ValueError(f"Source input is not a regular file: {name}")
    return {name: sha256(root / name) for name in sorted(paths)}


def docker(*arguments, timeout=120):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, encoding="utf-8", check=True, timeout=timeout).stdout.strip()


def remove_fixture(packet: dict):
    image = json.loads(docker("image", "inspect", packet["tag"]))[0]
    if image["Id"] != packet["image_id"] or image["Config"].get("Labels", {}).get(LABEL) != packet["owner"]:
        raise ValueError("Refusing to remove a foreign fixture image")
    if run := current():
        run.legacy_image(LABEL, packet["owner"])
        result = run.retire_owned()
        if result["errors"] or result["blocked"]:
            raise RuntimeError("CI fixture retirement incomplete; recovery retained")
    else:
        docker("image", "rm", "--no-prune", packet["tag"])


def package(output: Path, work: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    source = identity()
    inputs = source_files(ROOT)
    owner = uuid4().hex
    if run := current():
        run.legacy_image(LABEL, owner)
    tag = f"rf-ci-dry-run-{owner}:fixture"
    context = work / "context"
    for name in (*STARTUP, *schema_paths(ROOT)):
        target = context / "image-source" / ("entrypoint.sh" if name == "docker/entrypoint.sh" else name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (context / "image-source/SOURCE_REVISION").write_text(source["sha"], encoding="ascii")
    shutil.copyfile(ROOT / "deployment/runner/ci-dry-run.Dockerfile", context / "Dockerfile")
    result = subprocess.run(["docker", "build", "--build-arg", f"OWNER={owner}", "-t", tag, str(context)], capture_output=True, timeout=420)
    (work / "build.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Fixture build failed; see {work / 'build.log'}")
    packet = {"schema": 1, "purpose": PURPOSE, "publishable": False, "identity": source, "source_files": inputs, "owner": owner, "tag": tag}
    packet["image_id"] = json.loads(docker("image", "inspect", tag))[0]["Id"]
    try:
        # Export before deleting the producer tag; the consumer must import the packet.
        docker("image", "save", "--output", str(output / "image.tar"), tag, timeout=120)
        if inputs != source_files(ROOT):
            raise ValueError("Source changed while producing the packet")
        packet["archive_sha256"] = sha256(output / "image.tar")
        (output / "manifest.json").write_text(json.dumps(packet, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    finally:
        remove_fixture(packet)
    return {"manifest_sha256": sha256(output / "manifest.json"), "image_id": packet["image_id"]}


def verify_packet(directory: Path, manifest_sha256: str, expected: dict, files: dict) -> dict:
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != {"manifest.json", "image.tar"}:
        raise ValueError("Unexpected packet contents")
    if any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):
        raise ValueError("Packet entries must be regular files")
    if sha256(directory / "manifest.json") != manifest_sha256:
        raise ValueError("Manifest digest mismatch")
    packet = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if packet.get("schema") != 1 or packet.get("purpose") != PURPOSE or packet.get("publishable") is not False:
        raise ValueError("Not an infrastructure-only packet")
    if packet.get("identity") != expected or packet.get("source_files") != files:
        raise ValueError("Foreign source, workflow run or source files")
    owner = packet.get("owner", "")
    if not re.fullmatch(r"[a-f0-9]{32}", owner) or packet.get("tag") != f"rf-ci-dry-run-{owner}:fixture" or not re.fullmatch(r"sha256:[a-f0-9]{64}", packet.get("image_id", "")):
        raise ValueError("Invalid fixture identity")
    if sha256(directory / "image.tar") != packet.get("archive_sha256"):
        raise ValueError("Image archive digest mismatch")
    return packet


def consume(directory: Path, output: Path, manifest_sha256: str) -> dict:
    packet = verify_packet(directory, manifest_sha256, identity(), source_files(ROOT))
    output.mkdir(parents=True, exist_ok=False)
    report = {"purpose": PURPOSE, "publishable": False, "identity": packet["identity"], "image_id": packet["image_id"], "status": "FAIL"}
    loaded = False
    try:
        docker("image", "load", "--input", str(directory / "image.tar"))
        loaded = True
        if run := current():
            run.legacy_image(LABEL, packet["owner"])
            run.use_image(packet["tag"])
        image = json.loads(docker("image", "inspect", packet["tag"]))[0]
        if image["Id"] != packet["image_id"] or image["Config"].get("Labels", {}).get(LABEL) != packet["owner"]:
            raise ValueError("Imported image identity mismatch")
        revision = docker("run", "--rm", "--network", "none", "--entrypoint", "cat", packet["image_id"], "/ragflow/SOURCE_REVISION")
        if revision != packet["identity"]["sha"]:
            raise ValueError("Imported source SHA mismatch")
        previous = {key: os.environ.get(key) for key in ("RAGFLOW_TEST_DISPOSABLE", "SOURCE_SHA")}
        try:
            os.environ.update({"RAGFLOW_TEST_DISPOSABLE": "1", "SOURCE_SHA": revision})
            with (output / "blue-green.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
                harness = runpy.run_path(str(ROOT / "deployment/runner/test-blue-green.py"))
                proof = harness["qualify"](packet["tag"], output / "blue-green")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if proof["status"] != "PASS" or proof["cleanup"] != "PASS" or source_files(ROOT) != packet["source_files"]:
            raise ValueError("Incomplete controller proof or changed source")
        report.update(status="PASS", artifact_transport="PASS", blue_green=proof)
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        try:
            if loaded:
                remove_fixture(packet)
            report["fixture_cleanup"] = "PASS"
        except Exception:
            report.update(status="FAIL", fixture_cleanup="FAIL")
            raise
        finally:
            (output / "ci-dry-run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


@managed("ci-infrastructure-fixture", days=7)
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("package")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--work", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--packet", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    result = package(args.output.resolve(), args.work.resolve()) if args.command == "package" else consume(args.packet.resolve(), args.output.resolve(), args.manifest_sha256)
    note(args.output.resolve())
    if args.command == "package" and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            for key, value in result.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
