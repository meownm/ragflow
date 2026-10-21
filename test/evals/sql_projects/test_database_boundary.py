"""Routing validation must finish before any connection, creation or deletion."""

import pytest

from test.evals.sql_projects.database import connection_parameters, disposable_database


@pytest.mark.parametrize("dsn", [
    "postgresql://127.0.0.1/qa?host=remote.example", "postgresql://localhost/qa?hostaddr=192.0.2.1", "postgresql://127.0.0.1/qa?service=production",
    "postgresql://127.0.0.1/qa?host=127.0.0.1,remote.example", "postgresql://remote.example/qa", "postgresql:///qa", "host=127.0.0.1 dbname=qa",
])
def test_unsafe_effective_routing_is_rejected_before_connect(monkeypatch, dsn):
    monkeypatch.setenv("SQL_PROJECT_TEST_DISPOSABLE", "1")
    monkeypatch.setenv("SQL_PROJECT_TEST_POSTGRES_DSN", dsn)
    monkeypatch.delenv("PGSERVICE", raising=False)
    monkeypatch.setattr("test.evals.sql_projects.database.psycopg2.connect", lambda *args, **kwargs: pytest.fail("Connection must not be attempted"))
    with pytest.raises(RuntimeError):
        with disposable_database():
            pytest.fail("Database must not be created")


def test_inherited_address_cannot_redirect_executor_connections(monkeypatch):
    monkeypatch.setenv("SQL_PROJECT_TEST_DISPOSABLE", "1")
    monkeypatch.setenv("SQL_PROJECT_TEST_POSTGRES_DSN", "postgresql://postgres@localhost:64303/postgres")
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.delenv("PGSERVICE", raising=False)
    with pytest.raises(RuntimeError):
        connection_parameters()


def test_inherited_service_configuration_is_rejected(monkeypatch):
    monkeypatch.setenv("SQL_PROJECT_TEST_DISPOSABLE", "1")
    monkeypatch.setenv("SQL_PROJECT_TEST_POSTGRES_DSN", "postgresql://postgres@localhost/qa")
    monkeypatch.setenv("PGSERVICE", "production")
    with pytest.raises(RuntimeError, match="without a service"):
        connection_parameters()
