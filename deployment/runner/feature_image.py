"""Build/reuse an exact Feature image and cache its deterministic import probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deployment.runner import go_builder
from deployment.runner.native_artifacts import cache_key, identity as native_identity
from tools.quality.candidate import canonical, digest, file_digest, safe_path, verify
from tools.quality.check_cache import cache_lock, read_success, run_check, write_success
from tools.quality.artifact_lifecycle import docker_args, managed, note
from tools.quality.artifact_registry import save

FEATURE_REPOSITORY = "192.168.1.175:8443/docker-hosted/ragflow"


def image_import(reference: str, cache: Path, *, force: bool = False) -> dict:
    info = go_builder.inspect(reference)
    image_id = info["Id"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Expected immutable Docker image ID")
    context = {"image": image_id, "engine": go_builder.docker("info", "--format", "{{.ID}}"), "checker": file_digest(Path(__file__))}
    return run_check(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint", "/ragflow/.venv/bin/python", image_id, "-B", "-c", "import business_documents"],
        cache / "checks",
        lambda: context,
        cwd=Path.home(),
        force=force,
    )


def source_content(manifest: dict) -> dict:
    # Git HEAD, dirty status and explicit-untracked bookkeeping do not affect bytes.
    return {name: manifest["identity"][name] for name in ("files", "version", "build")}


def build(snapshot: Path, cache: Path, *, local: bool, force: bool = False) -> dict:
    manifest = verify(snapshot)
    source = snapshot / "source"
    builder_id = go_builder.prepare_builder(source, cache)
    libraries, _ = go_builder.native_paths()
    inputs = {
        "source": source_content(manifest),
        "bases": go_builder.base_images(source / "Dockerfile"),
        "native": native_identity(source, libraries, builder_id),
        "builder": file_digest(Path(__file__)),
        "platform": "linux/amd64",
        "python_jobs": "2",
    }
    key = digest(canonical(inputs))
    image = ("local/ragflow-feature:" if local else FEATURE_REPOSITORY + ":dev-") + key
    with cache_lock(cache, key):
        receipt = cache / (key + ".json")
        previous = read_success(receipt, key)
        if previous and not force:
            reusable = False
            try:
                info = go_builder.inspect(previous["image_id"])
                labels = info["Config"]["Labels"]
                reusable = (info["Os"], info["Architecture"], labels.get("org.ragflow.source-id"), labels.get("org.ragflow.validation")) == (
                    "linux",
                    "amd64",
                    previous["source_id"],
                    "feature-build-only",
                )
            except (subprocess.CalledProcessError, KeyError, TypeError, AttributeError):
                pass
            if reusable:
                if cache_key(native_identity(source, libraries, builder_id)) != cache_key(inputs["native"]):
                    raise ValueError("Native inputs changed during cache lookup")
                image_import(previous["image_id"], cache)
                note(receipt, receipt=True, category="cache")
                return {**previous, "cache_hit": True}
        receipt.unlink(missing_ok=True)
        go_builder.run_builder(source, builder_id, ["--artifacts"])
        version = manifest["identity"]["version"] + "-dev." + manifest["source_id"][:12]
        command = ["docker", "build", "--platform", "linux/amd64"]
        for name, reference in inputs["bases"].items():
            command.extend(["--build-context", name + "=docker-image://" + reference])
        command.extend(
            [
                "--build-arg",
                "RAGFLOW_BUILD_VERSION=" + version,
                "--build-arg",
                "RAGFLOW_SOURCE_REVISION=",
                "--build-arg",
                "RAGFLOW_BUILD_JOBS=2",
                "--label",
                "org.ragflow.source-id=" + manifest["source_id"],
                "--label",
                "org.ragflow.validation=feature-build-only",
                "-f",
                str(source / "Dockerfile"),
                "-t",
                image,
                str(source),
            ]
        )
        with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as temporary:
            iidfile = Path(temporary) / "image.id"
            subprocess.run(docker_args([*command[:-1], "--iidfile", str(iidfile), command[-1]]), check=True)
            image_id = iidfile.read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ValueError("Feature build did not produce an immutable Docker image ID")
        info = go_builder.inspect(image_id)
        labels = info["Config"]["Labels"] or {}
        if (info["Id"], info["Os"], info["Architecture"], labels.get("org.ragflow.source-id"), labels.get("org.ragflow.validation")) != (
            image_id,
            "linux",
            "amd64",
            manifest["source_id"],
            "feature-build-only",
        ):
            raise ValueError("Feature build image identity mismatch")
        if go_builder.docker("run", "--rm", "--network", "none", "--entrypoint", "cat", image_id, "/ragflow/SOURCE_REVISION") != "unverified":
            raise ValueError("Feature source revision mismatch")
        if go_builder.docker("run", "--rm", "--network", "none", "--entrypoint", "cat", image_id, "/ragflow/VERSION") != version:
            raise ValueError("Feature version mismatch")
        image_import(image_id, cache, force=force)
        # Native build intentionally adds outputs; every original source byte must remain stable.
        if any(file_digest(source / name) != value["sha256"] for name, value in manifest["identity"]["files"].items()):
            raise ValueError("Source changed during Feature build")
        if cache_key(native_identity(source, libraries, builder_id)) != cache_key(inputs["native"]):
            raise ValueError("Native inputs changed during Feature build")
        result = {"schema": 1, "key": key, "status": "PASS", "image": image, "image_id": image_id, "source_id": manifest["source_id"], "version": version, "bases": inputs["bases"]}
        write_success(receipt, result)
        note(receipt, receipt=True, category="cache")
        return {**result, "cache_hit": False}


def publish(result: dict) -> dict:
    image_id = result["image_id"]
    if result.get("status") != "PASS" or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("Publication requires a successfully checked immutable image")
    image = FEATURE_REPOSITORY + ":dev-" + image_id.removeprefix("sha256:")
    go_builder.docker("tag", image_id, image)
    with tempfile.TemporaryDirectory(prefix="ragflow-registry-") as temporary:
        # Docker's credentials live only in this private directory, never in receipts.
        command = ["docker", "--config", temporary]
        password = subprocess.check_output(["sudo", "docker", "exec", "nexus", "cat", "/nexus-data/admin.password"])
        subprocess.run(docker_args([*command, "login", FEATURE_REPOSITORY.split("/", 1)[0], "-u", "admin", "--password-stdin"]), input=password, stdout=subprocess.DEVNULL, check=True)
        subprocess.run(docker_args([*command, "push", image]), check=True)
        info = json.loads(subprocess.check_output(docker_args([*command, "image", "inspect", image_id]), text=True))[0]
        matches = [value for value in info.get("RepoDigests", []) if value.startswith(FEATURE_REPOSITORY + "@sha256:")]
        if info["Id"] != image_id or len(matches) != 1 or not re.fullmatch(re.escape(FEATURE_REPOSITORY) + r"@sha256:[0-9a-f]{64}", matches[0]):
            raise ValueError("Published image digest does not match the requested image/repository")
    return {**result, "image": image, "digest": matches[0]}


@managed("feature-image", category="cache", days=30)
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--snapshot", type=Path)
    action.add_argument("--image-import")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/ragflow-feature")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Publish the checked Feature image to Nexus on the builder host")
    parser.add_argument("--output", type=Path, help="Write the result as atomic JSON, independently of build logs")
    args = parser.parse_args()
    if args.publish and (args.local or args.image_import):
        parser.error("--publish requires --snapshot without --local")
    if args.output:
        safe_path(args.output.parent, args.output.name)
        args.output.unlink(missing_ok=True)
        note(args.output, category="metadata")
    result = image_import(args.image_import, args.cache, force=args.force) if args.image_import else build(args.snapshot, args.cache, local=args.local, force=args.force)
    if args.publish:
        result = publish(result)
    if args.output:
        save(result, args.output)
        note(args.output, receipt=True, category="metadata")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
