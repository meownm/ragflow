"""Connection probes share safe diagnostics and always release libpq resources."""

import sys
import traceback
from pathlib import Path
from types import ModuleType

import psycopg2
import pytest

if "api.apps" not in sys.modules:
    package = ModuleType("api.apps")
    package.__path__ = [str(Path(__file__).resolve().parents[5] / "api/apps")]
    sys.modules["api.apps"] = package

from api.apps.business_documents import sql_query_postgres as adapter
from business_documents.application.errors import BusinessDocumentError

CONFIG = {"host": "qa.invalid", "port": 5432, "database": "qa", "credentials": {"username": "qa", "password": "do-not-export"}}


class Connection:
    def __init__(self, error=None):
        self.error = error
        self.statements = []
        self.closed = False
        self.rolled_back = False

    def set_session(self, **options):
        assert options == {"readonly": True, "autocommit": False}

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, parameters=None):
        self.statements.append((sql, parameters))
        if self.error and sql == "SELECT 1":
            raise self.error

    def fetchone(self):
        return (1,)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_health_probe_is_read_only_bounded_and_closes_connection(monkeypatch):
    connection = Connection()
    calls = []
    monkeypatch.setattr(adapter.psycopg2, "connect", lambda **options: (calls.append(options), connection)[1])
    duration = adapter.check_postgres_connection(CONFIG, timeout_ms=500)
    assert duration >= 0
    assert connection.statements == [("SELECT set_config('statement_timeout', %s, true)", ("500ms",)), ("SELECT 1", None)]
    assert connection.closed and connection.rolled_back
    assert calls == [{"host": "qa.invalid", "port": 5432, "dbname": "qa", "user": "qa", "password": "do-not-export", "connect_timeout": 5}]


@pytest.mark.parametrize("operation", ["health", "preflight", "run"])
@pytest.mark.parametrize("message,code,status", [
    ('password authentication failed for user "qa"; password=do-not-export', "SQL_CONNECTION_AUTH_FAILED", 503),
    ('FATAL: role "qa" does not exist; password=do-not-export', "SQL_CONNECTION_AUTH_FAILED", 503),
    ('SSL error: certificate verify failed; password=do-not-export', "SQL_CONNECTION_TLS_FAILED", 503),
    ('timeout expired; password=do-not-export', "SQL_CONNECTION_TIMEOUT", 504),
    ('could not connect to server tls.example: Connection refused; password=do-not-export', "SQL_CONNECTION_NETWORK_FAILED", 503),
    ('unclassified driver failure; password=do-not-export', "SQL_SOURCE_UNAVAILABLE", 503),
])
def test_connection_error_families_never_export_driver_diagnostics(monkeypatch, operation, message, code, status):
    def fail(**options):
        raise psycopg2.OperationalError(message)

    monkeypatch.setattr(adapter.psycopg2, "connect", fail)
    with pytest.raises(BusinessDocumentError) as caught:
        if operation == "health":
            adapter.check_postgres_connection(CONFIG, timeout_ms=500)
        elif operation == "preflight":
            adapter.explain_postgres(CONFIG, "SELECT :value", {"value": 1}, timeout_ms=500)
        else:
            adapter.execute_postgres(CONFIG, "SELECT :value", {"value": 1}, timeout_ms=500, max_rows=1, max_result_bytes=100)
    assert (caught.value.code, caught.value.status) == (code, status)
    assert "do-not-export" not in str(caught.value) and "qa.invalid" not in str(caught.value)
    assert caught.value.details == {}
    assert "do-not-export" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("error,code", [
    (psycopg2.errors.QueryCanceled("timeout: do-not-export"), "SQL_CONNECTION_TIMEOUT"),
    (psycopg2.OperationalError("server closed the connection: do-not-export"), "SQL_CONNECTION_NETWORK_FAILED"),
])
def test_probe_releases_connection_after_failure(monkeypatch, error, code):
    connection = Connection(error)
    monkeypatch.setattr(adapter.psycopg2, "connect", lambda **options: connection)
    with pytest.raises(BusinessDocumentError) as caught:
        adapter.check_postgres_connection(CONFIG, timeout_ms=500)
    assert caught.value.code == code
    assert connection.closed
    assert "do-not-export" not in str(caught.value)


def test_bad_probe_response_is_unavailable_and_connection_is_closed(monkeypatch):
    connection = Connection()
    monkeypatch.setattr(connection, "fetchone", lambda: (2,))
    monkeypatch.setattr(adapter.psycopg2, "connect", lambda **options: connection)
    with pytest.raises(BusinessDocumentError) as caught:
        adapter.check_postgres_connection(CONFIG, timeout_ms=500)
    assert caught.value.code == "SQL_SOURCE_UNAVAILABLE" and connection.closed
