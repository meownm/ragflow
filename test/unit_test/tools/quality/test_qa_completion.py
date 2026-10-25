"""Standard QA finish seals durable evidence without closing the product task."""

from contextlib import contextmanager
from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.quality import artifact_docker as docker
from tools.quality import artifact_lifecycle as life
from tools.quality import pytest_artifacts as plugin


@pytest.fixture
def state(tmp_path, monkeypatch):
    store = tmp_path / "state"
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(store))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    for name in ("RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_OWNER_TOKEN", "PW_ARTIFACTS_DIR"):
        monkeypatch.delenv(name, raising=False)
    return store


class Engine:
    identity = "engine"
    prefix = ["docker"]
    env = {}

    def __init__(self, **_):
        pass

    def owned(self, _):
        return []

    def inspect(self, kind, name):
        assert kind == "image"
        return {"Id": name}


def result(run):
    return {"run_id": run.data["run_id"], "removed": [], "errors": [], "blocked": [], "retained": []}


def report_for(run):
    return {
        "schema": 1,
        "project": "ragflow",
        "kind": "ci-engine",
        "status": "PASS",
        "artifact_run_id": run["run_id"],
        "artifact_subject": deepcopy(life.consumption_subject(run)),
        "sha": run["source"]["head"],
        "ci": deepcopy(run["ci"]),
    }


@pytest.mark.parametrize("case", ["success", "no-report", "wrong-generation", "wrong-attempt", "wrong-source", "malformed", "before-lease", "during-teardown", "changed-report", "blocked-cleanup"])
def test_ci_finish_verifies_current_generation_under_lease_and_keeps_retryable_failure(state, tmp_path, monkeypatch, case):
    monkeypatch.setattr(docker, "Engine", Engine)
    with life.Run("ci-engine:infinity", days=0, store=state) as owner:
        owner._engine = Engine()
        owner.data.update(engine="engine", handoff=True, ci={"GITHUB_REPOSITORY": "owner/ragflow", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "b" * 40})
        owner.use_image("input-image")
    owner.data["state"] = "waiting"
    owner.persist()
    proof = report_for(owner.data)
    if case == "wrong-generation":
        proof["artifact_run_id"] = "other-run"
    elif case == "wrong-attempt":
        proof["ci"]["GITHUB_RUN_ATTEMPT"] = "2"
    elif case == "wrong-source":
        proof["sha"] = "f" * 40
    elif case == "malformed":
        proof = []
    path = tmp_path / "engine-infinity.json"
    path.write_text(json.dumps(proof))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_RUN_ID", owner.data["run_id"])
    monkeypatch.setenv("RAGFLOW_ARTIFACT_OWNER_TOKEN", owner.owner_token)
    argv = ["artifact_lifecycle", "finish", "--state", str(state)]
    if case != "no-report":
        argv += ["--evidence", str(path)]
    monkeypatch.setattr(sys, "argv", argv)
    called = []

    def cleanup(run):
        called.append(run.data["run_id"])
        if case == "during-teardown":
            life.event(state, run.data["run_id"], producer_activity="later-child")
        elif case == "changed-report":
            path.write_text(json.dumps({**proof, "unexpected_change": True}))
        value = result(run)
        if case == "blocked-cleanup":
            value["blocked"] = ["active consumer"]
        return value

    monkeypatch.setattr(life.Run, "retire_owned", cleanup)
    if case == "before-lease":
        original_lease = life.lease

        @contextmanager
        def changed_before_acquisition(store, run_id):
            life.event(store, run_id, producer_activity="child-before-lease")
            with original_lease(store, run_id):
                yield

        monkeypatch.setattr(life, "lease", changed_before_acquisition)
    successful = case in {"success", "no-report"}
    assert life.main() == (0 if successful else 1)
    saved = life.read_run(state, owner.data["run_id"])
    assert called == [owner.data["run_id"]]  # Invalid acceptance never suppresses own teardown.
    assert saved["state"] == ("completed" if successful else "waiting")
    assert life.consumption_closed(saved) is (case == "success")
    assert saved["task_status"] == "open" and not saved.get("recovery_released_at")
    if case == "success":
        path.unlink()
        assert life.consumption_closed(saved)


