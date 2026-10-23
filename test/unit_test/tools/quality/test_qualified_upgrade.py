"""Fail-closed qualification and cleanup of previous-image database replay."""

import hashlib
import json
from pathlib import Path
import subprocess
import types

import pytest

from tools.quality.release_evidence import verify_runtime_upgrade


SCRIPT = Path(__file__).parents[4] / "deployment/runner/test-qualified-upgrade.py"
OLD_IMAGE = "192.168.1.175:5443/ragflow@sha256:1c25531cf7e6b9e54b2e3ea1c46eb899b978db1fe804aebddddf4422498b6c63"
NEW_IMAGE = "registry/new@sha256:" + "b" * 64
PG_IMAGE = "registry/postgres@sha256:" + "c" * 64
SHA = "d" * 40
OLD_SHA = "3d168d5bc503f87f32eb255b8a21dad509f7b0db"


@pytest.fixture
def upgrade(monkeypatch):
    module = types.ModuleType("qualified_upgrade_test")
    module.__file__ = str(SCRIPT)
    exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), module.__dict__)
    monkeypatch.setenv("RAGFLOW_TEST_DISPOSABLE", "1")
    return module


@pytest.fixture
def approved_policy(upgrade, monkeypatch, tmp_path):
    policy = tmp_path / "approved-baseline.json"
    policy.write_bytes((SCRIPT.parents[2] / "tools/quality/release-bootstrap-baseline.json").read_bytes())
    monkeypatch.setattr(upgrade, "BOOTSTRAP_POLICY", policy)
    return policy


def test_baseline_missing_is_incomplete_without_docker(upgrade, monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Docker must not run"))
    result = upgrade.qualify(None, NEW_IMAGE, SHA, tmp_path, PG_IMAGE)
    assert result["status"] == "INCOMPLETE"
    assert "baseline" in result["reason"]


@pytest.mark.parametrize("image,sha", [("registry/new:latest", SHA), (NEW_IMAGE, "short")])
def test_candidate_identity_required_before_docker(upgrade, monkeypatch, tmp_path, image, sha):
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="immutable image digest"):
        upgrade.qualify(None, image, sha, tmp_path, PG_IMAGE)


def test_shared_state_refused(upgrade, monkeypatch, tmp_path):
    monkeypatch.delenv("RAGFLOW_TEST_DISPOSABLE")
    with pytest.raises(ValueError, match="DISPOSABLE"):
        upgrade.qualify(None, NEW_IMAGE, SHA, tmp_path, PG_IMAGE)


def test_previous_assets_require_live_origin_verification(upgrade, monkeypatch, tmp_path):
    (tmp_path / "candidate-receipt.json").write_text("{}")
    (tmp_path / "publication-receipt.json").write_text("{}")

    def reject(directory, proof):
        raise ValueError("release asset does not match GitHub origin")

    monkeypatch.setattr(upgrade, "verify_release_download", reject)
    monkeypatch.setattr(upgrade, "validate_publication", lambda *args: pytest.fail("Foreign asset accepted"))
    with pytest.raises(ValueError, match="GitHub origin"):
        upgrade.trusted_previous(tmp_path)


def test_snapshot_comparison_allows_only_additive_changes(upgrade):
    old = {"documents": [{"id": "one", "body": "kept"}], "events": []}
    upgrade.assert_preserved(old, {"documents": [{"id": "one", "body": "kept", "new": 1}], "events": [], "new": []})
    with pytest.raises(ValueError, match="rows changed"):
        upgrade.assert_preserved(old, {"documents": [{"id": "one", "body": "changed"}], "events": []})
    with pytest.raises(ValueError, match="rows changed"):
        upgrade.assert_preserved({"events": []}, {"events": [{"id": "unexpected"}]})
    with pytest.raises(ValueError, match="columns disappeared"):
        upgrade.assert_preserved(old, {"documents": [{"id": "one"}], "events": []})


