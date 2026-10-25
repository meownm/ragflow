"""Reuse successful local deterministic checks; never a CI/release attestation."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from tools.quality.candidate import canonical, digest, file_digest, safe_path


@contextmanager
def cache_lock(cache: Path, key: str):
    safe_path(cache, key)
    cache.mkdir(parents=True, exist_ok=True)
    with safe_path(cache, key + ".lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def read_success(path: Path, key: str) -> dict | None:
    safe_path(path.parent, path.name)
    try:
        data = json.loads(path.read_bytes())
        if (
            isinstance(data, dict)
            and data.get("schema") == 1
            and data.get("key") == key
            and data.get("status") == "PASS"
            and data.get("receipt_id") == digest(canonical({name: value for name, value in data.items() if name != "receipt_id"}))
        ):
            return data
    except (OSError, ValueError):
        pass
    return None


def write_success(path: Path, data: dict) -> None:
    safe_path(path.parent, path.name)
    data = {**data, "receipt_id": digest(canonical(data))}
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(canonical(data) + b"\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_check(command: list[str], cache: Path, inputs, *, cwd: Path, force: bool = False) -> dict:
    """inputs() must cover source, test/configuration, dependencies and execution context."""
    from tools.quality.artifact_lifecycle import docker_args, note

    before = inputs()
    program = Path(command[0])
    executable = str(cwd / program) if not program.is_absolute() and ("/" in command[0] or "\\" in command[0]) else shutil.which(command[0])
    if not executable:
        raise ValueError("Check executable is unavailable")
    if before.get("incomplete_environment"):
        # A check can still run, but an unknown dependency identity cannot
        # authorize either reading or writing a cached PASS.
        subprocess.run(docker_args(command), cwd=cwd, check=True)
        if inputs() != before:
            raise ValueError("Check inputs changed during execution")
        return {"status": "PASS", "cache_hit": False, "cacheable": False, "incomplete_environment": before["incomplete_environment"]}
    identity = {"schema": 1, "checker": file_digest(Path(__file__)), "executable": file_digest(Path(executable)), "command": command, "cwd": str(cwd.resolve()), "inputs": before}
    key = digest(canonical(identity))
    with cache_lock(cache, key):
        receipt = safe_path(cache, key + ".json")
        previous = read_success(receipt, key)
        if previous and not force:
            if inputs() != before:
                raise ValueError("Check inputs changed during cache lookup")
            note(receipt, receipt=True, category="cache")
            return {**previous, "cache_hit": True}
        # A failed forced rerun must invalidate the previous PASS as well.
        receipt.unlink(missing_ok=True)
        subprocess.run(docker_args(command), cwd=cwd, check=True)
        if inputs() != before:
            raise ValueError("Check inputs changed during execution")
        result = {"schema": 1, "key": key, "status": "PASS"}
        write_success(receipt, result)
        note(receipt, receipt=True, category="cache")
        return {**result, "cache_hit": False}


def checkout_inputs(root: Path, paths: list[str]) -> dict:
    """Default to all source, including tests; an explicit narrower boundary is caller-owned."""
    names = subprocess.check_output(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    files = {}
    for name in sorted(set(filter(None, names))):
        if paths and not any(name == path or name.startswith(path.rstrip("/") + "/") for path in paths):
            continue
        path = safe_path(root, name)
        if path.is_file():
            files[name] = file_digest(path)
    if not files:
        raise ValueError("No check inputs selected")
    # Installed package versions/records may change without lockfile changes.
    environment, incomplete = [], []
    for dist in metadata.distributions():
        info = dist.metadata or {}
        if not info.get("Name") or not info.get("Version"):
            incomplete.append(str(dist.locate_file("")))
            continue
        try:
            direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
            editable = not isinstance(direct_url, dict) or direct_url.get("dir_info", {}).get("editable")
        except (ValueError, AttributeError):
            editable = True
        if editable:
            # Package metadata does not fingerprint a mutable external source tree.
            incomplete.append(str(dist.locate_file("")))
            continue
        environment.append((info["Name"], info["Version"], digest((dist.read_text("RECORD") or "").encode())))
    return {
        "files": files,
        "python": sys.version,
        "platform": platform.platform(),
        "environment_files": sorted(environment),
        "incomplete_environment": sorted(set(incomplete)),
        # The tool transport changes this ID on each invocation; it is not a test input.
        "environment": digest(canonical({name: value for name, value in os.environ.items() if name not in {"CODEX_TOOL_CALL_ID", "RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_OWNER_TOKEN"}})),
        "cwd": str(root.resolve()),
    }


def main() -> int:
    from tools.quality.artifact_lifecycle import managed

    return managed("deterministic-check", category="cache", days=30)(_main)()


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--input", action="append", default=[], help="Owned source/test/config boundary; default all Git source")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A deterministic check command is required after --")
    root = args.root.resolve()
    result = run_check(command, args.cache or root / ".local/cache/deploy/checks", lambda: checkout_inputs(root, args.input), cwd=root, force=args.force)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
