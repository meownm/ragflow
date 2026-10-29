"""Inventory must expose unknowns and preserve boundaries used by future cleanup."""

import json
import os
from pathlib import Path
import subprocess

import pytest

from tools.quality import artifact_registry as registry


IMAGE = "sha256:" + "a" * 64


def test_scope_discovers_unlabeled_sandbox_images_and_named_builders_without_adopting_them():
    containers = [
        {"Id": "sandbox", "Name": "/sandbox_python_0", "Image": IMAGE},
        {"Id": "builder", "Name": "/ragflow-test-runner", "Image": "builder"},
        {"Id": "foreign", "Name": "/sandbox_python_other", "Image": "foreign"},
    ]
    records = registry.docker_records("engine", containers, [{"Id": IMAGE, "RepoTags": ["local/ragflow-sandbox:old"]}, {"Id": "builder"}, {"Id": "foreign"}], [], {})
    selected, _ = registry.project_selection(records, containers, [Path("S:/ragflow")], Path("S:/tmp/ragflow"))
    discovered = [r for r in selected if r["kind"] == "container"]
    assert {r["name"] for r in discovered} == {"sandbox_python_0", "ragflow-test-runner"}
    assert all(r["scope_basis"] == "container-hint-unconfirmed" and not r["owners"] and not r["auto_delete"] for r in discovered)


def test_scope_discovers_sandbox_image_configured_by_confirmed_manager():
    manager = {
        "Id": "manager",
        "Name": "/manager",
        "Image": "manager-image",
        "Config": {"Labels": {"com.docker.compose.project": "ragflow-local"}, "Env": ["SANDBOX_BASE_PYTHON_IMAGE=vendor/python:one", "PASSWORD=never-public"]},
    }
    child = {"Id": "child", "Name": "/sandbox_python_0", "Image": IMAGE, "Config": {"Image": "vendor/python:one"}}
    records = registry.docker_records("engine", [manager, child], [], [], {})
    selected, _ = registry.project_selection(records, [manager, child], [], Path("S:/tmp/ragflow"))
    assert {r["identity"] for r in selected} == {"manager", "child"}
    assert "never-public" not in json.dumps(selected)


