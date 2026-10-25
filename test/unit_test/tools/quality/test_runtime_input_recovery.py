"""Runtime metadata snapshots participate in the existing recovery reference graph."""

from datetime import datetime, timedelta, timezone
import pytest

from tools.quality import artifact_docker as docker
from tools.quality import artifact_lifecycle as life
from tools.quality.artifact_retention import collect
from test.unit_test.tools.quality.test_artifact_lifecycle import FakeEngine, ready, release_at, state as lifecycle_state


@pytest.fixture
def state(tmp_path, monkeypatch):
    return lifecycle_state.__wrapped__(tmp_path, monkeypatch)


def test_open_runtime_input_protects_shared_packet_from_released_owner_gc(state, tmp_path):
    root = docker.archive_root(state)
    path = root / "evidence" / "old-state.file"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"unique previous runtime configuration")
    packet = {"kind": "evidence-file", "archive": docker.archive_info(path)}
    old = ready(state, {"key": "path:old", "kind": "path", "status": "removed", "preserved_evidence": packet})
    release_at(old, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    runtime = life.Run("blue-green-runtime", category="runtime", store=state)
    runtime.data.update(
        state="completed",
        task_status="open",
        completed_at=life.utc(),
        input_files=[{"path": str(tmp_path / "runtime/state.json"), "kind": "path", "identity": [1, 2, 3], "category": "config", "preserved_evidence": packet}],
    )
    runtime.persist()
    result = collect(state, apply=True)
    assert not result["errors"] and path.exists() and not result["removed"]
    assert path.read_bytes() == b"unique previous runtime configuration"


def test_input_snapshot_changes_invalidate_subject_and_release_history_preserves_old_bytes(state):
    root = docker.archive_root(state)
    root.mkdir(parents=True)
    path = root / "input.file"
    path.write_bytes(b"previous configuration")
    packet = {"kind": "evidence-file", "archive": docker.archive_info(path)}
    runtime = ready(state, {"key": "path:state", "kind": "path", "status": "removed"})
    runtime.data["input_files"] = [{"preserved_evidence": packet}]
    release_at(runtime, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    before = life.run_subject(runtime.data)
    life.invalidate_release(runtime.data, "runtime metadata resumed")
    runtime.data["input_files"] = []
    runtime.data["state"] = "paused"
    runtime.persist()
    assert life.run_subject(runtime.data)["inputs_sha256"] != before["inputs_sha256"]
    assert packet in runtime.data["invalidated_releases"][0]["packets"]
    other = ready(state, {"key": "path:other", "kind": "path", "status": "removed", "preserved_evidence": packet})
    release_at(other, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    assert not collect(state, apply=True)["errors"] and path.exists()


def test_sealed_qa_witness_stays_durable_after_output_cleanup_and_shared_gc(state, tmp_path):
    receipt = tmp_path / "qa-result.json"
    receipt.write_text('{"status":"PASS"}')
    with life.Run("pytest-disposable", store=state) as qa:
        qa.close_consumption("pytest-disposable", exit_code=0, cleanup={"run_id": qa.data["run_id"], "errors": [], "blocked": []}, receipt=receipt)
    saved = life.read_run(state, qa.data["run_id"])
    witness = saved["consumption_closed"]["receipt"]
    receipt.unlink()
    old = ready(state, {"key": "path:old-witness", "kind": "path", "status": "removed", "preserved_evidence": {"archive": witness}})
    release_at(old, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    assert not collect(state, apply=True)["errors"]
    assert life.consumption_closed(saved)
    docker.verify_archive(witness)


def test_paused_runtime_recovery_holds_input_snapshots_without_overwriting_live_configuration(state, tmp_path):
    root = docker.archive_root(state)
    root.mkdir(parents=True)
    archive = root / "previous-state.file"
    archive.write_bytes(b"previous configuration")
    live = tmp_path / "runtime/state.json"
    live.parent.mkdir()
    live.write_bytes(b"current working configuration")
    packet = {"kind": "evidence-file", "archive": docker.archive_info(archive)}
    source = life.Run("blue-green-runtime", category="runtime", store=state)
    source.data.update(
        state="completed",
        task_status="completed",
        completed_at=life.utc(),
        engine="engine",
        input_files=[{"kind": "path", "path": str(live), "category": "config", "content_sha256": packet["archive"]["sha256"], "preserved_evidence": packet}],
    )
    source.persist()
    with life.Run("restore-runtime-context", store=state) as recovery:
        recovery._engine = FakeEngine()
        recovery.data["engine"] = "engine"
        recovery.preserve = True
        docker.restore(life.read_run(state, source.data["run_id"]), recovery)
        assert recovery.data["resources"] == []
        assert recovery.data["required_input_snapshots"][0]["snapshot"] == packet
        assert live.read_bytes() == b"current working configuration"
    source.data = life.read_run(state, source.data["run_id"])
    release_at(source, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    assert not collect(state, apply=True)["errors"]
    assert archive.read_bytes() == b"previous configuration"
