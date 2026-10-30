"""State transitions and late blockers for owned artifact retirement."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

import pytest

from tools.quality import artifact_lifecycle as life
from tools.quality import artifact_docker as docker


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)
    return tmp_path / "state"


def test_run_failure_is_durable_and_never_implies_completion(state):
    with pytest.raises(ValueError), life.Run("qa", store=state) as run:
        identity = run.data["run_id"]
        assert life.read_run(state, identity)["state"] == "running"
        raise ValueError("test failed")
    assert life.read_run(state, identity)["state"] == "failed"
    assert life.eligible(life.read_run(state, identity), datetime.now(timezone.utc)) == "run-not-completed"
    assert "RAGFLOW_ARTIFACT_RUN_ID" not in os.environ


def test_successful_producer_does_not_implicitly_close_product_acceptance(state):
    with life.Run("successful-builder", days=0, store=state) as run:
        pass
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == "completed" and saved["task_status"] == "open"
    assert life.eligible(saved, datetime.now(timezone.utc)) == "task-acceptance-open"


@pytest.mark.parametrize("exit_code,expected", [(None, "completed"), (0, "completed"), (1, "failed"), ("failure", "failed")])
def test_process_exit_classification_preserves_real_success_and_failure(state, exit_code, expected):
    with pytest.raises(SystemExit), life.Run("cli", store=state) as run:
        raise SystemExit(exit_code)
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == expected
    assert (saved.get("failure") == "SystemExit") is (expected == "failed")


def test_explicit_failed_flag_overrides_successful_process_exit(state):
    with pytest.raises(SystemExit), life.Run("cli", store=state) as run:
        run.failed = True
        raise SystemExit(0)
    assert life.read_run(state, run.data["run_id"])["state"] == "failed"


@pytest.mark.parametrize("phase", ["capture", "checkpoint"])
@pytest.mark.parametrize("exit_code", [None, 0, 7])
def test_final_accounting_failure_cannot_hide_producer_failure_or_turn_exit_zero_into_pass(state, monkeypatch, phase, exit_code):
    run = life.Run("producer", store=state)

    def fail():
        raise PermissionError("confidential-final-accounting-canary")

    with pytest.raises(PermissionError if phase == "checkpoint" and exit_code in {None, 0} else RuntimeError if exit_code in {None, 0} else SystemExit) as failure:
        with run:
            monkeypatch.setattr(run, "capture" if phase == "capture" else "persist", fail)
            if exit_code is not None:
                raise SystemExit(exit_code)
    saved = life.read_run(state, run.data["run_id"])
    if phase == "capture":
        assert saved["state"] == "failed" and saved["capture_failure"] == "PermissionError"
    else:
        # An unavailable store cannot be rewritten as a fake completion.
        assert saved["state"] == "running"
    if exit_code == 7:
        assert failure.value.code == 7
        assert all("confidential-final-accounting-canary" not in note for note in failure.value.__notes__)
    assert "RAGFLOW_ARTIFACT_RUN_ID" not in os.environ


@pytest.mark.parametrize("primary", [False, True])
def test_output_accounting_attempts_later_outputs_and_preserves_primary_error(state, tmp_path, monkeypatch, primary):
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("first output")
    second.write_text("second output")
    original = life.note

    def record(path, **kwargs):
        if path == first:
            raise PermissionError("confidential-accounting-canary")
        return original(path, **kwargs)

    monkeypatch.setattr(life, "note", record)
    with pytest.raises(RuntimeError if primary else ExceptionGroup) as failure:
        with life.Run("producer", store=state) as run:
            escaping = None
            try:
                if primary:
                    raise RuntimeError("original producer failure")
            except BaseException as error:
                escaping = error
                raise
            finally:
                life.note_all([(first, "evidence", False), (second, "evidence", False)], primary=escaping)
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == "failed"
    assert any(item.get("path") == str(second) for item in saved["resources"])
    assert "confidential-accounting-canary" not in "".join(traceback.format_exception(failure.value))
    if primary:
        assert str(failure.value) == "original producer failure"
        assert any("PermissionError" in note for note in failure.value.__notes__)
        assert all("confidential-accounting-canary" not in note for note in failure.value.__notes__)


@pytest.mark.parametrize("context", ["exit-none", "exit-zero", "handled-exception"])
def test_accounting_failure_is_not_suppressed_by_successful_exit_or_handled_outer_error(state, tmp_path, monkeypatch, context):
    def denied(*_args, **_kwargs):
        raise PermissionError("confidential-accounting-canary")

    monkeypatch.setattr(life, "note", denied)
    with pytest.raises(ExceptionGroup):
        with life.Run("producer", store=state) as run:
            if context == "handled-exception":
                try:
                    raise RuntimeError("already handled")
                except RuntimeError:
                    life.note_all([(tmp_path / "output", "evidence", False)])
            else:
                try:
                    raise SystemExit(None if context == "exit-none" else 0)
                finally:
                    life.note_all([(tmp_path / "output", "evidence", False)])
    assert life.read_run(state, run.data["run_id"])["state"] == "failed"


def operator_decision(tmp_path, run, action):
    path = tmp_path / "operator-decision.json"
    proof = {
        "schema": 1,
        "project": "ragflow",
        "action": action,
        "subject": life.run_subject(run),
        "execution_status": "completed",
        "approved_by": "qa-owner",
        "task_status": "completed",
        "recovery_needed": False,
    }
    path.write_text(json.dumps(proof))
    return path, proof


def release_at(run, when):
    run.data["task_status"] = "completed"
    path = run.store.parent / (run.data["run_id"] + "-release.json")
    proof = {
        "schema": 1,
        "project": "ragflow",
        "action": "release",
        "subject": life.run_subject(run.data),
        "execution_status": "completed",
        "approved_by": "qa-owner",
        "task_status": "completed",
        "recovery_needed": False,
    }
    path.write_text(json.dumps(proof))
    run.data.update(recovery_released_at=when, release_evidence=life.operator_evidence(path, run.data, "release"))
    run.data["release_evidence"]["applied_subject_sha256"] = life.checksum(life.run_subject(run.data))
    run.persist()


@pytest.mark.parametrize("action", ["complete", "release"])
@pytest.mark.parametrize("changed", ["plain-report", "run", "source", "scope", "generation", "action", "custodian", "execution"])
def test_operator_decision_with_valid_checksum_cannot_close_another_subject(state, tmp_path, monkeypatch, action, changed):
    run = ready(state, {"key": "volume:operator", "kind": "volume", "identity": "original", "category": "disposable", "status": "removed", "data_scope": "qa/data"})
    run.data["source"] = {"head": "a" * 40}
    run.persist()
    path, proof = operator_decision(tmp_path, run.data, action)
    if changed == "plain-report":
        proof = {"task": "completed", "status": "PASS"}
    elif changed == "run":
        proof["subject"]["run_id"] = "b" * 32
    elif changed == "source":
        proof["subject"]["source"] = {"head": "b" * 40}
    elif changed == "scope":
        proof["subject"]["resources"][0]["data_scope"] = "other/data"
    elif changed == "generation":
        proof["subject"]["resources"][0]["identity"] = "replacement"
    elif changed == "action":
        proof["action"] = "release" if action == "complete" else "complete"
    elif changed == "custodian":
        proof["approved_by"] = " "
    else:
        proof["execution_status"] = "running"
    path.write_text(json.dumps(proof))
    original = life.run_path(state, run.data["run_id"]).read_bytes()
    monkeypatch.setattr(sys, "argv", ["artifact_lifecycle", action, "--state", str(state), "--run", run.data["run_id"], "--evidence", str(path)])
    with pytest.raises(SystemExit) as error:
        life.main()
    assert error.value.code == 2
    assert life.run_path(state, run.data["run_id"]).read_bytes() == original


def test_generation_completion_does_not_release_recovery_or_close_product_task(state, tmp_path, monkeypatch):
    run = ready(state, {"key": "volume:operator", "kind": "volume", "identity": "original", "category": "disposable", "status": "removed"})
    run.data["state"] = "paused"
    run.persist()
    path, proof = operator_decision(tmp_path, run.data, "complete")
    proof["task_status"] = "open"
    path.write_text(json.dumps(proof))
    monkeypatch.setattr(sys, "argv", ["artifact_lifecycle", "complete", "--state", str(state), "--run", run.data["run_id"], "--evidence", str(path)])
    assert life.main() == 0
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == "completed" and saved["completion_evidence"]["task_status"] == "open"
    assert not saved.get("recovery_released_at")
    assert life.eligible(saved, datetime.now(timezone.utc) + timedelta(days=100)) == "task-acceptance-open"


@pytest.mark.parametrize("open_task,needed", [(True, False), (False, True), (False, None)])
def test_recovery_release_requires_both_closed_task_and_explicit_release(state, tmp_path, open_task, needed):
    run = ready(state, {"key": "volume:operator", "kind": "volume", "identity": "original", "category": "disposable", "status": "removed"})
    path, proof = operator_decision(tmp_path, run.data, "release")
    proof.update(task_status="open" if open_task else "completed", recovery_needed=needed)
    path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="closed task"):
        life.operator_evidence(path, run.data, "release")
    assert not life.read_run(state, run.data["run_id"]).get("recovery_released_at")


def test_subject_cli_is_read_only_and_omits_private_config_and_capability(state, monkeypatch, capsys):
    run = ready(state, {"key": "container:operator", "kind": "container", "identity": "original", "category": "disposable", "status": "removed", "Config": {"Env": ["PASSWORD=secret-canary"]}})
    run.data["owner_token_sha256"] = "private-capability-hash"
    run.persist()
    original = life.run_path(state, run.data["run_id"]).read_bytes()
    monkeypatch.setattr(sys, "argv", ["artifact_lifecycle", "subject", "--state", str(state), "--run", run.data["run_id"]])
    assert life.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output)["run_id"] == run.data["run_id"]
    assert "secret-canary" not in output and "private-capability-hash" not in output
    assert life.run_path(state, run.data["run_id"]).read_bytes() == original
    assert "teardown_sha256" not in json.loads(output), "Existing closure subjects must retain their exact historical shape"


def test_valid_recovery_release_uses_actual_approval_time_without_deleting_data(state, tmp_path, monkeypatch):
    run = ready(state, {"key": "volume:operator", "kind": "volume", "identity": "original", "category": "disposable", "status": "removed"})
    path, proof = operator_decision(tmp_path, run.data, "release")
    proof["approved_at"] = "1900-01-01T00:00:00+00:00"
    path.write_text(json.dumps(proof))
    now = "2026-10-25T15:00:00+00:00"
    monkeypatch.setattr(life, "utc", lambda: now)
    monkeypatch.setattr(sys, "argv", ["artifact_lifecycle", "release", "--state", str(state), "--run", run.data["run_id"], "--evidence", str(path)])
    assert life.main() == 0
    saved = life.read_run(state, run.data["run_id"])
    assert saved["recovery_released_at"] == now and not saved.get("recovery_expired_at")
    assert saved["release_evidence"]["approved_by"] == "qa-owner"


def test_operator_cannot_complete_a_generation_while_its_owner_lease_is_live(state, tmp_path, monkeypatch):
    with life.Run("active-owner", store=state) as run:
        path, _ = operator_decision(tmp_path, run.data, "complete")
        monkeypatch.setattr(sys, "argv", ["artifact_lifecycle", "complete", "--state", str(state), "--run", run.data["run_id"], "--evidence", str(path)])
        with pytest.raises(life.LeaseUnavailable):
            life.main()
        saved = life.read_run(state, run.data["run_id"])
        assert saved["state"] == "running" and not saved.get("completion_evidence")


@pytest.mark.parametrize("archive_fails", [False, True])
def test_snapshot_images_have_durable_intent_identity_and_removal_events(state, tmp_path, archive_fails):
    owner = ready(state, {"key": "container:source", "kind": "container", "identity": "source", "category": "disposable"})
    source = {"Id": "source", "State": {"Running": False}, "Config": {"Labels": {life.RUN: owner.data["run_id"]}}}
    engine = docker.Engine.__new__(docker.Engine)
    current = {}
    removed = []

    def command(*arguments, **kwargs):
        if arguments[:2] == ("container", "commit"):
            merged = life.merge_events(state, life.read_run(state, owner.data["run_id"]))
            tags = dict(value.removeprefix("LABEL ").split("=", 1) for value in arguments if value.startswith("LABEL "))
            assert merged["creation_intents"][-1]["object"] == tags[life.OBJECT]
            current.update(Id="sha256:" + "f" * 64, Config={"Labels": tags}, RootFS={"Layers": ["layer"]}, RepoTags=[])
            return current["Id"]
        assert arguments[:3] == ("image", "rm", "--no-prune")
        removed.append(arguments[-1])

    def archive(*_):
        if archive_fails:
            raise ValueError("synthetic image export failure")
        return {"image_id": current["Id"]}

    engine.run, engine.inspect, engine.image_archive = command, lambda *_: current, archive
    if archive_fails:
        with pytest.raises(ValueError, match="export failure"):
            engine.snapshot(source, tmp_path, store=state)
    else:
        assert engine.snapshot(source, tmp_path, store=state)["layers"] == ["layer"]
    merged = life.merge_events(state, life.read_run(state, owner.data["run_id"]))
    snapshots = [item for item in merged["resources"] if item["kind"] == "image"]
    assert len(snapshots) == 1 and snapshots[0]["status"] == "removed"
    assert removed == [snapshots[0]["identity"]]


def test_corrupt_run_cannot_authorize_cleanup(state):
    with life.Run("qa", store=state) as run:
        identity = run.data["run_id"]
    path = life.run_path(state, identity)
    value = json.loads(path.read_bytes())
    value["keep_days"] = 0
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Invalid lifecycle receipt"):
        life.read_run(state, identity)


def test_active_run_lease_cannot_be_claimed_by_cleanup(state):
    with life.Run("qa", store=state) as run:
        with pytest.raises(OSError):
            with life.lease(state, run.data["run_id"]):
                pytest.fail("active lease acquired")


def test_completion_published_before_lease_release_is_a_blocker_not_a_cleanup_error(state):
    with life.Run("qa", store=state, days=0) as run:
        run.data.update(
            state="completed",
            task_status="completed",
            completed_at=life.utc(),
            resources=[{"key": "path:pending", "kind": "path", "path": str(state / "output"), "category": "cache", "status": "present"}],
        )
        run.persist()
        result = life.sweep(state, apply=True)
        assert not result["errors"]
        assert result["runs"][0]["blocked"] == ["run-lease-unavailable"]


def test_retention_and_paused_state_are_independent_of_process_exit():
    now = datetime.now(timezone.utc)
    run = {"state": "completed", "task_status": "completed", "completed_at": now.isoformat(), "keep_days": 7}
    assert life.eligible(run, now) == "retention"
    assert life.eligible(run, now + timedelta(days=8)) is None
    run["state"] = "paused"
    assert life.eligible(run, now + timedelta(days=100)) == "run-not-completed"


class FakeEngine:
    identity = "engine"

    def owned(self, identifier):
        return []

    def all(self, kind):
        return []

    def guard(self, resource, run, pins, removable):
        if resource.get("late_consumer") and resource.get("stage") == "archived":
            return "container-reference"
        return None

    def backup(self, resource, store):
        return {"verified": True}

    def remove(self, resource):
        self.removed.append(resource["key"])

    def verify_backup(self, resource):
        pass

    def verify_recovery(self, packet):
        pass

    def __init__(self):
        self.removed = []


def delegated_qa(state, tmp_path, monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(docker, "Engine", lambda **_: engine)
    monkeypatch.setattr(life, "receipt_pins", lambda *_: {})
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    evidence = tmp_path / "qa-results"
    evidence.mkdir()
    (evidence / "result.json").write_text('{"status":"INCOMPLETE"}')
    return engine, evidence


def test_sweep_resumes_failed_qa_teardown_without_accepting_product_or_removing_other_classes(state, tmp_path, monkeypatch):
    engine, evidence = delegated_qa(state, tmp_path, monkeypatch)
    with life.Run("live-regression-qa", store=state) as run:
        run.request_teardown(evidence)
        for kind, category in [("container", "disposable"), ("image", "disposable"), ("container", "runtime"), ("path", "cache")]:
            run.add({"key": f"{kind}:{category}", "kind": kind, "identity": kind, "category": category, "status": "present"})
        assert life.sweep(state, apply=True)["runs"][0]["blocked"] == ["run-lease-unavailable"]
        run.failed = True
    result = life.sweep(state, apply=True)
    assert result["errors"] == []
    assert engine.removed == ["container:disposable"]
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == "failed" and saved["task_status"] == "open"
    packet = saved["teardown_request"]["preserved_evidence"]
    docker.verify_archive(packet["archive"])
    from tools.quality.artifact_retention import references

    assert packet["archive"]["path"] in references(saved, tmp_path / "recovery")
    assert json.loads(Path(packet["manifest"]["path"]).read_text())["result.json"]["sha256"]
    assert not saved.get("consumption_closed")
    life.sweep(state, apply=True)
    assert engine.removed == ["container:disposable"]


@pytest.mark.parametrize("change", ["missing-evidence", "different-source", "recovery-resumed", "different-engine", "no-request"])
def test_delegated_teardown_fails_closed_on_changed_or_missing_authority(state, tmp_path, monkeypatch, change):
    engine, evidence = delegated_qa(state, tmp_path, monkeypatch)
    with life.Run("live-regression-qa", store=state) as run:
        run.request_teardown(evidence)
        run.add({"key": "container:test", "kind": "container", "identity": "one", "category": "disposable", "status": "present"})
        run.failed = True
    saved = life.read_run(state, run.data["run_id"])
    if change == "missing-evidence":
        (evidence / "result.json").unlink()
        evidence.rmdir()
    elif change == "different-source":
        saved["source"] = {"head": "different"}
    elif change == "recovery-resumed":
        saved["recovery_epoch"] = 1
    elif change == "different-engine":
        saved["engine"] = "other"
    else:
        saved.pop("teardown_request")
    life.write_run(state, saved)
    result = life.sweep(state, apply=True)
    assert not engine.removed
    assert result["errors"] or result["runs"][0]["blocked"]


@pytest.mark.parametrize("case", ["snapshot", "finished-snapshot", "discovered", "ordinary", "different-token", "different-purpose", "runtime", "in-use"])
def test_delegated_teardown_recovers_only_its_transient_snapshot_images(state, tmp_path, monkeypatch, case):
    engine, evidence = delegated_qa(state, tmp_path, monkeypatch)
    image = {"key": "image:snapshot", "kind": "image", "identity": "snapshot-image", "token": "snapshot-token", "category": "disposable", "status": "present"}
    with life.Run("live-regression-qa", store=state) as run:
        run.request_teardown(evidence)
        if case != "ordinary":
            run.data["creation_intents"] = [
                {"kind": "image", "object": "other-token" if case == "different-token" else "snapshot-token", "purpose": "build" if case == "different-purpose" else "container-snapshot"}
            ]
        if case == "runtime":
            image["category"] = "runtime"
        if case != "discovered":
            run.add(image)
        if case == "finished-snapshot":
            run.data["teardown_request"]["finished_at"] = life.utc()
        run.failed = True
    if case == "discovered":
        engine.owned = lambda *_: [image]
    if case == "in-use":
        engine.guard = lambda *_: "image-in-use"
    result = life.retire(state, run.data["run_id"], engine, resume_teardown=True)
    assert not result["errors"]
    assert engine.removed == ([image["key"]] if case in {"snapshot", "finished-snapshot", "discovered"} else [])
    if case == "in-use":
        assert result["blocked"] == [{"key": image["key"], "reason": "image-in-use"}]
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == "failed" and saved["task_status"] == "open"
    assert not saved.get("consumption_closed")


def test_finished_delegation_does_not_reopen_for_ordinary_images(state, tmp_path, monkeypatch):
    engine, evidence = delegated_qa(state, tmp_path, monkeypatch)
    with life.Run("live-regression-qa", store=state) as run:
        run.request_teardown(evidence)
        run.add({"key": "image:ordinary", "kind": "image", "identity": "ordinary", "token": "ordinary", "category": "disposable", "status": "present"})
        run.data["teardown_request"]["finished_at"] = life.utc()
        run.failed = True
    assert not life.teardown_requested(life.read_run(state, run.data["run_id"]))
    with pytest.raises(ValueError, match="Missing live-QA teardown delegation"):
        life.retire(state, run.data["run_id"], engine, resume_teardown=True)
    assert not engine.removed


def test_delegated_teardown_keeps_active_and_changed_container_guards(state, tmp_path, monkeypatch):
    engine, evidence = delegated_qa(state, tmp_path, monkeypatch)
    with life.Run("live-regression-qa", store=state) as run:
        run.request_teardown(evidence)
        for identity in ["active", "changed"]:
            run.add({"key": "container:" + identity, "kind": "container", "identity": identity, "category": "disposable", "status": "present"})
        run.failed = True
    engine.guard = lambda target, *_: "container-active" if target["identity"] == "active" else "identity-or-configuration-changed"
    result = life.sweep(state, apply=True)
    assert not engine.removed
    assert len(result["runs"][0]["blocked"]) == 2
    assert result["runs"][0]["delegated_teardown"] is True


def test_delegated_teardown_discovers_unjournaled_resources_after_owner_crash(state, tmp_path, monkeypatch):
    engine, evidence = delegated_qa(state, tmp_path, monkeypatch)
    with life.Run("live-regression-qa", store=state) as run:
        run.request_teardown(evidence)
    saved = life.read_run(state, run.data["run_id"])
    saved["state"] = "running"
    life.write_run(state, saved)
    entry = {"key": "container:discovered", "kind": "container", "identity": "one", "category": "disposable", "status": "present"}
    engine.owned = lambda *_: [entry]
    result = life.sweep(state, apply=True)
    assert not result["errors"] and engine.removed == [entry["key"]]
    saved = life.read_run(state, run.data["run_id"])
    assert saved["state"] == "running" and saved["task_status"] == "open"
    assert saved["teardown_request"]["finished_at"]


def ready(state, resource):
    run = life.Run("qa", days=0, store=state)
    run.data.update(state="completed", task_status="completed", completed_at=life.utc(), engine="engine", resources=[resource])
    run.persist()
    return run


@pytest.mark.parametrize("task_status", [None, "unknown", "open", "failed", [], {}])
def test_completed_execution_without_confirmed_task_closure_cannot_retire(state, task_status):
    run = ready(state, {"key": "volume:unconfirmed", "kind": "volume", "identity": "one", "category": "disposable", "status": "present"})
    run.data.pop("task_status")
    if task_status is not None:
        run.data["task_status"] = task_status
    run.persist()
    engine = FakeEngine()
    result = life.retire(state, run.data["run_id"], engine)
    assert result["blocked"] and not engine.removed


def test_late_consumer_is_checked_after_durable_archive(state, monkeypatch):
    engine = FakeEngine()
    run = ready(state, {"key": "volume:test", "kind": "volume", "identity": "one", "category": "disposable", "late_consumer": True})
    monkeypatch.setattr(life, "receipt_pins", lambda *args: {})
    result = life.retire(state, run.data["run_id"], engine)
    assert not engine.removed and not result["errors"]
    assert result["blocked"][0]["reason"] == "container-reference"
    assert life.read_run(state, run.data["run_id"])["resources"][0]["recovery"] == {"verified": True}


def test_successful_retirement_is_retryable_without_redeleting(state, monkeypatch):
    engine = FakeEngine()
    run = ready(state, {"key": "volume:test", "kind": "volume", "identity": "one", "category": "disposable"})
    monkeypatch.setattr(life, "receipt_pins", lambda *args: {})
    assert life.retire(state, run.data["run_id"], engine)["removed"] == ["volume:test"]
    assert life.retire(state, run.data["run_id"], engine)["removed"] == []
    assert engine.removed == ["volume:test"]


def test_owned_discovery_does_not_rebaseline_parallel_container_configuration(state, monkeypatch):
    entry = {"key": "container:test", "kind": "container", "identity": "one", "category": "disposable", "config_sha256": "original"}
    run = ready(state, entry)
    engine = FakeEngine()
    engine.owned = lambda *_: [{**entry, "config_sha256": "parallel-change"}]
    engine.guard = lambda target, *_: "identity-or-configuration-changed" if target["config_sha256"] == "original" else None
    monkeypatch.setattr(life, "receipt_pins", lambda *_: {})
    result = life.retire(state, run.data["run_id"], engine)
    assert result["blocked"] == [{"key": entry["key"], "reason": "identity-or-configuration-changed"}]
    assert not engine.removed and not result["errors"]
    assert life.read_run(state, run.data["run_id"])["resources"][0]["config_sha256"] == "original"


def test_reappeared_removed_generation_is_reported_instead_of_false_cleanup_success(state, monkeypatch):
    entry = {"key": "image:restored", "kind": "image", "identity": "one", "category": "disposable", "status": "removed"}
    run = ready(state, entry)
    engine = FakeEngine()
    engine.owned = lambda *_: [{**entry, "status": "present"}]
    monkeypatch.setattr(life, "receipt_pins", lambda *_: {})
    result = life.retire(state, run.data["run_id"], engine)
    assert result["blocked"] == [{"key": entry["key"], "reason": "retired-generation-reappeared"}]
    assert not engine.removed


@pytest.mark.parametrize("offset,eligible", [(-1, False), (0, True), (1, True)])
def test_resource_retention_exact_boundary(offset, eligible):
    completed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    run = {"state": "completed", "task_status": "completed", "completed_at": completed.isoformat(), "keep_days": 7}
    assert (life.eligible(run, completed + timedelta(days=7, seconds=offset)) is None) is eligible


@pytest.mark.parametrize("completed,days", [("broken", 0), ("2026-01-01", 0), ("2026-01-01T00:00:00+00:00", -1), ("2026-01-01T00:00:00+00:00", True)])
def test_invalid_retention_cannot_make_resource_eligible(completed, days):
    assert life.eligible({"state": "completed", "task_status": "completed", "completed_at": completed, "keep_days": days}, datetime.now(timezone.utc)) == "invalid-retention"


def test_failed_backup_never_deletes_resource(state, monkeypatch):
    engine = FakeEngine()
    run = ready(state, {"key": "volume:test", "kind": "volume", "identity": "one", "category": "disposable"})
    monkeypatch.setattr(life, "receipt_pins", lambda *args: {})

    def fail(*args):
        raise ValueError("archive corrupt")

    monkeypatch.setattr(engine, "backup", fail)
    result = life.retire(state, run.data["run_id"], engine)
    assert result["errors"] and not engine.removed


@pytest.mark.parametrize("phase", ["after-publication", "late-guard"])
def test_recovery_corruption_before_final_mutation_preserves_original_image(state, tmp_path, monkeypatch, phase):
    inspection = tmp_path / "inspect.json"
    inspection.write_text("{}")
    archive = tmp_path / "image-archive"
    archive.write_bytes(b"original archive bytes")
    packet = {"inspect": docker.archive_info(inspection), "archives": [docker.archive_info(archive)]}
    engine = FakeEngine()
    engine.verify_image = docker.verify_archive
    engine.verify_recovery = lambda value: docker.Engine.verify_recovery(engine, value)
    engine.verify_backup = lambda target: docker.Engine.verify_backup(engine, target)

    def backup(*_):
        if phase == "after-publication":
            archive.write_bytes(b"corrupt")
        return packet

    def guard(target, *_):
        if phase == "late-guard" and target.get("stage") == "archived":
            archive.write_bytes(b"corrupt")

    engine.backup, engine.guard = backup, guard
    run = ready(state, {"key": "image:one", "kind": "image", "identity": "image", "category": "disposable"})
    monkeypatch.setattr(life, "receipt_pins", lambda *_: {})
    result = life.retire(state, run.data["run_id"], engine)
    assert result["errors"] and not engine.removed
    saved = life.read_run(state, run.data["run_id"])["resources"][0]
    assert saved["stage"] == "archived" and saved.get("status") != "removed"


@pytest.mark.parametrize("replacement", [False, True])
def test_process_death_after_remove_before_journal_is_recoverable_and_preserves_replacement(state, tmp_path, monkeypatch, replacement):
    source = tmp_path / "original-resource"
    source.write_bytes(b"unique original bytes")
    run = ready(state, {"key": "volume:crash", "kind": "volume", "identity": "original", "category": "disposable", "status": "present"})
    # A real child exits without finally/checkpoint after the external mutation.
    # File transport makes the journal boundary deterministic without a live daemon.
    script = r"""
