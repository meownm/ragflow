"""A successful QA consumer releases inputs without approving product retirement."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.quality import artifact_lifecycle as life


@pytest.fixture
def state(tmp_path, monkeypatch):
    store = tmp_path / "state"
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(store))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)
    return store


class Engine:
    identity = "engine"

    def owned(self, _):
        return []

    def inspect(self, kind, reference):
        assert kind == "image"
        return {"Id": reference}


def teardown(run):
    return {"run_id": run.data["run_id"], "errors": [], "blocked": []}


def producer(state):
    run = life.Run("builder", store=state, category="cache", days=0)
    run.data.update(state="completed", task_status="completed", completed_at=life.utc(), engine="engine")
    run.persist()
    return run


def test_explicit_successful_qa_consumption_releases_only_image_reference(state, tmp_path):
    owner = producer(state)
    image = {"kind": "image", "identity": "input-image"}
    receipt = tmp_path / "qa-report.json"
    receipt.write_text('{"status":"PASS"}')
    with life.Run("pytest-disposable", store=state) as consumer:
        consumer._engine = Engine()
        consumer.data["engine"] = "engine"
        consumer.use_image("input-image")
        assert life.image_has_user(state, owner.data, image)
        consumer.close_consumption("pytest-disposable", exit_code=0, cleanup=teardown(consumer), receipt=receipt)
        assert not life.consumption_closed(consumer.data)  # The owner is still running.
    saved = life.read_run(state, consumer.data["run_id"])
    assert life.consumption_closed(saved)
    assert not life.image_has_user(state, owner.data, image)
    assert saved["task_status"] == "open" and not saved.get("recovery_released_at")
    assert life.eligible(saved, datetime.now(timezone.utc)) == "task-acceptance-open"
    assert saved["image_dependencies"] == [{"image_id": "input-image", "reference": "input-image"}]
    receipt.unlink()  # Ordinary checkout/output cleanup must not reopen past QA use.
    assert life.consumption_closed(saved)


@pytest.mark.parametrize("changed", ["failed", "paused", "source", "engine", "epoch", "dependency", "generation", "receipt-changed", "receipt-missing", "foreign-proof"])
def test_changed_or_unfinished_consumer_keeps_image_protected(state, tmp_path, changed):
    owner = producer(state)
    receipt = tmp_path / "qa.json"
    receipt.write_text('{"status":"PASS"}')
    with life.Run("pytest-disposable", store=state) as consumer:
        consumer._engine = Engine()
        consumer.data["engine"] = "engine"
        consumer.use_image("input-image")
        consumer.close_consumption("pytest-disposable", exit_code=0, cleanup=teardown(consumer), receipt=receipt)
    saved = life.read_run(state, consumer.data["run_id"])
    if changed in {"failed", "paused"}:
        saved["state"] = changed
    elif changed == "source":
        saved["source"] = {"head": "f" * 40}
    elif changed == "engine":
        saved["consumption_closed"]["subject"]["engine"] = "foreign-engine"
    elif changed == "epoch":
        saved["usage_epoch"] = "new-activity"
    elif changed == "dependency":
        saved["image_dependencies"].append({"image_id": "another-image", "reference": "another-image"})
    elif changed == "generation":
        saved["resources"].append({"key": "container:later", "kind": "container", "identity": "later", "status": "present"})
    elif changed == "receipt-changed":
        Path(saved["consumption_closed"]["receipt"]["path"]).write_bytes(b"corrupt durable witness")
    elif changed == "receipt-missing":
        Path(saved["consumption_closed"]["receipt"]["path"]).unlink()
    else:
        saved["consumption_closed"]["subject"]["run_id"] = "other-run"
    life.write_run(state, saved)
    assert not life.consumption_closed(saved)
    assert life.image_has_user(state, owner.data, {"kind": "image", "identity": "input-image"})


@pytest.mark.parametrize("invalid", ["failed-qa", "failed-cleanup", "foreign-cleanup", "missing-cleanup", "live-generation", "wrong-producer"])
def test_owner_cannot_close_unverified_qa_or_other_product_scope(state, invalid):
    with life.Run("builder" if invalid == "wrong-producer" else "pytest-disposable", store=state) as run:
        cleanup = teardown(run)
        if invalid == "failed-cleanup":
            cleanup["errors"] = ["incomplete"]
        elif invalid == "foreign-cleanup":
            cleanup["run_id"] = "another-run"
        elif invalid == "missing-cleanup":
            cleanup = {}
        elif invalid == "live-generation":
            run.add({"key": "container:live", "kind": "container", "identity": "live", "status": "present"})
        with pytest.raises(ValueError):
            run.close_consumption("pytest-disposable", exit_code=1 if invalid == "failed-qa" else 0, cleanup=cleanup)
        assert "consumption_closed" not in run.data


def test_new_use_after_qa_seal_invalidates_it_before_exit(state):
    with life.Run("pytest-disposable", store=state) as run:
        run._engine = Engine()
        run.data["engine"] = "engine"
        run.use_image("input-image")
        run.close_consumption("pytest-disposable", exit_code=0, cleanup=teardown(run))
        run.use_image("another-image")
        assert "consumption_closed" not in life.read_run(state, run.data["run_id"])
        assert run.data["consumption_history"][0]["subject"]["images"] == [{"image_id": "input-image", "reference": "input-image"}]
    assert not life.consumption_closed(life.read_run(state, run.data["run_id"]))


@pytest.mark.parametrize("arguments", [["docker", "compose", "-p", "down", "-f", "up", "logs"], ["docker", "compose", "--project-name=up", "ps"]])
def test_compose_action_does_not_mistake_option_values_for_mutation(arguments):
    assert arguments[life.compose_action(arguments)] in {"logs", "ps"}


def test_child_activity_reopens_parent_consumption_without_replacing_owner_state(state):
    with life.Run("pytest-disposable", store=state) as run:
        run.close_consumption("pytest-disposable", exit_code=0, cleanup=teardown(run))
        life.event(state, run.data["run_id"], producer_activity="later-child-exec")
    saved = life.read_run(state, run.data["run_id"])
    assert saved["usage_epoch"] == "later-child-exec" and "consumption_closed" not in saved


def test_source_identity_tracks_explicit_checkout_target_and_retains_ci_workflow_identity(state, monkeypatch):
    monkeypatch.setenv("SOURCE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with life.Run("qa", store=state) as run:
        assert run.data["source"]["head"] == "a" * 40
        assert run.data["ci"]["GITHUB_SHA"] == "b" * 40
