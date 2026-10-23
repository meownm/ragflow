"""Publication retries reconcile durable state without replacing a working channel."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools.quality import release_publish as publisher
from tools.quality.release_evidence import file_manifest
from tools.quality.release_runtime import write_json
from test.unit_test.tools.quality.test_release_pipeline import receipt as receipt_fixture


class GitHub:
    def __init__(self, fail_at=None):
        self.releases = {}
        self.tags = {"previous": {"message": json.dumps({"ci_run_id": "122"})}}
        self.pointer = {"type": "tag", "sha": "previous"}
        self.fail_at = fail_at
        self.failed = False
        self.uploaded = []

    def fail(self, point):
        if self.fail_at == point and not self.failed:
            self.failed = True
            raise RuntimeError("injected process loss: " + point)

    def command(self, *args, **kwargs):
        assert args[0] == "gh"
        if args[1] == "api":
            endpoint = next(item for item in args if item.startswith("repos/"))
            if endpoint.endswith("/ref/tags/nightly"):
                return json.dumps({"object": self.pointer})
            if "/git/tags/" in endpoint:
                return json.dumps(self.tags[endpoint.rsplit("/", 1)[1]])
            if endpoint.endswith("/git/tags"):
                identifier = "new-" + str(len(self.tags))
                self.tags[identifier] = json.loads(kwargs["input"])
                return json.dumps({"sha": identifier})
            if endpoint.endswith("/refs/tags/nightly"):
                self.fail("channel-before")
                self.pointer = {"type": "tag", "sha": json.loads(kwargs["input"])["sha"]}
                self.fail("channel-after")
                return "{}"
            raise AssertionError(args)
        operation, tag = args[2:4]
        if operation == "view":
            if tag not in self.releases:
                assert kwargs.get("allow_missing")
                return None
            release = self.releases[tag]
            return json.dumps({"isDraft": release["draft"], "assets": [{"name": name, "digest": "sha256:" + hashlib.sha256(content).hexdigest()} for name, content in release["assets"].items()]})
        if operation == "create":
            assert tag not in self.releases
            self.releases[tag] = {"draft": True, "assets": {}}
            return ""
        release = self.releases[tag]
        if operation == "upload":
            path = Path(args[4])
            assert path.name not in release["assets"], "an immutable asset was overwritten"
            release["assets"][path.name] = path.read_bytes()
            self.uploaded.append((tag, path.name))
            if not tag.startswith("publication-"):
                self.fail("asset-after")
            return ""
        if operation == "download":
            directory = Path(args[args.index("--dir") + 1])
            pattern = args[args.index("--pattern") + 1] if "--pattern" in args else None
            for name, content in release["assets"].items():
                if pattern is None or name == pattern:
                    (directory / name).write_bytes(content)
            return ""
        if operation == "edit":
            if "--draft=false" in args:
                self.fail("visibility-before")
                release["draft"] = False
                self.fail("visibility-after")
            return ""
        raise AssertionError(args)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    receipt = receipt_fixture.__wrapped__()
    receipt["image"]["reference"] = receipt["image"]["reference"].replace("registry/release", "releases.example/release")
    receipt["image"]["digest"] = receipt["image"]["digest"].replace("registry/release", "releases.example/release")
    receipt["image"]["staging_digest"] = receipt["image"]["staging_digest"].replace("registry/staging", "staging.example/staging")
    packet = tmp_path / "packet"
    for directory, names in (("cli", ["ragflow-cli-linux-amd64"]), ("python", ["ragflow_sdk-1.whl", "ragflow_cli-1.whl"]), ("installers", ["install.sh", "install.ps1"])):
        (packet / directory).mkdir(parents=True)
        for name in names:
            (packet / directory / name).write_bytes(b"qualified artifact " + name.encode())
    receipt["artifacts"]["files"] = file_manifest(packet)
    serialized = tmp_path / "candidate-receipt.json"
    write_json(serialized, receipt)
    monkeypatch.setenv("QUALIFICATION_SHA256", hashlib.sha256(serialized.read_bytes()).hexdigest())
    monkeypatch.setenv("GITHUB_REPOSITORY", "meownm/ragflow")
    monkeypatch.setenv(
        "QUALIFICATION_ARTIFACT_ORIGIN", json.dumps({"id": 10, "name": "qualification-123-2", "digest": "sha256:" + "b" * 64, "workflow_run": {"id": 123, "head_sha": receipt["source_revision"]}})
    )
    for key in ("NEXUS_RELEASE_USER", "NEXUS_RELEASE_PASSWORD", "NEXUS_STAGING_USER", "NEXUS_STAGING_PASSWORD"):
        monkeypatch.setenv(key, "disposable-fixture")
    monkeypatch.setattr(publisher, "promote_image", lambda *_: None)
    return receipt, packet


@pytest.mark.parametrize("point", ["asset-after", "visibility-before", "visibility-after", "channel-before", "channel-after"])
def test_every_partial_publication_resumes_same_bundle(publication, monkeypatch, point):
    receipt, packet = publication
    original = deepcopy(receipt)
    github = GitHub(point)
    monkeypatch.setattr(publisher, "command", github.command)
    with pytest.raises(RuntimeError, match="process loss"):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    if point != "channel-after":
        assert github.pointer["sha"] == "previous"
    before = dict(github.releases["nightly-123-2"]["assets"])
    publisher.publish(receipt, packet, "nightly-123-2", True)
    complete = github.releases["nightly-123-2"]
    assert complete["draft"] is False
    assert all(complete["assets"][name] == value for name, value in before.items())
    assert receipt == original
    assert json.loads(github.tags[github.pointer["sha"]]["message"])["release_tag"] == "nightly-123-2"
    publisher.publish(receipt, packet, "nightly-123-2", True)
    assert len(github.uploaded) == len(set(github.uploaded))


def test_registry_failure_preserves_channel_and_durable_reason(publication, monkeypatch):
    receipt, packet = publication
    github = GitHub()
    monkeypatch.setattr(publisher, "command", github.command)

    def unavailable(*_):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(publisher, "promote_image", unavailable)
    with pytest.raises(RuntimeError, match="registry unavailable"):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    assert github.pointer["sha"] == "previous"
    assert "nightly-123-2" not in github.releases
    journal = github.releases["publication-nightly-123-2"]["assets"]
    assert json.loads(journal[sorted(journal)[-1]])["stage"] == "image-pending"


@pytest.mark.parametrize("corrupted", [b"foreign content", b"{}", b"\xff"])
def test_altered_partial_asset_blocks_retry_and_keeps_previous(publication, monkeypatch, corrupted):
    receipt, packet = publication
    github = GitHub("asset-after")
    monkeypatch.setattr(publisher, "command", github.command)
    with pytest.raises(RuntimeError):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    assets = github.releases["nightly-123-2"]["assets"]
    assets[next(iter(assets))] = corrupted
    with pytest.raises(ValueError, match="conflict"):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    assert github.pointer["sha"] == "previous"