@pytest.mark.parametrize("case", ["success", "failed-qa", "live-child", "failed-cleanup", "late-capture-failure"])
def test_pytest_finish_closes_consumption_only_after_verified_success(state, monkeypatch, case):
    run = life.Run("pytest-disposable", days=0, store=state)
    run.__enter__()
    run._engine = Engine()
    run.data["engine"] = "engine"
    run.use_image("input-image")
    cleanup = result(run)
    if case == "failed-cleanup":
        cleanup["errors"] = ["archive failure"]
    monkeypatch.setattr(run, "retire_owned", lambda: cleanup)
    children = [SimpleNamespace(poll=lambda: None)] if case == "live-child" else []
    session = SimpleNamespace(_artifact_lifecycle=(run, subprocess.Popen, children), exitstatus=0, config=SimpleNamespace(pluginmanager=SimpleNamespace(getplugin=lambda _: None)))
    if case == "late-capture-failure":
        original = run.capture
        calls = []

        def capture():
            calls.append(True)
            if len(calls) == 2:
                raise RuntimeError("late accounting unavailable")
            return original()

        monkeypatch.setattr(run, "capture", capture)
        with pytest.raises(RuntimeError):
            plugin.pytest_sessionfinish(session, 0)
    else:
        plugin.pytest_sessionfinish(session, 1 if case == "failed-qa" else 0)
    saved = life.read_run(state, run.data["run_id"])
    assert life.consumption_closed(saved) is (case == "success")
    assert saved["task_status"] == "open"
    if case in {"live-child", "failed-cleanup"}:
        assert session.exitstatus == pytest.ExitCode.TESTS_FAILED and saved["state"] == "paused"


def test_child_creation_uses_one_sealed_intent_before_execution(state):
    with life.Run("pytest-disposable", store=state) as run:
        run._engine = Engine()
        run.data["engine"] = "engine"
        run.persist()
        script = """
from tools.quality import artifact_lifecycle as life,artifact_docker as docker
from test.unit_test.tools.quality.test_qa_completion import Engine
docker.Engine=Engine
arguments=life.docker_args(['docker','network','create','synthetic-network'])
current=life.current()
saved=life.merge_events(current.store,life.read_run(current.store,current.data['run_id']))
labels=life.command_labels(arguments)
assert any(item.get('object')==labels[life.OBJECT] and item['kind']=='network' for item in saved['creation_intents'])
assert life.docker_args(arguments)==arguments
current=life.current()
saved=life.merge_events(current.store,life.read_run(current.store,current.data['run_id']))
assert len(saved['creation_intents'])==1
try:
    life.docker_args(['docker','network','create','--label',life.RUN+'='+current.data['run_id'],'unsealed'])
except ValueError:
    pass
else:
    raise AssertionError('Partial lifecycle labels bypassed annotation')
"""
        child = subprocess.run([sys.executable, "-B", "-c", script], text=True, capture_output=True, timeout=45)
        assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("reserved", [life.PROJECT, life.RUN, life.OBJECT, life.CLASS])
@pytest.mark.parametrize("canonical", [False, True])
def test_partial_reserved_labels_cannot_override_managed_creation(state, reserved, canonical):
    with life.Run("pytest-disposable", store=state) as run:
        run._engine = Engine()
        run.data["engine"] = "engine"
        value = {life.PROJECT: "ragflow", life.RUN: run.data["run_id"], life.OBJECT: "unsealed", life.CLASS: "disposable"}[reserved] if canonical else "foreign"
        with pytest.raises(ValueError, match="sealed intent"):
            life.docker_args(["docker", "network", "create", "--label", reserved + "=" + value, "not-created"])
        assert not run.data.get("creation_intents")


@pytest.mark.parametrize("explicit_labels", [False, True])
def test_named_volume_requires_fresh_ownership_with_implicit_or_explicit_labels(state, explicit_labels):
    class ForeignVolumeEngine(Engine):
        def inspect(self, kind, name):
            assert (kind, name) == ("volume", "foreign-volume")
            return {"Name": name, "Labels": {life.RUN: "foreign-owner"}}

    with life.Run("pytest-disposable", store=state) as run:
        run._engine = ForeignVolumeEngine()
        run.data["engine"] = "engine"
        arguments = ["docker", "volume", "create"]
        if explicit_labels:
            arguments += [part for key, value in run.labels("volume").items() for part in ("--label", key + "=" + value)]
        with pytest.raises(ValueError, match="existing volume"):
            life.docker_args([*arguments, "foreign-volume"])
