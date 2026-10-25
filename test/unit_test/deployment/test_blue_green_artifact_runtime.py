"""Persistent main observes live services without changing their ownership/config."""

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from deployment import blue_green
from deployment.artifact_runtime import RuntimeAccounting, current_runtime
from test.unit_test.deployment.test_blue_green import base_config
from tools.quality import artifact_docker as docker
from tools.quality import artifact_lifecycle as life
from tools.quality import artifact_paths


@pytest.fixture
def state(tmp_path, monkeypatch):
    store = tmp_path / "lifecycle"
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(store))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)
    monkeypatch.delenv("RAGFLOW_TEST_DISPOSABLE", raising=False)
    return store


@pytest.fixture
def engine(monkeypatch):
    container = {
        "Id": "gateway-id",
        "Name": "/live-gateway",
        "Image": "sha256:" + "b" * 64,
        "State": {"Running": True},
        "Config": {"Env": ["SERVICE_PASSWORD=confidential-runtime-canary"], "Labels": {"org.ragflow.blue-green": "live"}},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
        "Mounts": [{"Type": "volume", "Name": "shared-data", "Destination": "/data"}],
        "NetworkSettings": {"Networks": {"runtime-network": {"NetworkID": "network-id"}}},
    }

    class Engine:
        identity = "pinned-engine"
        prefix = ["docker", "--context", "pinned-context"]
        env = {"DOCKER_CONTEXT": "pinned-context"}
        containers = [container]

        def __init__(self, **_):
            pass

        def assert_engine(self):
            assert self.identity == "pinned-engine"

        def owned(self, _):
            return []

        def all(self, kind):
            assert kind == "container"
            return copy.deepcopy(self.containers)

        def inspect(self, kind, identifier):
            if kind == "image":
                return {"Id": identifier}
            if kind == "volume":
                return {"Name": identifier, "Driver": "local", "CreatedAt": "2026-01-01T00:00:00Z", "Labels": {"com.docker.compose.project": "another-project"}}
            assert kind == "network" and identifier == "network-id"
            return {"Id": identifier, "Name": "shared-network", "Labels": {}}

    monkeypatch.setattr(docker, "Engine", Engine)
    return Engine


