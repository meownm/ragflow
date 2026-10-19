import hashlib
import importlib.util
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[3]
spec = importlib.util.spec_from_file_location("rollback_retention", ROOT / "deployment/linux-pg/retention.py")
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


def image_id(number):
    return "sha256:" + str(number) * 64


def checksum_receipt(folder):
    paths = ["UPGRADE.env", "UPGRADE-COMPLETED.env", "config/docker.env"]
    (folder / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((folder / name).read_bytes()).hexdigest()}  ./{name}\n" for name in paths))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    root = tmp_path / "ragflow-pg"
    backups = tmp_path / "backups"
    secrets = tmp_path / "secrets"
    for folder in (root / "docker", backups, secrets):
        folder.mkdir(parents=True)
    (root / "docker/.env").write_text(f"RAGFLOW_IMAGE={image_id(5)}\n")
    (root / "pyproject.toml").write_text('version = "5.0.0"\n')
    directories, receipts = [], []
    images = {}
    for number in range(1, 6):
        images[image_id(number)] = {
            "Id": image_id(number),
            "RepoTags": [f"local/ragflow:v{number}"],
            "Created": f"2026-10-{number:02}T00:00:00Z",
            "RootFS": {"Layers": ["base", f"version-{number}"]},
        }
    for number in range(1, 5):
        directory = tmp_path / f"ragflow-pg.previous-2026100{number}T000000Z"
        (directory / "docker").mkdir(parents=True)
        config = f"RAGFLOW_IMAGE={image_id(number)}\n"
        (directory / "docker/.env").write_text(config)
        (directory / "code.py").write_text("retained source\n")
        receipt = backups / f"upgrade-{number}"
        (receipt / "config").mkdir(parents=True)
        (receipt / "UPGRADE.env").write_text(f"INSTALL_DIR={root}\nPROJECT_NAME=ragflow-pg\nPREVIOUS_DIR={directory}\nTO_VERSION=v{number + 1}.0.0\n")
        (receipt / "UPGRADE-COMPLETED.env").write_text("COMPLETED_AT_UTC=2026-10-01T00:00:00Z\n")
        (receipt / "config/docker.env").write_text(config)
        checksum_receipt(receipt)
        directories.append(directory)
        receipts.append(receipt)
    (secrets / "deployed-source.env").write_text(f"BACKUP_DIR={receipts[-1]}\nPREVIOUS_DIR={directories[-1]}\nRELEASE_VERSION=v5.0.0\n")
    containers = [{"Id": "app", "Image": image_id(5), "State": {"StartedAt": "unchanged", "Status": "running"}, "RestartCount": 0, "Mounts": []}]
    calls = []

    def command(*args):
        calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            if "--format" in args:
                return next(key for key, value in images.items() if args[3] in value["RepoTags"])
            return json.dumps([images[args[3]]])
        if args[:3] == ("docker", "image", "rm"):
            identifier = next(key for key, value in images.items() if args[3] in value["RepoTags"] or args[3] == key)
            del images[identifier]
            return "deleted"
        raise AssertionError(args)

    monkeypatch.setattr(retention, "docker_images", lambda: images.copy())
    monkeypatch.setattr(retention, "containers", lambda: containers)
    monkeypatch.setattr(retention, "healthy", lambda *_: None)
    monkeypatch.setattr(retention, "assert_unused", lambda *_: None)
    monkeypatch.setattr(retention, "volume_state", lambda: {"volume": "unchanged"})
    monkeypatch.setattr(retention, "old_cache", lambda _: [])
    monkeypatch.setattr(retention, "command", command)
    return root, backups, secrets, directories, receipts, images, containers, calls


def execute(fixture, apply=True):
    return retention.run(*fixture[:3], "ragflow-pg", apply=apply)


def test_retains_current_and_two_newest_verified_rollbacks(installation):
    root, backups, _, directories, _, images, _, calls = installation
    before = retention.file_hashes([backups, *directories[-2:]])
    result = execute(installation)
    assert result["status"] == "COMPLETE"
    assert result["keep"] == list(map(str, directories[-2:]))
    assert not any(path.exists() for path in directories[:2])
    assert all(path.is_dir() for path in [root, *directories[-2:]])
    assert set(images) == {image_id(3), image_id(4), image_id(5)}
    assert before == retention.file_hashes([backups, *directories[-2:]])
    assert all("--force" not in call and "volume" not in call for call in calls)
    assert execute(installation)["status"] == "NO_WORK"


