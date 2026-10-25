"""Build or restore the Linux native distribution inside the pinned builder.

The key includes source/embedded resources, verified-library bytes, the builder
image ID and build flags. Output hashes are checked before any cached file is
installed. A per-key lock and manifest-last writes protect concurrent builders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


OUTPUTS = ("bin/ragflow_server", "bin/ragflow-cli", "internal/binding/cpp/cmake-build-release/librag_tokenizer_c_api.a")
IGNORED = {"build", "cmake-build-release", "cmake-build-debug", "__pycache__", ".git"}
CPP_COMMAND = ("bash", "build.sh", "--cpp")
GO_COMMAND = ("bash", "build.sh", "--go", "--strip")
GO_FLAGS = ("-trimpath", "-buildvcs=false")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree(root: Path) -> dict[str, str]:
    result = {}
    for directory, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in IGNORED)
        if any((Path(directory) / name).is_symlink() for name in directories):
            raise ValueError(f"Symlink directory is not a native cache input: {directory}")
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"Symlink is not a native cache input: {path}")
            if path.suffix not in {".pyc", ".pyo"}:
                result[path.relative_to(root).as_posix()] = digest(path)
    return result


def identity(root: Path, libraries: Path, toolchain: str) -> dict:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", toolchain):
        raise ValueError("Native build requires an immutable builder image ID")
    if not libraries.is_dir():
        raise ValueError("Native library cache is missing")
    source = {name: tree(root / name) for name in ("cmd", "internal")}
    source["build"] = {
        name: digest(root / name)
        for name in (
            "go.mod",
            "go.sum",
            "build.sh",
            "ragflow_deps/prepare_native.py",
            "ragflow_deps/native-deps.json",
        )
    }
    return {
        "schema": 2,
        "source": source,
        "libraries": tree(libraries),
        "toolchain": toolchain,
        # Hash the recipe executed below, not orchestration/logging implementation.
        # The immutable image ID already identifies the complete toolchain.
        "recipe": {"cpp": CPP_COMMAND, "go": GO_COMMAND, "go_flags": GO_FLAGS},
        "environment": {name: os.environ.get(name, "") for name in ("GOFLAGS", "GOEXPERIMENT", "GOAMD64", "GOARCH", "GOOS", "CGO_CFLAGS", "CGO_LDFLAGS")},
    }


def cache_key(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verified_entry(entry: Path, key: str) -> dict | None:
    if entry.is_symlink() or (entry / "manifest.json").is_symlink():
        raise ValueError("Symlink native artifact cache is not allowed")
    try:
        manifest = json.loads((entry / "manifest.json").read_text())
        if not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("key") != key or not isinstance(manifest.get("outputs"), dict) or set(manifest["outputs"]) != set(OUTPUTS):
            return None
        for name, expected in manifest["outputs"].items():
            path = entry / Path(name).name
            if path.is_symlink() or digest(path) != expected:
                return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return manifest


def restore(entry: Path, root: Path, key: str) -> dict | None:
    manifest = verified_entry(entry, key)
    if manifest is None:
        return None
    for name in OUTPUTS:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry / Path(name).name, destination)
        destination.chmod(0o755 if name.startswith("bin/") else 0o644)
    return manifest


def store(entry: Path, root: Path, key: str) -> dict:
    if entry.is_symlink():
        raise ValueError("Symlink native artifact cache is not allowed")
    manifest = {"schema": 1, "key": key, "outputs": {name: digest(root / name) for name in OUTPUTS}}
    entry.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        source = root / name
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=entry, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, temporary)
            temporary_path.replace(entry / source.name)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
    temporary = entry / "manifest.next.json"
    temporary.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    temporary.replace(entry / "manifest.json")
    return manifest


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if os.environ.get("RAGFLOW_ARTIFACT_TRANSPORT") == "receipt" and not re.fullmatch(r"[0-9a-f]{32}", os.environ.get("RAGFLOW_ARTIFACT_RUN_ID", "")):
        raise ValueError("Receipt transport requires its host lifecycle run")
    if os.environ.get("RAGFLOW_ARTIFACT_RUN_ID"):
        return _main()
    from tools.quality.artifact_lifecycle import managed

    return managed("native-artifacts", category="cache", days=30)(_main)()


def _main() -> None:
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--libraries", type=Path, required=True)
    parser.add_argument("--toolchain", required=True)
    args = parser.parse_args()
    tracked = os.environ.get("RAGFLOW_ARTIFACT_TRANSPORT") != "receipt"
    if not tracked and not re.fullmatch(r"[0-9a-f]{32}", os.environ.get("RAGFLOW_ARTIFACT_RUN_ID", "")):
        raise ValueError("Receipt transport requires its host lifecycle run")
    if tracked:
        from tools.quality.artifact_lifecycle import note, note_all

        note(args.root / "output/ci-native/artifacts.json", category="metadata")
        note(args.cache, category="cache")
        note(args.libraries, category="runtime")
        for name in OUTPUTS:
            note(args.root / name, category="cache")
    output = args.root / "output/ci-native/artifacts.json"
    entry = None
    failure = None
    try:
        started = time.monotonic()
        output.parent.mkdir(parents=True, exist_ok=True)
        pending = {
            "schema": 1,
            "status": "INCOMPLETE",
            "toolchain": args.toolchain,
            "expected_outputs": list(OUTPUTS),
            "artifact_run_id": os.environ.get("RAGFLOW_ARTIFACT_RUN_ID"),
            "invocation": os.environ.get("RAGFLOW_NATIVE_INVOCATION"),
        }
        output.write_text(json.dumps(pending) + "\n")
        inputs = identity(args.root, args.libraries, args.toolchain)
        key = cache_key(inputs)
        output.write_text(json.dumps({**pending, "key": key}) + "\n")
        args.cache.mkdir(parents=True, exist_ok=True)
        entry = args.cache / key
        with (args.cache / f"{key}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest = restore(entry, args.root, key)
            hit = manifest is not None
            if hit and cache_key(identity(args.root, args.libraries, args.toolchain)) != key:
                raise ValueError("Native inputs changed during cache restoration")
            if not hit:
                subprocess.run(CPP_COMMAND, cwd=args.root, check=True)
                environment = {**os.environ, "GOFLAGS": " ".join(filter(None, (os.environ.get("GOFLAGS", ""), *GO_FLAGS)))}
                # Go's build cache does not track external cgo static libraries.
                # Reuse its incremental cache only within the same native-library set.
                namespace = cache_key({"libraries": inputs["libraries"], "toolchain": args.toolchain, "tokenizer": digest(args.root / OUTPUTS[2])})
                environment["GOCACHE"] = str(Path(os.environ["GOCACHE"]) / namespace)
                subprocess.run(GO_COMMAND, cwd=args.root, env=environment, check=True)
                if cache_key(identity(args.root, args.libraries, args.toolchain)) != key:
                    raise ValueError("Native inputs changed during compilation")
                manifest = store(entry, args.root, key)
        receipt = {**manifest, "status": "PASS", "cache_hit": hit, "toolchain": args.toolchain, "seconds": round(time.monotonic() - started, 3)}
        if os.environ.get("RAGFLOW_ARTIFACT_RUN_ID"):
            receipt["artifact_run_id"] = os.environ["RAGFLOW_ARTIFACT_RUN_ID"]
        if os.environ.get("RAGFLOW_NATIVE_INVOCATION"):
            receipt["invocation"] = os.environ["RAGFLOW_NATIVE_INVOCATION"]
        output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        print(json.dumps(receipt, sort_keys=True), flush=True)
    except BaseException as error:
        failure = error
        raise
    finally:
        if tracked:
            entries = [(output, "metadata", output.is_file()), (args.cache, "cache", False)]
            if entry is not None:
                entries.append((entry, "cache", False))
            entries.extend((args.root / name, "cache", False) for name in OUTPUTS)
            note_all(entries, primary=failure)


if __name__ == "__main__":
    main()
