"""Retire stopped ephemeral sandbox children from explicit manager-generation labels."""

from pathlib import Path
import re
import tarfile


def child_owner(info: dict) -> dict | None:
    labels = info.get("Config", {}).get("Labels") or {}
    values = {key: labels.get("org.ragflow.sandbox." + key) for key in ("manager", "epoch", "pool", "kind", "cleanup")}
    if (
        labels.get("org.ragflow.artifact.project") != "ragflow"
        or labels.get("org.ragflow.artifact.class") != "disposable"
        or values["cleanup"] != "owner-lifetime"
        or not re.fullmatch(r"[0-9a-f]{64}", values["manager"] or "")
        or not re.fullmatch(r"[0-9a-f]{32}", values["pool"] or "")
        or not re.fullmatch(r"[0-9a-f]{32}", labels.get("org.ragflow.artifact.object", ""))
        or not values["epoch"]
        or values["kind"] not in {"pool", "private"}
    ):
        return None
    return values


def blocker(info: dict, containers: list[dict]) -> str | None:
    owner = child_owner(info)
    if owner is None:
        return "unverified-sandbox-producer"
    manager = next((c for c in containers if c["Id"] == owner["manager"]), None)
    if manager and manager.get("State", {}).get("StartedAt") == owner["epoch"] and any(manager["State"].get(k) for k in ("Running", "Paused", "Restarting")):
        return "manager-active"
    if any(info.get("State", {}).get(k) for k in ("Running", "Paused", "Restarting")) or info.get("ExecIDs"):
        return "orphan-child-active"
    host = info.get("HostConfig") or {}
    if not host.get("ReadonlyRootfs") or info.get("Mounts") or set(host.get("Tmpfs") or {}) - {"/workspace", "/tmp"}:
        return "persistent-or-unverified-sandbox-storage"
    return None


def preserve_readonly_child(info, engine, store, folder):
    """Preserve empty Docker-created tmpfs mountpoints, never arbitrary writable data."""
    from tools.quality.artifact_docker import archive_info, archive_root, resource
    from tools.quality.artifact_registry import save

    changes = engine.run("container", "diff", info["Id"])
    if not changes:
        return engine.backup(resource(info, "container"), store)
    mountpoints = []
    for line in changes.splitlines():
        action, _, path = line.partition(" ")
        if action != "A" or path not in {"/tmp", "/workspace"} or path not in info["HostConfig"].get("Tmpfs", {}):
            raise ValueError("Unexpected writable sandbox state")
        archive = folder / (path.lstrip("/") + ".tar.gz")
        raw_sha = engine.export(["container", "cp", info["Id"] + ":" + path, "-"], archive)
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if len(members) != 1 or not members[0].isdir() or members[0].name.rstrip("/") != path.lstrip("/"):
                raise ValueError("Sandbox mountpoint contains persistent data")
        mountpoints.append({"mountpoint": path, "archive": archive_info(archive), "raw_sha256": raw_sha})
    save(info, folder / "inspect-private.json")
    return {
        "kind": "container",
        "inspect": archive_info(folder / "inspect-private.json"),
        "archives": [],
        "base_image": info["Image"],
        "base_image_archive": engine.image_archive(info["Image"], archive_root(store)),
        "bind_sources": {},
        "volume_sources": {},
        "state_store": str(store),
        "sandbox_mountpoints": mountpoints,
        "readonly_diff": changes,
    }


def verify_readonly_child(info, recovery, engine, folder):
    from tools.quality.artifact_docker import verify_archive

    if "sandbox_mountpoints" not in recovery:
        from tools.quality.artifact_docker import resource

        engine.verify_backup({**resource(info, "container"), "recovery": recovery})
        return
    engine.verify_recovery(recovery)
    if engine.run("container", "diff", info["Id"]) != recovery["readonly_diff"]:
        raise ValueError("Sandbox filesystem changed after preservation")
    for entry in recovery["sandbox_mountpoints"]:
        verify_archive(entry["archive"])
        probe = folder / (entry["mountpoint"].lstrip("/") + "-verify.tar.gz")
        if engine.export(["container", "cp", info["Id"] + ":" + entry["mountpoint"], "-"], probe) != entry["raw_sha256"]:
            raise ValueError("Sandbox mountpoint metadata changed after preservation")
        probe.unlink()


def cleanup(store: Path, *, apply=False, engine=None) -> dict:
    from tools.quality.artifact_docker import Engine, archive_root, resource
    from tools.quality.artifact_lifecycle import Run
    from tools.quality.artifact_registry import save

    engine = engine or Engine()
    containers = engine.all("container")
    result = {"removed": [], "candidates": [], "retained": [], "blocked": [], "errors": []}
    for info in containers:
        if child_owner(info) is None:
            continue
        reason = blocker(info, containers)
        if reason:
            result["retained" if reason == "manager-active" else "blocked"].append({"id": info["Id"], "reason": reason})
            continue
        result["candidates"].append(info["Id"])
        if not apply:
            continue
        try:
            with Run("sandbox-pool-retirement", category="archive", store=store) as audit:
                audit._engine = engine
                audit.data.update(engine=engine.identity, task_ref="runtime:sandbox-pool", purpose="Preserve and retire a stopped child of an inactive sandbox manager generation")
                expected = resource(info, "container")
                folder = archive_root(store) / "sandbox-pools" / info["Id"]
                audit.path(folder, "archive")
                folder.mkdir(parents=True, exist_ok=True)
                recovery = preserve_readonly_child(info, engine, store, folder)
                expected["recovery"] = recovery
                verify_readonly_child(info, recovery, engine, folder)
                audit.data.setdefault("recovery_dependencies", []).append({"sandbox_owner": child_owner(info), "packets": [recovery]})
                audit.persist()
                fresh = engine.inspect("container", info["Id"])
                if fresh is None or resource(fresh, "container") != resource(info, "container") or blocker(fresh, engine.all("container")):
                    raise ValueError("Sandbox ownership, configuration or manager generation changed")
                save({"container": info["Id"], "owner": child_owner(info), "engine": engine.identity, "recovery": recovery}, folder / "retirement.json")
                engine.remove(expected)
                audit.path(folder, "archive")
                result["removed"].append(info["Id"])
        except Exception as error:
            result["errors"].append({"id": info["Id"], "error": type(error).__name__})
    return result
