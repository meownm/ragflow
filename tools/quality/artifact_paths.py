"""Retire only verified cache entries; workspace and persistent data keep their owners."""

import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import stat
import tarfile
import tempfile

from tools.quality.artifact_registry import ROOT, save, unlinked, linked
from tools.quality.check_cache import cache_lock, read_success
from tools.quality.artifact_docker import archive_info, archive_root, verify_archive, digest


def evidence_entries(path: Path) -> dict:
    values, pending = {}, [path]
    while pending:
        folder = pending.pop()
        unlinked(folder)
        for child in folder.iterdir():
            info = child.lstat()
            name = child.relative_to(path).as_posix()
            row = {"mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns}
            if linked(info):
                row.update(type="link", target=str(child.readlink()))
            elif child.is_dir():
                row["type"] = "directory"
                pending.append(child)
            elif child.is_file():
                row.update(type="file", sha256=digest(child), size=info.st_size)
            else:
                raise ValueError("Unsupported evidence file type")
            values[name] = row
    return values


def temporary_evidence(path):
    ephemeral = Path(ROOT.anchor) / "tmp/ragflow"
    runner_temp = os.environ.get("RUNNER_TEMP")
    return os.environ.get("GITHUB_ACTIONS") == "true" or path.is_relative_to(ephemeral) or bool(runner_temp and path.is_relative_to(Path(runner_temp)))


def preserve_evidence(path: Path, store: Path, *, metadata_owner=None) -> dict | None:
    """Preserve temp outputs, or an active owner's exact declared metadata file."""
    expected = None
    if metadata_owner is not None:
        from tools.quality.artifact_lifecycle import current

        unlinked(path)
        if current() is not metadata_owner or metadata_owner.attached or metadata_owner.store != store or not path.is_file():
            raise ValueError("Persistent metadata snapshot requires its active declaring owner and a regular file")
        info = path.stat()
        generation = [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)]
        declarations = [*metadata_owner.data.get("input_files", []), *metadata_owner.data["resources"]]
        expected = digest(path)
        if not any(
            item.get("path") == str(path.absolute()) and item.get("identity") == generation and item.get("content_sha256") == expected and item.get("category") in {"config", "metadata", "evidence"}
            for item in declarations
        ):
            raise ValueError("Persistent metadata snapshot is not bound to this declared file generation")
    elif not temporary_evidence(path):
        return None
    root = archive_root(store) / "evidence"
    if root.is_relative_to(path) or path == ROOT or ROOT.is_relative_to(path):
        raise ValueError("Evidence must not contain the source repository or artifact store")
    unlinked(root)
    root.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        content_hash = digest(path)
        if expected is not None and content_hash != expected:
            raise ValueError("Declared metadata changed before snapshot")
        destination = root / (content_hash + ".file")
        unlinked(destination)
        if not destination.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                    temporary = Path(stream.name)
                    with path.open("rb") as source:
                        shutil.copyfileobj(source, stream)
                    stream.flush()
                if digest(temporary) != content_hash:
                    raise ValueError("Evidence changed while preserving")
                temporary.replace(destination)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        if digest(destination) != content_hash:
            raise ValueError("Preserved evidence integrity mismatch")
        info = path.stat()
        return {"kind": "evidence-file", "archive": archive_info(destination), "mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns}

    before = evidence_entries(path)
    signature = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    archive = root / (signature + ".tar.gz")
    unlinked(archive)
    temporary = None
    try:
        if not archive.exists():
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
                temporary = Path(stream.name)
                with tarfile.open(fileobj=stream, mode="w:gz", compresslevel=1) as bundle:
                    for name, row in sorted(before.items()):
                        if row["type"] == "link":
                            item = tarfile.TarInfo(name)
                            item.type, item.linkname = tarfile.SYMTYPE, row["target"]
                            bundle.addfile(item)
                        else:
                            bundle.add(path / name, arcname=name, recursive=False)
            if evidence_entries(path) != before:
                raise ValueError("Evidence changed while preserving")
            temporary.replace(archive)
        seen = set()
        with tarfile.open(archive, "r|gz") as bundle:
            for member in bundle:
                name = member.name.rstrip("/")
                row = before.get(name)
                if row is None or name in seen:
                    raise ValueError("Evidence archive member mismatch")
                seen.add(name)
                if member.isfile():
                    if row["type"] != "file" or hashlib.file_digest(bundle.extractfile(member), "sha256").hexdigest() != row["sha256"]:
                        raise ValueError("Evidence archive content mismatch")
                elif member.islnk():
                    if row["type"] != "file" or before.get(member.linkname, {}).get("sha256") != row["sha256"]:
                        raise ValueError("Evidence archive hardlink mismatch")
                elif member.issym():
                    if row["type"] != "link" or member.linkname != row["target"]:
                        raise ValueError("Evidence archive link mismatch")
                elif not member.isdir() or row["type"] != "directory":
                    raise ValueError("Evidence archive type mismatch")
        if seen != set(before):
            raise ValueError("Evidence archive is incomplete")
        manifest = archive.with_suffix(".manifest.json")
        save(before, manifest)
        return {"kind": "evidence-directory", "archive": archive_info(archive), "manifest": archive_info(manifest), "manifest_sha256": signature}
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def workspace_evidence_ready(workspace: Path, store: Path):
    from tools.quality.artifact_lifecycle import runs, current, lease
    from tools.quality.artifact_policy import PRESERVED_OUTPUT_CLASSES

    owner = current()
    for run in runs(store):
        overlap = any(
            entry.get("status") != "removed" and entry.get("path") and (Path(entry["path"]).is_relative_to(workspace) or workspace.is_relative_to(Path(entry["path"]))) for entry in run["resources"]
        )
        if overlap and run["state"] == "running" and (owner is None or owner.data["run_id"] != run["run_id"]):
            try:
                with lease(store, run["run_id"]):
                    pass
            except OSError as error:
                raise ValueError("Workspace has another active lifecycle user") from error
        for entry in run["resources"]:
            path = Path(entry.get("path", workspace.parent))
            if entry.get("category") in PRESERVED_OUTPUT_CLASSES and path.is_relative_to(workspace) and path.exists():
                if not entry.get("preserved_evidence"):
                    raise ValueError("Unique evidence must be preserved before workspace retirement")
                packet = entry["preserved_evidence"]
                if identity(path) != entry["identity"]:
                    raise ValueError("Evidence generation changed after preservation")
                verify_archive(packet["archive"])
                if packet["kind"] == "evidence-file":
                    if digest(path) != packet["archive"]["sha256"]:
                        raise ValueError("Evidence changed after preservation")
                else:
                    verify_archive(packet["manifest"])
                    if evidence_entries(path) != json.loads(Path(packet["manifest"]["path"]).read_bytes()):
                        raise ValueError("Evidence changed after preservation")


def identity(path: Path) -> list:
    info = path.stat()
    return [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)]


