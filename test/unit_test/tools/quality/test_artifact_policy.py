"""All classes have review/retirement rules; observation never grants deletion."""

from datetime import datetime, timedelta, timezone
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.quality import artifact_policy as policy
from tools.quality import artifact_registry as registry
from tools.quality import artifact_lifecycle as life


NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


@pytest.mark.parametrize("category", sorted(policy.CATEGORIES))
def test_every_artifact_class_has_a_review_and_retirement_contract(category):
    record = {"kind": "path", "category": category, "created_at": NOW.isoformat()}
    result = policy.assess(record, now=NOW)
    assert result["retention_until"] and result["retirement_executor"]
    assert result["responsible_role"] and result["review_after"]
    assert result["state"] == "review-required" and not result["deletion_authorized"]


def test_refresh_does_not_extend_the_review_deadline():
    record = {"kind": "volume", "created_at": NOW.isoformat()}
    first = policy.assess(record, now=NOW, declaration={"task_ref": "task", "purpose": "restore"})
    later = policy.assess(record, now=NOW + timedelta(days=31), previous=first, declaration={"task_ref": "task", "purpose": "restore"})
    assert later["review_after"] == first["review_after"]
    assert later["review_due"] and later["state"] == "review-required"
    assert not later["deletion_authorized"]


def test_active_unknown_resource_still_enters_the_review_queue():
    report = {"observed_at": NOW.isoformat(), "records": [{"id": "container:one", "identity": "one", "kind": "container", "running": True}], "scope": {"docker_engine": "engine"}}
    policy.apply(report)
    assert report["records"][0]["lifetime"]["state"] == "active"
    assert report["review_queue"][0]["action"] == "identify owner and purpose"


def test_old_path_declaration_cannot_claim_a_new_generation():
    resource = {"kind": "path", "category": "backup", "path": "backup.dump", "identity": [1, 2, 3]}
    report = {
        "observed_at": NOW.isoformat(),
        "records": [{**resource, "identity": [1, 4, 3], "id": "new"}],
        "lifecycle": [{"run_id": "old", "task_ref": "old task", "producer": "backup", "created_at": NOW.isoformat(), "resources": [resource]}],
    }
    policy.apply(report)
    assert report["records"][0]["lifetime"]["owner"] is None


@pytest.mark.parametrize("produced", [False, True])
def test_runtime_observation_tracks_custody_without_adoption_or_new_review_deadline(produced):
    from tools.quality.artifact_review import subjects

    item = {
        "id": "volume:shared",
        "kind": "volume",
        "identity": {"Name": "shared", "Driver": "local", "CreatedAt": "2026-10-01T00:00:00+00:00"},
        "created_at": NOW.isoformat(),
        "category": "data",
        "name": "shared",
    }
    report = {"observed_at": NOW.isoformat(), "scope": {"docker_engine": "engine"}, "records": [deepcopy(item)], "lifecycle": []}
    for project in ("live", "another-service"):
        report["lifecycle"].append(
            {
                "run_id": project,
                "producer": "blue-green-runtime",
                "task_ref": "deployment-task",
                "created_at": NOW.isoformat(),
                "engine": "engine",
                "runtime_project": project,
                "resources": [],
                "runtime_observations": [{**item, "custodian": project, "ownership_source": "observation"}],
            }
        )
    if produced:
        report["lifecycle"].append(
            {"run_id": "original", "task_ref": "original-volume-task", "producer": "volume-builder", "created_at": NOW.isoformat(), "engine": "engine", "resources": [deepcopy(item)]}
        )
    policy.apply(report)
    result = report["records"][0]["lifetime"]
    assert result["origin_owner"] == ("original-volume-task" if produced else None)
    assert result["ownership_source"] == ("producer" if produced else "observation")
    assert result["custodians"] == ["another-service", "live"]
    assert result["retained_by"] == ["runtime-run:another-service", "runtime-run:live"]
    assert not result["deletion_authorized"]
    assert len(subjects(report)[item["id"]]["producers"]) == int(produced)
    later = deepcopy(report)
    later["observed_at"] = (NOW + timedelta(days=31)).isoformat()
    policy.apply(later, report)
    assert later["records"][0]["lifetime"]["review_after"] == result["review_after"]
    assert later["lifecycle"][0]["runtime_observations"][0]["lifetime"]["review_after"] == report["lifecycle"][0]["runtime_observations"][0]["lifetime"]["review_after"]


