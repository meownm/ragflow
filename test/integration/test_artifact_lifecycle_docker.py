"""Disposable end-to-end preservation, retirement and stopped recovery."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import uuid
from datetime import datetime, timezone

import pytest

from tools.quality import artifact_lifecycle as life
from tools.quality.artifact_docker import restore

pytestmark = pytest.mark.skipif(os.environ.get("RAGFLOW_ARTIFACT_DOCKER_TEST") != "1", reason="explicit disposable Docker opt-in required")


def close_fixture_task(run, tmp_path):
    """The disposable fixture closes its own task through the real decision boundary."""
    record = life.read_run(run.store, run.data["run_id"])
    decision = tmp_path / (run.data["run_id"] + "-complete.json")
    life.save(
        {
            "schema": 1,
            "project": "ragflow",
            "action": "complete",
            "subject": life.run_subject(record),
            "execution_status": "completed",
            "approved_by": "disposable-acceptance-owner",
            "task_status": "completed",
            "recovery_needed": True,
        },
        decision,
    )
    subprocess.run(
        [sys.executable, "-B", "-m", "tools.quality.artifact_lifecycle", "complete", "--state", str(run.store), "--run", run.data["run_id"], "--evidence", str(decision)],
        check=True,
        capture_output=True,
    )


def test_owned_generation_survives_archive_restore_and_safe_repeated_cleanup(tmp_path):
    tag = "local/ragflow-lifecycle-qa:" + uuid.uuid4().hex
    base = "ragflow-lifecycle-qa-" + uuid.uuid4().hex
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as bundle:
        entry = tarfile.TarInfo("original.txt")
        entry.size = 9
        entry.mode = 0o640
        bundle.addfile(entry, io.BytesIO(b"original\n"))
    with life.Run("lifecycle-acceptance", days=0) as run:
        engine = run.engine()
        imported = subprocess.run(life.docker_args(["docker", "image", "import", "-", tag]), input=data.getvalue(), capture_output=True, check=True)
        image = imported.stdout.decode().strip()
        subprocess.run(life.docker_args(["docker", "volume", "create", base]), capture_output=True, check=True)
        subprocess.run(life.docker_args(["docker", "network", "create", "--internal", base]), capture_output=True, check=True)
        subprocess.run(
            life.docker_args(
                ["docker", "create", "--name", base, "--network", base, "--mount", f"type=volume,src={base},dst=/data", "--env", "RECOVERY_TEST=preserved", "--entrypoint", "/never-start", image]
            ),
            capture_output=True,
            check=True,
        )
        helper = engine.helper()
        engine.run(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--mount",
            f"type=volume,src={base},dst=/data",
            "--entrypoint",
            "bash",
            helper,
            "-c",
            "printf 'recovery-data\n' > /data/proof; chmod 640 /data/proof; ln /data/proof /data/hard; ln -s proof /data/link",
        )
        run.capture()
        run_id = run.data["run_id"]
        assert life.eligible(life.read_run(run.store, run_id), datetime.now(timezone.utc)) == "run-not-completed"
    assert life.eligible(life.read_run(run.store, run_id), datetime.now(timezone.utc)) == "task-acceptance-open"
    close_fixture_task(run, tmp_path)
    result = life.retire(run.store, run_id, engine)
    assert not result["errors"] and not result["blocked"], result
    assert len(result["removed"]) == 4
    assert all(engine.inspect(kind, name) is None for kind, name in (("container", base), ("volume", base), ("network", base), ("image", image)))
    assert not life.retire(run.store, run_id, engine)["removed"]
    original = life.read_run(run.store, run_id)
    with life.Run("lifecycle-recovery-acceptance", days=0, store=run.store) as restored:
        restored.preserve = True
        restore(original, restored)
        container = engine.inspect("container", base)
        assert container is not None and container["State"]["Running"] is False
        assert "RECOVERY_TEST=preserved" in container["Config"]["Env"]
        content = engine.run(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--mount",
            f"type=volume,src={base},dst=/data,readonly",
            "--entrypoint",
            "bash",
            helper,
            "-c",
            "cat /data/proof; test $(stat -c %i /data/proof) = $(stat -c %i /data/hard); test $(readlink /data/link) = proof; test $(stat -c %a /data/proof) = 640",
        )
        assert content == "recovery-data"
        with engine.process("container", "export", container["Id"], stdout=subprocess.PIPE) as process:
            original_bytes = None
            with tarfile.open(fileobj=process.stdout, mode="r|") as bundle:
                for entry in bundle:
                    if entry.name.lstrip("./") == "original.txt":
                        original_bytes = bundle.extractfile(entry).read()
            process.stdout.read()
            assert process.wait(timeout=60) == 0 and original_bytes == b"original\n"
        with pytest.raises(ValueError, match="already exists"):
            restore(life.read_run(run.store, run_id), restored)
        cleanup = restored.retire_owned()
        assert not cleanup["errors"] and not cleanup["blocked"], cleanup
        restored.preserve = False  # The acceptance fixture has finished using its restored image.
    close_fixture_task(restored, tmp_path)
    cleanup = life.retire(run.store, run_id, engine)
    assert not cleanup["errors"] and not cleanup["blocked"], cleanup
    assert engine.inspect("container", base) is None and engine.inspect("volume", base) is None
    report = {
        "status": "PASS",
        "run_id": run_id,
        "restore_run_id": restored.data["run_id"],
        "checks": ["stopped-container-config", "volume-content-mode-links", "image-archive", "network", "collision-refusal", "idempotent-cleanup"],
    }
    destination = Path(os.environ["RAGFLOW_ARTIFACT_ACCEPTANCE_REPORT"])
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")


def test_changed_container_filesystem_is_recovered_from_deduplicated_snapshot(tmp_path):
    name = "ragflow-lifecycle-qa-" + uuid.uuid4().hex
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as bundle:
        item = tarfile.TarInfo("original.txt")
        item.size = 9
        bundle.addfile(item, io.BytesIO(b"original\n"))
    with life.Run("lifecycle-delta-acceptance", days=0) as run:
        engine = run.engine()
        image = subprocess.run(life.docker_args(["docker", "image", "import", "-"]), input=data.getvalue(), capture_output=True, check=True).stdout.decode().strip()
        subprocess.run(life.docker_args(["docker", "create", "--name", name, "--entrypoint", "/never-start", image]), capture_output=True, check=True)
        changed = tmp_path / "changed.txt"
        changed.write_text("unique container change\n")
        engine.run("cp", str(changed), name + ":/changed.txt")
    close_fixture_task(run, tmp_path)
    result = life.retire(run.store, run.data["run_id"], engine)
    assert not result["errors"] and not result["blocked"], result
    source = life.read_run(run.store, run.data["run_id"])
    container_entry = next(r for r in source["resources"] if r["kind"] == "container")
    assert container_entry["recovery"]["snapshot"]["archive"]["kind"] == "image-cas"
    with life.Run("lifecycle-delta-recovery", days=0) as recovered:
        restore(source, recovered)
        assert not engine.inspect("container", name)["State"]["Running"]
        destination = tmp_path / "restored.txt"
        engine.run("cp", name + ":/changed.txt", str(destination))
        assert destination.read_text() == changed.read_text()
        cleanup = recovered.retire_owned()
        assert not cleanup["errors"] and not cleanup["blocked"], cleanup
    close_fixture_task(recovered, tmp_path)
    cleanup = life.retire(run.store, run.data["run_id"], engine)
    assert not cleanup["errors"] and not cleanup["blocked"], cleanup


def test_cli_handoff_and_opted_in_pytest_share_durable_resource_accounting(tmp_path):
    command = [sys.executable, "-B", "-m", "tools.quality.artifact_lifecycle"]
    owner = json.loads(subprocess.check_output([*command, "begin", "--producer", "lifecycle-handoff-acceptance", "--keep-days", "0"], text=True))
    environment = {**os.environ, "RAGFLOW_ARTIFACT_RUN_ID": owner["run_id"], "RAGFLOW_ARTIFACT_STATE": owner["state"], "RAGFLOW_ARTIFACT_OWNER_TOKEN": owner["owner_token"]}
    network = "ragflow-lifecycle-qa-" + uuid.uuid4().hex
    subprocess.run([*command, "docker", "--", "docker", "network", "create", network], env=environment, capture_output=True, check=True)
    before = life.read_run(Path(owner["state"]), owner["run_id"])
    life.merge_events(Path(owner["state"]), before)
    assert any(item["kind"] == "network" for item in before["creation_intents"])
    proof = tmp_path / "handoff-qa.json"
    life.save(
        {
            "schema": 1,
            "project": "ragflow",
            "kind": "handoff-qa",
            "status": "PASS",
            "artifact_run_id": owner["run_id"],
            "artifact_subject": life.consumption_subject(before),
            "sha": before["source"]["head"],
        },
        proof,
    )
    result = json.loads(subprocess.check_output([*command, "finish", "--evidence", str(proof)], env=environment, text=True))
    assert result["removed"] == 1 and result["errors"] == 0 and result["blocked"] == 0
    record = life.read_run(Path(owner["state"]), owner["run_id"])
    assert record["state"] == "completed" and record["resources"][0]["status"] == "removed"
    assert record["task_status"] == "open" and life.consumption_closed(record)
    proof.unlink()
    assert life.consumption_closed(record)
    fixture = tmp_path / "test_managed_fixture.py"
    fixture.write_text("import subprocess\ndef test_network():\n    subprocess.run(['docker','network','create','" + network + "'],check=True,capture_output=True)\n")
    subprocess.run(
        [*command, "exec", "--", sys.executable, "-B", "-m", "pytest", "--noconftest", "-q", str(fixture), "-o", "cache_dir=" + str(tmp_path / "pytest-cache")], capture_output=True, check=True
    )
    with life.Run("lifecycle-handoff-observer") as observer:
        assert observer.engine().inspect("network", network) is None