def cache_kind(path: Path) -> str | None:
    if path.suffix == ".json" and re.fullmatch(r"[0-9a-f]{64}", path.stem):
        roots = [ROOT / ".local/cache/deploy", Path.home() / ".cache/ragflow-feature"]
        if any(path.is_relative_to(root) for root in roots):
            return "receipt"
    if re.fullmatch(r"[0-9a-f]{64}", path.name) and path.parent.name == "artifacts":
        allowed = Path(os.environ.get("RAGFLOW_GO_CACHE_ROOT", Path.home() / ".cache/ragflow-go-builder")).absolute()
        if path.parent == allowed / "artifacts":
            return "native"
    return None


def guard(target: dict, run: dict, all_runs: list[dict], engine) -> str | None:
    path = Path(target["path"])
    unlinked(path)
    if not path.exists():
        return "already-absent"
    if target.get("category") != "cache" or not (kind := cache_kind(path)):
        return "owning-path-cleaner-or-persistent-policy"
    if identity(path) != target["identity"]:
        return "path-generation-changed"
    for other in all_runs:
        for entry in other["resources"]:
            if entry.get("path") and entry.get("status") != "removed" and other["run_id"] != run["run_id"]:
                other_path = Path(entry["path"])
                overlap = path.is_relative_to(other_path) or other_path.is_relative_to(path)
                if (overlap and other["state"] != "completed") or (other_path == path and other.get("completed_at", "") > run.get("completed_at", "")):
                    return "newer-or-unclosed-cache-user"
    # Dedicated cache mounts are consumers; a generic source checkout mount is not a cache lease.
    for container in engine.all("container"):
        for mount in container.get("Mounts", []):
            if mount["Type"] == "bind":
                source = Path(re.sub(r"^/run/desktop/mnt/host/([a-zA-Z])/", r"\1:/", mount["Source"]))
                cache_root = path.parent.parent if kind == "native" else ROOT / ".local/cache"
                if source.is_relative_to(cache_root) and (path.is_relative_to(source) or source.is_relative_to(path)):
                    return "cache-mounted"
    if os.name == "nt":
        executable = shutil.which("pwsh")
        if not executable:
            return "cannot-check-processes"
        script = "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID } | Select-Object -ExpandProperty CommandLine | ConvertTo-Json -Compress"
        commands = json.loads(subprocess.check_output([executable, "-NoProfile", "-Command", script], text=True, timeout=45) or "[]")
        if isinstance(commands, str):
            commands = [commands]
        if any(str(path).casefold() in (value or "").replace("/", "\\").casefold() for value in (commands or [])):
            return "cache-process-user"
    else:
        commands = subprocess.check_output(["ps", "-eo", "args="], text=True, timeout=30)
        if any(str(path) in command for command in commands.splitlines() if not command.startswith("ps -eo")):
            return "cache-process-user"
    if kind == "receipt":
        if read_success(path, path.stem) is None or digest(path) != target.get("content_sha256"):
            return "cache-receipt-changed"
    else:
        from deployment.runner.native_artifacts import verified_entry

        if target.get("recovery"):
            verify_archive(target["recovery"]["metadata"])
            manifest = json.loads(Path(target["recovery"]["metadata"]["path"]).read_bytes())
            for name, expected in manifest["outputs"].items():
                file = path / Path(name).name
                unlinked(file)
                if file.exists() and digest(file) != expected:
                    return "native-cache-changed"
        elif verified_entry(path, path.name) is None or digest(path / "manifest.json") != target.get("manifest_sha256"):
            return "native-cache-changed"
        if {p.name for p in path.iterdir()} - {"manifest.json", "ragflow_server", "ragflow-cli", "librag_tokenizer_c_api.a"}:
            return "unexpected-native-cache-files"
    return None