@pytest.mark.parametrize("failure", [None, "repeat", "restore", "previous_restore", "coexistence", "probe", "cleanup"])
@pytest.mark.parametrize("mode", ["qualified", "diagnostic", "bootstrap"])
def test_real_image_sequence_and_cleanup(upgrade, approved_policy, monkeypatch, tmp_path, failure, mode):
    diagnostic = mode == "diagnostic"
    historical = mode != "qualified"
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "candidate-receipt.json").write_text("{}")
    monkeypatch.setattr(upgrade, "trusted_previous", lambda _: {"image": {"digest": OLD_IMAGE}, "source_revision": OLD_SHA})
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ["gh", "api"]:
            value = {"status": "ahead", "merge_base_commit": {"sha": OLD_SHA}} if "/compare/" in args[2] else {"sha": OLD_SHA}
            return json.dumps(value).encode()
        return b""

    monkeypatch.setattr(upgrade, "command", command)
    old = {table: [{"id": "one"}] for table in ("business_document_revision", "business_document_evidence_snapshot", "system_audit_event")}
    current = {**old, "additive_table": []}

    class State:
        owner = "owned"
        database = "t1_upgrade_owned"

        def __init__(self, output):
            pass

        def start_postgres(self, image):
            assert image == PG_IMAGE
            return "owned-network"

        def run_probe(self, image, revision, network, mode, database, label):
            assert revision == (OLD_SHA if image == OLD_IMAGE else SHA)
            assert network == "owned-network"
            calls.append((label, image, mode))
            if failure == "probe" and label == "candidate-upgraded":
                raise RuntimeError("Old dependency mismatch")
            if failure == "coexistence" and label == "previous-reads-upgraded":
                raise RuntimeError("Previous runtime cannot read the upgraded schema")
            if (failure == "repeat" and label == "candidate-repeat") or (failure == "restore" and label == "candidate-restored") or (failure == "previous_restore" and label == "previous-restored"):
                return {}
            return old if image == OLD_IMAGE else current

        def postgres_tool(self, tool, database, data=None):
            return b"synthetic dump"

        def cleanup(self):
            calls.append("cleanup")
            if failure == "cleanup":
                raise RuntimeError("Cleanup failed")

    monkeypatch.setattr(upgrade, "DisposableUpgrade", State)
    options = dict(historical_image=OLD_IMAGE, historical_sha=OLD_SHA, bootstrap=mode == "bootstrap") if historical else {}
    result = upgrade.qualify(None if historical else previous, NEW_IMAGE, SHA, tmp_path, PG_IMAGE, **options)
    assert result["status"] == ("INCOMPLETE" if diagnostic else "PASS" if failure is None else "FAIL")
    if diagnostic:
        assert result["purpose"] == "historical-image-upgrade-diagnostic"
        assert result["diagnostic_status"] == ("PASS" if failure is None else "FAIL")
        assert result["historical_baseline"]["qualified"] is False
        assert "previous_receipt_sha256" not in result
        assert "cannot authorize publication" in result["reason"]
    if mode == "bootstrap":
        assert result["purpose"] == "initial-runtime-baseline-upgrade"
        assert result["bootstrap_baseline_sha256"] == hashlib.sha256(approved_policy.read_bytes()).hexdigest()
        assert result["historical_baseline"]["qualified"] is False
        assert "previous_receipt_sha256" not in result
        assert "diagnostic_status" not in result
        assert "no prior full qualification" in result["scope"]
    assert calls[-1] == "cleanup"
    if failure is None:
        assert ("previous-seeded", OLD_IMAGE, "seed") in calls
        assert ("candidate-upgraded", NEW_IMAGE, "upgrade") in calls
        assert ("previous-restored", OLD_IMAGE, "read") in calls
        assert ("previous-reads-upgraded", OLD_IMAGE, "read") in calls
    if failure is None and not diagnostic:
        verify_runtime_upgrade(result, SHA, NEW_IMAGE)
    else:
        with pytest.raises(ValueError, match="migration/restore incomplete"):
            verify_runtime_upgrade(result, SHA, NEW_IMAGE)
    if failure is None and mode == "bootstrap":
        with pytest.raises(ValueError, match="unapproved initial runtime baseline"):
            verify_runtime_upgrade({**result, "previous_image_digest": NEW_IMAGE}, SHA, NEW_IMAGE)
    if failure is None and diagnostic:
        with pytest.raises(ValueError, match="migration/restore incomplete"):
            verify_runtime_upgrade({**result, "status": "PASS"}, SHA, NEW_IMAGE)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("schema", 2),
        ("purpose", "other"),
        ("repository", "foreign/ragflow"),
        ("image_digest", NEW_IMAGE),
        ("source_sha", SHA),
        ("qualification", "qualified"),
        ("extra", True),
    ],
)
def test_bootstrap_policy_mismatch_rejected_before_external_operations(upgrade, approved_policy, monkeypatch, tmp_path, field, value):
    policy = json.loads(approved_policy.read_bytes())
    policy[field] = value
    approved_policy.write_text(json.dumps(policy))
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Unapproved bootstrap reached external operation"))
    with pytest.raises(ValueError, match="approved initial runtime policy"):
        upgrade.qualify(None, NEW_IMAGE, SHA, tmp_path, PG_IMAGE, historical_image=OLD_IMAGE, historical_sha=OLD_SHA, bootstrap=True)


