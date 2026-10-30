"""Runtime SPA provenance must bind the exact frozen source, image and bytes."""

import json

import pytest

from tools.quality import candidate
from tools.quality import frontend_artifact as frontend
from test.unit_test.tools.quality import test_frontend_artifact as existing

setup = existing.setup
IMAGE = "sha256:" + "1" * 64


def packet(setup):
    snapshot, work, archive, _ = setup
    receipt = frontend.build(snapshot, work, archive)
    for key in ("profile", "toolchain", "build_host", "receipt_id"):
        receipt.pop(key)
    receipt.update(kind="frontend-image", image_id=IMAGE)
    return snapshot, archive, receipt


def seal(archive, receipt):
    receipt.pop("receipt_id", None)
    receipt["receipt_id"] = candidate.digest(candidate.canonical(receipt))
    path = type(archive)(str(archive) + ".json")
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def test_actual_runtime_frontend_does_not_claim_the_host_node_toolchain(setup, monkeypatch):
    snapshot, archive, receipt = packet(setup)
    seen = []

    def actual(image, source):
        seen.append((image, source))
        return receipt["files"]

    monkeypatch.setattr(frontend, "_runtime_files", actual)
    result = frontend.verify(snapshot, archive, seal(archive, receipt))
    assert seen == [(IMAGE, candidate.verify(snapshot)["source_id"])]
    assert result["validation"] == "NOT_ASSERTED" and "toolchain" not in result


@pytest.mark.parametrize("mutation", ["source", "test-claim", "kind", "runtime-bytes"])
def test_runtime_receipt_cannot_hide_mismatched_source_or_files(setup, monkeypatch, mutation):
    snapshot, archive, receipt = packet(setup)
    monkeypatch.setattr(frontend, "_runtime_files", lambda *_: {} if mutation == "runtime-bytes" else receipt["files"])
    if mutation == "source":
        receipt["source_id"] = "another-source"
    elif mutation == "test-claim":
        receipt["validation"] = "PASS"
    elif mutation == "kind":
        receipt["kind"] = "unknown-kind"
    with pytest.raises(ValueError):
        frontend.verify(snapshot, archive, seal(archive, receipt))


@pytest.mark.parametrize("mutation", [None, "id", "source", "platform", "architecture"])
def test_actual_image_identity_precedes_read_only_hash_probe(monkeypatch, mutation):
    from tools.quality import artifact_lifecycle

    info = {"Id": IMAGE, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {"org.ragflow.source-id": "frozen-source"}}}
    if mutation == "id":
        info["Id"] = "different-image"
    elif mutation == "source":
        info["Config"]["Labels"]["org.ragflow.source-id"] = "other-source"
    elif mutation == "platform":
        info["Os"] = "windows"
    elif mutation == "architecture":
        info["Architecture"] = "arm64"
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        return json.dumps([info] if command[1:3] == ["image", "inspect"] else {"index.html": {"sha256": "synthetic", "size": 1}}).encode()

    monkeypatch.setattr(frontend.subprocess, "check_output", output)
    monkeypatch.setattr(artifact_lifecycle, "docker_args", lambda args: args)
    if mutation:
        with pytest.raises(ValueError, match="image/source identity"):
            frontend._runtime_files(IMAGE, "frozen-source")
        assert len(calls) == 1
    else:
        assert frontend._runtime_files(IMAGE, "frozen-source")["index.html"]["size"] == 1
        assert len(calls) == 2 and "--network" in calls[1] and "--rm" in calls[1]


def test_mutable_runtime_image_reference_is_rejected_before_docker():
    with pytest.raises(ValueError, match="immutable"):
        frontend._runtime_files("local/image:latest", "frozen-source")
