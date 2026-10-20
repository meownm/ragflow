"""Durable private evidence, retention and archive boundaries."""

import hashlib
import json
import sys
import time
from pathlib import Path
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "admin/server"))
import document_quality_evidence as evidence


@pytest.fixture(autouse=True)
def private_root(monkeypatch, tmp_path):
    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_DIR", str(tmp_path / "evidence"))


def test_failed_execution_retains_full_response_traceback_and_checksums():
    response = "Полный ответ\n" * 10000
    with pytest.raises(RuntimeError):
        with evidence.record_quality_evidence("run", "failed", {"model": "synthetic"}) as directory:
            (directory / "report.exchanges.json").write_bytes(response.encode("utf-8"))
            (directory / "pytest.log").write_text("full assertion detail", encoding="utf-8")
            (directory / "private.sqlite").write_text("excluded", encoding="utf-8")
            raise RuntimeError("provider detail")
    assert evidence.evidence_available("run", "failed")
    with evidence.evidence_archive("run", "failed") as stream, ZipFile(stream) as archive:
        assert archive.read("report.exchanges.json").decode() == response
        assert "RuntimeError: provider detail" in archive.read("runner-error.txt").decode()
        assert "private.sqlite" not in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["state"] == "FINISHED"
        assert manifest["files"]["report.exchanges.json"]["sha256"] == hashlib.sha256(response.encode()).hexdigest()
    with pytest.raises(FileExistsError):
        with evidence.record_quality_evidence("run", "failed", {}):
            pass


def test_interrupted_execution_can_be_downloaded_before_finalization():
    with evidence.record_quality_evidence("run", "running", {}) as directory:
        (directory / "report.calls.jsonl").write_text('{"event":"request"}\n', encoding="utf-8")
        with evidence.evidence_archive("run", "running") as stream, ZipFile(stream) as archive:
            assert json.loads(archive.read("manifest.json"))["state"] == "RUNNING"
            assert archive.read("report.calls.jsonl").decode().splitlines() == ['{"event":"request"}']


@pytest.mark.parametrize("kind,identifier", [("other", "valid"), ("run", "../outside"), ("run", ""), ("run", "x/../../y")])
def test_archive_rejects_untrusted_identity(kind, identifier):
    with pytest.raises(ValueError):
        evidence.evidence_archive(kind, identifier)


def test_archive_rejects_linked_files(tmp_path):
    with evidence.record_quality_evidence("run", "linked", {}) as directory:
        target = tmp_path / "secret"
        target.write_text("private", encoding="utf-8")
        try:
            (directory / "pytest.log").symlink_to(target)
        except OSError:
            pytest.skip("Symlinks unavailable on this platform")
    with pytest.raises(ValueError, match="Linked"):
        evidence.evidence_archive("run", "linked")


def test_retention_preserves_latest_and_active_but_removes_expired_interrupted(monkeypatch, tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    now = time.time()
    for identifier, state, stamp in [("old", "FINISHED", now - 20 * 86400), ("latest", "FINISHED", now - 19 * 86400), ("active", "RUNNING", now), ("interrupted", "RUNNING", now - 20 * 86400)]:
        directory = root / f"run-{identifier}"
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({"state": state, "started_at": stamp, "finished_at": stamp}), encoding="utf-8")
    evidence._prune()
    assert {path.name for path in root.iterdir()} == {"run-latest", "run-active"}
    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_MAX_BYTES", "1")
    with evidence.record_quality_evidence("run", "newest", {}):
        pass
    assert {path.name for path in root.iterdir()} == {"run-newest", "run-active"}


def test_retention_keeps_last_completed_evidence_for_each_product(monkeypatch, tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setenv("BUSINESS_DOCUMENT_QUALITY_EVIDENCE_MAX_BYTES", "1")
    now = time.time()
    for index, product in enumerate(("business_requirements", "technical_specification")):
        for suffix, age in (("old", 21), ("latest", 20)):
            directory = root / f"run-{product}-{suffix}"
            directory.mkdir()
            (directory / "manifest.json").write_text(json.dumps({"state": "FINISHED", "document_type": product, "finished_at": now - age * 86400 + index}), encoding="utf-8")
    evidence._prune()
    assert {item.name for item in root.iterdir()} == {"run-business_requirements-latest", "run-technical_specification-latest"}
