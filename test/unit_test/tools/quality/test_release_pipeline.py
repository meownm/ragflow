"""Reject incomplete, foreign and altered release evidence without network I/O."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from tools.quality.release_evidence import (
    RELEASE_LANES,
    CLI_PLATFORMS,
    file_manifest,
    make_release_receipt,
    require_publishable,
    validate_receipt,
    verify_branch_coverage,
    verify_ci_origin,
    verify_files,
)
from tools.quality.release_runtime import queue_reason, runners_ready, validate_spa, spa_manifest
from tools.quality.release_publish import channel_is_newer, promote_image

ROOT = Path(__file__).resolve().parents[4]
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64


@pytest.fixture
def receipt():
    return make_release_receipt(
        SHA,
        "meownm/ragflow",
        "123",
        2,
        {"reference": "registry/release:" + SHA + "-123-2", "digest": "registry/release@" + DIGEST, "staging_digest": "registry/staging@" + DIGEST},
        dict.fromkeys(RELEASE_LANES, "success"),
        {"files": {"report.xml": "c" * 64}, "spa_sha256": "d" * 64, "browser_inventory_sha256": "e" * 64, "cli_platforms": sorted(CLI_PLATFORMS), "python_packages": ["ragflow-sdk", "ragflow-cli"]},
        {"python": "3.13", "go": "1.25", "node": "22", "native": "pinned", "runtime": "pinned"},
        [{"name": name, "digest": "f" * 64} for name in ("qwen3.8:latest", "qwen3.6:27b", "bge-m3:latest")],
    )


@pytest.mark.parametrize("state,status", [("failure", "FAIL"), ("cancelled", "CANCELLED"), ("skipped", "INCOMPLETE")])
def test_non_success_blocks_publication(receipt, state, status):
    receipt["ci"]["jobs"]["ragflow_tests_infinity"] = state
    receipt["status"] = status
    validate_receipt(receipt)
    with pytest.raises(ValueError, match="complete"):
        require_publishable(receipt)


def test_targeted_does_not_become_release(receipt):
    receipt["ci"]["profile"] = "targeted"
    receipt["status"] = "INCOMPLETE"
    with pytest.raises(ValueError):
        require_publishable(receipt)


@pytest.mark.parametrize("key", RELEASE_LANES)
def test_missing_required_lane_is_rejected(receipt, key):
    del receipt["ci"]["jobs"][key]
    with pytest.raises(ValueError, match="lane set"):
        validate_receipt(receipt)


def test_policy_is_not_chosen_by_receipt(receipt):
    receipt["ci"]["required_lanes"] = ["frontend"]
    with pytest.raises(ValueError, match="policy"):
        validate_receipt(receipt)


def test_changed_promoted_digest_is_rejected(receipt):
    receipt["image"]["digest"] = "registry/release@sha256:" + "d" * 64
    with pytest.raises(ValueError, match="changed"):
        validate_receipt(receipt)


def test_tampered_or_extra_artifact_fails(tmp_path):
    (tmp_path / "report").write_text("original")
    expected = file_manifest(tmp_path)
    verify_files(tmp_path, expected)
    (tmp_path / "extra").write_text("foreign")
    with pytest.raises(ValueError, match="mismatch"):
        verify_files(tmp_path, expected)
    (tmp_path / "extra").unlink()
    (tmp_path / "report").write_text("modified")
    with pytest.raises(ValueError, match="mismatch"):
        verify_files(tmp_path, expected)


def ci_packet(receipt):
    run = {"id": 123, "run_attempt": 2, "head_sha": SHA, "repository": {"full_name": "meownm/ragflow"}, "event": "schedule", "path": ".github/workflows/release.yml"}
    jobs = []
    for lane in RELEASE_LANES:
        for platform in sorted(CLI_PLATFORMS) if lane == "build_cli" else [None]:
            jobs.append({"name": "qualification / " + lane + (f" ({platform})" if platform else ""), "head_sha": SHA, "run_attempt": 2, "conclusion": "success"})
    return run, jobs


@pytest.mark.parametrize("key,value", [("id", 999), ("run_attempt", 1), ("head_sha", "c" * 40), ("event", "pull_request")])
def test_foreign_ci_origin_fails(receipt, key, value):
    run, jobs = ci_packet(receipt)
    verify_ci_origin(receipt, run, jobs)
    run[key] = value
    with pytest.raises(ValueError, match="foreign"):
        verify_ci_origin(receipt, run, jobs)


def test_cancelled_or_missing_cli_matrix_member_fails(receipt):
    run, jobs = ci_packet(receipt)
    jobs.pop()
    with pytest.raises(ValueError):
        verify_ci_origin(receipt, run, jobs)


def test_unavailable_runner_and_queued_deadlines():
    now = datetime.now(timezone.utc)
    runner = {"status": "online", "busy": True, "labels": [{"name": name} for name in ("self-hosted", "Linux", "ragflow-test")]}
    with pytest.raises(ValueError, match="ragflow-test"):
        runners_ready([runner], ["ragflow-test"])
    runner["labels"].append({"name": "ragflow-isolated-vm"})
    runners_ready([runner], ["ragflow-test"])
    with pytest.raises(ValueError, match="ragflow-release"):
        runners_ready([runner], ["ragflow-release"])
    job = {"status": "queued", "labels": ["self-hosted", "ragflow-test"], "created_at": (now - timedelta(minutes=16)).isoformat()}
    assert queue_reason(job, [], now) == "runner-offline-over-15-minutes"
    assert queue_reason(job, [runner], now) is None
    job["created_at"] = (now - timedelta(hours=3)).isoformat()
    assert queue_reason(job, [runner], now) == "runner-queue-over-two-hours"
    runner["busy"] = False
    assert queue_reason(job, [runner], now) is None


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"purpose": "historical-image-upgrade-diagnostic"},
        {"status": "INCOMPLETE"},
        {"cleanup": "FAIL"},
        {"source_sha": "f" * 40},
        {"image_digest": "registry/foreign@sha256:" + "b" * 64},
        {"previous_receipt_sha256": None},
        {"previous_receipt_sha256": "untrusted"},
        {"repeated_initializer": False},
        {"candidate_restore": False},
        {"previous_restore": False},
    ],
)
def test_only_complete_qualified_upgrade_allows_release(changes):
    from tools.quality.release_evidence import verify_runtime_upgrade

    image = "registry/staging@sha256:" + "b" * 64
    proof = {
        "purpose": "previous-qualified-image-upgrade",
        "status": "PASS",
        "cleanup": "PASS",
        "source_sha": SHA,
        "image_digest": image,
        "previous_receipt_sha256": "c" * 64,
        "previous_source_sha": "d" * 40,
        "previous_image_digest": "registry/previous@sha256:" + "e" * 64,
        "repeated_initializer": True,
        "candidate_restore": True,
        "previous_restore": True,
    }
    proof.update(changes)
    if changes:
        with pytest.raises(ValueError, match="migration/restore incomplete"):
            verify_runtime_upgrade(proof, SHA, image)
    else:
        verify_runtime_upgrade(proof, SHA, image)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"bootstrap_baseline_sha256": "f" * 64},
        {"previous_source_sha": "f" * 40},
        {"previous_image_digest": "registry/foreign@sha256:" + "b" * 64},
        {"previous_receipt_sha256": "c" * 64},
        {"historical_baseline": {"qualified": True}},
        {"purpose": "historical-image-upgrade-diagnostic"},
    ],
)
def test_initial_baseline_is_explicit_and_cannot_claim_previous_qualification(changes):
    from tools.quality.release_evidence import BOOTSTRAP_BASELINE, verify_runtime_upgrade

    content = BOOTSTRAP_BASELINE.read_bytes()
    baseline = json.loads(content)
    image = "registry/staging@sha256:" + "b" * 64
    proof = {
        "purpose": "initial-runtime-baseline-upgrade",
        "status": "PASS",
        "cleanup": "PASS",
        "source_sha": SHA,
        "image_digest": image,
        "previous_image_digest": baseline["image_digest"],
        "previous_source_sha": baseline["source_sha"],
        "repeated_initializer": True,
        "candidate_restore": True,
        "previous_restore": True,
        "bootstrap_baseline_sha256": hashlib.sha256(content).hexdigest(),
        "historical_baseline": {
            "repository": baseline["repository"],
            "image_digest": baseline["image_digest"],
            "source_sha": baseline["source_sha"],
            "qualified": False,
            "github_commit_verified": True,
            "candidate_ancestor_verified": True,
        },
    }
    proof.update(changes)
    if changes:
        with pytest.raises(ValueError):
            verify_runtime_upgrade(proof, SHA, image)
    else:
        verify_runtime_upgrade(proof, SHA, image)


@pytest.mark.parametrize("status", ["pending", "waiting", "in_progress", "completed"])
def test_publication_concurrency_and_environment_wait_are_not_runner_timeout(status):
    now = datetime.now(timezone.utc)
    job = {"status": status, "labels": ["self-hosted", "ragflow-release"], "created_at": (now - timedelta(hours=3)).isoformat()}
    assert queue_reason(job, [], now) is None
    runner = {"status": "online", "busy": True, "labels": [{"name": name} for name in job["labels"]]}
    assert queue_reason(job, [runner], now) is None


def test_publisher_runner_queue_has_the_same_outage_and_busy_deadlines():
    now = datetime.now(timezone.utc)
    job = {"status": "queued", "labels": ["self-hosted", "ragflow-release"], "created_at": (now - timedelta(minutes=16)).isoformat()}
    assert queue_reason(job, [], now) == "runner-offline-over-15-minutes"
    runner = {"status": "online", "busy": True, "labels": [{"name": name} for name in job["labels"]]}
    assert queue_reason(job, [runner], now) is None
    job["created_at"] = (now - timedelta(hours=3)).isoformat()
    assert queue_reason(job, [runner], now) == "runner-queue-over-two-hours"
    runner["busy"] = False
    assert queue_reason(job, [runner], now) is None


@pytest.mark.parametrize("label", ["ragflow-test", "ragflow-release"])
def test_watchdog_cancels_only_its_overdue_active_run(monkeypatch, capsys, label):
    from tools.quality import release_runtime as runtime

    now = datetime.now(timezone.utc)
    old = (now - timedelta(minutes=30)).isoformat()
    run = {"id": 101, "path": ".github/workflows/tests.yml", "status": "queued"}
    other = {"id": 102, "path": ".github/workflows/unrelated.yml", "status": "queued"}
    calls, cancelled = [], []

    def items(path, key):
        calls.append((path, key))
        if key == "runners":
            return []
        if key == "workflow_runs":
            return [run, other] if path.endswith("status=queued") else [run]
        return [{"status": "queued", "created_at": old, "labels": ["self-hosted", label]}]

    monkeypatch.setattr(runtime, "api_items", items)
    monkeypatch.setattr(runtime, "github", lambda path, method: cancelled.append((path, method)))
    runtime.watchdog("meownm/ragflow")
    assert cancelled == [("repos/meownm/ragflow/actions/runs/101/cancel", "POST")]
    assert calls == [
        ("repos/meownm/ragflow/actions/runners", "runners"),
        ("repos/meownm/ragflow/actions/runs?status=queued", "workflow_runs"),
        ("repos/meownm/ragflow/actions/runs?status=in_progress", "workflow_runs"),
        ("repos/meownm/ragflow/actions/runs/101/jobs", "jobs"),
    ]
    assert json.loads(capsys.readouterr().out)["reasons"] == ["runner-offline-over-15-minutes"]


def test_spa_must_match_image_and_closed_file_set(tmp_path):
    (tmp_path / "index.html").write_text("SPA")
    manifest = spa_manifest(tmp_path, SHA, "registry/staging@" + DIGEST)
    validate_spa(manifest, tmp_path, SHA, "registry/staging@" + DIGEST)
    with pytest.raises(ValueError):
        validate_spa(manifest, tmp_path, "c" * 40, "registry/staging@" + DIGEST)


def test_promote_preserves_digest_and_rejects_existing_conflict(receipt, monkeypatch, tmp_path):
    import tools.quality.release_publish as publisher

    digests = iter([DIGEST, None, DIGEST])
    monkeypatch.setattr(publisher, "manifest_digest", lambda *_: next(digests))
    calls = []
    monkeypatch.setattr(publisher, "command", lambda *args, **kw: calls.append(args))
    promote_image(receipt, tmp_path / "auth")
    assert "published" not in receipt["image"]
    assert len(calls) == 1 and "--preserve-digests" in calls[0]
    digests = iter([DIGEST, "sha256:" + "c" * 64])
    monkeypatch.setattr(publisher, "manifest_digest", lambda *_: next(digests))
    with pytest.raises(ValueError, match="different content"):
        promote_image(receipt, tmp_path / "auth")
    assert len(calls) == 1


def test_registry_failure_does_not_mark_image_published(receipt, monkeypatch, tmp_path):
    import tools.quality.release_publish as publisher

    digests = iter([DIGEST, None])
    monkeypatch.setattr(publisher, "manifest_digest", lambda *_: next(digests))

    def fail(*args, **kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(publisher, "command", fail)
    with pytest.raises(RuntimeError):
        promote_image(receipt, tmp_path / "auth")
    assert "published" not in receipt["image"]


def test_old_nightly_never_overwrites_newer_channel():
    assert channel_is_newer({"ci_run_id": "124"}, "123")
    assert not channel_is_newer({"ci_run_id": "123"}, "124")


@pytest.mark.parametrize("covered,passes", [(79, True), (78, False), (68, False)])
def test_combined_coverage_cannot_mask_branch_floor(tmp_path, covered, passes):
    report = tmp_path / "coverage.xml"
    report.write_text(f'<coverage line-rate="1" branches-valid="100" branches-covered="{covered}"/>')
    if passes:
        assert verify_branch_coverage(report)["branches_covered"] == covered
    else:
        with pytest.raises(ValueError, match="branch coverage"):
            verify_branch_coverage(report)