@pytest.mark.parametrize("previous,historical_image,historical_sha", [(None, None, None), (Path("previous"), None, None), (None, OLD_IMAGE, None), (Path("previous"), OLD_IMAGE, OLD_SHA)])
def test_bootstrap_requires_exclusively_complete_historical_baseline(upgrade, monkeypatch, tmp_path, previous, historical_image, historical_sha):
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Incomplete bootstrap reached external operation"))
    with pytest.raises(ValueError, match="Bootstrap requires|complete historical diagnostic baseline"):
        upgrade.qualify(previous, NEW_IMAGE, SHA, tmp_path, PG_IMAGE, historical_image=historical_image, historical_sha=historical_sha, bootstrap=True)


def test_bootstrap_missing_approval_is_rejected(upgrade, approved_policy, monkeypatch, tmp_path):
    approved_policy.unlink()
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Missing approval reached external operation"))
    with pytest.raises(FileNotFoundError):
        upgrade.qualify(None, NEW_IMAGE, SHA, tmp_path, PG_IMAGE, historical_image=OLD_IMAGE, historical_sha=OLD_SHA, bootstrap=True)


@pytest.mark.parametrize(
    "image,revision,repository",
    [
        ("registry/old:latest", OLD_SHA, "meownm/ragflow"),
        ("registry/old @sha256:" + "a" * 64, OLD_SHA, "meownm/ragflow"),
        (OLD_IMAGE, "short", "meownm/ragflow"),
        (OLD_IMAGE, OLD_SHA, "meownm/ragflow/other"),
        (OLD_IMAGE, OLD_SHA, "meownm/ragflow?foreign"),
    ],
)
def test_historical_identity_rejected_before_external_commands(upgrade, monkeypatch, image, revision, repository):
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Invalid identity reached external command"))
    with pytest.raises(ValueError):
        upgrade.historical_baseline(image, revision, SHA, repository)


@pytest.mark.parametrize(
    "commit,comparison",
    [
        ({"sha": SHA}, {}),
        ({"sha": OLD_SHA}, {"status": "diverged", "merge_base_commit": {"sha": OLD_SHA}}),
        ({"sha": OLD_SHA}, {"status": "ahead", "merge_base_commit": {"sha": SHA}}),
        ({"sha": OLD_SHA}, {"status": "ahead", "merge_base_commit": None}),
        ({"sha": OLD_SHA}, []),
    ],
)
def test_historical_origin_and_ancestry_rejected_before_docker(upgrade, monkeypatch, tmp_path, commit, comparison):
    calls = []

    def command(args, **kwargs):
        assert args[:2] == ["gh", "api"]
        calls.append(args)
        return json.dumps(comparison if "/compare/" in args[2] else commit).encode()

    monkeypatch.setattr(upgrade, "command", command)
    with pytest.raises(ValueError, match="GitHub repository|verified ancestor"):
        upgrade.qualify(None, NEW_IMAGE, SHA, tmp_path, PG_IMAGE, historical_image=OLD_IMAGE, historical_sha=OLD_SHA)
    assert calls[0] == ["gh", "api", f"repos/meownm/ragflow/commits/{OLD_SHA}"]


@pytest.mark.parametrize("historical_image,historical_sha,previous", [(OLD_IMAGE, None, None), (None, OLD_SHA, None), (OLD_IMAGE, OLD_SHA, Path("previous"))])
def test_historical_mode_requires_one_complete_baseline(upgrade, monkeypatch, tmp_path, historical_image, historical_sha, previous):
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Ambiguous baseline reached external command"))
    with pytest.raises(ValueError, match="complete historical diagnostic baseline"):
        upgrade.qualify(previous, NEW_IMAGE, SHA, tmp_path, PG_IMAGE, historical_image=historical_image, historical_sha=historical_sha)


