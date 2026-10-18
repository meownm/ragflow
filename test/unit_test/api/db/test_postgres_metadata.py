"""Metadata selection, SQL types and transaction retry after backend retirement."""

from types import SimpleNamespace

from peewee import OperationalError
import pytest

from common import settings
from api.db.db_models import DB, LongTextField, PostgresDatabaseLock, RetryingPooledPostgresqlDatabase
from api.db.services.common_service import _is_deadlock_error, retry_deadlock_operation


def test_metadata_defaults_to_postgres_and_rejects_other_engines(monkeypatch):
    monkeypatch.delenv("DB_TYPE", raising=False)
    assert settings._metadata_database_type() == "postgres"
    monkeypatch.setenv("DB_TYPE", " POSTGRES ")
    assert settings._metadata_database_type() == "postgres"
    for invalid in ("sqlite", "unsupported", ""):
        monkeypatch.setenv("DB_TYPE", invalid)
        with pytest.raises(ValueError, match="only supported metadata database"):
            settings._metadata_database_type()
    assert isinstance(DB, RetryingPooledPostgresqlDatabase)
    assert DB.lock is PostgresDatabaseLock and LongTextField.field_type == "TEXT"


def test_postgres_deadlock_retries_full_operation(monkeypatch):
    monkeypatch.setattr("api.db.services.common_service.time.sleep", lambda _: None)
    driver_error = type("DriverError", (Exception,), {"pgcode": "40P01"})("deadlock")
    wrapped = OperationalError("deadlock detected")
    wrapped.__cause__ = driver_error
    assert _is_deadlock_error(wrapped)
    calls = []

    @retry_deadlock_operation(max_retries=2)
    def transaction():
        calls.append("start")
        if len(calls) == 1:
            raise wrapped
        return "committed"

    assert transaction() == "committed" and calls == ["start", "start"]
    assert not _is_deadlock_error(OperationalError("permission denied"))
    assert not _is_deadlock_error(SimpleNamespace(pgcode="40P01"))