import os, sys
from pathlib import Path
from tools.quality import artifact_lifecycle as life, artifact_docker as docker
state, source, run_id = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
class Engine:
    identity = 'engine'
    def owned(self, *_): return []
    def guard(self, *_): return None
    def backup(self, *_):
        archive = source.with_name('recovery-original')
        archive.write_bytes(source.read_bytes())
        return {'archive': docker.archive_info(archive)}
    def verify_backup(self, target): docker.verify_archive(target['recovery']['archive'])
    def verify_recovery(self, packet): docker.verify_archive(packet['archive'])
    def remove(self, target):
        source.unlink()
        os._exit(73)
life.receipt_pins = lambda *_: {}
life.retire(state, run_id, Engine())
"""
    child = subprocess.run([sys.executable, "-B", "-c", script, str(state), str(source), run.data["run_id"]], capture_output=True, text=True, timeout=45)
    assert child.returncode == 73, child.stderr
    saved = life.read_run(state, run.data["run_id"])["resources"][0]
    assert saved["stage"] == "archived" and saved["status"] == "present" and not source.exists()
    docker.verify_archive(saved["recovery"]["archive"])
    assert Path(saved["recovery"]["archive"]["path"]).read_bytes() == b"unique original bytes"
    if replacement:
        source.write_bytes(b"foreign replacement")
    engine = FakeEngine()
    engine.guard = lambda *_: "ownership-or-generation-changed" if source.exists() else "already-absent"
    engine.backup = lambda *_: pytest.fail("Retry must not overwrite durable recovery")
    monkeypatch.setattr(life, "receipt_pins", lambda *_: {})
    result = life.retire(state, run.data["run_id"], engine)
    assert not result["errors"] and not engine.removed
    if replacement:
        assert result["blocked"] and source.read_bytes() == b"foreign replacement"
    else:
        assert not result["blocked"]
        assert life.read_run(state, run.data["run_id"])["resources"][0]["stage"] == "absence-confirmed"


def test_concurrent_sweeper_processes_share_one_generation_lease(state, tmp_path):
    import time

    source = tmp_path / "source-object"
    source.write_bytes(b"generation bytes")
    run = ready(state, {"key": "volume:concurrent", "kind": "volume", "identity": "one", "category": "disposable", "status": "present"})
    script = r"""