def test_plan_does_not_delete_files_or_images(installation):
    result = execute(installation, False)
    assert result["status"] == "PLANNED"
    assert all(path.is_dir() for path in installation[3])
    assert len(installation[5]) == 5
    assert not any(call[:3] == ("docker", "image", "rm") for call in installation[-1])


@pytest.mark.parametrize("fault", ["recovery", "receipt", "missing_directory", "missing_image", "version", "current_receipt", "archive", "symlink"])
def test_invalid_state_fails_before_any_deletion(installation, fault):
    root, _, secrets, directories, receipts, images, _, calls = installation
    if fault == "recovery":
        (secrets / "upgrade-recovery-required.env").write_text("pending")
    elif fault == "receipt":
        (receipts[0] / "UPGRADE-COMPLETED.env").write_text("changed")
    elif fault == "missing_directory":
        import shutil

        shutil.rmtree(directories[-1])
    elif fault == "missing_image":
        images.pop(image_id(4))
    elif fault == "version":
        (root / "pyproject.toml").write_text('version = "6.0.0"\n')
    elif fault == "current_receipt":
        (secrets / "deployed-source.env").write_text("BACKUP_DIR=unknown\n")
    elif fault == "archive":
        (directories[0] / "postgres.dump").write_bytes(b"protected backup")
    elif fault == "symlink":
        import shutil

        shutil.rmtree(directories[0])
        try:
            directories[0].symlink_to(root, target_is_directory=True)
        except OSError as exc:
            pytest.fail(f"Symlink prerequisite unavailable: {exc}")
    with pytest.raises(ValueError):
        execute(installation)
    assert not any(call[:3] == ("docker", "image", "rm") for call in calls)


def test_unknown_and_failed_installations_are_preserved(installation):
    _, _, _, directories, receipts, images, containers, _ = installation
    (receipts[0] / "UPGRADE-FAILED.env").write_text("failed")
    result = execute(installation)
    assert directories[0].is_dir()
    assert image_id(1) in images
    assert str(directories[0]) in result["unknown_preserved"]
    assert image_id(2) not in images


def test_stopped_container_image_is_also_protected(installation):
    containers = installation[6]
    containers.append({"Id": "stopped", "Image": image_id(2), "State": {"StartedAt": "unchanged", "Status": "exited"}, "RestartCount": 0, "Mounts": []})
    execute(installation)
    assert image_id(2) in installation[5]


def test_current_health_failure_prevents_cleanup(installation, monkeypatch):
    def unhealthy(*_):
        raise ValueError("unhealthy")

    monkeypatch.setattr(retention, "healthy", unhealthy)
    with pytest.raises(ValueError, match="unhealthy"):
        execute(installation)
    assert len(installation[5]) == 5


def test_future_candidate_and_other_project_alias_are_preserved(installation):
    images = installation[5]
    images[image_id(6)] = {"Id": image_id(6), "RepoTags": ["local/ragflow:future"], "Created": "2026-10-19T00:00:00Z", "RootFS": {"Layers": ["base", "version-1"]}}
    images[image_id(1)]["RepoTags"].append("other-project:staged")
    execute(installation)
    assert image_id(1) in images
    assert image_id(6) in images


def test_mutable_historical_tag_does_not_authorize_deletion(installation):
    _, _, _, directories, receipts, images, _, _ = installation
    text = "RAGFLOW_IMAGE=local/ragflow:v1\n"
    (directories[0] / "docker/.env").write_text(text)
    (receipts[0] / "config/docker.env").write_text(text)
    checksum_receipt(receipts[0])
    result = execute(installation)
    assert directories[0].is_dir() and image_id(1) in images
    assert str(directories[0]) in result["unknown_preserved"]


