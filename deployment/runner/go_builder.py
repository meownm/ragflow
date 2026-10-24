"""One pinned Linux Go builder for Feature, CI and package-scoped builds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.quality.candidate import canonical, digest, file_digest
from tools.quality.check_cache import cache_lock, read_success, write_success


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def inspect(reference: str) -> dict:
    return json.loads(docker("image", "inspect", reference))[0]


def base_images(recipe_path: Path) -> dict:
    recipe = recipe_path.read_text()
    defaults = dict(re.findall(r"^ARG (\w+)=([^\s]+)", recipe, re.M))
    bases = {}
    stages = set()
    for image, stage in re.findall(r"^FROM (\S+)(?: AS (\S+))?", recipe, re.M):
        for name, value in defaults.items():
            image = image.replace("${" + name + "}", value)
        if image not in stages:
            try:
                info = inspect(image)
            except subprocess.CalledProcessError:
                subprocess.run(["docker", "pull", image], check=True)
                info = inspect(image)
            repository = image.split("@")[0]
            if ":" in repository.rsplit("/", 1)[-1]:
                repository = repository.rsplit(":", 1)[0]
            references = [value for value in info.get("RepoDigests", []) if value.split("@")[0] == repository]
            if not references:
                raise ValueError(f"Base image needs an immutable repository digest: {image}")
            bases[image] = sorted(references)[0]
        if stage:
            stages.add(stage)
    return bases


def prepare_builder(source: Path, cache: Path) -> str:
    recipe = source / "deployment/runner/Dockerfile.go-builder"
    bases = base_images(recipe)
    key = digest(canonical({"schema": 2, "recipe": file_digest(recipe), "bases": bases, "platform": "linux/amd64"}))
    cache = cache / "builders"
    with cache_lock(cache, key):
        receipt = cache / (key + ".json")
        previous = read_success(receipt, key)
        if previous:
            try:
                info = inspect(previous["image_id"])
                if (info["Os"], info["Architecture"]) == ("linux", "amd64"):
                    return info["Id"]
            except (subprocess.CalledProcessError, KeyError):
                pass
        receipt.unlink(missing_ok=True)
        command = ["docker", "build", "--platform", "linux/amd64", "-f", str(recipe)]
        for name, reference in bases.items():
            command.extend(["--build-context", name + "=docker-image://" + reference])
        with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as temporary:
            iidfile = Path(temporary) / "image.id"
            # Keep the immutable image available independently of other builds.
            build_tag = "ragflow-go-builder:" + Path(temporary).name
            subprocess.run([*command, "-t", build_tag, "--iidfile", str(iidfile), str(recipe.parent)], check=True)
            image_id = iidfile.read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ValueError("Builder did not produce an immutable Docker image ID")
        write_success(receipt, {"schema": 1, "key": key, "status": "PASS", "image_id": image_id})
        return image_id


def native_paths() -> tuple[Path, Path]:
    return (
        Path(os.environ.get("RAGFLOW_NATIVE_CACHE", str(Path.home() / "ragflow-native-libs"))).resolve(),
        Path(os.environ.get("RAGFLOW_GO_CACHE_ROOT", str(Path.home() / ".cache/ragflow-go-builder"))).resolve(),
    )


def run_builder(source: Path, builder_id: str, arguments: list[str]) -> None:
    libraries, cache = native_paths()
    if not libraries.is_dir():
        raise ValueError(f"Native dependency cache not found: {libraries}")
    artifacts = arguments == ["--artifacts"]
    if "--artifacts" in arguments and not artifacts:
        raise ValueError("--artifacts does not accept build.sh arguments")
    command = ["docker", "run", "--rm", "--platform", "linux/amd64", "--workdir", "/ragflow", "--entrypoint", "python3" if artifacts else "/bin/bash"]
    if hasattr(os, "getuid"):
        command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    for name, value in {
        "HOME": "/tmp/builder-home",
        "GOCACHE": "/tmp/go-build-cache",
        "GOMODCACHE": "/tmp/go-mod-cache",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": "*",
    }.items():
        command.extend(["--env", f"{name}={value}"])
    for name in ("GOFLAGS", "GOEXPERIMENT", "GOAMD64", "GOARCH", "GOOS", "CGO_CFLAGS", "CGO_LDFLAGS"):
        command.extend(["--env", name])
    for host, guest, readonly in (
        (source.resolve(), "/ragflow", False),
        (libraries, "/tmp/builder-home/ragflow-native-libs", True),
        (cache / "build", "/tmp/go-build-cache", False),
        (cache / "modules", "/tmp/go-mod-cache", False),
        (cache / "artifacts", "/tmp/native-artifacts", False),
    ):
        if not readonly:
            host.mkdir(parents=True, exist_ok=True)
        command.extend(["--mount", f"type=bind,src={host},dst={guest}" + (",readonly" if readonly else "")])
    command.append(builder_id)
    if artifacts:
        command.extend(["deployment/runner/native_artifacts.py", "--cache", "/tmp/native-artifacts", "--libraries", "/tmp/builder-home/ragflow-native-libs", "--toolchain", builder_id])
    else:
        command.extend(["build.sh", *(arguments or ["--go"])])
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/ragflow-feature")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    run_builder(args.root, prepare_builder(args.root, args.cache), arguments)


if __name__ == "__main__":
    main()
