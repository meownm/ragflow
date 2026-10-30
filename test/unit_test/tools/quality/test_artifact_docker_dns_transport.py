"""Docker restart preserves identity when only empty DNS serialization changes."""

from copy import deepcopy
import json

import pytest

from tools.quality import artifact_docker as docker


def container(mask=0):
    return {
        "Id": "same-id",
        "Name": "/own-qa",
        "Config": {"Image": "immutable-image", "Env": ["SYNTHETIC=yes"], "Labels": {docker.PROJECT: "ragflow", docker.RUN: "own-run", docker.OBJECT: "same-token", docker.CLASS: "disposable"}},
        "HostConfig": {"ReadonlyRootfs": True, **{key: [] if mask & (1 << index) else None for index, key in enumerate(("Dns", "DnsOptions", "DnsSearch"))}},
        "Mounts": [{"Type": "volume", "Name": "own-data", "Destination": "/data", "RW": True}],
        "State": {"Running": False},
    }


def guarded(before, after):
    engine = docker.Engine.__new__(docker.Engine)
    engine.assert_engine = lambda: None
    engine.inspect = lambda *_: after
    engine.all = lambda *_: []
    return engine.guard(docker.resource(before, "container"), "own-run", {}, set())


@pytest.mark.parametrize("before_mask", range(8))
@pytest.mark.parametrize("after_mask", range(8))
def test_empty_dns_round_trip_keeps_the_complete_original_seal(before_mask, after_mask):
    before, after = container(before_mask), container(after_mask)
    original = deepcopy(after)
    assert guarded(before, after) is None
    assert docker._configuration_matches(after, docker.resource(before, "container")["config_sha256"])
    assert after == original


@pytest.mark.parametrize("key", ["Dns", "DnsOptions", "DnsSearch"])
@pytest.mark.parametrize("direction", [False, True])
def test_real_dns_configuration_is_never_normalized(key, direction):
    before, after = container(0), container(7)
    (before if direction else after)["HostConfig"][key] = ["configured-value"]
    assert guarded(before, after) == "identity-or-configuration-changed"


@pytest.mark.parametrize("change", ["image", "environment", "readonly", "mount", "missing-key"])
def test_all_other_configuration_changes_stay_protected(change):
    before, after = container(0), container(7)
    if change == "image":
        after["Config"]["Image"] = "another-image"
    elif change == "environment":
        after["Config"]["Env"] = ["SYNTHETIC=changed"]
    elif change == "readonly":
        after["HostConfig"]["ReadonlyRootfs"] = False
    elif change == "mount":
        after["Mounts"][0]["Name"] = "foreign-data"
    else:
        del after["HostConfig"]["Dns"]
    assert guarded(before, after) == "identity-or-configuration-changed"


@pytest.mark.parametrize("change", ["generation", "identity", "running"])
def test_generation_identity_and_active_consumers_keep_their_guards(change):
    before, after = container(0), container(7)
    if change == "generation":
        after["Config"]["Labels"][docker.OBJECT] = "new-token"
        expected = "ownership-or-generation-changed"
    elif change == "identity":
        after["Id"] = "new-id"
        expected = "identity-or-configuration-changed"
    else:
        after["State"]["Running"] = True
        expected = "container-active"
    assert guarded(before, after) == expected


@pytest.mark.parametrize("state", [None, "Running", "Paused", "Restarting"])
def test_own_container_guard_is_independent_of_unavailable_unrelated_inventory(state):
    before, after = container(0), container(7)
    if state:
        after["State"][state] = True
    engine = docker.Engine.__new__(docker.Engine)
    engine.assert_engine = lambda: None
    engine.inspect = lambda *_: after

    def unavailable(*_):
        raise RuntimeError("Unrelated container inventory is unavailable")

    engine.all = unavailable
    assert engine.guard(docker.resource(before, "container"), "own-run", {}, set()) == ("container-active" if state else None)


@pytest.mark.parametrize("custom_dns", [False, True])
def test_preserved_backup_accepts_only_empty_dns_transport_changes(tmp_path, monkeypatch, custom_dns):
    before, after = container(0), container(7)
    if custom_dns:
        after["HostConfig"]["Dns"] = ["different-resolver"]
    target = docker.resource(before, "container")
    root = tmp_path / "recovery"
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(root))
    folder = root / "resources" / target["key"].replace(":", "-")
    folder.mkdir(parents=True)
    inspect = folder / "inspect.json"
    inspect.write_text(json.dumps(before), encoding="utf-8")
    packet = {"kind": "container", "inspect": docker.archive_info(inspect), "archives": []}
    (folder / "recovery.json").write_text(json.dumps(packet), encoding="utf-8")
    engine = docker.Engine.__new__(docker.Engine)
    engine.inspect = lambda *_: after
    engine.verify_backup = lambda *_: None
    if custom_dns:
        with pytest.raises(ValueError, match="configuration changed"):
            engine.backup(target, tmp_path / "state")
    else:
        assert engine.backup(target, tmp_path / "state")["inspect"] == packet["inspect"]
    assert json.loads(inspect.read_text(encoding="utf-8")) == before


@pytest.fixture
def own_stop_engine():
    info = container()
    info["State"]["Running"] = True
    info["ExecIDs"] = ["same-exec"]
    stopped = []
    engine = docker.Engine.__new__(docker.Engine)
    engine.assert_engine = lambda: None
    engine.all = lambda kind, run_id: [info] if (kind, run_id) == ("container", "own-run") else pytest.fail("Unexpected ownership scope")
    engine.run = lambda *args: stopped.append(args)
    return engine, info, stopped


@pytest.mark.parametrize("running", [False, True])
def test_completed_exec_metadata_does_not_block_own_container_teardown(own_stop_engine, running):
    engine, info, stopped = own_stop_engine
    info["State"]["Running"] = running

    def inspect_exec(method, path, body):
        assert (method, path, body) == ("GET", "/exec/same-exec/json", {})
        return {"ContainerID": info["Id"], "Running": False, "ExitCode": 0}

    engine.api = inspect_exec
    engine.stop_owned("own-run")
    assert stopped == ([("container", "stop", "--time", "30", info["Id"])] if running else [])


@pytest.mark.parametrize("execution", [{"ContainerID": "same-id", "Running": True}, {"ContainerID": "other-id", "Running": False}, {"ContainerID": "same-id"}])
def test_active_unknown_or_foreign_exec_keeps_own_container_protected(own_stop_engine, execution):
    engine, _info, stopped = own_stop_engine
    engine.api = lambda *_: execution
    with pytest.raises(ValueError, match="active or unverifiable exec"):
        engine.stop_owned("own-run")
    assert stopped == []


def test_exec_inspection_failure_never_stops_the_container(own_stop_engine):
    engine, _info, stopped = own_stop_engine

    def unavailable(*_):
        raise RuntimeError("Exec inspection unavailable")

    engine.api = unavailable
    with pytest.raises(RuntimeError, match="inspection unavailable"):
        engine.stop_owned("own-run")
    assert stopped == []


@pytest.mark.parametrize("change", ["persistent", "missing-generation"])
def test_completed_exec_cannot_bypass_persistent_or_generation_guards(own_stop_engine, change):
    engine, info, stopped = own_stop_engine
    if change == "persistent":
        info["Config"]["Labels"][docker.CLASS] = "runtime"
    else:
        del info["Config"]["Labels"][docker.OBJECT]
    engine.api = lambda *_: pytest.fail("Protected container must not reach exec inspection")
    with pytest.raises(ValueError, match="persistent container"):
        engine.stop_owned("own-run")
    assert stopped == []
