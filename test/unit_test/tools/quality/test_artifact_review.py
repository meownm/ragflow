import copy
import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from tools.quality import artifact_lifecycle as life
from tools.quality import artifact_policy as policy
from tools.quality import artifact_review as review


def fixture(tmp_path):
    evidence = tmp_path / "closure.json"
    evidence.write_text('{"task":"completed"}')
    record = {"id": "path:generation", "identity": [1, 2, 3], "kind": "path", "category": "build", "path": "old-build"}
    successor = {**record, "id": "path:accepted", "identity": [4, 5, 6], "path": "new-build"}
    inventory = {"complete": True, "records": [record, successor], "observed_at": datetime.now(timezone.utc).isoformat()}
    closure = tmp_path / "acceptance.json"
    bound = review.subjects(inventory)
    closure.write_text(
        json.dumps({"schema": 1, "project": "ragflow", "artifact": bound[record["id"]], "task_ref": "original/task", "outcome": "superseded", "accepted": True, "successor": bound[successor["id"]]})
    )
    decision = {
        "id": record["id"],
        "owner": "deployment/task",
        "purpose": "historical build",
        "outcome": "superseded",
        "successor": successor["id"],
        "origin_owner": "original/task",
        "closure": str(closure),
        "reason": "replacement accepted",
        "evidence": [str(evidence)],
    }
    return inventory, decision, evidence


def test_review_survives_refresh_without_adopting_resources_or_authorizing_delete(tmp_path):
    inventory, decision, _ = fixture(tmp_path)
    review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = life.runs(tmp_path)
    policy.apply(inventory)
    result = inventory["records"][0]["lifetime"]
    assert result["owner"] == "deployment/task" and not result["review_required"]
    assert result["review"]["successor"] == "path:accepted"
    assert result["origin_owner"] == "original/task" and result["custodian"] == "deployment/task"
    assert result["review"]["closure_valid"]
    assert not result["deletion_authorized"]
    assert inventory["lifecycle"][0]["resources"] == []
    later = copy.deepcopy(inventory)
    policy.apply(later, inventory)
    assert later["records"][0]["lifetime"]["review_after"] == result["review_after"]


@pytest.mark.parametrize("change", ["evidence", "missing", "generation", "unresolved"])
def test_invalid_or_unresolved_review_remains_in_queue(tmp_path, change):
    inventory, decision, evidence = fixture(tmp_path)
    if change == "unresolved":
        decision["outcome"] = "unresolved"
    review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = life.runs(tmp_path)
    if change == "evidence":
        evidence.write_text("changed")
    if change == "missing":
        evidence.unlink()
    if change == "generation":
        inventory["records"][0]["identity"] = [1, 2, 4]
    policy.apply(inventory)
    assert inventory["review_queue"] and not inventory["records"][0]["lifetime"]["deletion_authorized"]


@pytest.mark.parametrize("change", ["incomplete", "duplicate", "evidence", "successor", "outcome"])
def test_review_rejects_incomplete_proof(tmp_path, change):
    inventory, decision, evidence = fixture(tmp_path)
    decisions = [decision]
    if change == "incomplete":
        inventory["complete"] = False
    elif change == "duplicate":
        decisions.append(decision)
    elif change == "evidence":
        evidence.unlink()
    elif change == "successor":
        decision.pop("successor")
    else:
        decision["outcome"] = "delete"
    with pytest.raises((ValueError, OSError)):
        review.record(tmp_path, inventory, decisions)


@pytest.mark.parametrize("change", ["subject", "generation", "scope", "source", "acceptance", "task", "successor", "missing-successor", "self-successor"])
def test_correct_checksum_does_not_validate_unrelated_or_unaccepted_closure(tmp_path, change):
    inventory, decision, _ = fixture(tmp_path)
    closure = json.loads(Path(decision["closure"]).read_bytes())
    if change == "subject":
        closure["artifact"]["id"] = "other"
    elif change == "generation":
        closure["artifact"]["identity"] = [8, 8, 8]
    elif change in {"scope", "source"}:
        closure["artifact"]["data_scope" if change == "scope" else "source"] = "different"
    elif change == "acceptance":
        closure["accepted"] = False
    elif change == "task":
        closure["task_ref"] = "custodian/other"
    elif change == "successor":
        closure["successor"]["identity"] = [9, 9, 9]
    elif change == "missing-successor":
        decision["successor"] = "not-observed"
    else:
        decision["successor"] = decision["id"]
    Path(decision["closure"]).write_text(json.dumps(closure))
    with pytest.raises(ValueError):
        review.record(tmp_path, inventory, [decision])
    assert not life.runs(tmp_path)


def test_newer_unresolved_review_wins_even_when_wall_clock_moves_backwards(tmp_path, monkeypatch):
    inventory, decision, _ = fixture(tmp_path)
    review.record(tmp_path, inventory, [decision])
    monkeypatch.setattr(review, "utc", lambda: "2000-01-01T00:00:00+00:00")
    monkeypatch.setattr(life, "utc", lambda: "2000-01-01T00:00:00+00:00")
    decision.update(outcome="unresolved", reason="new evidence is pending")
    review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = list(reversed(life.runs(tmp_path)))
    policy.apply(inventory)
    assert inventory["records"][0]["lifetime"]["review"]["outcome"] == "unresolved"
    assert inventory["records"][0]["lifetime"]["review_required"]
    assert sorted(run["review_sequence"] for run in inventory["lifecycle"]) == [1, 2]


