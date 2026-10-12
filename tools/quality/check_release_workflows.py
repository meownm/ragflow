"""Lint release workflows with a pinned tool and one bounded queue compatibility check."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

import yaml


VERSION = "1.7.12"
CHECKSUMS_SHA256 = "433028cf0ba3c42163ea1a668dedce30fcdbe84fe912b1a5e288c006eab8a4f5"
WORKFLOWS = tuple(f".github/workflows/{name}.yml" for name in ("tests", "release", "browser-regression", "release-cli", "runner-watchdog", "sep-tests"))
QUEUE_DIAGNOSTIC = 'unexpected key "queue" for "concurrency" section. expected one of "cancel-in-progress", "group"'


@dataclass(frozen=True)
class QueueLocation:
    line: int
    column: int


def _mapping(node: yaml.Node, name: str) -> dict[str, tuple[yaml.Node, yaml.Node]]:
    if not isinstance(node, yaml.MappingNode):
        raise ValueError(f"{name} must be a mapping")
    result = {}
    for key, value in node.value:
        if not isinstance(key, yaml.ScalarNode) or key.value in result:
            raise ValueError(f"{name} contains an invalid or duplicate key")
        result[key.value] = (key, value)
    return result


def _queues(node: yaml.Node, path: tuple[str, ...] = (), ancestors: frozenset[int] = frozenset()):
    if id(node) in ancestors:
        raise ValueError("cyclic workflow YAML alias")
    ancestors = ancestors | {id(node)}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode):
                child_path = (*path, key.value)
                if key.value == "queue":
                    yield child_path, key, value
                yield from _queues(value, child_path, ancestors)
    elif isinstance(node, yaml.SequenceNode):
        for index, value in enumerate(node.value):
            yield from _queues(value, (*path, str(index)), ancestors)


def validate_queue(root: Path) -> QueueLocation:
    """Require durable publication queue, without granting other unknown-key exceptions."""
    location = None
    for relative in WORKFLOWS:
        document = yaml.compose((root / relative).read_text(encoding="utf-8"))
        if document is None:
            raise ValueError(f"empty workflow: {relative}")
        queues = list(_queues(document))
        if relative != ".github/workflows/release.yml":
            if queues:
                raise ValueError(f"queue is only permitted on release.publish: {relative}")
            continue
        expected_path = ("jobs", "publish", "concurrency", "queue")
        if len(queues) != 1 or queues[0][0] != expected_path:
            raise ValueError("release.publish must contain exactly one queue: max")
        key, queue = queues[0][1:]
        if not isinstance(queue, yaml.ScalarNode) or queue.tag != "tag:yaml.org,2002:str" or queue.value != "max":
            raise ValueError("release.publish queue must be literal max")
        jobs = _mapping(document, "workflow")["jobs"][1]
        publish = _mapping(jobs, "jobs")["publish"][1]
        concurrency = _mapping(publish, "release.publish")["concurrency"][1]
        values = _mapping(concurrency, "release.publish.concurrency")
        cancel = values.get("cancel-in-progress", (None, None))[1]
        if not isinstance(cancel, yaml.ScalarNode) or cancel.tag != "tag:yaml.org,2002:bool" or cancel.value != "false":
            raise ValueError("release.publish cancel-in-progress must be literal false")
        group = values.get("group", (None, None))[1]
        if not isinstance(group, yaml.ScalarNode) or group.value != "release-publication":
            raise ValueError("release.publish must use the shared release-publication group")
        location = QueueLocation(key.start_mark.line + 1, key.start_mark.column + 1)
    if location is None:
        raise ValueError("release publication queue is missing")
    return location


def _queue_compatibility_diagnostic(error: dict, location: QueueLocation) -> bool:
    return (
        error.get("filepath") == ".github/workflows/release.yml"
        and error.get("kind") == "syntax-check"
        and error.get("message") == QUEUE_DIAGNOSTIC
        and error.get("line") == location.line
        and error.get("column") == location.column
    )


def _download(name: str, limit: int) -> bytes:
    url = f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=60) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"actionlint download exceeds size limit: {name}")
    return content


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
            output.close()
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _checked_file(path: Path, digest: str, limit: int) -> bytes:
    if path.is_symlink():
        raise ValueError(f"actionlint cache file must not be a symlink: {path.name}")
    if path.exists() and path.stat().st_size > limit:
        raise ValueError(f"cached actionlint file exceeds size limit: {path.name}")
    content = path.read_bytes() if path.exists() else _download(path.name, limit)
    if len(content) > limit or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError(f"actionlint checksum mismatch: {path.name}")
    if not path.exists():
        _atomic_write(path, content)
    return content


def actionlint_binary(root: Path, cache: Path) -> Path:
    """Verify the upstream checksum manifest, archive and extracted binary before execution."""
    root, cache = root.resolve(), cache.resolve()
    if cache == root or root in cache.parents:
        raise ValueError("actionlint cache must be outside the workspace")
    systems = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}
    machines = {"AMD64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}
    system, machine = systems.get(platform.system()), machines.get(platform.machine())
    if system is None or machine is None:
        raise ValueError("unsupported actionlint platform")
    directory = cache / f"v{VERSION}" / f"{system}_{machine}"
    if directory.parent.is_symlink() or directory.is_symlink():
        raise ValueError("actionlint version cache directory must not be a symlink")
    if directory.resolve() == root or root in directory.resolve().parents:
        raise ValueError("actionlint cache must be outside the workspace")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    checksums = _checked_file(directory / f"actionlint_{VERSION}_checksums.txt", CHECKSUMS_SHA256, 256 * 1024)
    extension = "zip" if system == "windows" else "tar.gz"
    name = f"actionlint_{VERSION}_{system}_{machine}.{extension}"
    matches = [line.split()[0] for line in checksums.decode("ascii").splitlines() if line.split()[-1:] == [name]]
    if len(matches) != 1:
        raise ValueError("pinned actionlint checksum manifest does not identify the archive")
    archive = _checked_file(directory / name, matches[0], 30 * 1024 * 1024)
    binary_name = "actionlint.exe" if system == "windows" else "actionlint"
    if system == "windows":
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            executable = source.read(binary_name)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            entry = source.getmember(binary_name)
            if not entry.isfile():
                raise ValueError("actionlint executable archive member must be a regular file")
            with source.extractfile(entry) as content:
                executable = content.read()
    binary = directory / binary_name
    if binary.is_symlink() or (binary.exists() and binary.read_bytes() != executable):
        raise ValueError("cached actionlint executable differs from verified archive")
    if not binary.exists():
        _atomic_write(binary, executable, 0o700)
    return binary


def check(root: Path, cache: Path) -> int:
    location = validate_queue(root)
    binary = actionlint_binary(root, cache)
    result = subprocess.run(
        [str(binary), "-format", "{{json .}}", *WORKFLOWS],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode not in (0, 1):
        raise ValueError(f"actionlint failed to execute: exit {result.returncode}")
    errors = json.loads(result.stdout or "[]")
    if not isinstance(errors, list):
        raise ValueError("unexpected actionlint JSON output")
    remaining = [error for error in errors if not _queue_compatibility_diagnostic(error, location)]
    for error in remaining:
        print(f"{error['filepath']}:{error['line']}:{error['column']}: {error['message']} [{error['kind']}]")
    if remaining or result.stderr or (result.returncode == 1 and not errors):
        return 1
    print(f"actionlint {VERSION}: {len(WORKFLOWS)} workflows passed; release publication queue independently verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    default_cache = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    parser.add_argument("--cache-dir", type=Path, default=default_cache / "ragflow-tools/actionlint")
    args = parser.parse_args()
    try:
        return check(args.root.resolve(), args.cache_dir)
    except (ValueError, KeyError, OSError, yaml.YAMLError, subprocess.SubprocessError) as error:
        print(f"release workflow check failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
