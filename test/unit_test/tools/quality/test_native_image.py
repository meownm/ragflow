"""Exercise the native image proof reader with incomplete and forged transcripts."""

import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[4] / "deployment/runner/test-native-image.sh"
CASES = (
    "TestPDFParser_ParseWithResult_CGOFixture",
    "TestPDFParser_ParseWithResult_CGOFixtureMarkdown",
    "TestPDFParser_ParseWithResult_CGOFixturePlainText",
    "TestPDFParser_ParseWithResult_CGOInvalidPDF",
    "TestPDFParser_ParseWithResult_CGOEmpty",
    "TestDOCXParser_ParseWithResult_CGOMinimalDocument",
)


def validate(tmp_path, monkeypatch, transcript):
    receipt = tmp_path / "native-image.json"
    receipt.write_text(json.dumps({"source_sha": "a" * 40, "status": "INCOMPLETE"}))
    log = tmp_path / "native-image.log"
    log.write_text(transcript)
    monkeypatch.setattr("sys.argv", ["native-validator", str(receipt), str(log)])
    delimiter = 'python3 - "$output/native-image.json" "$output/native-image.log" <<\'PY\'\n'
    code = SCRIPT.read_text().split(delimiter, 1)[1].split("\nPY", 1)[0]
    exec(compile(code, str(SCRIPT), "exec"), {})
    return json.loads(receipt.read_text())


def complete_transcript():
    return "\n".join(f"--- PASS: {case} (0.00s)" for case in CASES) + (
        "\nNATIVE_CLI_PDF_BEGIN\nPurpose of RAGFlow\n"
        "NATIVE_CLI_DOCX_BEGIN\nRAGFlow native DOCX qualification marker\n"
        "NATIVE_IMAGE_CLI_SHA256 " + "b" * 64 + "  /ragflow/bin/ragflow-cli\n"
        "NATIVE_SOURCE_SHA " + "a" * 40 + "\n"
    )


def test_native_proof_accepts_complete_transcript(tmp_path, monkeypatch):
    result = validate(tmp_path, monkeypatch, complete_transcript())
    assert result["status"] == "PASS"
    assert result["image_cli_sha256"] == "b" * 64
    assert result["cases"] == sorted(CASES)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f"--- PASS: {CASES[0]} (0.00s)", ""),
        (f"--- PASS: {CASES[0]}", f"--- SKIP: {CASES[0]}"),
        (f"--- PASS: {CASES[0]} (0.00s)", f"--- PASS: {CASES[0]} (0.00s)\n--- PASS: {CASES[0]} (0.00s)"),
        ("Purpose of RAGFlow", "SUCCESS"),
        ("RAGFlow native DOCX qualification marker", "SUCCESS"),
        ("NATIVE_SOURCE_SHA " + "a" * 40, "NATIVE_SOURCE_SHA " + "c" * 40),
        ("NATIVE_IMAGE_CLI_SHA256 " + "b" * 64, "NATIVE_IMAGE_CLI_SHA256 unknown"),
    ],
)
def test_native_proof_rejects_incomplete_or_foreign_results(tmp_path, monkeypatch, old, new):
    with pytest.raises(SystemExit):
        validate(tmp_path, monkeypatch, complete_transcript().replace(old, new))
