"""Expire explicitly released recovery packets without following links or shared references."""

from datetime import datetime, timezone
import json
from pathlib import Path

from tools.quality.artifact_docker import archive_info, archive_root, verify_archive
from tools.quality.artifact_registry import unlinked, save


def packets(value):
    if isinstance(value, dict):
        if {"path", "sha256", "bytes"} <= value.keys():
            yield value
        else:
            for child in value.values():
                yield from packets(child)
    elif isinstance(value, list):
        for child in value:
            yield from packets(child)


def references(run, root):
    from tools.quality import artifact_blobs

    found = {}
    values = [run.get("invalidated_releases", []), run.get("recovery_dependencies", [])]
    values.extend(item.get("preserved_evidence") for item in run.get("input_files", []))
    values.append((run.get("consumption_closed") or {}).get("receipt"))
    values.extend(item.get("receipt") for item in run.get("consumption_history", []))
    for resource in run["resources"]:
        recovery = resource.get("recovery") or {}
        # Older container receipts name an immutable base without an explicit
        # packet. Protect that transitive dependency until the receipt is renewed.
        legacy_base = None
        if recovery.get("base_image") and not recovery.get("base_image_archive"):
            identifier = recovery["base_image"]
            metadata = root / "images" / (identifier.removeprefix("sha256:") + ".image.json")
            unlinked(metadata)
            if not metadata.exists():
                raise ValueError("Legacy container base-image recovery is unresolved")
            legacy_base = archive_info(metadata, image_id=identifier, kind="image-cas")
            if json.loads(metadata.read_bytes()).get("image_id") != identifier:
                raise ValueError("Legacy base-image manifest identity mismatch")
        values.extend([resource.get("recovery"), resource.get("preserved_evidence"), legacy_base])
    for packet in packets(values):
        path = Path(packet["path"]).absolute()
        if not path.is_relative_to(root):
            raise ValueError("Recovery reference escapes the owned archive root")
        found[str(path)] = packet
        if packet.get("kind") == "image-cas":
            verify_archive(packet)
            for member in json.loads(path.read_bytes())["members"]:
                blob = member["blob"]
                blob_path = artifact_blobs.path(root, blob)
                found[str(blob_path)] = {"path": str(blob_path), "sha256": blob["sha256"], "bytes": blob["bytes"]}
    return found


def collect(store, *, apply=False, now=None):
    from tools.quality.artifact_lifecycle import checksum, operator_evidence, run_subject, runs, lease, mutation, write_run, utc

    now = now or datetime.now(timezone.utc)
    root = archive_root(store).absolute()
    result = {"checked_at": utc(), "apply": apply, "removed": [], "blocked": [], "errors": []}
    # A producer can be exporting data before its packet is published. Avoid that window.
    with mutation(store):
        values = runs(store)
        if any(run["state"] == "running" for run in values):
            result["blocked"].append("producer-running")
            return result
        protected = set()
        expired = []
        for run in values:
            if run["state"] != "completed" or run.get("recovery_expired_at") or not run.get("recovery_released_at"):
                continue
            try:
                released = datetime.fromisoformat(run["recovery_released_at"])
                if released.tzinfo is None:
                    raise ValueError("Recovery release requires an explicit timezone")
                if (now - released).total_seconds() >= 90 * 86400:
                    if any(item["kind"] != "path" and item.get("status") != "removed" for item in run["resources"]):
                        raise ValueError("Released generation has live resources")
                    recorded = run["release_evidence"]
                    current = operator_evidence(Path(recorded["path"]), run, "release")
                    if current["sha256"] != recorded["sha256"] or recorded.get("applied_subject_sha256") != checksum(run_subject(run)):
                        raise ValueError("Release approval changed")
                    expired.append(run)
            except (OSError, KeyError, ValueError, TypeError) as error:
                result["errors"].append({"run_id": run["run_id"], "error": type(error).__name__})
        if not expired:
            if apply:
                save(result, store / "recovery-cleanup.json")
            return result
        expired_ids = {run["run_id"] for run in expired}
        for run in values:
            if run["run_id"] not in expired_ids and not run.get("recovery_expired_at"):
                try:
                    protected.update(references(run, root))
                except (OSError, ValueError, KeyError) as error:
                    result["errors"].append({"run_id": run["run_id"], "error": type(error).__name__})
        if result["errors"]:
            if apply:
                save(result, store / "recovery-cleanup.json")
            return result
        for run in expired:
            if run.get("recovery_expired_at"):
                continue
            with lease(store, run["run_id"]):
                try:
                    owned = run.get("expiry_files") or references(run, root)
                    subject = checksum(run_subject(run))
                    if run.get("expiry_files") and run.get("expiry_subject") != subject:
                        raise ValueError("Expiry checkpoint belongs to a different release subject")
                    if apply and not run.get("expiry_files"):
                        run["expiry_files"] = owned
                        run["expiry_subject"] = subject
                        write_run(store, run)
                    for name, packet in owned.items():
                        if name in protected:
                            continue
                        path = Path(name)
                        unlinked(path)
                        if path.exists():
                            verify_archive(packet)
                            if apply:
                                path.unlink()
                            result["removed"].append(name)
                    if apply:
                        run["recovery_expired_at"] = utc()
                        write_run(store, run)
                except (OSError, ValueError, KeyError) as error:
                    result["errors"].append({"run_id": run["run_id"], "error": type(error).__name__})
        if apply:
            save(result, store / "recovery-cleanup.json")
    return result
