"""Crash reconciliation preserves a complete previous or new channel bundle."""

from copy import deepcopy
import hashlib
import json
import sys

import pytest

from tools.quality.release_publish import move_nightly
from tools.quality.release_evidence import bundle_identity, validate_publication
from test.unit_test.tools.quality import test_release_pipeline


@pytest.fixture
def receipt():
    return test_release_pipeline.receipt.__wrapped__()


@pytest.mark.parametrize("fault", [None, "changed_receipt", "foreign_origin"])
def test_publisher_entrypoint_verifies_origin_before_writes(receipt, tmp_path, monkeypatch, fault):
    from tools.quality import release_publish as publisher

    path = tmp_path / "candidate-receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setenv("QUALIFICATION_SHA256", "f" * 64 if fault == "changed_receipt" else hashlib.sha256(path.read_bytes()).hexdigest())
    packet = tmp_path / "packet"
    packet.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["release_publish", "--receipt", str(path), "--packet", str(packet), "--tag", "nightly-123-2", "--nightly"])
    events = []

    def verify(actual, directory):
        assert actual == receipt and directory == packet
        events.append("origin")
        if fault == "foreign_origin":
            raise ValueError("foreign qualification origin")

    monkeypatch.setattr(publisher, "verify_qualification_origin", verify)
    monkeypatch.setattr(publisher, "publish", lambda *_args: events.append("publish"))
    if fault:
        with pytest.raises(ValueError):
            publisher.main()
        assert "publish" not in events
    else:
        publisher.main()
        assert events == ["origin", "publish"]
    assert json.loads((tmp_path / "publication-result.json").read_text())["status"] == ("INCOMPLETE" if fault else "PASS")


class Journal:
    def __init__(self):
        self.state = {}
        self.saved = []

    def save(self, **changes):
        self.state.update(changes)
        self.saved.append(deepcopy(self.state))


@pytest.mark.parametrize("crash_after_patch", [False, True])
def test_resume_channel_after_process_loss(receipt, monkeypatch, tmp_path, crash_after_patch):
    import tools.quality.release_publish as publisher

    monkeypatch.setenv("QUALIFICATION_SHA256", "c" * 64)
    pointer = {"type": "tag", "sha": "old"}
    tags = {"old": {"message": json.dumps({"ci_run_id": "122"})}}
    journal = Journal()
    fail_once = [True]
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        path = next((item for item in args if item.startswith("repos/")), "")
        if path.endswith("/ref/tags/nightly"):
            if crash_after_patch and pointer["sha"] == "new" and fail_once[0]:
                fail_once[0] = False
                raise RuntimeError("process lost after atomic pointer mutation")
            return json.dumps({"object": pointer})
        if "/git/tags/" in path:
            return json.dumps(tags[path.rsplit("/", 1)[1]])
        if path.endswith("/tags"):
            tags["new"] = json.loads(kwargs["input"])
            return json.dumps({"sha": "new"})
        if path.endswith("/refs/tags/nightly"):
            assert journal.state["stage"] == "channel-pending"
            if not crash_after_patch and fail_once[0]:
                fail_once[0] = False
                raise RuntimeError("process lost before pointer mutation")
            pointer["sha"] = json.loads(kwargs["input"])["sha"]
            return "{}"
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "command", command)
    with pytest.raises(RuntimeError):
        move_nightly("meownm/ragflow", receipt, "nightly-123-2", tmp_path, journal)
    assert pointer["sha"] == ("new" if crash_after_patch else "old")
    move_nightly("meownm/ragflow", receipt, "nightly-123-2", tmp_path, journal)
    assert pointer["sha"] == "new"
    assert len([call for call in calls if any(item.endswith("/git/tags") for item in call)]) == 1
    channel = json.loads(tags["new"]["message"])
    assert channel["bundle_id"] == bundle_identity(receipt)


def test_publication_does_not_rewrite_qualification(receipt):
    proof = {
        "schema": 2,
        "status": "PASS",
        "repository": receipt["ci"]["repository"],
        "release_tag": "nightly-123-2",
        "qualification_sha256": "c" * 64,
        "qualification_artifact": {"id": 1, "name": "qualification-123-2", "digest": "sha256:" + "b" * 64, "workflow_run": {"id": 123, "head_sha": receipt["source_revision"]}},
        "bundle_id": bundle_identity(receipt),
        "image_digest": receipt["image"]["digest"],
    }
    original = deepcopy(receipt)
    validate_publication(receipt, proof, "c" * 64)
    assert receipt == original
    proof["qualification_sha256"] = "d" * 64
    with pytest.raises(ValueError):
        validate_publication(receipt, proof, "c" * 64)


def test_bundle_identity_excludes_verification_attempt_but_includes_product_bytes(receipt):
    original = bundle_identity(receipt)
    receipt["ci"]["attempt"] += 1
    assert bundle_identity(receipt) == original
    receipt["artifacts"]["files"]["cli/ragflow-cli-linux-amd64"] = "e" * 64
    assert bundle_identity(receipt) != original