@pytest.mark.parametrize("mode", ["missing_sha", "sha_with_previous_tag", "foreign_commit", "github_unavailable"])
def test_diagnostic_cli_failure_is_incomplete_and_nonzero(upgrade, monkeypatch, tmp_path, mode):
    arguments = ["probe", "--historical-image", OLD_IMAGE, "--image", NEW_IMAGE, "--sha", SHA, "--postgres-image", PG_IMAGE, "--output", str(tmp_path)]
    if mode != "missing_sha":
        arguments.extend(["--historical-sha", OLD_SHA])
    if mode == "sha_with_previous_tag":
        arguments[1:3] = ["--previous-tag", "previous"]
    monkeypatch.setattr(upgrade.sys, "argv", arguments)
    monkeypatch.setattr(upgrade.signal, "signal", lambda *args: None)

    def command(args, **kwargs):
        assert mode not in {"missing_sha", "sha_with_previous_tag"}, "Incomplete identity reached external command"
        assert args[:2] == ["gh", "api"], "Invalid baseline reached Docker"
        if mode == "github_unavailable":
            raise RuntimeError("GitHub origin unavailable")
        return json.dumps({"sha": SHA}).encode()

    monkeypatch.setattr(upgrade, "command", command)
    assert upgrade.main() == 2
    result = json.loads((tmp_path / "qualified-upgrade.json").read_text())
    assert result["purpose"] == "historical-image-upgrade-diagnostic"
    assert result["status"] == "INCOMPLETE"
    assert result["diagnostic_status"] == "FAIL"


def test_historical_candidate_revision_is_full_before_github(upgrade, monkeypatch):
    monkeypatch.setattr(upgrade, "command", lambda *args, **kwargs: pytest.fail("Invalid candidate source reached GitHub"))
    with pytest.raises(ValueError, match="full source SHA"):
        upgrade.historical_baseline(OLD_IMAGE, OLD_SHA, "short", "meownm/ragflow")


@pytest.mark.parametrize("mode", ["missing_historical", "foreign_commit"])
def test_bootstrap_cli_failure_cannot_emit_success(upgrade, approved_policy, monkeypatch, tmp_path, mode):
    arguments = ["probe", "--bootstrap", "--image", NEW_IMAGE, "--sha", SHA, "--postgres-image", PG_IMAGE, "--output", str(tmp_path)]
    if mode != "missing_historical":
        arguments.extend(["--historical-image", OLD_IMAGE, "--historical-sha", OLD_SHA])
    monkeypatch.setattr(upgrade.sys, "argv", arguments)
    monkeypatch.setattr(upgrade.signal, "signal", lambda *args: None)

    def command(args, **kwargs):
        assert mode != "missing_historical", "Incomplete bootstrap reached external operation"
        assert args[:2] == ["gh", "api"], "Unverified bootstrap reached Docker"
        return json.dumps({"sha": SHA}).encode()

    monkeypatch.setattr(upgrade, "command", command)
    assert upgrade.main() == 1
    report = json.loads((tmp_path / "qualified-upgrade.json").read_text())
    assert report["purpose"] == "initial-runtime-baseline-upgrade"
    assert report["status"] == "FAIL"
    with pytest.raises(ValueError, match="migration/restore incomplete"):
        verify_runtime_upgrade(report, SHA, NEW_IMAGE)


def test_cleanup_never_removes_foreign_resource_and_continues(upgrade, monkeypatch, tmp_path):
    state = upgrade.DisposableUpgrade(tmp_path)
    state.resources = [("network", "f" * 64), ("container", "a" * 64)]
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        if args[2] == "inspect":
            owner = "foreign" if args[1] == "container" else state.owner
            return json.dumps({upgrade.OWNER_LABEL: owner}).encode()
        return b""

    monkeypatch.setattr(upgrade, "command", docker)
    monkeypatch.setattr(upgrade.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"No such network"))
    with pytest.raises(RuntimeError, match="cleanup incomplete"):
        state.cleanup()
    assert ["docker", "network", "rm", "f" * 64] in calls
    assert not any(args[:3] == ["docker", "container", "rm"] for args in calls)


def test_probe_rejects_different_running_image(upgrade, monkeypatch, tmp_path):
    state = upgrade.DisposableUpgrade(tmp_path)

    def docker(args, **kwargs):
        if args[1] == "create":
            return ("a" * 64).encode()
        if args[1] == "start":
            return b"probe logs"
        if args[1] == "inspect":
            return b"0" if args[-1] == "{{.State.ExitCode}}" else b"sha256:foreign"
        return b"sha256:expected"

    monkeypatch.setattr(upgrade, "command", docker)
    with pytest.raises(ValueError, match="different image"):
        state.run_probe(OLD_IMAGE, SHA, "owned", "seed", "t1_upgrade_owned", "old")
    assert state.resources == [("container", "a" * 64)]