@pytest.mark.parametrize("outcome", ["success", "before-journal-failure", "after-journal-failure", "interrupted", "recover"])
def test_production_main_accounts_metadata_and_observes_services_without_overlay(state, engine, tmp_path, monkeypatch, outcome):
    # Exercise the persistent-path branch while keeping all test bytes inside
    # our reserved disposable filesystem. The production root is not temp.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(artifact_paths, "temporary_evidence", lambda _: False)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    original = base_config()
    original["services"]["ragflow-cpu"]["environment"]["SYNTHETIC_PASSWORD"] = "confidential-runtime-canary"
    config = tmp_path / "candidate.json"
    config.write_text(json.dumps(original))
    output = tmp_path / "deployment.json"
    previous = {"slot": "web-blue", "config": {"services": {"worker": {"image": "sha256:" + "c" * 64}}}}
    (runtime / "state.json").write_text(json.dumps(previous))
    (runtime / "nginx.conf").write_text("previous-route")
    commands = []

    def execute(arguments, **kwargs):
        commands.append(list(arguments))
        assert list(arguments[:3]) == ["docker", "--context", "pinned-context"]
        assert "--label" not in arguments and "--force-recreate" not in arguments
        assert kwargs["env"] == engine.env
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    monkeypatch.setattr(blue_green, "subprocess", SimpleNamespace(run=execute))

    class Controller:
        def __init__(self, project, root, timeout):
            assert project == "live" and root == runtime

        def deploy(self, prepared, **_):
            assert current_runtime() is not None
            saved = life.read_run(state, life.current().data["run_id"])
            assert any(item["path"] == str(runtime / "pending.json") for item in saved["creation_intents"])
            assert not saved["resources"]
            assert prepared["services"]["gateway"]["labels"] == {"org.ragflow.blue-green": "live", "org.ragflow.role": "gateway"}
            if outcome == "before-journal-failure":
                raise RuntimeError("synthetic before journal")
            blue_green.write_json(runtime / "pending.json", {"stage": "warming"})
            blue_green.write_json(runtime / "compose.json", prepared)
            blue_green.command("docker", "compose", "-p", "live", "-f", str(runtime / "compose.json"), "up", "--no-deps", "gateway")
            if outcome == "after-journal-failure":
                raise RuntimeError("synthetic after journal")
            if outcome == "interrupted":
                raise KeyboardInterrupt()
            blue_green.write_json(runtime / "state.json", {"slot": "web-green", "config": prepared})
            (runtime / "nginx.conf").write_text("new-route")
            return {"slot": "web-green"}

        def recover(self):
            assert current_runtime() is not None
            blue_green.write_json(runtime / "state.json", {**previous, "recovered": True})

    monkeypatch.setattr(blue_green, "BlueGreen", Controller)
    argv = ["blue_green.py", "--project", "live", "--runtime", str(runtime)]
    argv += ["--recover"] if outcome == "recover" else ["--config", str(config), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    if outcome in {"before-journal-failure", "after-journal-failure", "interrupted"}:
        with pytest.raises(KeyboardInterrupt if outcome == "interrupted" else RuntimeError):
            blue_green.main()
    else:
        assert blue_green.main() == 0
    (saved,) = list(life.runs(state))
    assert saved["state"] == ("completed" if outcome in {"success", "recover"} else "failed")
    assert saved["category"] == "runtime" and saved["task_status"] == "open"
    assert "confidential-runtime-canary" not in json.dumps(saved)
    assert all(item["kind"] == "path" for item in saved["resources"])
    observed = saved["runtime_observations"]
    assert {item["kind"] for item in observed} == {"container", "volume", "network", "path"}
    assert all(item["ownership_source"] == "observation" for item in observed)
    assert {item["image_id"] for item in saved["image_dependencies"]} >= {"sha256:" + "b" * 64, "sha256:" + "c" * 64}
    assert json.loads(config.read_bytes()) == original
    assert engine.containers[0]["State"]["Running"] is True
    original_state = next(item for item in saved["input_files"] if item["path"] == str(runtime / "state.json"))
    packet = original_state["preserved_evidence"]
    docker.verify_archive(packet["archive"])
    assert json.loads(Path(packet["archive"]["path"]).read_bytes()) == previous
    if outcome == "recover":
        assert any(item.get("path") == str(runtime / "state.json") for item in saved["resources"])
    if outcome == "success":
        assert output.exists() and len(commands) == 1
        old_route = next(item for item in saved["input_files"] if item["path"] == str(runtime / "nginx.conf"))
        new_route = next(item for item in saved["resources"] if item.get("path") == str(runtime / "nginx.conf"))
        assert old_route["identity"] == new_route["identity"]
        assert Path(old_route["preserved_evidence"]["archive"]["path"]).read_bytes() == b"previous-route"
        assert Path(new_route["preserved_evidence"]["archive"]["path"]).read_bytes() == b"new-route"
    assert current_runtime() is None


def test_one_off_probe_has_disposable_intent_while_persistent_services_are_observations(state, engine, tmp_path, monkeypatch):
    image = "sha256:" + "a" * 64

    def execute(arguments, **kwargs):
        owner = life.current()
        assert owner.data["producer"] == "blue-green-preflight"
        assert any(item["kind"] == "container" for item in life.read_run(state, owner.data["run_id"])["creation_intents"])
        assert "--label" in arguments and kwargs["env"] == engine.env
        assert list(arguments[:3]) == ["docker", "--context", "pinned-context"]
        return subprocess.CompletedProcess(arguments, 0, b"contract-ok", b"")

    monkeypatch.setattr(blue_green, "subprocess", SimpleNamespace(run=execute))
    monkeypatch.setattr(engine, "env", {})  # Default context changes cannot retarget a probe.
    with RuntimeAccounting("live", tmp_path / "runtime", lock=blue_green.deployment_lock(tmp_path / "runtime")):
        assert blue_green.command("docker", "run", "--rm", "--network", "none", image, "contract") == "contract-ok"
    saved = list(life.runs(state))
    assert {run["producer"] for run in saved} == {"blue-green-runtime", "blue-green-preflight"}
    persistent = next(run for run in saved if run["producer"] == "blue-green-runtime")
    assert persistent["resources"] == []


def test_persistent_file_snapshot_requires_declaration_and_never_walks_runtime_data(state, tmp_path, monkeypatch):
    monkeypatch.setattr(artifact_paths, "temporary_evidence", lambda _: False)
    path = tmp_path / ".local/runtime/project/state.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"old":"configuration"}')
    assert artifact_paths.preserve_evidence(path, state) is None
    with life.Run("blue-green-runtime", category="runtime", store=state) as run:
        with pytest.raises(ValueError, match="bound"):
            artifact_paths.preserve_evidence(path, state, metadata_owner=run)
        with pytest.raises(ValueError, match="regular file"):
            artifact_paths.preserve_evidence(path.parent, state, metadata_owner=run)


def test_disposable_parent_keeps_existing_docker_ownership_path(state, engine, tmp_path, monkeypatch):
    monkeypatch.setenv("RAGFLOW_TEST_DISPOSABLE", "1")
    monkeypatch.setattr(sys, "argv", ["blue_green.py", "--project", "live", "--runtime", str(tmp_path / "runtime"), "--recover"])
    seen = []

    class Controller:
        def __init__(self, *_):
            pass

        def recover(self):
            seen.append(life.current().data["run_id"])
            assert current_runtime() is None

    monkeypatch.setattr(blue_green, "BlueGreen", Controller)
    with life.Run("existing-disposable-qa", store=state) as parent:
        assert blue_green.main() == 0
    assert seen == [parent.data["run_id"]] and len(list(life.runs(state))) == 1


def test_parallel_deployment_after_unlock_is_not_adopted_as_first_output(state, engine, tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = tmp_path / "candidate.json"
    source.write_text(json.dumps(base_config()))
    output = tmp_path / "result.json"
    original_lock = blue_green.deployment_lock

    @contextmanager
    def second_deployer_after_release(root):
        with original_lock(root):
            yield
        (root / "state.json").write_text('{"slot":"parallel"}')

    monkeypatch.setattr(blue_green, "deployment_lock", second_deployer_after_release)

    class Controller:
        def __init__(self, *_):
            pass

        def deploy(self, *_args, **_kwargs):
            blue_green.write_json(runtime / "state.json", {"slot": "first"})
            return {"slot": "first"}

    monkeypatch.setattr(blue_green, "BlueGreen", Controller)
    monkeypatch.setattr(sys, "argv", ["blue_green.py", "--project", "live", "--runtime", str(runtime), "--config", str(source), "--output", str(output)])
    assert blue_green.main() == 0
    (saved,) = list(life.runs(state))
    owned_state = next(item for item in saved["resources"] if item.get("path") == str(runtime / "state.json"))
    archive = owned_state["preserved_evidence"]["archive"]
    docker.verify_archive(archive)
    assert json.loads(Path(archive["path"]).read_bytes()) == {"slot": "first"}
    assert json.loads((runtime / "state.json").read_bytes()) == {"slot": "parallel"}
