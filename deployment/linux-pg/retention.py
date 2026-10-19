#!/usr/bin/env python3
"""Retain two verified rollback installations after successful RAGFlow upgrades."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


KEEP = 2
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=300).strip()


def read_env(path):
    return {key: value.strip().strip("\"'") for line in path.read_text().splitlines() if "=" in line and not line.startswith("#") for key, value in [line.split("=", 1)]}


def verified_file(folder, relative):
    path = folder / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder):
        raise ValueError("Missing or linked upgrade receipt file")
    expected = {}
    for line in (folder / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  \./(.+)", line)
        if match:
            expected[match[2]] = match[1]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected.get(relative):
        raise ValueError("Upgrade receipt checksum mismatch")
    return path


def checked_path(path, parent, pattern):
    if path.parent != parent or path.is_symlink() or path.resolve() != path or not re.fullmatch(pattern, path.name):
        raise ValueError("Unexpected installation path")
    return path


def receipts(root, backups, project):
    records = []
    for folder in sorted(backups.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or not (folder / "UPGRADE.env").exists():
            continue
        if not (folder / "UPGRADE-COMPLETED.env").exists() or (folder / "UPGRADE-FAILED.env").exists():
            continue
        data = read_env(verified_file(folder, "UPGRADE.env"))
        verified_file(folder, "UPGRADE-COMPLETED.env")
        config = read_env(verified_file(folder, "config/docker.env"))
        if data.get("INSTALL_DIR") != str(root) or data.get("PROJECT_NAME") != project:
            continue
        previous = checked_path(Path(data["PREVIOUS_DIR"]), root.parent, re.escape(root.name) + r"\.previous-\d{8}T\d{6}Z")
        records.append({"directory": str(previous), "backup": str(folder), "config": config, "version": data["TO_VERSION"].removeprefix("v")})
    if len({record["directory"] for record in records}) != len(records):
        raise ValueError("Conflicting upgrade receipts")
    return sorted(records, key=lambda item: item["directory"])


def docker_images():
    ids = sorted(set(command("docker", "image", "ls", "-aq", "--no-trunc").split()))
    return {item["Id"]: item for item in json.loads(command("docker", "image", "inspect", *ids))}


def containers():
    ids = command("docker", "ps", "-aq").split()
    return json.loads(command("docker", "inspect", *ids))


def identity(items):
    return {
        item["Id"]: {
            "image": item["Image"],
            "started": item["State"]["StartedAt"],
            "status": item["State"]["Status"],
            "restarts": item["RestartCount"],
            "config": item.get("Config"),
            "host_config": item.get("HostConfig"),
            "mounts": sorted(item["Mounts"], key=lambda mount: mount["Destination"]),
        }
        for item in items
    }


def resolve(reference, images):
    # Mutable tags cannot identify the historical image safely.
    if not DIGEST.fullmatch(reference) and not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", reference):
        raise ValueError("Rollback image reference must be immutable")
    if reference in images:
        return reference
    for image in images.values():
        if reference in (image.get("RepoDigests") or []):
            return image["Id"]
    return None


def referenced_images(config, images, required):
    result = set()
    if not config.get("RAGFLOW_IMAGE"):
        raise ValueError("Missing RAGFlow image reference")
    for key, value in config.items():
        if key.endswith("_IMAGE"):
            if key == "RAGFLOW_IMAGE":
                image = resolve(value, images)
            else:
                image = next((item["Id"] for item in images.values() if value == item["Id"] or value in (item.get("RepoTags") or []) or value in (item.get("RepoDigests") or [])), None)
            if required and image is None:
                raise ValueError("Retained rollback image is missing")
            if image:
                result.add(image)
    return result


def make_plan(root, backups, secrets, project, current_containers, images):
    if (secrets / "upgrade-recovery-required.env").exists():
        raise ValueError("Upgrade recovery is pending")
    current = read_env(secrets / "deployed-source.env")
    records = receipts(root, backups, project)
    if len(records) < KEEP:
        raise ValueError("Two verified rollback receipts are required")
    kept, retired = records[-KEEP:], records[:-KEEP]
    latest = kept[-1]
    if current.get("BACKUP_DIR") != latest["backup"] or current.get("PREVIOUS_DIR") != latest["directory"]:
        raise ValueError("Installed release does not match the latest successful upgrade")
    version = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(), re.MULTILINE)
    if not version or version[1] != latest["version"] or current.get("RELEASE_VERSION", "").removeprefix("v") != latest["version"]:
        raise ValueError("Installed version does not match the successful upgrade receipt")
    current_config = read_env(root / "docker/.env")
    protected = {item["Image"] for item in current_containers} | referenced_images(current_config, images, True)
    for record in kept:
        directory = Path(record["directory"])
        if not directory.is_dir():
            raise ValueError("A retained rollback directory is missing")
        if read_env(directory / "docker/.env") != record["config"]:
            raise ValueError("Retained rollback configuration differs from its verified backup")
        protected |= referenced_images(record["config"], images, True)
    known = {record["directory"] for record in records}
    unknown = [path for path in root.parent.glob(root.name + ".previous-*") if str(path) not in known]
    for directory in unknown:
        checked_path(directory, root.parent, re.escape(root.name) + r"\.previous-\d{8}T\d{6}Z")
        protected |= referenced_images(read_env(directory / "docker/.env"), images, True)
    old_ids = set()
    unresolved = []
    for record in retired:
        try:
            image = resolve(record["config"]["RAGFLOW_IMAGE"], images)
        except ValueError:
            if Path(record["directory"]).exists():
                unresolved.append(record["directory"])
            continue
        if image:
            old_ids.add(image)
    old_layers = [images[image]["RootFS"]["Layers"] for image in old_ids]
    cutoff = datetime.strptime(retired[-1]["directory"].rsplit(".previous-", 1)[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if retired else None
    candidates = []
    for image in images.values():
        layers = image["RootFS"]["Layers"]
        tags = image.get("RepoTags") or []
        owned = not tags or all(tag.startswith("local/ragflow:") or re.fullmatch(r"[^\s]+/ragflow:[^/\s]+", tag) for tag in tags)
        ancestor = any(layers == branch[: len(layers)] for branch in old_layers)
        created = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", image["Created"]).replace("Z", "+00:00"))
        if image["Id"] not in protected and owned and cutoff and created <= cutoff and (image["Id"] in old_ids or ancestor):
            candidates.append(image)
    directories = [Path(record["directory"]) for record in retired if Path(record["directory"]).exists() and record["directory"] not in unresolved]
    # Archives or mounted paths may contain recovery data; do not retire them.
    for path in directories:
        checked_path(path, root.parent, re.escape(root.name) + r"\.previous-\d{8}T\d{6}Z")
        if any(protected_path == path or protected_path.is_relative_to(path) for protected_path in (root, backups, secrets)):
            raise ValueError("Obsolete installation overlaps a protected path")
        for item in path.rglob("*"):
            if item.is_file() and item.name.endswith((".dump", ".bak", ".backup", ".tar", ".tar.gz", ".tgz", ".zip")):
                raise ValueError("Possible backup in an obsolete installation")
    return {
        "keep": [record["directory"] for record in kept],
        "directories": directories,
        "images": candidates,
        "unknown_preserved": list(map(str, unknown)) + unresolved,
        "protected_images": protected,
        "cache_before": cutoff,
    }


def assert_unused(paths, items):
    def touches(value):
        return any(value == str(path) or value.startswith(str(path) + "/") for path in paths)

    for item in items:
        if any(touches(mount["Source"]) for mount in item["Mounts"]):
            raise ValueError("An obsolete installation is mounted by a container")
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            for line in (proc / "mountinfo").read_text().splitlines():
                if any(touches(value) for value in line.split()[3:5]):
                    raise ValueError("An obsolete installation is mounted by a process")
            for link in [proc / "cwd", proc / "root", *(proc / "fd").iterdir()]:
                try:
                    if touches(os.readlink(link).removesuffix(" (deleted)")):
                        raise ValueError("An obsolete installation is open in a process")
                except OSError:
                    continue
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


def file_hashes(paths):
    result = {}
    for root in paths:
        for path in [root] if root.is_file() else root.rglob("*"):
            if path.is_symlink():
                result[str(path)] = os.readlink(path)
            elif path.is_file():
                hasher = hashlib.sha256()
                with path.open("rb") as stream:
                    for data in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(data)
                result[str(path)] = hasher.hexdigest()
    return result


def old_cache(cutoff):
    if cutoff is None:
        return []
    rows = {row["ID"]: row for row in map(json.loads, command("docker", "buildx", "du", "--format", "{{json .}}").splitlines())}
    owned = set()
    pending = [row["ID"] for row in rows.values() if "/ragflow/" in row.get("Description", "") and (row["Description"].startswith("[2/") or row["Description"].startswith("mount / from exec "))]
    while pending:
        identifier = pending.pop()
        if identifier in owned or identifier not in rows:
            continue
        owned.add(identifier)
        pending.extend(rows[identifier].get("Parents") or [])
    result = []
    for row in rows.values():
        if row["ID"] not in owned or not row["Reclaimable"] or row["Shared"]:
            continue
        if "+0000" not in row["CreatedAt"]:
            raise ValueError("Unknown build cache timestamp format")
        created = datetime.strptime(row["CreatedAt"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if created <= cutoff:
            result.append(row["ID"])
    return result


def prune_cache(ids):
    if not ids:
        return
    selector = "id~=^(" + "|".join(re.escape(value) for value in ids) + ")$"
    # Boolean filters on older Buildx versions do not implement key=false.
    # Select explicit IDs and let Docker exclude records acquired by a build.
    command("docker", "buildx", "prune", "--filter", selector, "--force")
    remaining = {json.loads(line)["ID"] for line in command("docker", "buildx", "du", "--format", "{{json .}}").splitlines()}
    if remaining.intersection(ids):
        raise ValueError("Some obsolete cache records remain; retry after the active build")


def volume_state():
    names = command("docker", "volume", "ls", "-q").split()
    return {item["Name"]: item for item in json.loads(command("docker", "volume", "inspect", *names))}


def healthy(items, project):
    app = next(
        item for item in items if (item["Config"].get("Labels") or {}).get("com.docker.compose.project") == project and item["Config"]["Labels"].get("com.docker.compose.service") == "ragflow-cpu"
    )
    if any(item["State"].get("Health", {}).get("Status", "healthy") != "healthy" for item in items if item["State"]["Status"] == "running"):
        raise ValueError("A running container is unhealthy")
    data = json.loads(command("docker", "exec", app["Id"], "curl", "-fsS", "--max-time", "15", "http://localhost:9380/api/v1/system/healthz"))
    if any(data.get(key) != "ok" for key in ("status", "db", "doc_engine", "redis", "storage")):
        raise ValueError("RAGFlow is unhealthy")


def run(root, backups, secrets, project, apply=False):
    before = containers()
    images = docker_images()
    plan = make_plan(root, backups, secrets, project, before, images)
    healthy(before, project)
    assert_unused(plan["directories"], before)
    report = {
        "keep": plan["keep"],
        "unknown_preserved": plan["unknown_preserved"],
        "remove_images": [item["Id"] for item in plan["images"]],
        "remove_directories": list(map(str, plan["directories"])),
        "status": "PLANNED",
    }
    cache = old_cache(plan["cache_before"])
    report["remove_cache_records"] = cache
    if not apply or not (plan["images"] or plan["directories"] or cache):
        report["status"] = "NO_WORK" if not (plan["images"] or plan["directories"] or cache) else "PLANNED"
        return report
    protected_paths = [backups, *map(Path, plan["keep"]), root / "docker/.env", root / "pyproject.toml", root / "web/dist/index.html", root / "deployment/linux-pg"]
    protected_files = file_hashes(protected_paths)
    volumes = volume_state()
    space = shutil.disk_usage(root).free
    for image in sorted(plan["images"], key=lambda item: (len(item["RootFS"]["Layers"]), item["Created"]), reverse=True):
        if identity(containers()) != identity(before) or image["Id"] in plan["protected_images"]:
            raise ValueError("Container state changed before cleanup")
        fresh = json.loads(command("docker", "image", "inspect", image["Id"]))[0]
        if set(fresh.get("RepoTags") or []) != set(image.get("RepoTags") or []):
            raise ValueError("Image tags changed before cleanup")
        for tag in fresh.get("RepoTags") or []:
            if command("docker", "image", "inspect", tag, "--format", "{{.Id}}") != image["Id"]:
                raise ValueError("Image tag now refers to a different image")
        command("docker", "image", "rm", *(image.get("RepoTags") or [image["Id"]]))
    for path in plan["directories"]:
        checked_path(path, root.parent, re.escape(root.name) + r"\.previous-\d{8}T\d{6}Z")
        assert_unused([path], containers())
        shutil.rmtree(path)
    cache = old_cache(plan["cache_before"])
    prune_cache(cache)
    report["remove_cache_records"] = cache
    if identity(containers()) != identity(before) or volume_state() != volumes or file_hashes(protected_paths) != protected_files:
        raise ValueError("Protected resources changed during cleanup")
    after_images = docker_images()
    if any(image not in after_images for image in plan["protected_images"]):
        raise ValueError("A protected image disappeared")
    if any(item["Id"] in after_images for item in plan["images"]):
        raise ValueError("An obsolete image remains")
    healthy(containers(), project)
    report.update(status="COMPLETE", freed_bytes=shutil.disk_usage(root).free - space)
    return report


def main():
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", type=Path, default=Path("/opt/ragflow-pg"))
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/ragflow-pg"))
    parser.add_argument("--secrets-dir", type=Path, default=Path("/etc/ragflow-pg"))
    parser.add_argument("--project", default="ragflow-pg")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root, backups, secrets = args.install_dir, args.backup_root, args.secrets_dir
    for path in (root, backups, secrets):
        if not path.is_absolute() or path.resolve() != path or path == Path("/") or not path.is_dir():
            raise ValueError("Expected an existing absolute non-linked directory")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        raise ValueError("Invalid Compose project")
    with open(f"/run/lock/{args.project}.upgrade.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            result = run(root, backups, secrets, args.project, args.apply)
        except Exception:
            if args.apply:
                write_report(root, {"status": "ERROR", "checked_at": datetime.now(timezone.utc).isoformat()})
            raise
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        if args.apply:
            write_report(root, result)
        print(json.dumps(result))


def write_report(root, data):
    directory = root / "docker/ragflow-logs/maintenance"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / ".retention.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False))
    os.chmod(temporary, 0o644)
    os.replace(temporary, directory / "retention.json")


if __name__ == "__main__":
    main()
