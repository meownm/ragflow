"""A short CI packet must reject foreign/tampered inputs and cannot authorize release."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools.quality import ci_dry_run as smoke
from tools.quality.release_evidence import require_publishable

ROOT = Path(__file__).resolve().parents[4]


def test_supplied_source_must_match_workflow_context(monkeypatch):
    monkeypatch.setenv("SOURCE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    with pytest.raises(ValueError, match="Source SHA"):
        smoke.identity()


def test_github_checkout_must_match_expected_source(monkeypatch):
    monkeypatch.setenv("SOURCE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(smoke.subprocess, "check_output", lambda *a, **kw: "b" * 40)
    with pytest.raises(ValueError, match="Checkout"):
        smoke.identity()


@pytest.fixture
def packet(tmp_path):
    directory = tmp_path / "packet"
    directory.mkdir()
    (directory / "image.tar").write_bytes(b"exported image fixture")
    data = {
        "schema": 1,
        "purpose": smoke.PURPOSE,
        "publishable": False,
        "identity": {"sha": "a" * 40, "repository": "meownm/ragflow", "run_id": "123", "attempt": "1"},
        "source_files": {"controller.py": "b" * 64},
        "owner": "c" * 32,
        "tag": "rf-ci-dry-run-" + "c" * 32 + ":fixture",
        "image_id": "sha256:" + "d" * 64,
        "archive_sha256": smoke.sha256(directory / "image.tar"),
    }
    return directory, data


def verify(packet, changed=None, expected_hash=None):
    directory, original = packet
    (directory / "manifest.json").write_text(json.dumps(changed or original), encoding="utf-8")
    return smoke.verify_packet(directory, expected_hash or smoke.sha256(directory / "manifest.json"), original["identity"], original["source_files"])


def test_matching_packet_preserves_exact_image_identity(packet):
    assert verify(packet) == packet[1]


@pytest.mark.parametrize("field", ["sha", "repository", "run_id", "attempt"])
def test_foreign_source_or_workflow_attempt_is_rejected(packet, field):
    changed = deepcopy(packet[1])
    changed["identity"][field] = "foreign"
    with pytest.raises(ValueError, match="Foreign"):
        verify(packet, changed)


def test_changed_controller_source_is_rejected(packet):
    changed = deepcopy(packet[1])
    changed["source_files"]["controller.py"] = "f" * 64
    with pytest.raises(ValueError, match="source files"):
        verify(packet, changed)


def test_changed_manifest_cannot_supply_its_own_trusted_digest(packet):
    verify(packet)
    digest = smoke.sha256(packet[0] / "manifest.json")
    changed = deepcopy(packet[1])
    changed["image_id"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="Manifest digest"):
        verify(packet, changed, digest)


def test_changed_image_archive_is_rejected_before_import(packet):
    (packet[0] / "image.tar").write_bytes(b"changed payload")
    with pytest.raises(ValueError, match="Image archive digest"):
        verify(packet)


@pytest.mark.parametrize("name,value", [("schema", 2), ("purpose", "release"), ("publishable", True), ("publishable", 0)])
def test_packet_cannot_claim_release_authority(packet, name, value):
    changed = {**packet[1], name: value}
    with pytest.raises(ValueError, match="infrastructure-only"):
        verify(packet, changed)


def test_release_publisher_rejects_infrastructure_packet(packet):
    with pytest.raises(ValueError):
        require_publishable(verify(packet))


@pytest.mark.parametrize("name,value", [("owner", "../foreign"), ("tag", "ragflow-local:production"), ("image_id", "latest")])
def test_cleanup_identity_cannot_name_shared_images(packet, name, value):
    with pytest.raises(ValueError, match="fixture identity"):
        verify(packet, {**packet[1], name: value})


def test_unexpected_release_receipt_is_not_silently_transported(packet):
    (packet[0] / "candidate-receipt.json").write_text("{}")
    with pytest.raises(ValueError, match="Unexpected packet"):
        verify(packet)


def test_short_workflow_is_manual_read_only_and_separate_from_release():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci-dry-run.yml").read_text(encoding="utf-8"))
    assert workflow["on"] == {"workflow_dispatch": {}}
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    assert set(workflow["jobs"]) == {"package", "verify"}
    for job in workflow["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04"
        assert "uses" not in job and "environment" not in job and "secrets" not in job
    verify_job = workflow["jobs"]["verify"]
    download = next(step for step in verify_job["steps"] if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["artifact-ids"] == "${{ needs.package.outputs.artifact_id }}"
    probe = next(step for step in verify_job["steps"] if "run" in step)
    assert probe["env"]["MANIFEST_SHA256"] == "${{ needs.package.outputs.manifest_sha256 }}"
    scripts = "\n".join(step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"])
    assert "release_publish" not in scripts and "test/testcases/" not in scripts and "docker push" not in scripts


def test_workflow_lint_uses_runner_labels_in_an_exported_source_tree(monkeypatch, tmp_path):
    from tools.quality import check_release_workflows as lint

    monkeypatch.setattr(lint, "actionlint_binary", lambda *_: Path("verified-actionlint"))

    def run(arguments, **options):
        assert arguments[1:3] == ["-config-file", str(ROOT / ".github/actionlint.yaml")]
        assert options["cwd"] == ROOT
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(lint.subprocess, "run", run)
    assert lint.check(ROOT, tmp_path / "cache") == 0
