from api.utils import health_utils


class _Cursor:
    def __init__(self, row):
        self._row = row
        self.closed = False

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


class _Response:
    def __init__(self, body, ok=True):
        self._body = body
        self.ok = ok
        self.text = ""

    def json(self):
        return self._body


def test_get_postgres_status(monkeypatch):
    cursor = _Cursor(("PostgreSQL 16.4", "rag_flow", "rag_flow"))
    monkeypatch.setattr(health_utils.DB, "execute_sql", lambda query: cursor)

    result = health_utils.get_postgres_status()

    assert result == {
        "status": "alive",
        "message": {
            "database": "rag_flow",
            "user": "rag_flow",
            "version": "PostgreSQL 16.4",
        },
    }
    assert cursor.closed is True


def test_check_asr_alive_requires_ready_status(monkeypatch):
    monkeypatch.setattr(
        health_utils,
        "get_base_config",
        lambda key, default: {
            "host": "t-one-asr",
            "port": 9011,
            "health_path": "/health/ready",
        },
    )
    monkeypatch.setattr(
        health_utils.requests,
        "get",
        lambda url, timeout: _Response({"status": "ready", "checks": {}}),
    )

    result = health_utils.check_asr_alive()

    assert result["status"] == "alive"
    assert result["message"]["status"] == "ready"
    assert "elapsed" in result["message"]


def test_check_asr_alive_reports_not_ready_as_timeout(monkeypatch):
    monkeypatch.setattr(health_utils, "get_base_config", lambda key, default: {})
    monkeypatch.setattr(
        health_utils.requests,
        "get",
        lambda url, timeout: _Response({"status": "not_ready"}),
    )

    result = health_utils.check_asr_alive()

    assert result["status"] == "timeout"
    assert result["message"]["status"] == "not_ready"
