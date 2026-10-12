"""One-time recovery bootstrap cannot bypass previously published qualification."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test.unit_test.tools.quality.test_publication_recovery import GitHub, publication as base_publication
from tools.quality import release_publish as publisher
from tools.quality.release_evidence import bundle_identity, file_manifest
from tools.quality.release_runtime import write_json


class HistoryGitHub(GitHub):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.release_ids = {}
        self.asset_ids = {}
        self.asset_metadata = {}
        self.notes = []

    def asset_bytes(self, repository, asset_id):
        assert repository == "meownm/ragflow"
        tag, name = next(key for key, value in self.asset_ids.items() if value == asset_id)
        return self.releases[tag]["assets"][name]

    def command(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("gh", "api") and args[2].startswith("repos/meownm/ragflow/releases?"):
            page = int(args[2].split("page=")[-1])
            entries = []
            for tag, release in self.releases.items():
                release_id = self.release_ids.setdefault(tag, len(self.release_ids) + 1)
                entries.append({"id": release_id, "tag_name": tag, "draft": release["draft"]})
            return json.dumps(entries[(page - 1) * 100 : page * 100])
        if args[:2] == ("gh", "api") and "/assets?" in args[2]:
            release_id = int(args[2].split("/releases/")[1].split("/")[0])
            tag = next(tag for tag, value in self.release_ids.items() if value == release_id)
            page = int(args[2].split("page=")[-1])
            entries = []
            for name, raw in self.releases[tag]["assets"].items():
                asset_id = self.asset_ids.setdefault((tag, name), len(self.asset_ids) + 1)
                metadata = {"id": asset_id, "name": name, "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
                metadata.update(self.asset_metadata.get((tag, name), {}))
                entries.append(metadata)
            return json.dumps(entries[(page - 1) * 100 : page * 100])
        if args[:3] == ("gh", "release", "create") and "--notes-file" in args:
            self.notes.append(Path(args[args.index("--notes-file") + 1]).read_text())
        return super().command(*args, **kwargs)

    def writes(self):
        return [args for args in self.calls if args[:2] == ("gh", "release") and args[2] in {"create", "upload", "edit"}]


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    receipt, packet = base_publication.__wrapped__(tmp_path, monkeypatch)
    write_json(packet / "upgrade/qualified-upgrade.json", {"purpose": "initial-runtime-baseline-upgrade"})
    receipt["artifacts"]["files"] = file_manifest(packet)
    serialized = tmp_path / "current.json"
    write_json(serialized, receipt)
    monkeypatch.setenv("QUALIFICATION_SHA256", hashlib.sha256(serialized.read_bytes()).hexdigest())
    github = HistoryGitHub()
    monkeypatch.setattr(publisher, "command", github.command)
    monkeypatch.setattr(publisher, "_github_asset_bytes", github.asset_bytes)
    return receipt, packet, github


def qualified_release(receipt, tmp_path, tag="v1.0.0"):
    path = tmp_path / "prior.json"
    write_json(path, receipt)
    raw = path.read_bytes()
    proof = {
        "schema": 2,
        "status": "PASS",
        "repository": "meownm/ragflow",
        "release_tag": tag,
        "qualification_sha256": hashlib.sha256(raw).hexdigest(),
        "bundle_id": bundle_identity(receipt),
        "image_digest": receipt["image"]["digest"],
        "qualification_artifact": {"id": 10, "name": "qualification-123-2", "digest": "sha256:" + "b" * 64, "workflow_run": {"id": 123, "head_sha": receipt["source_revision"]}},
    }
    return {"draft": False, "assets": {"candidate-receipt.json": raw, "publication-receipt.json": json.dumps(proof).encode()}}


def test_first_bootstrap_then_exact_retry_proves_same_bytes(bootstrap):
    receipt, packet, github = bootstrap
    publisher.publish(receipt, packet, "nightly-123-2", True)
    original = deepcopy(github.releases["nightly-123-2"])
    uploaded = list(github.uploaded)
    publisher.publish(receipt, packet, "nightly-123-2", True)
    assert github.releases["nightly-123-2"] == original
    # Only append-only journal records may be added on a retry.
    assert [item for item in github.uploaded[len(uploaded) :] if not item[0].startswith("publication-")] == []
    assert "historical-unqualified" in github.notes[0]
    assert "does not establish a previous qualified release" in github.notes[0]
    assert len(github.asset_ids) >= 2


@pytest.mark.parametrize("next_tag", ["v1.0.0", "nightly-124-1"])
def test_serialized_second_bootstrap_cannot_reuse_bundle_under_another_tag(bootstrap, next_tag):
    receipt, packet, github = bootstrap
    publisher.publish(receipt, packet, "nightly-123-2", True)
    before = len(github.writes())
    with pytest.raises(ValueError, match="already consumed"):
        publisher.publish(receipt, packet, next_tag, next_tag.startswith("nightly-"))
    assert len(github.writes()) == before
    assert next_tag not in github.releases


def test_published_qualification_on_later_release_and_asset_pages_blocks_before_writes(bootstrap, tmp_path):
    receipt, packet, github = bootstrap
    for index in range(100):
        github.releases[f"old-{index}"] = {"draft": index % 2 == 0, "assets": {}}
    prior = qualified_release(receipt, tmp_path)
    prior["assets"] = {**{f"other-{index}": b"unrelated" for index in range(100)}, **prior["assets"]}
    github.releases["v1.0.0"] = prior
    with pytest.raises(ValueError, match="already consumed"):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    assert not github.writes()
    assert any("releases?per_page=100&page=2" in args[2] for args in github.calls)
    assert any("assets?per_page=100&page=2" in args[2] for args in github.calls)


def test_draft_and_unqualified_published_history_do_not_consume_bootstrap(bootstrap, tmp_path):
    receipt, packet, github = bootstrap
    draft = qualified_release(receipt, tmp_path)
    draft["draft"] = True
    github.releases["v1.0.0"] = draft
    github.releases["legacy"] = {"draft": False, "assets": {"install.sh": b"legacy"}}
    publisher.publish(receipt, packet, "nightly-123-2", True)
    assert github.releases["nightly-123-2"]["draft"] is False


@pytest.mark.parametrize(
    "fault",
    [
        "receipt-only",
        "proof-only",
        "invalid-json",
        "foreign-repository",
        "foreign-candidate",
        "foreign-tag",
        "proof-hash",
        "proof-bundle",
        "missing-digest",
        "wrong-digest",
        "duplicate-name",
        "non-object-proof",
        "historical-schema",
    ],
)
def test_unverifiable_published_evidence_fails_closed_before_writes(bootstrap, tmp_path, fault):
    receipt, packet, github = bootstrap
    tag = "nightly-123-2"
    prior = qualified_release(receipt, tmp_path, tag)
    github.releases[tag] = prior
    assets = prior["assets"]
    proof = json.loads(assets["publication-receipt.json"])
    if fault == "receipt-only":
        del assets["publication-receipt.json"]
    elif fault == "proof-only":
        del assets["candidate-receipt.json"]
    elif fault == "invalid-json":
        assets["candidate-receipt.json"] = b"{broken"
    elif fault == "foreign-repository":
        proof["repository"] = "foreign/ragflow"
    elif fault == "foreign-candidate":
        changed = json.loads(assets["candidate-receipt.json"])
        changed["ci"]["repository"] = "foreign/ragflow"
        assets["candidate-receipt.json"] = json.dumps(changed).encode()
        proof["repository"] = "foreign/ragflow"
        proof["qualification_sha256"] = hashlib.sha256(assets["candidate-receipt.json"]).hexdigest()
    elif fault == "foreign-tag":
        proof["release_tag"] = "v9.0.0"
    elif fault == "proof-hash":
        proof["qualification_sha256"] = "f" * 64
    elif fault == "proof-bundle":
        proof["bundle_id"] = "f" * 64
    elif fault in {"missing-digest", "wrong-digest"}:
        github.asset_metadata[(tag, "candidate-receipt.json")] = {"digest": None if fault == "missing-digest" else "sha256:" + "f" * 64}
    elif fault == "duplicate-name":
        github.asset_metadata[(tag, "publication-receipt.json")] = {"name": "candidate-receipt.json"}
    elif fault == "non-object-proof":
        proof = []
    elif fault == "historical-schema":
        changed = json.loads(assets["candidate-receipt.json"])
        changed["schema"] = 1
        assets["candidate-receipt.json"] = json.dumps(changed).encode()
        proof["qualification_sha256"] = hashlib.sha256(assets["candidate-receipt.json"]).hexdigest()
    if "publication-receipt.json" in assets:
        assets["publication-receipt.json"] = json.dumps(proof).encode()
    with pytest.raises(ValueError):
        publisher.publish(receipt, packet, tag, True)
    assert not github.writes()


def test_asset_readback_tampering_does_not_authorize_exact_tag_retry(bootstrap, tmp_path, monkeypatch):
    receipt, packet, github = bootstrap
    tag = "nightly-123-2"
    github.releases[tag] = qualified_release(receipt, tmp_path, tag)
    monkeypatch.setattr(publisher, "_github_asset_bytes", lambda *_: b"{}")
    with pytest.raises(ValueError, match="digest mismatch"):
        publisher.publish(receipt, packet, tag, True)
    assert not github.writes()


@pytest.mark.parametrize("change", ["qualification-only", "bundle"])
def test_same_tag_different_qualification_is_not_an_idempotent_retry(bootstrap, tmp_path, monkeypatch, change):
    receipt, packet, github = bootstrap
    tag = "nightly-123-2"
    github.releases[tag] = qualified_release(receipt, tmp_path, tag)
    previous_bundle = bundle_identity(receipt)
    if change == "qualification-only":
        receipt["ci"]["attempt"] = 3
        receipt["image"]["reference"] = receipt["image"]["reference"].removesuffix("-2") + "-3"
        origin = json.loads(os.environ["QUALIFICATION_ARTIFACT_ORIGIN"])
        origin["name"] = "qualification-123-3"
        monkeypatch.setenv("QUALIFICATION_ARTIFACT_ORIGIN", json.dumps(origin))
        assert bundle_identity(receipt) == previous_bundle
    else:
        receipt["toolchain"]["python"] = "different-toolchain"
        assert bundle_identity(receipt) != previous_bundle
    serialized = tmp_path / "changed.json"
    write_json(serialized, receipt)
    monkeypatch.setenv("QUALIFICATION_SHA256", hashlib.sha256(serialized.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="already consumed"):
        publisher.publish(receipt, packet, tag, True)
    assert not github.writes()


def test_exact_target_match_does_not_hide_another_qualified_release(bootstrap, tmp_path):
    receipt, packet, github = bootstrap
    tag = "nightly-123-2"
    github.releases[tag] = qualified_release(receipt, tmp_path, tag)
    github.releases["v1.0.0"] = qualified_release(receipt, tmp_path)
    with pytest.raises(ValueError, match="already consumed"):
        publisher.publish(receipt, packet, tag, True)
    assert not github.writes()


@pytest.mark.parametrize(
    "response",
    ["{}", '[{"id":1,"draft":"false","tag_name":"old"}]', '[{"id":1,"draft":false,"tag_name":"old"},{"id":1,"draft":false,"tag_name":"new"}]', '[{"id":true,"draft":false,"tag_name":"old"}]'],
)
def test_malformed_history_is_not_empty_history(bootstrap, monkeypatch, response):
    receipt, packet, github = bootstrap

    def invalid(*args, **kwargs):
        if args[:2] == ("gh", "api") and "releases?" in args[2]:
            return response
        if args[:2] == ("gh", "api") and "assets?" in args[2]:
            return "[]"
        return github.command(*args, **kwargs)

    monkeypatch.setattr(publisher, "command", invalid)
    with pytest.raises(ValueError):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    assert not github.writes()


def test_history_api_failure_is_not_empty_history(bootstrap, monkeypatch):
    receipt, packet, github = bootstrap

    def unavailable(*args, **kwargs):
        if args[:2] == ("gh", "api"):
            raise RuntimeError("GitHub unavailable")
        return github.command(*args, **kwargs)

    monkeypatch.setattr(publisher, "command", unavailable)
    with pytest.raises(RuntimeError, match="unavailable"):
        publisher.publish(receipt, packet, "nightly-123-2", True)
    assert not github.writes()


def test_raw_asset_readback_preserves_bytes_and_uses_asset_id(monkeypatch):
    raw = b'{"text":"\xd0\xb0"}\r\n'
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=raw)

    monkeypatch.setattr(publisher.subprocess, "run", run)
    assert publisher._github_asset_bytes("meownm/ragflow", 42) == raw
    assert calls[0][0] == ["gh", "api", "repos/meownm/ragflow/releases/assets/42", "--header", "Accept: application/octet-stream"]
    assert "text" not in calls[0][1]