import sys,time
from pathlib import Path
from tools.quality import artifact_lifecycle as life,artifact_docker as docker
from test.unit_test.tools.quality.test_artifact_lifecycle import FakeEngine
state,root,run_id=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
class Engine(FakeEngine):
    def backup(self,*_):
        archive=root/'recovery-bytes'
        archive.write_bytes((root/'source-object').read_bytes())
        (root/'at-backup').touch()
        deadline=time.monotonic()+60
        while not (root/'release').exists():
            if time.monotonic()>deadline: raise RuntimeError('barrier expired')
            time.sleep(0.01)
        return {'archive':docker.archive_info(archive)}
    def remove(self,*_):
        (root/'source-object').unlink()
        with (root/'deletions').open('a') as out: out.write('delete\n')
life.receipt_pins=lambda *_:{}
try:
    result=life.retire(state,run_id,Engine())
except life.LeaseUnavailable:
    sys.exit(42)
assert not result['errors'] and not result['blocked'],result
"""
    command = [sys.executable, "-B", "-c", script, str(state), str(tmp_path), run.data["run_id"]]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 30
        while not (tmp_path / "at-backup").exists():
            assert first.poll() is None, first.communicate()
            assert time.monotonic() < deadline, "First sweeper did not reach its barrier"
            time.sleep(0.01)
        second = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert second.returncode == 42, second.stderr
        assert source.read_bytes() == b"generation bytes"
    finally:
        (tmp_path / "release").touch()
        stdout, stderr = first.communicate(timeout=45)
    assert first.returncode == 0, stderr or stdout
    assert (tmp_path / "deletions").read_text() == "delete\n"
    saved = life.read_run(state, run.data["run_id"])["resources"][0]
    assert saved["status"] == "removed"
    docker.verify_archive(saved["recovery"]["archive"])


def test_late_receipt_pin_is_refreshed(state, monkeypatch):
    engine = FakeEngine()
    run = ready(state, {"key": "image:test", "kind": "image", "identity": "image", "category": "disposable"})
    calls = []

    def pins(*args):
        calls.append(True)
        return {"image": ["late-receipt.json"]} if len(calls) > 1 else {}

    monkeypatch.setattr(life, "receipt_pins", pins)
    monkeypatch.setattr(engine, "guard", lambda resource, owner, pins, allowed: "receipt-pin" if pins else None)
    assert life.retire(state, run.data["run_id"], engine)["blocked"][0]["reason"] == "receipt-pin"
    assert not engine.removed


def test_foreign_generation_and_running_container_are_guarded():
    info = {
        "Id": "one",
        "Name": "/qa",
        "Config": {"Labels": {life.PROJECT: "ragflow", life.RUN: "run", life.OBJECT: "object", life.CLASS: "disposable"}},
        "HostConfig": {},
        "Mounts": [],
        "State": {"Running": True},
    }
    engine = docker.Engine.__new__(docker.Engine)
    engine.assert_engine = lambda: None
    engine.inspect = lambda *args: info
    engine.all = lambda *args: []
    target = docker.resource(info, "container")
    assert engine.guard(target, "run", {}, set()) == "container-active"
    info["State"]["Running"] = False
    info["Config"]["Labels"][life.OBJECT] = "replacement"
    assert engine.guard(target, "run", {}, set()) == "ownership-or-generation-changed"


def test_shared_volume_cannot_be_removed_even_if_foreign_container_is_stopped():
    info = {"Name": "qa", "Driver": "local", "CreatedAt": "today", "Labels": {life.PROJECT: "ragflow", life.RUN: "run", life.OBJECT: "object", life.CLASS: "disposable"}}
    engine = docker.Engine.__new__(docker.Engine)
    engine.assert_engine = lambda: None
    engine.inspect = lambda *args: info
    engine.all = lambda *args: [{"Id": "foreign", "State": {"Running": False}, "Mounts": [{"Type": "volume", "Name": "qa"}]}]
    assert engine.guard(docker.resource(info, "volume"), "run", {}, set()) == "container-reference"


def test_volume_restore_refuses_existing_destination_before_writing(tmp_path):
    # Full restore is covered by the disposable Docker probe; collision preflight is unit-testable.
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps({"Name": "data"}))
    recovery = {"inspect": docker.archive_info(path), "archives": []}
    run = {"run_id": "b" * 32, "resources": [{"key": "volume:data", "kind": "volume", "status": "removed", "recovery": recovery}]}

    class Recovery:
        store = tmp_path / "state"
        data = {"run_id": "c" * 32}

        def persist(self):
            pass

        def engine(self):
            engine = docker.Engine.__new__(docker.Engine)
            engine.inspect = lambda *args: {"Name": "data"}
            return engine

    with pytest.raises(ValueError, match="already exists"):
        docker.restore(run, Recovery())


class RestoreEngine:
    identity = "engine"

    def __init__(self):
        self.volumes, self.containers, self.actions = {}, {}, []
        self.on_create = lambda: None

    def inspect(self, kind, identifier):
        if kind == "image":
            return {"Id": identifier}
        return (self.volumes if kind == "volume" else self.containers).get(identifier)

    def api(self, method, path, body):
        assert method == "POST" and path.startswith("/containers/create")
        self.actions.append(("create", body))
        self.containers["created-id"] = {"Id": "created-id", "Config": {"Labels": body["Labels"]}, "HostConfig": body["HostConfig"], "Mounts": [], "State": {"Running": False}}
        self.on_create()
        return {"Id": "created-id"}

    def run(self, *arguments):
        self.actions.append(arguments)
        return "GNU tar" if arguments[-1] == "--version" else ""

    def verify_image(self, packet):
        docker.verify_archive(packet)


class RestoreOwner:
    def __init__(self, store, engine):
        self.store, self.client = store, engine
        self.data = {"run_id": "a" * 32}
        self.dependencies = []
        self.on_image = lambda: None

    def engine(self):
        return self.client

    def labels(self, kind):
        return {life.PROJECT: "ragflow", life.RUN: self.data["run_id"], life.OBJECT: "new-object", life.CLASS: "disposable"}

    def use_image(self, reference):
        self.dependencies.append(reference)
        self.on_image()

    def capture(self):
        pass

    def persist(self):
        pass


def container_recovery(tmp_path, mounts, **extra):
    inspection = tmp_path / "container-inspect.json"
    inspection.write_text(json.dumps({"Id": "old-id", "Name": "/original", "Config": {}, "HostConfig": {}, "Mounts": mounts}))
    return {
        "schema": 1,
        "project": "ragflow",
        "run_id": "b" * 32,
        "task_ref": "restore/source",
        "source": None,
        "state": "completed",
        "engine": "engine",
        "created_at": life.utc(),
        "completed_at": life.utc(),
        "keep_days": 0,
        "resources": [
            {"key": "container:one", "kind": "container", "status": "removed", "recovery": {"inspect": docker.archive_info(inspection), "archives": [], "base_image": "sha256:" + "f" * 64, **extra}}
        ],
    }


def test_restore_rejects_foreign_same_name_volume_before_container_create(state, tmp_path):
    original = {"Name": "data", "Driver": "local", "CreatedAt": "old", "Labels": {life.RUN: "old", life.OBJECT: "old"}}
    engine = RestoreEngine()
    engine.volumes["data"] = {**original, "CreatedAt": "new", "Labels": {life.RUN: "foreign", life.OBJECT: "foreign"}}
    run = container_recovery(tmp_path, [{"Type": "volume", "Name": "data"}], volume_sources={"data": docker.volume_identity(original)})
    with pytest.raises(ValueError, match="volume generation"):
        docker.restore(run, RestoreOwner(state, engine))
    assert not engine.actions
    assert engine.volumes["data"]["Labels"][life.RUN] == "foreign"


def test_restore_rechecks_bind_generation_after_loading_image(state, tmp_path, monkeypatch):
    current = [1, 2, 3]
    monkeypatch.setattr(docker, "bind_identity", lambda _: current.copy())
    engine = RestoreEngine()
    owner = RestoreOwner(state, engine)
    owner.on_image = lambda: current.__setitem__(1, 99)
    run = container_recovery(tmp_path, [{"Type": "bind", "Source": "original-path"}], bind_sources={"original-path": current.copy()})
    with pytest.raises(ValueError, match="bind source generation changed"):
        docker.restore(run, owner)
    assert not engine.actions


def test_restore_rejects_replacement_during_container_create_without_starting(state, tmp_path):
    original = {"Name": "data", "Driver": "local", "CreatedAt": "old", "Labels": {life.RUN: "old", life.OBJECT: "old"}}
    engine = RestoreEngine()
    engine.volumes["data"] = original
    engine.on_create = lambda: engine.volumes.update(data={**original, "CreatedAt": "replacement"})
    run = container_recovery(tmp_path, [{"Type": "volume", "Name": "data"}], volume_sources={"data": docker.volume_identity(original)})
    with pytest.raises(ValueError, match="volume generation"):
        docker.restore(run, RestoreOwner(state, engine))
    assert ("container", "rm", "created-id") in engine.actions
    assert not any(action[:2] == ("container", "start") for action in engine.actions)
    assert engine.volumes["data"]["CreatedAt"] == "replacement"


def test_original_volume_generation_and_stopped_container_restore_are_accepted(state, tmp_path):
    original = {"Name": "data", "Driver": "local", "CreatedAt": "old", "Labels": {life.RUN: "old", life.OBJECT: "old"}}
    engine = RestoreEngine()
    engine.volumes["data"] = original
    run = container_recovery(tmp_path, [{"Type": "volume", "Name": "data"}], volume_sources={"data": docker.volume_identity(original)})
    docker.restore(run, RestoreOwner(state, engine))
    assert len(engine.actions) == 1 and engine.actions[0][0] == "create"
    assert engine.containers["created-id"]["State"]["Running"] is False


def test_restore_consumes_old_removal_events_before_reopening_snapshot_and_invalidates_release(state, tmp_path):
    run = container_recovery(tmp_path, [])
    identifier = run["resources"][0]["recovery"]["base_image"]
    image = {"Id": identifier, "Config": {"Labels": {life.PROJECT: "ragflow", life.RUN: run["run_id"], life.OBJECT: "snapshot", life.CLASS: "disposable"}}, "RepoTags": []}
    snapshot = {**docker.resource(image, "image"), "status": "removed"}
    source = life.Run("source", store=state)
    source.data = run
    release_at(source, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    approval = Path(source.data["release_evidence"]["path"])
    life.event(state, run["run_id"], resource={**snapshot, "status": "present"})
    life.event(state, run["run_id"], resource=snapshot)
    engine = RestoreEngine()
    inspect = engine.inspect
    engine.inspect = lambda kind, name: image if kind == "image" else inspect(kind, name)
    docker.restore(run, RestoreOwner(state, engine))
    saved = life.merge_events(state, life.read_run(state, run["run_id"]))
    assert next(entry for entry in saved["resources"] if entry["key"] == snapshot["key"])["status"] == "present"
    assert saved["recovery_epoch"] == 1 and not saved.get("recovery_released_at")
    assert saved["invalidated_releases"][0]["reason"] == "recovery-resumed"
    with pytest.raises(ValueError, match="exact run"):
        life.operator_evidence(approval, saved, "release")


@pytest.mark.parametrize("action", ["complete", "release"])
def test_repeated_identical_operator_decision_is_idempotent(state, tmp_path, monkeypatch, action):
    run = ready(state, {"key": "volume:operator", "kind": "volume", "identity": "original", "category": "disposable", "status": "removed"})
    path, _ = operator_decision(tmp_path, run.data, action)
    monkeypatch.setattr(sys, "argv", ["artifact_lifecycle", action, "--state", str(state), "--run", run.data["run_id"], "--evidence", str(path)])
    assert life.main() == 0
    first = life.run_path(state, run.data["run_id"]).read_bytes()
    assert life.main() == 0
    assert life.run_path(state, run.data["run_id"]).read_bytes() == first


@pytest.mark.parametrize("replacement", [False, True])
def test_volume_pin_validates_generation_before_any_io_and_accounts_cleanup(state, replacement):
    original = {"Name": "data", "Driver": "local", "CreatedAt": "old", "Labels": {life.RUN: "b" * 32, life.OBJECT: "old"}}
    engine = RestoreEngine()
    engine.volumes["data"] = original
    if replacement:
        engine.on_create = lambda: engine.volumes.update(data={**original, "CreatedAt": "foreign"})
    reached = False
    try:
        with docker.Engine.volume_access(engine, original, "immutable-helper", state, writable=True):
            reached = True
            assert not replacement
    except ValueError as error:
        assert replacement and "generation changed" in str(error)
    assert reached is not replacement
    assert ("container", "rm", "created-id") in engine.actions
    assert any(action[:2] == ("container", "start") for action in engine.actions) is not replacement
    merged = life.merge_events(state, {"run_id": "b" * 32, "resources": []})
    assert merged["creation_intents"][0]["purpose"] == "volume-archive-pin"
    assert merged["resources"][0]["status"] == "removed"


def test_workspace_generation_completion_does_not_close_replacement(state, tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    life.workspace_reserved(path)
    first = life.runs(state)[0]
    old_identity = first["resources"][0]["identity"]
    path.rename(tmp_path / "old")
    path.mkdir()
    life.workspace_reserved(path)
    life.workspace_retired(path, old_identity)
    assert sorted(r["state"] for r in life.runs(state)) == ["completed", "waiting"]


def test_child_events_merge_without_reopening_removed_resource(state):
    with life.Run("owner", store=state) as run:
        identifier = run.data["run_id"]
        life.event(state, identifier, resource={"key": "image:test", "kind": "image", "status": "present"})
        life.merge_events(state, run.data)
        run.data["resources"][0]["status"] = "removed"
        run.persist()
        life.merge_events(state, run.data)
        assert run.data["resources"][0]["status"] == "removed"
    assert life.runs(state)[0]["resources"][0]["status"] == "removed"


@pytest.mark.parametrize("child", [False, True])
def test_path_intent_is_durable_before_creation_without_claiming_an_absent_resource(state, tmp_path, child):
    target = tmp_path / "planned-evidence.json"
    with life.Run("owner", store=state) as run:
        if child:
            script = """