def test_runtime_observation_does_not_claim_replaced_path_generation():
    original = {"kind": "path", "path": "runtime.json", "identity": [1, 2, 3], "category": "config"}
    report = {
        "observed_at": NOW.isoformat(),
        "records": [{**original, "id": "replacement", "identity": [1, 9, 3]}],
        "lifecycle": [
            {"run_id": "runtime", "producer": "blue-green-runtime", "task_ref": "deploy", "created_at": NOW.isoformat(), "runtime_project": "live", "resources": [], "input_files": [original]}
        ],
    }
    policy.apply(report)
    assert report["records"][0]["lifetime"]["owner"] is None


def test_backup_references_preserve_missing_and_out_of_scope_sources(tmp_path):
    receipt = tmp_path / "output/local-deploy/job/deployment.json"
    backup = tmp_path / ".local/backups/data.dump"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"synthetic backup")
    values = [
        (receipt, {"backup": {"path": str(backup), "sha256": "declared"}}),
        (receipt, {"backup": {"path": str(tmp_path / ".local/backups/missing.dump")}}),
        (receipt, {"backup": {"path": str(tmp_path.parent / "other-project/data.dump")}}),
    ]
    errors = []
    records = registry.backup_records(values, [tmp_path], errors)
    assert not errors and len(records) == 3
    assert records[0]["reported_sha256"] == "declared"
    assert records[0]["verification"] == "reported-checksum-only"
    assert records[1]["blockers"] == ["backup-source-missing"]
    assert records[2]["blockers"] == ["backup-source-outside-declared-scope"]


def test_backup_registration_records_scope_receipt_and_partial_failure(tmp_path):
    artifact = tmp_path / "partial.dump"
    artifact.write_bytes(b"incomplete")
    state = tmp_path / "state"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tools.quality.artifact_lifecycle",
            "register",
            "--state",
            str(state),
            "--category",
            "backup",
            "--task-ref",
            "deployment/task",
            "--purpose",
            "postgres recovery",
            "--data-scope",
            "test/postgres",
            "--validation",
            "incomplete",
            "--related-receipt",
            str(tmp_path / "deployment.json"),
            "--evidence",
            str(artifact),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    run = life.read_run(state, json.loads(result.stdout)["run_id"])
    assert run["state"] == "failed" and run["resources"][0]["validation"] == "incomplete"
    assert run["resources"][0]["data_scope"] == "test/postgres"
    assert run["resources"][0]["retained_by"] == [str(tmp_path / "deployment.json")]
    assert artifact.read_bytes() == b"incomplete"


def test_backup_tracking_precedes_any_deployment_mutation():
    script = (Path(__file__).resolve().parents[4] / "deployment/local/deploy.ps1").read_text(encoding="utf-8")
    assert script.index("Register-BackupArtifact -BackupPath $backup.path") < script.index('$deploymentAction = "none"')
    assert "'format-checked' } else { 'incomplete'" in script
    assert script.index("if ($CheckOnly)") < script.index("function Register-BackupArtifact")


@pytest.mark.parametrize("category", ["backup", "data", "model", "runtime", "config", "dataset"])
def test_persistent_classes_cannot_inherit_disposable_docker_labels(tmp_path, monkeypatch, category):
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)
    with life.Run("owner", category=category, store=tmp_path / "state") as run:
        run.data["engine"] = "test"

        class Engine:
            def owned(self, *_):
                return []

        run._engine = Engine()
        assert run.labels("container")[life.CLASS] == category


def test_incomplete_backup_is_diagnosis_even_before_review_date():
    value = policy.assess({"kind": "path", "category": "backup", "created_at": NOW.isoformat()}, now=NOW, declaration={"task_ref": "deploy", "purpose": "restore", "validation": "incomplete"})
    assert value["state"] == "diagnosis" and value["review_required"]
    assert not value["deletion_authorized"]
