"""OpenMetadata login over disposable loopback HTTP; no shared catalog or credentials."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def login_server():
    state = SimpleNamespace(delay=0.0, status=200, logins=0, reads=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_POST(self):
            assert self.path == "/api/v1/users/login"
            self.rfile.read(int(self.headers["Content-Length"]))
            state.logins += 1
            delay, status = state.delay, state.status
            time.sleep(delay)
            self.reply(status, {"accessToken": "test-token"})

        def do_GET(self):
            state.reads += 1
            if self.headers.get("Authorization") != "Bearer test-token":
                self.reply(401, {})
                return
            self.reply(200, {"data": [{"id": "test-table"}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def client(login_server, monkeypatch):
    origin, _state = login_server
    path = Path(__file__).resolve().parents[2] / "api/apps/services/openmetadata_copilot_service.py"
    spec = importlib.util.spec_from_file_location("openmetadata_login_transport", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setenv("OPENMETADATA_URL", origin)
    monkeypatch.setenv("OPENMETADATA_USERNAME", "test@example.test")
    monkeypatch.setenv("OPENMETADATA_PASSWORD", "test-password")
    monkeypatch.delenv("OPENMETADATA_JWT_TOKEN", raising=False)
    monkeypatch.delenv("OPENMETADATA_LOGIN_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("OPENMETADATA_TIMEOUT_SECONDS", "1")
    instance = module.OpenMetadataClient(module.OpenMetadataConfig.from_env())
    try:
        yield module, instance
    finally:
        instance._session.close()


def test_slow_login_serves_concurrent_catalog_requests_once(client, login_server):
    _module, instance = client
    _origin, state = login_server
    state.delay = 32
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: instance.get("/api/v1/tables"), range(2)))
    assert results == [{"data": [{"id": "test-table"}]}] * 2
    assert state.logins == 1
    assert state.reads == 2


def test_login_timeout_allows_recovery_without_caching_a_failed_token(client, login_server):
    from dataclasses import replace

    module, instance = client
    _origin, state = login_server
    instance.config = replace(instance.config, timeout_seconds=0.1, login_timeout_seconds=0.1)
    state.delay = 0.5
    with pytest.raises(module.OpenMetadataError) as error:
        instance.get("/api/v1/tables")
    assert not isinstance(error.value, module.OpenMetadataAuthenticationError)
    assert state.logins == 1
    assert state.reads == 0
    assert instance._token == ""

    state.delay = 0
    assert instance.get("/api/v1/tables") == {"data": [{"id": "test-table"}]}
    assert state.logins == 2
    assert state.reads == 1


@pytest.mark.parametrize("status", [401, 503])
def test_login_rejection_and_server_failure(client, login_server, status):
    module, instance = client
    _origin, state = login_server
    state.status = status
    with pytest.raises(module.OpenMetadataError) as error:
        instance.get("/api/v1/tables")
    assert isinstance(error.value, module.OpenMetadataAuthenticationError) == (status == 401)
    assert state.logins == 1
    assert state.reads == 0