import sys
from pathlib import Path
from tools.quality import artifact_lifecycle as life
target=Path(sys.argv[1])
life.note(target,category='metadata')
life.note(target,category='metadata')
current=life.current()
merged=life.merge_events(current.store,life.read_run(current.store,current.data['run_id']))
assert not target.exists() and merged['resources']==[]
assert len([i for i in merged['creation_intents'] if i.get('path')==str(target)])==1
target.write_text('unique evidence')
life.note(target,receipt=True,category='metadata')
"""
            process = subprocess.run([sys.executable, "-B", "-c", script, str(target)], capture_output=True, text=True, timeout=45)
            assert process.returncode == 0, process.stderr
        else:
            life.note(target, category="metadata")
            life.note(target, category="metadata")
            saved = life.read_run(state, run.data["run_id"])
            assert not target.exists() and saved["resources"] == []
            assert len([item for item in saved["creation_intents"] if item.get("path") == str(target)]) == 1
            target.write_text("unique evidence")
            life.note(target, receipt=True, category="metadata")
    saved = life.read_run(state, run.data["run_id"])
    assert len(saved["resources"]) == len(saved["receipts"]) == 1
    assert saved["resources"][0]["path"] == saved["receipts"][0]["path"] == str(target)
    assert target.read_text() == "unique evidence"


@pytest.mark.parametrize("change", ["reopen", "generation"])
def test_new_child_event_cannot_reopen_or_replace_a_sealed_resource(state, change):
    original = {"key": "volume:sealed", "kind": "volume", "identity": "original", "status": "removed" if change == "reopen" else "present"}
    run = ready(state, original)
    entry = {**original, "status": "present"}
    if change == "generation":
        entry["identity"] = "replacement"
    life.event(state, run.data["run_id"], resource=entry)
    with pytest.raises(ValueError, match="cannot reopen|cannot replace"):
        life.merge_events(state, life.read_run(state, run.data["run_id"]))
    assert life.read_run(state, run.data["run_id"])["resources"] == [original]


def test_forged_child_capability_is_rejected(state, monkeypatch):
    with life.Run("owner", store=state) as run:
        life._live.pop(run.data["run_id"])
        token = life._current.set(None)
        monkeypatch.setenv("RAGFLOW_ARTIFACT_OWNER_TOKEN", "wrong")
        try:
            with pytest.raises(ValueError, match="capability"):
                life.current()
        finally:
            life._current.reset(token)


def test_child_can_publish_but_cannot_replace_owner_state(state):
    with life.Run("owner", store=state) as run:
        life._live.pop(run.data["run_id"])
        token = life._current.set(None)
        try:
            child = life.current()
            child.add({"key": "path:child", "kind": "path", "category": "evidence", "status": "present"})
            with pytest.raises(ValueError, match="child publishes"):
                child.persist()
            assert life.read_run(state, run.data["run_id"])["resources"] == []
        finally:
            life._current.reset(token)
    assert life.runs(state)[0]["resources"][0]["key"] == "path:child"


def test_new_image_tag_blocks_deletion():
    info = {"Id": "image", "RepoTags": ["qa:original"], "Config": {"Labels": {life.PROJECT: "ragflow", life.RUN: "run", life.OBJECT: "object", life.CLASS: "cache"}}}
    target = docker.resource(info, "image")
    info["RepoTags"].append("release:parallel")
    engine = docker.Engine.__new__(docker.Engine)
    engine.assert_engine = lambda: None
    engine.inspect = lambda *args: info
    assert engine.guard(target, "run", {}, set()) == "image-tags-changed"


def test_late_active_build_dependency_blocks_image_retirement(state, monkeypatch):
    engine = FakeEngine()
    run = ready(state, {"key": "image:test", "kind": "image", "identity": "one", "category": "cache"})
    other = life.Run("builder", store=state)
    other.data.update(state="waiting", engine="engine", image_dependencies=[{"image_id": "one", "reference": "qa:base"}])
    other.persist()
    monkeypatch.setattr(life, "receipt_pins", lambda *args: {})
    assert life.retire(state, run.data["run_id"], engine)["blocked"][0]["reason"] == "other-unclosed-image-user"
    assert not engine.removed


def test_ci_completion_requires_exact_attempt_and_source(state, monkeypatch):
    run = life.Run("ci", store=state)
    run.data.update(state="waiting", ci={"GITHUB_REPOSITORY": "owner/ragflow", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "expected"})
    run.persist()
    monkeypatch.setattr(life.subprocess, "check_output", lambda *args, **kwargs: json.dumps({"status": "completed", "id": 123, "run_attempt": 1, "head_sha": "expected"}))
    with pytest.raises(ValueError, match="does not match"):
        life.reconcile_ci(state, run.data)
    assert life.read_run(state, run.data["run_id"])["state"] == "waiting"


def test_evidence_modified_after_archive_blocks_workspace_retirement(state, tmp_path, monkeypatch):
    from tools.quality.artifact_paths import workspace_evidence_ready

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("before")
    with life.Run("qa", store=state) as run:
        run.path(evidence)
    evidence.write_text("after")
    with pytest.raises(ValueError, match="Evidence changed"):
        workspace_evidence_ready(tmp_path, state)


def test_recovery_expiry_preserves_shared_packets_and_requires_release(state, tmp_path, monkeypatch):
    from tools.quality.artifact_retention import collect

    archive = tmp_path / "recovery/proof"
    archive.parent.mkdir()
    archive.write_text("unique")
    first = ready(state, {"key": "image:old", "kind": "image", "status": "removed", "recovery": {"archives": [docker.archive_info(archive)]}})
    second = ready(state, {"key": "image:new", "kind": "image", "status": "removed", "recovery": {"archives": [docker.archive_info(archive)]}})
    release_at(first, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    assert not collect(state, apply=True)["removed"]
    assert archive.exists()
    release_at(second, first.data["recovery_released_at"])
    assert collect(state, apply=False)["removed"] and archive.exists()
    assert collect(state, apply=True)["removed"] and not archive.exists()


@pytest.mark.parametrize("offset,deleted", [(-1, False), (0, True), (1, True)])
def test_recovery_release_expires_at_exact_ninety_day_boundary(state, tmp_path, offset, deleted):
    from tools.quality.artifact_retention import collect

    archive = tmp_path / "recovery/proof"
    archive.parent.mkdir()
    archive.write_bytes(b"recovery")
    run = ready(state, {"key": "volume:old", "kind": "volume", "status": "removed", "recovery": {"archives": [docker.archive_info(archive)]}})
    released = datetime(2026, 1, 1, tzinfo=timezone.utc)
    release_at(run, released.isoformat())
    result = collect(state, apply=True, now=released + timedelta(days=90, seconds=offset))
    assert not result["errors"]
    assert archive.exists() is not deleted
    assert bool(life.read_run(state, run.data["run_id"]).get("recovery_expired_at")) is deleted


@pytest.mark.parametrize("released", ["invalid", "2026-01-01", "2999-01-01T00:00:00+00:00"])
def test_invalid_or_future_release_never_deletes_recovery(state, tmp_path, released):
    from tools.quality.artifact_retention import collect

    archive = tmp_path / "recovery/proof"
    archive.parent.mkdir()
    archive.write_bytes(b"recovery")
    run = ready(state, {"key": "volume:old", "kind": "volume", "status": "removed", "recovery": {"archives": [docker.archive_info(archive)]}})
    release_at(run, released)
    result = collect(state, apply=True)
    assert not result["removed"] and archive.exists()
    assert not life.read_run(state, run.data["run_id"]).get("recovery_expired_at")


@pytest.mark.parametrize("legacy", [False, True])
def test_retained_container_protects_transitive_base_image_cas(state, tmp_path, legacy):
    import io
    from tools.quality import artifact_blobs
    from tools.quality.artifact_retention import collect

    root = tmp_path / "recovery"
    blob = artifact_blobs.put(io.BytesIO(b"shared immutable layer"), root)
    identifier = "sha256:" + "f" * 64
    manifest = root / "images" / ("f" * 64 + ".image.json")
    life.save({"image_id": identifier, "members": [{"blob": blob}]}, manifest)
    packet = docker.archive_info(manifest, image_id=identifier, kind="image-cas")
    image = ready(state, {"key": "image:old", "kind": "image", "status": "removed", "recovery": {"archives": [packet]}})
    release_at(image, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    recovery = {"base_image": identifier, **({} if legacy else {"base_image_archive": packet})}
    ready(state, {"key": "container:retained", "kind": "container", "status": "removed", "recovery": recovery})
    result = collect(state, apply=True)
    assert not result["errors"] and not result["removed"]
    assert manifest.exists() and artifact_blobs.path(root, blob).exists()


def test_distinct_cas_manifests_keep_shared_blob_until_last_release(state, tmp_path):
    import io
    from tools.quality import artifact_blobs
    from tools.quality.artifact_retention import collect

    root = tmp_path / "recovery"
    blob = artifact_blobs.put(io.BytesIO(b"common layer"), root)
    manifests, owners = [], []
    for name in ("first", "second"):
        manifest = root / "images" / (name + ".image.json")
        life.save({"members": [{"blob": blob}]}, manifest)
        packet = docker.archive_info(manifest, kind="image-cas")
        manifests.append(manifest)
        owners.append(ready(state, {"key": "image:" + name, "kind": "image", "status": "removed", "recovery": {"archives": [packet]}}))
    released = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    release_at(owners[0], released)
    result = collect(state, apply=True)
    assert not result["errors"] and not manifests[0].exists() and manifests[1].exists()
    assert artifact_blobs.path(root, blob).exists()
    release_at(owners[1], released)
    result = collect(state, apply=True)
    assert not result["errors"] and not manifests[1].exists()
    assert not (root / "blobs" / blob["name"]).exists()


def test_missing_retained_manifest_blocks_gc_of_unknown_shared_dependencies(state, tmp_path):
    from tools.quality.artifact_retention import collect

    root = tmp_path / "recovery"
    root.mkdir()
    retained = root / "retained-manifest.json"
    retained.write_text('{"members": []}')
    packet = docker.archive_info(retained, kind="image-cas")
    ready(state, {"key": "image:retained", "kind": "image", "status": "removed", "recovery": {"archives": [packet]}})
    retained.unlink()
    expired = root / "possibly-shared-blob"
    expired.write_bytes(b"keep")
    owner = ready(state, {"key": "image:expired", "kind": "image", "status": "removed", "recovery": {"archives": [docker.archive_info(expired)]}})
    release_at(owner, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    result = collect(state, apply=True)
    assert result["errors"] and not result["removed"] and expired.exists()


def test_partial_recovery_gc_retries_durable_expiry_list(state, tmp_path, monkeypatch):
    from pathlib import Path
    from tools.quality.artifact_retention import collect

    root = tmp_path / "recovery"
    root.mkdir()
    files = [root / "first", root / "second"]
    for path in files:
        path.write_bytes(b"unique recovery")
    run = ready(state, {"key": "volume:old", "kind": "volume", "status": "removed", "recovery": {"archives": [docker.archive_info(p) for p in files]}})
    release_at(run, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path == files[1]:
            raise PermissionError("synthetic lock")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked)
        result = collect(state, apply=True)
    partial = life.read_run(state, run.data["run_id"])
    assert result["errors"] and not files[0].exists() and files[1].exists()
    assert partial["expiry_files"] and not partial.get("recovery_expired_at")
    result = collect(state, apply=True)
    assert not result["errors"] and not files[1].exists()
    assert life.read_run(state, run.data["run_id"])["recovery_expired_at"]


@pytest.mark.parametrize("changed", ["new-packet", "present", "pending-event", "approval-changed", "approval-missing", "legacy-release"])
def test_old_release_cannot_expire_changed_or_reopened_recovery_subject(state, tmp_path, changed):
    from tools.quality.artifact_retention import collect

    root = tmp_path / "recovery"
    root.mkdir()
    original, newer = root / "original", root / "newer"
    original.write_bytes(b"original recovery")
    newer.write_bytes(b"new recovery")
    run = ready(state, {"key": "volume:released", "kind": "volume", "identity": "original", "status": "removed", "recovery": {"archives": [docker.archive_info(original)]}})
    release_at(run, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    if changed == "new-packet":
        run.data["resources"][0]["recovery"]["archives"] = [docker.archive_info(newer)]
    elif changed == "present":
        run.data["resources"][0]["status"] = "present"
    elif changed == "pending-event":
        life.event(state, run.data["run_id"], resource={"key": "path:late", "kind": "path", "path": str(root / "late"), "identity": [1, 2, 3], "category": "metadata", "status": "present"})
    elif changed == "approval-changed":
        path = Path(run.data["release_evidence"]["path"])
        proof = json.loads(path.read_bytes())
        proof["approved_by"] = "another-owner"
        path.write_text(json.dumps(proof))
    elif changed == "approval-missing":
        Path(run.data["release_evidence"]["path"]).unlink()
    else:
        run.data.pop("release_evidence")
    run.persist()
    result = collect(state, apply=True)
    assert result["errors"] or result["blocked"]
    assert not result["removed"] and original.exists() and newer.exists()
    assert not life.read_run(state, run.data["run_id"]).get("recovery_expired_at")


def test_paused_recovery_without_own_outputs_keeps_consumed_input_packets(state, tmp_path):
    from tools.quality.artifact_retention import collect

    archive = tmp_path / "recovery/input-data"
    archive.parent.mkdir()
    archive.write_bytes(b"shared restore input")
    packet = {"archives": [docker.archive_info(archive)]}
    owner = ready(state, {"key": "volume:source", "kind": "volume", "status": "removed", "recovery": packet})
    release_at(owner, (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())
    consumer = life.Run("paused-image-only-recovery", store=state)
    consumer.data.update(state="paused", recovery_dependencies=[{"run_id": owner.data["run_id"], "epoch": 0, "packets": [packet]}])
    consumer.persist()
    result = collect(state, apply=True)
    assert not result["errors"] and not result["removed"]
    assert archive.read_bytes() == b"shared restore input" and consumer.data["resources"] == []


def test_recovery_blob_storage_deduplicates_and_rejects_corruption(tmp_path):
    import io
    from tools.quality import artifact_blobs

    first = artifact_blobs.put(io.BytesIO(b"shared layer"), tmp_path)
    assert artifact_blobs.put(io.BytesIO(b"shared layer"), tmp_path) == first
    assert len(list((tmp_path / "blobs").iterdir())) == 1
    artifact_blobs.path(tmp_path, first).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        artifact_blobs.path(tmp_path, first)


def test_known_recovery_blob_reuse_checks_source_bytes_and_corrupt_archive(tmp_path, monkeypatch):
    import io
    from tools.quality import artifact_blobs

    blob = artifact_blobs.put(io.BytesIO(b"shared layer"), tmp_path)
    monkeypatch.setattr(artifact_blobs.tempfile, "NamedTemporaryFile", lambda **_: pytest.fail("Known content must not be compressed again"))
    assert artifact_blobs.put(io.BytesIO(b"shared layer"), tmp_path, reuse=blob) == blob
    with pytest.raises(ValueError, match="differs"):
        artifact_blobs.put(io.BytesIO(b"changed layer"), tmp_path, reuse=blob)
    artifact_blobs.path(tmp_path, blob).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        artifact_blobs.put(io.BytesIO(b"shared layer"), tmp_path, reuse=blob)


def test_unmanaged_command_is_returned_without_changing_its_type(state):
    arguments = ("sudo", "-n", "docker", "run", "image", "version")
    assert life.docker_args(arguments) is arguments


@pytest.mark.parametrize("command", [["docker", "system", "prune", "-af"], ["docker", "buildx", "prune", "-af"]])
def test_managed_producer_rejects_global_prune(state, command):
    with life.Run("qa", store=state) as run:
        run.data["engine"] = "engine"
        run._engine = FakeEngine()
        run._engine.prefix = ["docker", "--context", "unit"]
        with pytest.raises(ValueError, match="prune"):
            life.docker_args(command)


def test_cache_receipt_is_preserved_and_changed_generation_is_not_deleted(state, tmp_path, monkeypatch):
    from tools.quality import artifact_paths
    from tools.quality.check_cache import write_success

    monkeypatch.setattr(artifact_paths, "ROOT", tmp_path)
    monkeypatch.setattr(artifact_paths.subprocess, "check_output", lambda *args, **kwargs: "[]")
    key = "f" * 64
    receipt = tmp_path / ".local/cache/deploy/checks" / (key + ".json")
    receipt.parent.mkdir(parents=True)
    write_success(receipt, {"schema": 1, "key": key, "status": "PASS"})
    with life.Run("cache", days=0, store=state) as run:
        run._engine = FakeEngine()
        run.data["engine"] = "engine"
        run.path(receipt, "cache")
        run.data["task_status"] = "completed"  # This fixture explicitly closes its synthetic task.
    result = life.retire(state, run.data["run_id"], run._engine)
    assert result["removed"] and not result["errors"] and not receipt.exists()
    entry = life.read_run(state, run.data["run_id"])["resources"][0]
    docker.verify_archive(entry["recovery"]["metadata"])
    receipt.write_text("parallel replacement")
    with life.Run("restoring", store=state) as restoring:
        restoring._engine = FakeEngine()
        with pytest.raises(ValueError, match="already exists"):
            docker.restore(life.read_run(state, run.data["run_id"]), restoring)
    assert receipt.read_text() == "parallel replacement"


def test_mount_of_whole_native_cache_protects_an_individual_entry(tmp_path, monkeypatch):
    from tools.quality import artifact_paths

    cache = tmp_path / "native-cache"
    entry = cache / "artifacts" / ("a" * 64)
    entry.mkdir(parents=True)
    monkeypatch.setenv("RAGFLOW_GO_CACHE_ROOT", str(cache))
    target = {"path": str(entry), "category": "cache", "identity": artifact_paths.identity(entry)}
    engine = FakeEngine()
    engine.all = lambda *_: [{"Mounts": [{"Type": "bind", "Source": str(cache)}]}]
    assert artifact_paths.guard(target, {"run_id": "owner"}, [], engine) == "cache-mounted"


@pytest.mark.parametrize("missing", [True, False])
def test_disappearing_inspection_consumer_is_distinct_from_docker_failure(monkeypatch, missing):
    import subprocess

    engine = docker.Engine.__new__(docker.Engine)
    engine.prefix, engine.privilege, engine.configuration, engine.env = ["docker"], [], [], {}
    engine.run = lambda *_: "stable transient"
    response = subprocess.CompletedProcess([], 1, b'[{"Id":"stable"}]', b"Error: No such object: transient" if missing else b"permission denied")
    monkeypatch.setattr(docker.subprocess, "run", lambda *args, **kwargs: response)
    if missing:
        assert engine.all("container") == [{"Id": "stable"}]
    else:
        with pytest.raises(RuntimeError, match="enumerate"):
            engine.all("container")


@pytest.mark.parametrize("inside", [True, False])
def test_generated_compose_metadata_cleans_only_its_exact_namespace(state, tmp_path, inside):
    with life.Run("compose", store=state, days=0) as run:
        run._engine = FakeEngine()
        run.data["engine"] = "engine"
        path = (state / "compose" / run.data["run_id"] if inside else tmp_path / "parallel") / ("a" * 32 + ".json")
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        run.path(path, "disposable")
        run.add({**run.data["resources"][0], "generated_by": "compose-overlay", "recovery": {"kind": "rebuildable-compose-overlay", "content": {}}})
        run.data["task_status"] = "completed"  # Exercise namespace protection after task closure.
    result = life.retire(state, run.data["run_id"], run._engine)
    assert path.exists() is not inside
    assert bool(result["errors"]) is not inside