def test_newer_invalid_review_does_not_fall_back_to_valid_older_proof(tmp_path):
    inventory, decision, evidence = fixture(tmp_path)
    review.record(tmp_path, inventory, [decision])
    newer = tmp_path / "newer.json"
    newer.write_text("newer proof")
    decision.update(evidence=[str(newer)])
    review.record(tmp_path, inventory, [decision])
    newer.write_text("changed")
    inventory["lifecycle"] = life.runs(tmp_path)
    policy.apply(inventory)
    assert evidence.exists() and inventory["records"][0]["lifetime"]["review_required"]
    assert not inventory["records"][0]["lifetime"]["review"]["evidence_valid"]


def test_equal_order_legacy_conflicts_remain_unresolved_regardless_of_list_order(tmp_path):
    inventory, decision, _ = fixture(tmp_path)
    review.record(tmp_path, inventory, [decision])
    original = life.runs(tmp_path)[0]
    original.pop("review_sequence")
    conflict = copy.deepcopy(original)
    conflict["run_id"] = "f" * 32
    conflict["assessments"][0]["outcome"] = "unresolved"
    for ordered in ([original, conflict], [conflict, original]):
        inventory["lifecycle"] = ordered
        policy.apply(inventory)
        actual = inventory["records"][0]["lifetime"]
        assert actual["review_required"] and actual["review"]["order_conflict"]
        assert not actual["deletion_authorized"]


def test_custody_review_does_not_invent_origin_or_replace_recorded_producer(tmp_path):
    inventory, decision, _ = fixture(tmp_path)
    decision.update(outcome="retained", origin_owner=None)
    review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = life.runs(tmp_path)
    policy.apply(inventory)
    first = inventory["records"][0]["lifetime"]
    assert first["custodian"] == "deployment/task" and first["origin_owner"] is None
    assert first["ownership_source"] == "custody" and not first["deletion_authorized"]
    resource = {key: inventory["records"][0][key] for key in ("kind", "path", "identity", "category")}
    with life.Run("producer", store=tmp_path) as producer:
        producer.data.update(task_ref="actual/producer", resources=[resource])
    inventory["lifecycle"] = life.runs(tmp_path)
    policy.apply(inventory)
    actual = inventory["records"][0]["lifetime"]
    assert actual["owner"] == actual["origin_owner"] == "actual/producer"
    assert actual["custodian"] == "deployment/task" and actual["ownership_source"] == "producer"


def test_legacy_completed_custody_without_bound_proof_is_requeued(tmp_path):
    inventory, decision, _ = fixture(tmp_path)
    review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = life.runs(tmp_path)
    inventory["lifecycle"][0]["assessments"][0].pop("closure")
    policy.apply(inventory)
    assert inventory["records"][0]["lifetime"]["review_required"]
    assert not inventory["records"][0]["lifetime"]["review"]["closure_valid"]


def test_supersession_cycle_is_rejected_before_ledger_creation(tmp_path):
    inventory, first, _ = fixture(tmp_path)
    second = {**first, "id": first["successor"], "successor": first["id"]}
    content = json.loads(Path(first["closure"]).read_bytes())
    content["artifact"], content["successor"] = content["successor"], content["artifact"]
    proof = tmp_path / "second-closure.json"
    proof.write_text(json.dumps(content))
    second["closure"] = str(proof)
    with pytest.raises(ValueError, match="cycle"):
        review.record(tmp_path, inventory, [first, second])
    assert not life.runs(tmp_path)


@pytest.mark.parametrize("changed", ["source", "data_scope"])
def test_closure_binds_sparse_inventory_to_real_producer_declaration(tmp_path, changed):
    inventory, decision, _ = fixture(tmp_path)
    with life.Run("builder", store=tmp_path) as producer:
        producer.data.update(task_ref="original/task", source={"head": "a" * 40}, resources=[{**item, "data_scope": "qa/data"} for item in inventory["records"]])
    # Registry records are deliberately sparse: source/scope belong to the ledger.
    assert "source" not in inventory["records"][0] and "data_scope" not in inventory["records"][0]
    with pytest.raises(ValueError, match="exact artifact"):
        review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = life.runs(tmp_path)
    bound = review.subjects(inventory)
    proof = json.loads(Path(decision["closure"]).read_bytes())
    proof.update(artifact=bound[decision["id"]], successor=bound[decision["successor"]])
    Path(decision["closure"]).write_text(json.dumps(proof))
    review.record(tmp_path, inventory, [decision])
    inventory["lifecycle"] = life.runs(tmp_path)
    policy.apply(inventory)
    assert inventory["records"][0]["lifetime"]["review"]["closure_valid"]
    if changed == "source":
        producer.data["source"] = {"head": "b" * 40}
    else:
        producer.data["resources"][0]["data_scope"] = "different/data"
    producer.persist()
    inventory["lifecycle"] = life.runs(tmp_path)
    policy.apply(inventory)
    actual = inventory["records"][0]["lifetime"]
    assert actual["review_required"] and not actual["review"]["closure_valid"]
