"""Failure reports must not persist credentials renewed by browser login."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from test.integration.live_ragflow import runner
from test.playwright.helpers.redaction import redact_browser_secrets


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer renewed-session-token",
        "- authorization: renewed-session-token",
        "{'Authorization': 'Bearer renewed-session-token'}",
        "Cookie: session=renewed-session-token; csrf=another-secret",
        "{'cookie': 'session=renewed-session-token; csrf=another-secret'}",
        "Set-Cookie: session=renewed-session-token; HttpOnly",
    ],
)
def test_renewed_headers_are_redacted_without_knowing_the_token(text):
    redacted = redact_browser_secrets(text)
    assert "renewed-session-token" not in redacted
    assert "another-secret" not in redacted
    assert "[REDACTED]" in redacted


def test_runner_preserves_bootstrap_redaction_and_scrubs_renewed_headers(monkeypatch):
    monkeypatch.setattr(runner, "SECRET_VALUES", ["initial-credential"])
    text = runner.sanitized("initial-credential\nAuthorization: Bearer renewed-session-token")
    assert "initial-credential" not in text
    assert "renewed-session-token" not in text


def test_report_and_junit_are_redacted_before_persistence(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(
        "def test_probe():\n    print('Cookie: session=renewed-session-token; csrf=another-secret')\n    raise RuntimeError('Authorization: Bearer renewed-session-token')\n",
        encoding="utf-8",
    )
    junit = tmp_path / "probe.xml"
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-p", "test.playwright.conftest", "-c", str(tmp_path / "pytest.ini"), "-q", "--color=no", f"--junitxml={junit}", str(tmp_path / "test_probe.py")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": "",
            "PYTEST_ADDOPTS": "",
            "PLAYWRIGHT_HANG_TIMEOUT_S": "0",
            "PW_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 1
    for evidence in (result.stdout + result.stderr, junit.read_text(encoding="utf-8")):
        assert "renewed-session-token" not in evidence
        assert "another-secret" not in evidence
        assert "[REDACTED]" in evidence
