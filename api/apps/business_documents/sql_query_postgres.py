"""Bound, read-only PostgreSQL execution for a compiled SQL project."""

from __future__ import annotations

import json
import re
import threading
from time import monotonic
from typing import Any, Callable, Mapping

import psycopg2

from business_documents.application.errors import BusinessDocumentError, ValidationError

_PLACEHOLDER = re.compile(r"(?<!:):([A-Za-z_][A-Za-z_0-9]*)\b")


def _dbapi_sql(sql: str, parameters: Mapping[str, Any]) -> str:
    """Translate only guarded named binds, leaving PostgreSQL casts intact."""
    names: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        names.add(name)
        return f"%({name})s"

    translated = _PLACEHOLDER.sub(replace, sql)
    if names != set(parameters):
        raise ValidationError("SQL_PARAMETERS_CHANGED", "SQL parameters do not match the compiled statement")
    return translated


def explain_postgres(config: Mapping[str, Any], sql: str, parameters: Mapping[str, Any], *, timeout_ms: int) -> None:
    """Check the live relation and column shape without reading result rows."""
    credentials = config.get("credentials")
    if not isinstance(credentials, Mapping):
        raise BusinessDocumentError("SQL_SOURCE_UNAVAILABLE", "PostgreSQL source is unavailable", 503)
    try:
        with psycopg2.connect(
            host=config["host"], port=int(config["port"]), dbname=config["database"],
            user=credentials["username"], password=credentials["password"], connect_timeout=5,
        ) as connection:
            connection.set_session(readonly=True, autocommit=False)
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{timeout_ms}ms",))
                cursor.execute("EXPLAIN " + _dbapi_sql(sql, parameters), dict(parameters))
            connection.rollback()
    except (psycopg2.Error, KeyError, ValueError, TypeError) as exc:
        raise BusinessDocumentError("SQL_PHYSICAL_SCHEMA_INVALID", "Query does not match the live PostgreSQL schema or permissions", 422) from exc


def execute_postgres(
    config: Mapping[str, Any],
    sql: str,
    parameters: Mapping[str, Any],
    *,
    timeout_ms: int,
    max_rows: int,
    max_result_bytes: int,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Return a bounded result; caller owns authorization and persistence."""
    credentials = config.get("credentials")
    if not isinstance(credentials, Mapping):
        raise BusinessDocumentError("SQL_SOURCE_UNAVAILABLE", "PostgreSQL source is unavailable", 503)
    started = monotonic()
    cancel_requested = threading.Event()
    try:
        with psycopg2.connect(
            host=config["host"], port=int(config["port"]), dbname=config["database"],
            user=credentials["username"], password=credentials["password"],
            connect_timeout=5,
        ) as connection:
            connection.set_session(readonly=True, autocommit=False)
            stop_watcher = threading.Event()
            watcher = None
            if should_cancel is not None:
                def watch_cancel() -> None:
                    while not stop_watcher.wait(0.5):
                        try:
                            requested = should_cancel()
                        except Exception:
                            requested = True
                        if requested:
                            cancel_requested.set()
                            connection.cancel()
                            return

                watcher = threading.Thread(target=watch_cancel, daemon=True, name="sql-query-cancel")
                watcher.start()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{timeout_ms}ms",))
                    cursor.execute(_dbapi_sql(sql, parameters), dict(parameters))
                    if cursor.description is None:
                        raise BusinessDocumentError("SQL_RESULT_INVALID", "Query returned no table", 422)
                    columns = [column.name for column in cursor.description]
                    rows: list[list[Any]] = []
                    result_bytes = 0
                    while batch := cursor.fetchmany(min(100, max_rows + 1 - len(rows))):
                        for source in batch:
                            if len(rows) >= max_rows:
                                raise BusinessDocumentError("SQL_ROW_LIMIT_EXCEEDED", "Result exceeds the profile row limit", 422)
                            row = json.loads(json.dumps(list(source), default=str, ensure_ascii=False))
                            result_bytes += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                            if result_bytes > max_result_bytes:
                                raise BusinessDocumentError("SQL_RESULT_TOO_LARGE", "Result exceeds the profile byte limit", 422)
                            rows.append(row)
            finally:
                stop_watcher.set()
                if watcher is not None:
                    watcher.join(timeout=1)
            connection.rollback()
    except psycopg2.errors.QueryCanceled as exc:
        if cancel_requested.is_set():
            raise BusinessDocumentError("SQL_CANCELED", "Query was canceled", 409) from exc
        raise BusinessDocumentError("SQL_TIMEOUT", "Query exceeded the profile timeout", 408) from exc
    except (psycopg2.Error, KeyError, ValueError, TypeError) as exc:
        raise BusinessDocumentError("SQL_EXECUTION_FAILED", "PostgreSQL query failed", 502) from exc
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "result_bytes": result_bytes,
        "duration_ms": round((monotonic() - started) * 1000),
    }
