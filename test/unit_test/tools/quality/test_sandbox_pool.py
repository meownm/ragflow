"""Only explicitly delegated stopped children of inactive manager generations retire."""

from copy import deepcopy
import io
import tarfile

import pytest

from tools.quality import sandbox_pool as pool


def child():
    return {
        "Id": "c" * 64,
        "Name": "/child",
        "Image": "sha256:" + "a" * 64,
        "Config": {
            "Labels": {
                "org.ragflow.artifact.project": "ragflow",
                "org.ragflow.artifact.class": "disposable",
                "org.ragflow.artifact.object": "b" * 32,
                "org.ragflow.sandbox.manager": "d" * 64,
                "org.ragflow.sandbox.epoch": "2026-10-29T00:00:00Z",
                "org.ragflow.sandbox.pool": "e" * 32,
                "org.ragflow.sandbox.kind": "pool",
                "org.ragflow.sandbox.cleanup": "owner-lifetime",
            }
        },
        "HostConfig": {"ReadonlyRootfs": True, "Tmpfs": {"/tmp": "rw", "/workspace": "rw"}},
        "Mounts": [],
        "State": {"Running": False},
    }


def test_inventory_tracks_pool_owner_without_exposing_runtime_environment():
    import json
    from tools.quality import artifact_policy, artifact_registry

    c = child()
    c["Config"]["Env"] = ["PASSWORD=never-public"]
    record = artifact_registry.docker_records("engine", [c], [], [], {})[0]
    report = {"observed_at": "2026-10-29T12:00:00Z", "scope": {"docker_engine": "engine"}, "lifecycle": [], "records": [record]}
    artifact_policy.apply(report)
    policy = report["records"][0]["lifetime"]
    assert policy["owner"] == "sandbox-manager:" + "d" * 64 + "@2026-10-29T00:00:00Z"
    assert policy["retirement_executor"] == "sandbox-manager-and-stopped-orphan-cleaner"
    assert not policy["deletion_authorized"] and "never-public" not in json.dumps(report)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("active", "orphan-child-active"),
        ("mount", "persistent-or-unverified-sandbox-storage"),
        ("writable", "persistent-or-unverified-sandbox-storage"),
        ("no-label", "unverified-sandbox-producer"),
        ("exec", "orphan-child-active"),
    ],
)
def test_orphan_guards(change, reason):
    c = child()
    if change == "active":
        c["State"]["Running"] = True
    elif change == "mount":
        c["Mounts"] = [{"Type": "volume", "Name": "data"}]
    elif change == "writable":
        c["HostConfig"]["ReadonlyRootfs"] = False
    elif change == "exec":
        c["ExecIDs"] = ["job"]
    else:
        c["Config"]["Labels"] = {}
    assert pool.blocker(c, []) == reason


def test_manager_epoch_not_just_its_name_controls_retirement():
    c = child()
    manager = {"Id": "d" * 64, "Name": "/any-name", "State": {"Running": True, "StartedAt": "2026-10-29T00:00:00Z"}}
    assert pool.blocker(c, [manager]) == "manager-active"
    manager["State"]["StartedAt"] = "2026-10-30T00:00:00Z"
    assert pool.blocker(c, [manager]) is None


@pytest.mark.parametrize("change", ["unexpected-diff", "file-in-mountpoint"])
def test_preservation_rejects_unexpected_filesystem_data(tmp_path, change):
    class Engine:
        def run(self, *_):
            return "C /etc" if change == "unexpected-diff" else "A /workspace"

        def export(self, _, archive):
            with tarfile.open(archive, "w:gz") as bundle:
                directory = tarfile.TarInfo("workspace")
                directory.type = tarfile.DIRTYPE
                bundle.addfile(directory)
                file = tarfile.TarInfo("workspace/result.txt")
                file.size = 4
                bundle.addfile(file, io.BytesIO(b"data"))
            return "archive-hash"

    with pytest.raises(ValueError, match="Unexpected writable|contains persistent data"):
        pool.preserve_readonly_child(child(), Engine(), tmp_path / "state", tmp_path)


@pytest.mark.parametrize("late_change", [False, True])
def test_cleanup_preserves_before_removal_and_rechecks_live_owner(tmp_path, monkeypatch, late_change):
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    c = child()

    class Engine:
        identity = "test-engine"
        removed = []
        archived = False

        def all(self, _):
            rows = [c]
            if self.archived and late_change:
                rows.append({"Id": "d" * 64, "State": {"Running": True, "StartedAt": "2026-10-29T00:00:00Z"}})
            return rows

        def owned(self, _):
            return []

        def run(self, *_):
            return ""

        def inspect(self, *_):
            return deepcopy(c)

        def backup(self, *_):
            self.archived = True
            return {"verified": True}

        def verify_backup(self, _):
            assert self.archived

        def remove(self, target):
            assert self.archived
            self.removed.append(target["identity"])

    e = Engine()
    result = pool.cleanup(tmp_path / "state", apply=True, engine=e)
    from tools.quality.artifact_lifecycle import runs

    saved = list(runs(tmp_path / "state"))[0]
    assert saved["recovery_dependencies"][0]["packets"] == [{"verified": True}]
    assert bool(result["errors"]) == late_change
    assert bool(e.removed) != late_change
