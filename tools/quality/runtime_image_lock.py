"""Freeze disposable qualification Compose images without persisting rendered secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


ENGINES = ("elasticsearch", "infinity")
COMPOSE_FILES = ("docker/docker-compose.yml", "docker/docker-compose.ci-postgres.yml", "docker/docker-compose.release-test.yml")
PRODUCT_SERVICES = {"ragflow-cpu", "ragflow-gpu"}
HEX = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SERVICE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def repository_name(reference: str) -> str:
    """Validate a Docker reference and normalize Docker Hub aliases for digest matching."""
    if not isinstance(reference, str) or not reference or reference.count("@") > 1:
        raise ValueError("invalid runtime image reference")
    image, separator, digest = reference.partition("@")
    if separator and not DIGEST.fullmatch(digest):
        raise ValueError("invalid runtime image digest")
    prefix, slash, last = image.rpartition("/")
    name, colon, tag = last.partition(":")
    if colon and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
        raise ValueError("invalid runtime image tag")
    repository = prefix + slash + name
    parts = repository.split("/")
    for index, part in enumerate(parts):
        host, port_separator, port = part.partition(":")
        if not re.fullmatch(r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*", host) or (port_separator and (index != 0 or len(parts) == 1 or not port.isdigit())):
            raise ValueError("invalid runtime image repository")
    registry = parts[0] if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost") else "docker.io"
    path = parts[1:] if registry != "docker.io" or parts[0] == "docker.io" else parts
    if registry in {"index.docker.io", "registry-1.docker.io"}:
        registry = "docker.io"
    if registry == "docker.io" and len(path) == 1:
        path = ["library", *path]
    return registry + "/" + "/".join(path)


def identity(repository: str, sha: str, run_id: str, attempt: int) -> dict:
    if (
        not isinstance(repository, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or not isinstance(run_id, str)
        or not re.fullmatch(r"[1-9][0-9]*", run_id)
        or type(attempt) is not int
        or attempt < 1
    ):
        raise ValueError("runtime image lock requires repository, full SHA, run ID and attempt")
    return {"repository": repository, "sha": sha, "run_id": run_id, "attempt": attempt}


def _command(arguments: list[str], *, env: dict | None = None, timeout: int = 120) -> bytes:
    try:
        result = subprocess.run(arguments, env=env, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("runtime image lock prerequisite command unavailable") from error
    if result.returncode:
        # Compose output and registry errors can contain credentials. Never propagate them.
        raise ValueError("runtime image lock prerequisite command failed")
    return result.stdout


def compose_images(engine: str, env_file: Path, candidate_image: str, docker: list[str]) -> dict[str, str]:
    if engine not in ENGINES:
        raise ValueError("unsupported qualification engine")
    repository_name(candidate_image)
    if "@" not in candidate_image:
        raise ValueError("candidate image must be pinned by digest")
    environment = {
        **os.environ,
        "DOC_ENGINE": engine,
        "COMPOSE_PROFILES": f"{engine},cpu,tei-cpu,deepdoc",
        "RAGFLOW_IMAGE": candidate_image,
        "CI_ENV_FILE": str(env_file.resolve()),
        "CI_LOG_DIR": os.environ.get("CI_LOG_DIR", str(env_file.parent.resolve() / "runtime-logs")),
    }
    arguments = [*docker, "compose", "--env-file", str(env_file)]
    for path in COMPOSE_FILES:
        arguments.extend(("-f", path))
    rendered = json.loads(_command([*arguments, "config", "--format", "json"], env=environment))
    return supporting_images(rendered, engine, candidate_image)


def supporting_images(config: dict, engine: str, candidate_image: str) -> dict[str, str]:
    """Select active images only; discard environment, mounts and all other rendered config."""
    if engine not in ENGINES or not isinstance(config, dict) or not isinstance(config.get("services"), dict):
        raise ValueError("invalid qualification Compose configuration")
    active = {engine, "cpu", "tei-cpu", "deepdoc"}
    images = {}
    product_found = False
    for name, service in config["services"].items():
        if not isinstance(name, str) or not SERVICE.fullmatch(name) or not isinstance(service, dict):
            raise ValueError("invalid qualification Compose service")
        profiles = service.get("profiles", [])
        if not isinstance(profiles, list) or any(not isinstance(item, str) for item in profiles):
            raise ValueError("invalid qualification Compose profiles")
        if profiles and active.isdisjoint(profiles):
            continue
        if name in PRODUCT_SERVICES:
            if service.get("image") != candidate_image:
                raise ValueError("Compose product image differs from qualified candidate")
            product_found = True
            continue
        image = service.get("image")
        repository_name(image)
        images[name] = image
    if not product_found or not images:
        raise ValueError("qualification Compose lacks candidate or supporting images")
    return dict(sorted(images.items()))


def manifest_digest(manifest: dict | list, platform: str) -> str:
    operating_system, architecture = platform.split("/")
    if isinstance(manifest, list):
        descriptors = [entry.get("Descriptor", {}) for entry in manifest if isinstance(entry, dict)]
        matching = [
            descriptor
            for descriptor in descriptors
            if descriptor.get("platform", {}).get("os") == operating_system
            and descriptor.get("platform", {}).get("architecture") == architecture
            and descriptor.get("platform", {}).get("variant")
            in (
                None,
                "",
                *(("v8",) if architecture == "arm64" else ()),
            )
        ]
        if len(matching) != 1:
            raise ValueError("runtime manifest lacks one compatible platform")
        digest = matching[0].get("digest")
    elif isinstance(manifest, dict):
        digest = manifest.get("Descriptor", {}).get("digest")
    else:
        digest = None
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise ValueError("runtime manifest digest unavailable")
    return digest


def inspected_image(details: list, reference: str, platform: str) -> dict:
    if not isinstance(details, list) or len(details) != 1 or not isinstance(details[0], dict):
        raise ValueError("runtime image inspection unavailable")
    image = details[0]
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not DIGEST.fullmatch(image_id) or f"{image.get('Os')}/{image.get('Architecture')}" != platform:
        raise ValueError("runtime image ID or platform mismatch")
    requested_repository = repository_name(reference)
    requested_digest = reference.split("@", 1)[1]
    digests = image.get("RepoDigests", [])
    if not isinstance(digests, list):
        raise ValueError("runtime image repository digests unavailable")
    matching = [item for item in digests if isinstance(item, str) and "@" in item and repository_name(item) == requested_repository and item.split("@", 1)[1] == requested_digest]
    if not matching:
        raise ValueError("pulled runtime image lacks requested repository digest")
    return {"image": requested_repository + "@" + requested_digest, "image_id": image_id, "platform": platform}


def resolve_image(reference: str, docker: list[str], platform: str) -> dict:
    repository = repository_name(reference)
    if "@" in reference:
        digest = reference.split("@", 1)[1]
    else:
        manifest = json.loads(_command([*docker, "manifest", "inspect", "--verbose", reference]))
        digest = manifest_digest(manifest, platform)
    pinned = repository + "@" + digest
    _command([*docker, "pull", "--platform", platform, pinned], timeout=600)
    details = json.loads(_command([*docker, "image", "inspect", pinned]))
    return inspected_image(details, pinned, platform)


def build_lock(current: dict, env_file: Path, candidate_image: str, docker: list[str], platform: str = "linux/amd64") -> dict:
    if platform not in {"linux/amd64", "linux/arm64"}:
        raise ValueError("qualification runtime images require a supported Linux platform")
    identity(**current)
    services = {}
    resolved = {}
    for engine in ENGINES:
        services[engine] = {}
        for name, source in compose_images(engine, env_file, candidate_image, docker).items():
            if source not in resolved:
                resolved[source] = resolve_image(source, docker, platform)
            services[engine][name] = {"source_image": source, **resolved[source]}
    lock = {"schema": 1, "purpose": "qualification-runtime-images", **current, "candidate_image": candidate_image, "platform": platform, "services": services}
    content = serialize(lock)
    verify_lock(content, hashlib.sha256(content).hexdigest(), current, candidate_image)
    return lock


def serialize(lock: dict) -> bytes:
    return (json.dumps(lock, sort_keys=True, indent=2) + "\n").encode("utf-8")


def verify_lock(content: bytes, expected_sha256: str, current: dict, candidate_image: str) -> dict:
    if not isinstance(expected_sha256, str) or not HEX.fullmatch(expected_sha256) or hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("runtime image lock file digest mismatch")
    lock = json.loads(content)
    expected_keys = {"schema", "purpose", "repository", "sha", "run_id", "attempt", "candidate_image", "platform", "services"}
    if (
        not isinstance(lock, dict)
        or set(lock) != expected_keys
        or type(lock.get("schema")) is not int
        or lock.get("schema") != 1
        or lock.get("purpose") != "qualification-runtime-images"
        or lock.get("candidate_image") != candidate_image
        or type(lock.get("attempt")) is not int
        or any(lock.get(key) != value for key, value in identity(**current).items())
        or lock.get("platform") not in {"linux/amd64", "linux/arm64"}
        or not isinstance(lock.get("services"), dict)
        or set(lock["services"]) != set(ENGINES)
    ):
        raise ValueError("foreign or invalid runtime image lock")
    repository_name(candidate_image)
    if "@" not in candidate_image:
        raise ValueError("candidate image must be pinned by digest")
    by_source = {}
    by_service = {}
    for engine in ENGINES:
        services = lock["services"][engine]
        if not isinstance(services, dict) or not services:
            raise ValueError("runtime image lock lacks supporting services")
        for name, item in services.items():
            if (
                not isinstance(name, str)
                or not SERVICE.fullmatch(name)
                or name in PRODUCT_SERVICES
                or not isinstance(item, dict)
                or set(item) != {"source_image", "image", "image_id", "platform"}
                or not isinstance(item.get("image_id"), str)
                or not DIGEST.fullmatch(item["image_id"])
                or item.get("platform") != lock["platform"]
            ):
                raise ValueError("invalid runtime image service lock")
            source_repository = repository_name(item["source_image"])
            if not isinstance(item["image"], str) or "@" not in item["image"] or repository_name(item["image"]) != source_repository:
                raise ValueError("runtime image lock repository mismatch or missing digest")
            if "@" in item["source_image"] and item["source_image"].split("@", 1)[1] != item["image"].split("@", 1)[1]:
                raise ValueError("runtime image lock changed a source digest")
            previous = by_source.setdefault(item["source_image"], item)
            if previous != item:
                raise ValueError("runtime image resolution changed between engine lanes")
            previous_service = by_service.setdefault(name, item)
            if previous_service != item:
                raise ValueError("shared supporting service changed between engine lanes")
    return lock


def materialize(lock: dict, engine: str, env_file: Path, docker: list[str]) -> bytes:
    if engine not in ENGINES:
        raise ValueError("unsupported qualification engine")
    services = lock["services"][engine]
    if compose_images(engine, env_file, lock["candidate_image"], docker) != {name: item["source_image"] for name, item in services.items()}:
        raise ValueError("Compose supporting image set differs from qualified lock")
    for item in services.values():
        actual = resolve_image(item["image"], docker, lock["platform"])
        if actual != {key: item[key] for key in ("image", "image_id", "platform")}:
            raise ValueError("runtime image bytes differ from qualification lock")
    # JSON is valid YAML. No environment, mounts, credentials or source config are emitted.
    return serialize({"services": {name: {"image": item["image"]} for name, item in services.items()}})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify", "materialize"))
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--lock-sha256")
    parser.add_argument("--env-file", type=Path, default=Path("docker/.env"))
    parser.add_argument("--candidate-image", default=os.getenv("RAGFLOW_IMAGE"))
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument("--sha", default=os.getenv("SOURCE_SHA"))
    parser.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", type=int, default=int(os.getenv("GITHUB_RUN_ATTEMPT", "1")))
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--engine", choices=ENGINES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sudo", action="store_true")
    args = parser.parse_args()
    try:
        current = identity(args.repository, args.sha, args.run_id, args.run_attempt)
        docker = ["sudo", "-n", "docker"] if args.sudo else ["docker"]
        if os.environ.get("DOCKER_CONFIG"):
            docker.extend(("--config", os.environ["DOCKER_CONFIG"]))
        if args.action == "build":
            lock = build_lock(current, args.env_file, args.candidate_image, docker, args.platform)
            content = serialize(lock)
            args.lock.parent.mkdir(parents=True, exist_ok=True)
            with args.lock.open("xb") as output:
                output.write(content)
            print(hashlib.sha256(content).hexdigest())
        else:
            lock = verify_lock(args.lock.read_bytes(), args.lock_sha256, current, args.candidate_image)
            if args.action == "materialize":
                if args.output is None:
                    raise ValueError("materialize requires --output")
                content = materialize(lock, args.engine, args.env_file, docker)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("xb") as output:
                    output.write(content)
            print("runtime image lock verified")
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(f"runtime image lock failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
