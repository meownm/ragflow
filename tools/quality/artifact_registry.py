"""Read-only artifact inventory. Observations never authorize resource deletion."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
AREAS = {"archives": "evidence", "backups": "backup", "runtime": "runtime", "quality": "dataset", "cache": "cache", "tools": "tool", "maintenance": "metadata"}
OUTPUTS = {
    "web/dist": "build",
    "web/node_modules": "tool",
    ".venv": "tool",
    "ragflow_deps": "tool",
    ".pytest_cache": "cache",
    ".ruff_cache": "cache",
    "bin": "build",
    "internal/binding/cpp/cmake-build-release": "build",
    "output/ci-image": "build",
    "output/ci-native": "build",
    "output/ci-engine": "evidence",
    "test/playwright/artifacts": "evidence",
    "logs": "log",
}
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}$")
OWNER_LABELS = (
    "org.ragflow.artifact.project",
    "org.ragflow.artifact.task",
    "org.ragflow.artifact.run",
    "org.ragflow.sandbox.manager",
    "org.ragflow.sandbox.pool",
    "org.ragflow.ci-dry-run",
    "ragflow.task",
    "codex.task",
    "com.docker.compose.project",
)


def linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def unlinked(path: Path) -> None:
    for item in [*reversed(path.parents), path]:
        try:
            if linked(item.lstat()):
                raise ValueError(f"Linked inventory path: {item}")
        except FileNotFoundError:
            continue


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, encoding="utf-8", errors="replace", timeout=90).strip()


def docker_objects(kind: str) -> list[dict]:
    options = ("-q",) if kind in {"volume", "network"} else ("-aq", "--no-trunc")
    identifiers = sorted(set(command("docker", kind, "ls", *options).split()))
    result = []
    for offset in range(0, len(identifiers), 40):
        result.extend(json.loads(command("docker", kind, "inspect", *identifiers[offset : offset + 40])))
    return result


def owners(labels: dict | None) -> list[str]:
    return sorted({f"{name}={labels[name]}" for name in OWNER_LABELS if (labels or {}).get(name)})


def project_path(value: str, roots: list[Path]) -> bool:
    value = re.sub(r"^/run/desktop/mnt/host/([a-zA-Z])/", r"\1:/", value)
    normalized = ntpath.normcase(ntpath.normpath(value))
    return any(normalized == (root_path := ntpath.normcase(ntpath.normpath(str(root)))) or normalized.startswith(root_path + "\\") for root in roots)


def project_labels(labels: dict | None) -> bool:
    labels = labels or {}
    project = labels.get("org.ragflow.artifact.project", labels.get("com.docker.compose.project", "")).lower()
    return project == "ragflow" or project.startswith("ragflow-") or bool(labels.get("org.ragflow.ci-dry-run"))


def project_selection(records: list[dict], containers: list[dict], repositories: list[Path], temp_root: Path) -> tuple[list[dict], dict]:
    """Select RAGFlow resources, retaining global consumers of shared dependencies."""
    roots = [*repositories, temp_root]
    owned = set()

    def hint(value):
        return bool(re.search(r"(^|[/_-])ragflow([:/_-]|$)", value, re.IGNORECASE))

    image_hints = {r["identity"] for r in records if r["kind"] == "image" and any(hint(value) for value in r.get("tags", []))}
    for container in containers:
        labels = container.get("Config", {}).get("Labels") or {}
        paths = [m["Source"] for m in container.get("Mounts", []) if m["Type"] == "bind"]
        paths.extend(labels.get("com.docker.compose.project.working_dir", "").split(","))
        if project_labels(labels) or any(project_path(p, roots) for p in paths if p):
            owned.add(container["Id"])
    sandbox_images = {
        value.split("=", 1)[1]
        for container in containers
        if container["Id"] in owned
        for value in container.get("Config", {}).get("Env", [])
        if value.partition("=")[0] in {"SANDBOX_BASE_PYTHON_IMAGE", "SANDBOX_BASE_NODEJS_IMAGE"} and "=" in value
    }
    hinted = {c["Id"] for c in containers if c.get("Image") in image_hints or c.get("Config", {}).get("Image") in sandbox_images or hint(c.get("Name", "").lstrip("/"))}
    selected, omitted = [], Counter()
    for record in records:
        if record["kind"] == "container":
            reason = "project-container" if record["identity"] in owned else "container-hint-unconfirmed" if record["identity"] in hinted else None
        else:
            reason = "project-consumer" if owned.intersection(record.get("consumers", [])) else None
            if not reason and hinted.intersection(record.get("consumers", [])):
                reason = "hint-consumer-unconfirmed"
            if not reason and record.get("receipts"):
                reason = "project-receipt"
            if not reason and project_labels(dict(value.split("=", 1) for value in record.get("owners", []))):
                reason = "project-label"
            # Hints include orphan build generations in the review queue, never deletion eligibility.
            hints = record.get("tags", []) if record["kind"] == "image" else [record.get("name", "")]
            if not reason and any(re.search(r"(^|[/_-])ragflow([:/_-]|$)", hint, re.IGNORECASE) for hint in hints):
                reason = "name-hint-unconfirmed"
        if reason:
            selected.append({**record, "scope_basis": reason})
        else:
            omitted[record["kind"]] += 1
    return selected, dict(omitted)


def docker_records(engine: str, containers: list[dict], images: list[dict], volumes: list[dict], pins: dict[str, list[str]], networks=None) -> list[dict]:
    """Include stopped consumers and exact receipt references; names are not evidence."""
    image_refs, volume_refs = {}, {}
    result = []
    from tools.quality.sandbox_pool import child_owner

    for container in containers:
        identity = container["Id"]
        sandbox = child_owner(container)
        image_refs.setdefault(container["Image"], []).append(identity)
        for mount in container.get("Mounts", []):
            if mount["Type"] == "volume":
                volume_refs.setdefault(mount["Name"], []).append(identity)
        result.append(
            {
                "id": f"docker:{engine}:container:{identity}",
                "kind": "container",
                "identity": identity,
                "name": container["Name"].lstrip("/"),
                "owners": owners(container.get("Config", {}).get("Labels")),
                "running": container.get("State", {}).get("Running"),
                "created_at": container.get("Created"),
                "image_id": container["Image"],
                **({"sandbox": sandbox, "category": "disposable"} if sandbox else {}),
                "bind_sources": [m["Source"] for m in container.get("Mounts", []) if m["Type"] == "bind"],
                "status": "protected",
                "blockers": ["container-exists"],
                "auto_delete": False,
            }
        )
    for image in images:
        identity = image["Id"]
        consumers = sorted(image_refs.get(identity, []))
        receipts = sorted({p for reference in [identity, *(image.get("RepoDigests") or [])] for p in pins.get(reference, [])})
        blockers = (["container-reference"] if consumers else []) + (["receipt-reference"] if receipts else [])
        result.append(
            {
                "id": f"docker:{engine}:image:{identity}",
                "kind": "image",
                "identity": identity,
                "tags": sorted(image.get("RepoTags") or []),
                "digests": sorted(image.get("RepoDigests") or []),
                "created_at": image.get("Created"),
                "logical_bytes": image.get("Size"),
                "reclaimable_bytes": None,
                "owners": owners(image.get("Config", {}).get("Labels")),
                "consumers": consumers,
                "receipts": receipts,
                "status": "protected" if blockers else "needs-review",
                "blockers": blockers or ["retirement-unconfirmed"],
                "auto_delete": False,
            }
        )
    for volume in volumes:
        name = volume["Name"]
        consumers = sorted(volume_refs.get(name, []))
        generation = {key: volume.get(key) for key in ("Name", "CreatedAt", "Driver", "Scope")}
        identity = hashlib.sha256(json.dumps(generation, sort_keys=True).encode()).hexdigest()
        result.append(
            {
                "id": f"docker:{engine}:volume:{name}:{identity}",
                "kind": "volume",
                "identity": generation,
                "name": name,
                "created_at": volume.get("CreatedAt"),
                "owners": owners(volume.get("Labels")),
                "consumers": consumers,
                "size_bytes": None,
                "status": "protected" if consumers else "needs-review",
                "blockers": ["container-reference"] if consumers else ["data-retirement-unconfirmed"],
                "auto_delete": False,
            }
        )
    for network in networks or []:
        result.append(
            {
                "id": f"docker:{engine}:network:{network['Id']}",
                "kind": "network",
                "identity": network["Id"],
                "name": network["Name"],
                "created_at": network.get("Created"),
                "owners": owners(network.get("Labels")),
                "consumers": sorted(network.get("Containers", {})),
                "status": "protected",
                "auto_delete": False,
                "blockers": ["network-owner-retirement-required"],
            }
        )
    return result


def file_references(value) -> set[str]:
    if isinstance(value, str):
        return {value} if IMAGE_ID.fullmatch(value) or ("@sha256:" in value and IMAGE_ID.fullmatch(value.rsplit("@", 1)[-1])) else set()
    if isinstance(value, dict):
        return set().union(*(file_references(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(file_references(v) for v in value)) if value else set()
    return set()


def walk_files(root: Path):
    """Never follow a link/reparse point, including an ancestor supplied by the caller."""
    unlinked(root)
    if not root.exists():
        return
    pending = [root]
    while pending:
        current = pending.pop()
        unlinked(current)
        with os.scandir(current) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if linked(info):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    yield Path(entry.path), info


def read_receipts(repositories: list[Path], errors: list[str]):
    for repo in repositories:
        # Small authoritative build/deployment receipts, not arbitrary archives or .env.
        for area in (repo / ".local/cache/deploy", repo / "output/local-deploy", repo / ".local/runtime"):
            try:
                unlinked(area)
                if area == repo / "output/local-deploy":
                    # Deployment runs own these direct receipts, not JSON inside source snapshots.
                    files = ((path, path.lstat()) for path in area.glob("*/deployment.json"))
                else:
                    files = walk_files(area)
                for path, info in files:
                    if path.suffix != ".json" or (area == repo / ".local/runtime" and path.name != "deployment.json"):
                        continue
                    try:
                        unlinked(path)
                        if info.st_size > 2_000_000:
                            raise ValueError("Receipt exceeds supported size")
                        yield path, json.loads(path.read_bytes())
                    except (OSError, ValueError, RecursionError):
                        errors.append(f"unreadable-receipt:{path}")
            except (OSError, ValueError) as exc:
                errors.append(f"receipt-scan:{area}:{type(exc).__name__}")


def receipt_pins(repositories: list[Path], errors: list[str], *, receipts=None) -> dict[str, list[str]]:
    pins = {}
    for path, value in read_receipts(repositories, errors) if receipts is None else receipts:
        for reference in file_references(value):
            pins.setdefault(reference, []).append(str(path))
    return pins


def backup_records(receipts, repositories, errors):
    records = []
    for receipt, value in receipts:
        backup = value.get("backup") if isinstance(value, dict) else None
        if receipt.name != "deployment.json" or not isinstance(backup, dict) or not isinstance(backup.get("path"), str):
            continue
        path = Path(backup["path"]).absolute()
        owned = next((repo for repo in repositories if path.is_relative_to(repo / ".local") or path.is_relative_to(repo / "output/local-deploy")), None)
        try:
            if owned and os.path.lexists(path):
                record = path_record(path, "backup", owned, False)
            else:
                record = {
                    "id": "backup-reference:" + hashlib.sha256(str(path).encode()).hexdigest(),
                    "kind": "backup-reference",
                    "identity": str(path),
                    "locator": str(path),
                    "category": "backup",
                    "auto_delete": False,
                    "blockers": ["backup-source-missing" if owned else "backup-source-outside-declared-scope"],
                }
            record.update(receipts=[str(receipt)], reported_sha256=backup.get("sha256"), verification="reported-checksum-only")
            records.append(record)
        except (OSError, ValueError) as error:
            errors.append(f"backup-reference:{receipt}:{type(error).__name__}")
    return records


def declared_path_exists(entry):
    path = Path(entry["path"])
    unlinked(path)
    try:
        info = path.stat()
    except FileNotFoundError:
        return False
    return [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)] == entry.get("identity")


def path_record(path: Path, category: str, repo: Path | None, measure: bool) -> dict:
    unlinked(path.parent)
    info = path.lstat()
    generation = [info.st_dev, info.st_ino, getattr(info, "st_birthtime_ns", 0)]
    result = {
        "id": "path:" + hashlib.sha256((os.path.normcase(str(path)) + json.dumps(generation)).encode()).hexdigest(),
        "kind": "path",
        "path": str(path),
        "identity": generation,
        "category": category,
        "project": str(repo) if repo else None,
        "status": "protected",
        "auto_delete": False,
        "blockers": ["owner-retention-unconfirmed"],
        "size_bytes": None,
        "created_at": datetime.fromtimestamp(getattr(info, "st_birthtime", info.st_mtime), timezone.utc).isoformat(),
        "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
    }
    if linked(info):
        result["blockers"] = ["link-not-followed"]
        return result
    if category == "task":
        result["markers"] = [name for name in (".keep", ".ragflow-temp", ".process-job", ".lease") if os.path.lexists(path / name)] if path.is_dir() else []
        result["blockers"] = ["existing-workspace-cleaner-only"]
        if ".keep" in result["markers"]:
            result["blockers"].append("task-pin")
    if measure:
        result["size_bytes"] = sum(entry.st_size for _, entry in walk_files(path)) if stat.S_ISDIR(info.st_mode) else info.st_size
    return result


def filesystem_records(repositories: list[Path], temp_root: Path, measure: bool, errors: list[str]) -> list[dict]:
    result = []
    areas = [(repo / ".local" / area, category, repo) for repo in repositories for area, category in AREAS.items()]
    areas.extend((repo / "output/local-deploy", "evidence", repo) for repo in repositories)
    areas.append((temp_root, "task", None))
    for area, category, repo in areas:
        try:
            unlinked(area)
            if area.exists():
                for child in sorted(area.iterdir()):
                    if area.name == "maintenance" and child.name == "artifacts":
                        continue  # Do not count the replaceable index as an artifact of itself.
                    result.append(path_record(child, category, repo, measure))
        except (OSError, ValueError) as exc:
            errors.append(f"path-scan:{area}:{type(exc).__name__}")
    for repo in repositories:
        for relative in OUTPUTS:
            path = repo / relative
            try:
                if os.path.lexists(path):
                    result.append(path_record(path, OUTPUTS[relative], repo, measure))
            except (OSError, ValueError) as exc:
                errors.append(f"path-scan:{path}:{type(exc).__name__}")
        if repo == ROOT:
            for path, category in (
                (Path(os.environ.get("RAGFLOW_GO_CACHE_ROOT", Path.home() / ".cache/ragflow-go-builder")), "cache"),
                (Path.home() / ".cache/ragflow-feature", "cache"),
                (Path(os.environ.get("RAGFLOW_NATIVE_CACHE", Path.home() / "ragflow-native-libs")), "tool"),
            ):
                try:
                    if os.path.lexists(path):
                        result.append(path_record(path.absolute(), category, repo, measure))
                except (OSError, ValueError) as exc:
                    errors.append(f"declared-home-path:{path}:{type(exc).__name__}")
    return result


def inventory(repositories: list[Path], temp_root: Path, *, measure: bool = False, previous=None) -> dict:
    errors = []
    receipts = list(read_receipts(repositories, errors))
    pins = receipt_pins(repositories, errors, receipts=receipts)
    records = filesystem_records(repositories, temp_root, measure, errors)
    for backup in backup_records(receipts, repositories, errors):
        existing = next((record for record in records if record["id"] == backup["id"]), None)
        if existing:
            existing["receipts"] = sorted(set(existing.get("receipts", []) + backup["receipts"]))
            existing.update(reported_sha256=backup["reported_sha256"], verification=backup["verification"])
        else:
            records.append(backup)
    engine = None
    omitted = {}
    lifecycle = []
    from tools.quality.artifact_lifecycle import runs, eligible, consumption_closed

    for repository in repositories:
        state = Path(os.environ.get("RAGFLOW_ARTIFACT_STATE", repository / ".local/maintenance/artifacts"))
        try:
            for run in runs(state):
                lifecycle.append(
                    {
                        key: run.get(key)
                        for key in (
                            "run_id",
                            "producer",
                            "task_ref",
                            "purpose",
                            "source",
                            "state",
                            "task_status",
                            "created_at",
                            "completed_at",
                            "engine",
                            "category",
                            "keep_days",
                            "cleanup",
                            "receipts",
                            "resources",
                            "assessments",
                            "review_sequence",
                            "image_dependencies",
                            "recovery_dependencies",
                            "consumption_closed",
                            "consumption_history",
                            "runtime_project",
                            "runtime_observations",
                            "input_files",
                            "usage_epoch",
                            "handoff",
                            "recovery_epoch",
                            "unresolved_image_dependencies",
                        )
                    }
                )
                lifecycle[-1].update(receipt=str(state / "runs" / (run["run_id"] + ".json")), retirement_blocker=eligible(run, datetime.now(timezone.utc)))
                lifecycle[-1]["inputs_consumption_state"] = "closed" if consumption_closed(run) else "protected"
                for key in ("resources", "input_files"):
                    lifecycle[-1][key] = [{**entry, **({"source_observed_exists": declared_path_exists(entry)} if entry.get("path") else {})} for entry in run.get(key, [])]
        except (OSError, ValueError, KeyError) as exc:
            errors.append(f"lifecycle-inventory:{type(exc).__name__}")
    try:
        context = command("docker", "context", "show")
        engine = json.loads(command("docker", "info", "--format", "{{json .ID}}"))
        if not engine:
            raise ValueError("Docker engine has no identity")
        containers, images, volumes = (docker_objects(kind) for kind in ("container", "image", "volume"))
        networks = docker_objects("network")
        selected, omitted = project_selection(docker_records(engine, containers, images, volumes, pins, networks), containers, repositories, temp_root)
        statistics = [json.loads(line) for line in command("docker", "system", "df", "--format", "{{json .}}").splitlines() if line.strip()]
        cache = next(row for row in statistics if row.get("Type") == "Build Cache")
        shared_cache = {
            "id": f"docker:{engine}:shared-build-cache",
            "kind": "build-cache",
            "identity": engine,
            "shared": True,
            "category": "cache",
            "status": "protected",
            "auto_delete": False,
            "usage": {key: cache.get(key) for key in ("Size", "Reclaimable", "TotalCount", "Active")},
            "blockers": ["shared-engine-cache-not-exclusive-to-ragflow"],
        }
        if context != command("docker", "context", "show") or engine != json.loads(command("docker", "info", "--format", "{{json .ID}}")):
            raise ValueError("Docker target changed during inventory")
        records.extend([*selected, shared_cache])
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        errors.append(f"docker-inventory:{type(exc).__name__}")
    from tools.quality.artifact_policy import apply

    return apply(
        {
            "schema": 1,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "mode": "read-only",
            "complete": not errors,
            "scope": {
                "repositories": [str(p) for p in repositories],
                "temp_root": str(temp_root),
                "docker_engine": engine,
                "project": "ragflow",
                "docker_selection": "project-ownership-dependencies-and-unconfirmed-ragflow-name-hints",
                "not_selected_docker_counts": omitted,
                "exclusions": ["remote-hosts-and-registries", "undeclared-filesystem-roots", "file-level-index", "live-process-and-lease-verification"],
            },
            "errors": errors,
            "lifecycle": lifecycle,
            "counts": dict(Counter(r["kind"] for r in records)),
            "records": sorted(records, key=lambda r: r["id"]),
        },
        previous,
    )


def save(report: dict, destination: Path) -> None:
    unlinked(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".inventory-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
    try:
        unlinked(destination)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, action="append", help="RAGFlow checkout; repeat only for additional checkouts of this project")
    parser.add_argument("--temp-root", type=Path, default=Path(ROOT.anchor) / "tmp/ragflow")
    parser.add_argument("--measure", action="store_true", help="Sum regular file lengths without following links; not disk reclamation")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/maintenance/artifacts/inventory.json")
    args = parser.parse_args()
    repositories = list(dict.fromkeys(Path(os.path.abspath(p)) for p in (args.repo or [ROOT])))
    previous = None
    unlinked(args.output.absolute())
    if args.output.exists():
        previous = json.loads(args.output.read_bytes())
    report = inventory(repositories, Path(os.path.abspath(args.temp_root)), measure=args.measure, previous=previous)
    save(report, Path(os.path.abspath(args.output)))
    print(json.dumps({"output": str(args.output), "complete": report["complete"], "counts": report["counts"], "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
