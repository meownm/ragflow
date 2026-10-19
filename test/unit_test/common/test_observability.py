import json
import logging
from pathlib import Path

from common.log_utils import JsonLogFormatter, redact_log_text
from common.observability import bind_context, inject_queue_context
from common.config_utils import redact_config_secrets


def test_json_log_formatter_adds_chain_context_and_redacts_credentials():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        "ragflow.test",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer super-secret",
        (),
        None,
    )

    with bind_context(request_id="req-1", correlation_id="chain-1"):
        payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "req-1"
    assert payload["correlation_id"] == "chain-1"
    assert "super-secret" not in payload["message"]


def test_queue_envelope_carries_context_without_mutating_source():
    source = {"id": "job-1"}
    with bind_context(request_id="req-1", correlation_id="chain-1"):
        result = inject_queue_context(source)

    assert source == {"id": "job-1"}
    assert result["_telemetry"]["x-request-id"] == "req-1"
    assert result["_telemetry"]["x-correlation-id"] == "chain-1"


def test_redaction_covers_common_secret_assignments():
    assert "s3cr3t" not in redact_log_text("api_key=s3cr3t")
    assert "s3cr3t" not in redact_log_text('{"password": "s3cr3t"}')
    assert "s3cr3t" not in redact_log_text("client_secret='s3cr3t'")


def test_config_redaction_is_recursive_and_does_not_mutate_source():
    source = {
        "database": {"password": "nested-secret", "host": "database"},
        "oauth": [{"client_secret": "oauth-secret", "client_id": "client"}],
    }

    result = redact_config_secrets(source)

    assert result == {
        "database": {"password": "********", "host": "database"},
        "oauth": [{"client_secret": "********", "client_id": "client"}],
    }
    assert source["database"]["password"] == "nested-secret"


def test_daily_log_rotation_retains_sixty_uncompressed_archives(tmp_path, monkeypatch):
    from logging.handlers import TimedRotatingFileHandler
    from common import log_utils

    root = logging.Logger("retention-test")
    monkeypatch.setattr(log_utils, "initialized_root_logger", False)
    monkeypatch.setattr(log_utils, "get_project_base_directory", lambda: str(tmp_path))
    monkeypatch.setattr(logging, "captureWarnings", lambda _capture: None)
    original_get_logger = logging.getLogger
    monkeypatch.setattr(logging, "getLogger", lambda name=None: root if name is None else original_get_logger(name))
    try:
        log_utils.init_root_logger("server")
        handler = root.handlers[0]
        assert isinstance(handler, TimedRotatingFileHandler)
        for day in range(1, 32):
            (tmp_path / f"logs/server.log.2026-08-{day:02}").write_bytes(b"old")
            (tmp_path / f"logs/server.log.2026-09-{day:02}").write_bytes(b"new")
        deleted = handler.getFilesToDelete()
        assert len(deleted) == 2
        assert [Path(path).name for path in deleted] == ["server.log.2026-08-01", "server.log.2026-08-02"]
    finally:
        for handler in root.handlers:
            handler.close()
