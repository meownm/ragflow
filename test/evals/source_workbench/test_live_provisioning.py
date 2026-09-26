"""Offline checks for the disposable Source Workbench quality fixture."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from test.evals.source_workbench.evaluate import GOLDEN
from test.playwright.e2e.test_source_workbench_quality_live import _guard, _source_revision, _upload_body


def test_upload_contains_exactly_the_gold_texts_and_titles():
    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    body, boundary = _upload_body(gold, "abcdef")
    assert body.count('Content-Disposition: form-data; name="file"; filename="'.encode()) == 4
    for document in gold["documents"].values():
        assert f'filename="{document["title"]}.txt"'.encode() in body
        assert body.count(document["text"].encode()) == 1
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_guard_rejects_non_disposable_target(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGFLOW_BASE_URL", "http://example.com")
    monkeypatch.setenv("QA_DISPOSABLE_PROJECT", "shared-stack")
    monkeypatch.setenv("QA_LIVE_TOKEN", "example-test-token")
    monkeypatch.setenv("QA_LIVE_EVIDENCE", str(tmp_path))
    (tmp_path / "identity.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="loopback"):
        _guard()


def test_guard_requires_matching_runner_project(monkeypatch, tmp_path):
    monkeypatch.setenv("RAGFLOW_BASE_URL", "http://127.0.0.1:19382")
    monkeypatch.setenv("QA_DISPOSABLE_PROJECT", "ragflow-t1-live-20260906-b-abcdef12")
    monkeypatch.setenv("QA_LIVE_TOKEN", "example-test-token")
    monkeypatch.setenv("QA_LIVE_EVIDENCE", str(tmp_path))
    (tmp_path / "identity.json").write_text('{"project":"different"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match"):
        _guard()


def test_candidate_revision_keeps_source_id_separate(tmp_path):
    source = tmp_path / "candidate" / "source"
    source.mkdir(parents=True)
    (source.parent / "candidate.json").write_text(
        json.dumps({"source_id": "snapshot-content-id", "identity": {"head": "a" * 40, "dirty": False}}),
        encoding="utf-8",
    )
    assert _source_revision({"source": str(source), "source_id": "snapshot-content-id"}) == ("a" * 40, False)
    with pytest.raises(RuntimeError, match="does not match"):
        _source_revision({"source": str(source), "source_id": "different"})


def test_backend_only_runner_does_not_create_files_inside_frozen_source(tmp_path):
    source = tmp_path / "candidate" / "source"
    (source / "api").mkdir(parents=True)
    (source / "api" / "ragflow_server.py").write_text("", encoding="utf-8")
    (source.parent / "candidate.json").write_text("{}", encoding="utf-8")
    runner = Path(__file__).resolve().parents[2] / "integration/live_ragflow/runner.py"
    evidence = source / "evidence"
    dist = source / "empty-dist"
    result = subprocess.run(
        [sys.executable, str(runner), "--source-root", str(source), "--dist-root", str(dist), "--evidence-dir", str(evidence), "--business-documents-quality", "--skip-browser-tests"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "outside the frozen source snapshot" in result.stderr
    assert not evidence.exists()
    assert not dist.exists()
