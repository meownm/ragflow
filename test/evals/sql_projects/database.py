"""One disposable database per test run; never reuse shared application data."""

from contextlib import contextmanager
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg2
from psycopg2 import sql


def connection_parameters():
    dsn = os.getenv("SQL_PROJECT_TEST_POSTGRES_DSN", "")
    target = urlparse(dsn)
    loopback = {"127.0.0.1", "localhost", "::1"}
    if os.getenv("SQL_PROJECT_TEST_DISPOSABLE") != "1" or target.scheme != "postgresql" or target.hostname not in loopback:
        raise RuntimeError("SQL project acceptance requires SQL_PROJECT_TEST_DISPOSABLE=1 and its own loopback SQL_PROJECT_TEST_POSTGRES_DSN")
    try:
        parameters = psycopg2.extensions.parse_dsn(dsn)
    except (psycopg2.Error, ValueError):
        raise RuntimeError("Invalid QA PostgreSQL DSN") from None
    if parameters.get("host") not in loopback or parameters.get("hostaddr", "127.0.0.1") not in {"127.0.0.1", "::1"} or parameters.get("service") or os.getenv("PGSERVICE") or os.getenv("PGHOSTADDR"):
        raise RuntimeError("QA PostgreSQL routing must resolve to one explicit loopback host without a service")
    # Explicit hostaddr also overrides inherited PGHOSTADDR. Never give libpq
    # an unchecked original URL with query-string routing overrides.
    parameters["hostaddr"] = parameters.get("hostaddr") or ("::1" if parameters["host"] == "::1" else "127.0.0.1")
    return parameters


@contextmanager
def disposable_database(*, extra_sql=""):
    parameters = connection_parameters()
    name = "sql_project_golden_" + uuid4().hex
    control = psycopg2.connect(**parameters)
    control.autocommit = True
    created = False
    try:
        with control.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
        parameters = dict(parameters)
        parameters["dbname"] = name
        with psycopg2.connect(**parameters) as connection:
            with connection.cursor() as cursor:
                root = Path(__file__).parent / "fixtures"
                cursor.execute((root / "schema.sql").read_text(encoding="utf-8"))
                cursor.execute((root / "data.sql").read_text(encoding="utf-8"))
                if extra_sql:
                    cursor.execute(extra_sql)
        yield {"host": parameters["hostaddr"], "port": int(parameters.get("port", 5432)), "database": name,
               "credentials": {"username": parameters.get("user", "postgres"), "password": parameters.get("password", "")}}
    finally:
        try:
            if created:
                with control.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
                    cursor.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,))
                    if cursor.fetchone() is not None:
                        raise RuntimeError("Disposable SQL fixture database was not cleaned up")
        finally:
            control.close()