def test_mount_reference_blocks_removing_a_directory(tmp_path):
    directory = tmp_path / "previous"
    with pytest.raises(ValueError, match="mounted by a container"):
        retention.assert_unused([directory], [{"Mounts": [{"Source": str(directory)}]}])


def test_cache_selection_is_bounded_to_retired_ragflow_graph(monkeypatch):
    rows = [
        {"ID": "ragflow", "Description": "[2/2] COPY payload/ /ragflow/", "Parents": ["base", "shared"], "CreatedAt": "2026-10-01 00:00:00.0 +0000 UTC", "Reclaimable": True, "Shared": False},
        {"ID": "base", "Description": "from local ", "CreatedAt": "2026-10-01 00:00:00.0 +0000 UTC", "Reclaimable": True, "Shared": False},
        {"ID": "shared", "Description": "from local ", "CreatedAt": "2026-10-01 00:00:00.0 +0000 UTC", "Reclaimable": True, "Shared": True},
        {"ID": "other", "Description": "from local ", "CreatedAt": "2026-10-01 00:00:00.0 +0000 UTC", "Reclaimable": True, "Shared": False},
        {"ID": "future", "Description": "[2/2] COPY payload/ /ragflow/", "CreatedAt": "2026-10-18 00:00:00.0 +0000 UTC", "Reclaimable": True, "Shared": False},
        {"ID": "active", "Description": "[2/2] COPY payload/ /ragflow/", "CreatedAt": "2026-10-01 00:00:00.0 +0000 UTC", "Reclaimable": False, "Shared": False},
    ]
    monkeypatch.setattr(retention, "command", lambda *_: "\n".join(map(json.dumps, rows)))
    assert set(retention.old_cache(datetime(2026, 10, 10, tzinfo=timezone.utc))) == {"ragflow", "base"}


def test_native_upgrade_dispatches_only_after_success_and_recovery_marker_removal():
    source = (ROOT / "deployment/linux-pg/upgrade.sh").read_text()
    hook = source.index("if sudo systemctl cat ragflow-retention.service")
    assert source.index('sudo rm -- "${RECOVERY_REQUIRED_FILE}"') < hook
    assert source.index('sudo tee "${BACKUP_DIR}/UPGRADE-COMPLETED.env"') < hook
    assert source.index('if [[ ${CHECK_ONLY} == "1" ]]') < hook
    assert "start --no-block ragflow-retention.service" in source[hook:]


@pytest.mark.parametrize("installed", [True, False])
def test_retention_dispatch_cannot_fail_a_completed_upgrade(installed):
    source = (ROOT / "deployment/linux-pg/upgrade.sh").read_text()
    hook = source[source.index("if sudo systemctl cat ragflow-retention.service") :]
    bash = shutil.which("bash")
    assert bash, "Bash is required to check the upgrade success boundary"
    body = f"""
set -Eeuo pipefail
trap 'echo UNEXPECTED_RECOVERY; exit 99' ERR
sudo() {{
  if [[ $2 == cat ]]; then return {0 if installed else 1}; fi
  echo DISPATCH_ATTEMPTED
  return 1
}}
{hook}
echo UPGRADE_STILL_COMPLETE
"""
    result = subprocess.run([bash, "-c", "bash -s"], input=body.encode(), cwd=ROOT, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert b"UPGRADE_STILL_COMPLETE" in result.stdout
    assert b"UNEXPECTED_RECOVERY" not in result.stdout
    assert (b"DISPATCH_ATTEMPTED" in result.stdout) == installed


def test_unlabelled_companion_container_does_not_break_health_check(monkeypatch):
    items = [
        {"Id": "other", "Config": {"Labels": None}, "State": {"Status": "running"}},
        {"Id": "app", "Config": {"Labels": {"com.docker.compose.project": "ragflow-pg", "com.docker.compose.service": "ragflow-cpu"}}, "State": {"Status": "running", "Health": {"Status": "healthy"}}},
    ]
    monkeypatch.setattr(retention, "command", lambda *_: json.dumps(dict.fromkeys(["status", "db", "doc_engine", "redis", "storage"], "ok")))
    retention.healthy(items, "ragflow-pg")
    assert retention.resolve(image_id(9), {image_id(5): {"RepoDigests": None}}) is None
