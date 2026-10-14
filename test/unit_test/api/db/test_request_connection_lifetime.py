"""Request teardown must not close a cursor owned by another thread."""

import ast
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
from threading import Event
import time

from playhouse.pool import PooledSqliteDatabase


def test_request_teardown_preserves_another_threads_active_cursor(tmp_path):
    source = Path(__file__).resolve().parents[4] / "api/db/db_models.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "close_connection")
    database = PooledSqliteDatabase(tmp_path / "requests.db", max_connections=2, check_same_thread=False)
    namespace = {"DB": database, "logging": logging}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    close_request = namespace["close_connection"]
    ready = Event()
    release = Event()

    def read_document():
        try:
            cursor = database.execute_sql("SELECT 1")
            # Age the checkout without waiting 30 seconds. The cursor remains active.
            with database._pool_lock:
                key = database.conn_key(database.connection())
                connection = database._in_use[key]
                database._in_use[key] = connection._replace(checked_out=time.time() - 60)
            ready.set()
            assert release.wait(10)
            return cursor.fetchone()
        finally:
            database.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(read_document)
            try:
                assert ready.wait(10)
                database.execute_sql("SELECT 2").fetchone()
                close_request()
            finally:
                release.set()
            assert result.result(timeout=10) == (1,)
            assert database.is_closed()
    finally:
        database.close_all()