def retire(target: dict, run: dict, all_runs: list[dict], engine, store: Path, *, checkpoint) -> dict:
    path = Path(target["path"])
    if not cache_kind(path):
        return {"blocked": "owning-path-cleaner-or-persistent-policy"}
    key = path.stem if path.suffix == ".json" else path.name
    with cache_lock(path.parent, key):
        if reason := guard(target, run, all_runs, engine):
            return {"blocked": reason}
        folder = archive_root(store) / "cache-metadata" / target["key"].replace(":", "-")
        unlinked(folder)
        folder.mkdir(parents=True, exist_ok=True)
        packet = target.get("recovery")
        if not packet:
            original = path if path.is_file() else path / "manifest.json"
            preserved = folder / "manifest.json"
            save(json.loads(original.read_bytes()), preserved)
            packet = {
                "kind": "cache-file" if path.is_file() else "rebuildable-native-cache",
                "metadata": archive_info(preserved),
                "destination": str(path),
                "rebuild": "deployment/runner/go_builder.py -- --artifacts",
            }
            save(packet, folder / "recovery.json")
        checkpoint(packet)
        from tools.quality.artifact_lifecycle import mutation, runs

        with mutation(store):
            # Recheck late use with the same publication boundary used by producers.
            if reason := guard(target, run, runs(store), engine):
                return {"blocked": reason}
            if path.is_file():
                path.unlink()
            else:
                from tools.quality.workspace import _tree_stats, _task_lock

                with _task_lock(path):
                    _tree_stats(path)
                    manifest = json.loads(Path(packet["metadata"]["path"]).read_bytes())
                    expected = {Path(name).name: sha for name, sha in manifest["outputs"].items()}
                    expected["manifest.json"] = target["manifest_sha256"]
                    for name, sha in expected.items():
                        file = path / name
                        unlinked(file)
                        if file.exists():
                            if digest(file) != sha:
                                raise ValueError("Native cache changed during removal")
                            file.unlink()
                    path.rmdir()  # A late unrelated file survives and prevents directory removal.
        return {"recovery": packet}