def test_declared_path_generation_distinguishes_same_name_replacement(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text("original")
    original = registry.path_record(path, "config", tmp_path, False)
    assert registry.declared_path_exists(original)
    replacement = tmp_path / "replacement.json"
    replacement.write_text("replacement")
    replacement.replace(path)
    assert not registry.declared_path_exists(original)
    assert registry.declared_path_exists(registry.path_record(path, "config", tmp_path, False))


def test_stopped_container_protects_untagged_image_and_volume():
    container = {"Id": "consumer", "Name": "/stopped", "Image": IMAGE, "State": {"Running": False}, "Mounts": [{"Type": "volume", "Name": "data"}]}
    records = registry.docker_records("engine", [container], [{"Id": IMAGE}], [{"Name": "data", "CreatedAt": "old"}], {})
    assert all(r["status"] == "protected" and not r["auto_delete"] for r in records)
    assert records[1]["consumers"] == records[2]["consumers"] == ["consumer"]


def test_digest_and_image_receipts_protect_builder_without_container():
    digest = "registry/builder@sha256:" + "b" * 64
    records = registry.docker_records("engine", [], [{"Id": IMAGE, "RepoTags": ["builder:build-123"], "RepoDigests": [digest]}], [], {IMAGE: ["builder.json"], digest: ["base.json"]})
    assert records[0]["receipts"] == ["base.json", "builder.json"]
    assert records[0]["blockers"] == ["receipt-reference"]


def test_unused_is_unknown_and_recreated_volume_is_distinct():
    image = registry.docker_records("engine", [], [{"Id": IMAGE}], [], {})[0]
    assert image["status"] == "needs-review" and image["auto_delete"] is False
    first = registry.docker_records("engine", [], [], [{"Name": "data", "CreatedAt": "first"}], {})[0]
    second = registry.docker_records("engine", [], [], [{"Name": "data", "CreatedAt": "second"}], {})[0]
    assert first["id"] != second["id"]


def test_inventory_omits_secrets_and_keeps_known_owner():
    container = {"Id": "consumer", "Name": "/qa", "Image": IMAGE, "Config": {"Env": ["PASSWORD=secret"], "Cmd": ["secret"], "Labels": {"codex.task": "qa", "private": "secret"}}}
    record = registry.docker_records("engine", [container], [], [], {})[0]
    assert record["owners"] == ["codex.task=qa"]
    assert "secret" not in json.dumps(record)


def test_corrupt_receipt_marks_scan_incomplete(tmp_path):
    receipts = tmp_path / ".local/cache/deploy"
    receipts.mkdir(parents=True)
    (receipts / "good.json").write_text(json.dumps({"builder": {"image_id": IMAGE}}))
    (receipts / "bad.json").write_text("{")
    errors = []
    pins = registry.receipt_pins([tmp_path], errors)
    assert pins[IMAGE] == [str(receipts / "good.json")]
    assert errors == [f"unreadable-receipt:{receipts / 'bad.json'}"]


def test_source_snapshot_json_is_not_a_deployment_receipt(tmp_path):
    run = tmp_path / "output/local-deploy/run"
    (run / "checkroot/web").mkdir(parents=True)
    (run / "checkroot/web/tsconfig.json").write_text("// JSON with comments is valid source configuration")
    (run / "containers-before.json").write_text("historical diagnostic, not an authoritative receipt")
    (run / "deployment.json").write_text(json.dumps({"candidate_image_id": IMAGE}))
    errors = []
    assert registry.receipt_pins([tmp_path], errors) == {IMAGE: [str(run / "deployment.json")]}
    assert not errors


def test_persistent_data_and_pinned_workspace_are_never_deletion_candidates(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / ".keep").touch()
    (task / "log").write_bytes(b"test")
    record = registry.path_record(task, "task", None, True)
    assert record["size_bytes"] == 4
    assert "task-pin" in record["blockers"]
    backup = registry.path_record(task, "backup", tmp_path, False)
    assert backup["blockers"] == ["owner-retention-unconfirmed"] and backup["auto_delete"] is False


def test_symlink_is_not_followed_and_output_link_is_refused(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "important").write_bytes(b"keep")
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    record = registry.path_record(link, "cache", tmp_path, True)
    assert record["size_bytes"] is None and record["blockers"] == ["link-not-followed"]
    with pytest.raises(ValueError, match="Linked"):
        registry.save({}, link / "inventory.json")
    assert (target / "important").read_bytes() == b"keep"
    assert not (target / "inventory.json").exists()


def test_linked_receipt_root_is_not_read(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "image.json").write_text(json.dumps({"image_id": IMAGE}))
    (tmp_path / ".local/cache").mkdir(parents=True)
    try:
        (tmp_path / ".local/cache/deploy").symlink_to(other, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    errors = []
    assert registry.receipt_pins([tmp_path], errors) == {}
    assert errors


def test_docker_failure_publishes_explicit_partial_inventory(tmp_path, monkeypatch):
    def fail(*args):
        raise subprocess.TimeoutExpired(args, 90)

    monkeypatch.setattr(registry, "command", fail)
    report = registry.inventory([tmp_path], tmp_path / "missing")
    assert not report["complete"]
    assert report["errors"] == ["docker-inventory:TimeoutExpired"]
    assert "live-process-and-lease-verification" in report["scope"]["exclusions"]


def test_atomic_replace_retains_previous_report_if_write_fails(tmp_path, monkeypatch):
    destination = tmp_path / "inventory.json"
    registry.save({"previous": True}, destination)
    original = Path.replace

    def fail(self, target):
        if target == destination:
            raise PermissionError("locked report")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        registry.save({"next": True}, destination)
    assert json.loads(destination.read_bytes()) == {"previous": True}
    assert list(tmp_path.iterdir()) == [destination]


def test_directory_recreation_changes_resource_identity(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    previous = registry.path_record(task, "task", None, False)["id"]
    os.rename(task, tmp_path / "old")
    task.mkdir()
    assert registry.path_record(task, "task", None, False)["id"] != previous


def test_scope_excludes_foreign_resources_but_preserves_shared_consumers():
    own = {"Id": "own", "Name": "/own", "Image": IMAGE, "Config": {"Labels": {"com.docker.compose.project": "ragflow-local"}}, "Mounts": [{"Type": "volume", "Name": "shared"}]}
    foreign = {"Id": "foreign", "Name": "/other", "Image": IMAGE, "Config": {"Labels": {"com.docker.compose.project": "translate"}}, "Mounts": [{"Type": "volume", "Name": "shared"}]}
    records = registry.docker_records("engine", [own, foreign], [{"Id": IMAGE}], [{"Name": "shared"}, {"Name": "translate_data"}], {})
    selected, omitted = registry.project_selection(records, [own, foreign], [Path("S:/ragflow")], Path("S:/tmp/ragflow"))
    assert {r["name"] for r in selected if "name" in r} == {"own", "shared"}
    assert next(r for r in selected if r["kind"] == "image")["consumers"] == ["foreign", "own"]
    assert omitted == {"container": 1, "volume": 1}


def test_scope_uses_bind_ownership_with_component_boundary():
    own = {"Id": "own", "Name": "/qa", "Image": IMAGE, "Mounts": [{"Type": "bind", "Source": "/run/desktop/mnt/host/s/tmp/ragflow/asr"}]}
    foreign = {"Id": "foreign", "Name": "/qa-other", "Image": IMAGE, "Mounts": [{"Type": "bind", "Source": "S:/ragflow-other/file"}]}
    records = registry.docker_records("engine", [own, foreign], [], [], {})
    selected, omitted = registry.project_selection(records, [own, foreign], [Path("S:/ragflow")], Path("S:/tmp/ragflow"))
    assert [r["identity"] for r in selected] == ["own"]
    assert omitted == {"container": 1}


def test_scope_keeps_receipt_pins_and_marks_orphan_name_as_unconfirmed():
    records = registry.docker_records("engine", [], [{"Id": IMAGE, "RepoTags": ["builder:latest"]}, {"Id": "other", "RepoTags": ["local/ragflow-qa:old"]}], [], {IMAGE: ["receipt.json"]})
    selected, omitted = registry.project_selection(records, [], [Path("S:/ragflow")], Path("S:/tmp/ragflow"))
    assert [r["scope_basis"] for r in selected] == ["project-receipt", "name-hint-unconfirmed"]
    assert all(r["auto_delete"] is False for r in selected)
    assert not omitted


def test_scope_ignores_unrelated_project_labels_and_preserves_known_project_volume():
    records = registry.docker_records(
        "engine", [], [], [{"Name": "cache", "Labels": {"com.docker.compose.project": "ragflow-local"}}, {"Name": "other-cache", "Labels": {"com.docker.compose.project": "translate"}}], {}
    )
    selected, omitted = registry.project_selection(records, [], [Path("S:/ragflow")], Path("S:/tmp/ragflow"))
    assert [r["name"] for r in selected] == ["cache"]
    assert omitted == {"volume": 1}
