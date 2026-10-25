import json
import sys

import pytest
import zstandard

from tools.quality.artifact_provenance import lookup
from tools.quality import artifact_provenance as provenance
from tools.quality import artifact_lifecycle as life


def sources(tmp_path, data):
    plain = tmp_path / "session.jsonl"
    compressed = tmp_path / "session.jsonl.zst"
    plain.write_bytes(data)
    compressed.write_bytes(zstandard.ZstdCompressor().compress(data))
    return plain, compressed


def test_plain_and_zstd_find_same_exact_evidence_without_exposing_content(tmp_path):
    raw = b'{"type":"event_msg","payload":{"id":"task-123","text":"secret-canary: completed"}}\n'
    plain, compressed = sources(tmp_path, raw)
    reports = [lookup([path], ["task-123"]) for path in (plain, compressed)]
    assert all(report["complete"] and not report["deletion_authorized"] for report in reports)
    assert reports[0]["matches"][0]["record_sha256"] == reports[1]["matches"][0]["record_sha256"]
    assert "secret-canary" not in json.dumps(reports)


def test_identifier_prefix_is_not_a_task_match(tmp_path):
    plain, _ = sources(tmp_path, b'{"id":"task-1234"}\n{"text":"(task-123)"}\n')
    report = lookup([plain], ["task-123"])
    assert report["complete"] and [entry["line"] for entry in report["matches"]] == [2]


def test_truncated_zstd_with_a_decoded_hit_remains_incomplete(tmp_path):
    _, compressed = sources(tmp_path, b'{"id":"task-123"}\n')
    compressed.write_bytes(compressed.read_bytes()[:-1])
    report = lookup([compressed], ["task-123"])
    assert not report["complete"] and not report["deletion_authorized"]


def test_invalid_zstd_is_reported_without_propagating_or_exposing_payload(tmp_path):
    path = tmp_path / "broken.jsonl.zst"
    path.write_bytes(b"secret-canary-not-zstd")
    report = lookup([path], ["task-123"])
    assert not report["complete"] and report["errors"][0]["error"] == "invalid-zstd-stream"
    assert "secret-canary" not in json.dumps(report)


def test_multiple_zstd_frames_and_final_line_without_newline(tmp_path):
    path = tmp_path / "session.jsonl.zst"
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(b'{"id":"task-123"}\n') + compressor.compress(b'{"id":"task-456"}'))
    report = lookup([path], ["task-123", "task-456"])
    assert report["complete"] and [entry["line"] for entry in report["matches"]] == [1, 2]


@pytest.mark.parametrize("limit", ["bytes", "record", "matches", "files"])
def test_budget_exhaustion_never_becomes_negative_or_closure_evidence(tmp_path, limit):
    plain, compressed = sources(tmp_path, b'{"id":"task-123"}\n{"id":"task-123"}\n')
    options = {"max_bytes": 4} if limit == "bytes" else {"max_line_bytes": 4} if limit == "record" else {"max_matches": 1} if limit == "matches" else {"max_files": 1}
    report = lookup([plain, compressed], ["task-123"], **options)
    assert not report["complete"] and report["errors"] and not report["deletion_authorized"]


def test_exact_match_budget_and_json_document_are_supported(tmp_path):
    path = tmp_path / "attempt.json"
    path.write_text('{"id":12345}')
    report = lookup([path], ["12345"], max_matches=1)
    assert report["complete"] and len(report["matches"]) == 1


@pytest.mark.parametrize("data", [b'{"id":"task-123"}\nnot-json', b"\xff\xfe"])
def test_malformed_or_invalid_encoding_is_incomplete_and_redacted(tmp_path, data):
    plain, _ = sources(tmp_path, data)
    report = lookup([plain], ["task-123"])
    assert not report["complete"] and "not-json" not in json.dumps(report)


def test_invalid_budgets_and_identifiers_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        lookup([], ["task-123"], max_bytes=0)
    with pytest.raises(ValueError):
        lookup([], ["../unsafe"])


@pytest.mark.parametrize("complete", [False, True])
def test_provenance_cli_declares_output_before_lookup_and_records_incomplete_execution(tmp_path, monkeypatch, complete):
    state, output = tmp_path / "state", tmp_path / "result.json"
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(state))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)
    monkeypatch.setattr(sys, "argv", ["artifact_provenance", str(tmp_path / "source.jsonl"), "--id", "task-123", "--output", str(output)])

    def scan(*_args, **_kwargs):
        saved = life.read_run(state, life.current().data["run_id"])
        assert not output.exists() and not saved["resources"]
        assert any(item.get("path") == str(output) for item in saved["creation_intents"])
        return {"complete": complete, "deletion_authorized": False, "matches": [], "errors": [] if complete else [{"error": "incomplete-source"}]}

    monkeypatch.setattr(provenance, "lookup", scan)
    assert provenance.main() == (0 if complete else 1)
    (saved,) = list(life.runs(state))
    assert saved["state"] == ("completed" if complete else "failed")
    assert saved["resources"][0]["path"] == str(output)
