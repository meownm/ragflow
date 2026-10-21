"""Exercise the local SPA server's connection lifecycle and API isolation."""

from functools import partial
from http.client import HTTPConnection
from pathlib import Path
import runpy
import sys
from threading import Thread

import pytest


@pytest.fixture
def spa_server(tmp_path):
    runner = runpy.run_path(str(Path(__file__).resolve().parents[3] / "test/run_browser_regression.py"))

    class RecordingServer(runner["SPAServer"]):
        accepted_connections = 0

        def get_request(self):
            connection = super().get_request()
            self.accepted_connections += 1
            return connection

    (tmp_path / "index.html").write_bytes(b"<title>SPA fixture</title>")
    (tmp_path / "chunk.js").write_bytes(b"export const fixture = 1;")
    server = RecordingServer(("127.0.0.1", 0), partial(runner["SPAHandler"], directory=str(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        yield server, client
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_assets_and_spa_navigation_reuse_one_connection(spa_server):
    server, client = spa_server
    for path, body in [("/chunk.js", b"export const fixture = 1;"), ("/chat/session", b"<title>SPA fixture</title>"), ("/chunk.js", b"export const fixture = 1;")]:
        client.request("GET", path)
        response = client.getresponse()
        assert response.status == 200
        assert int(response.getheader("Content-Length")) == len(body)
        assert response.read() == body
    assert server.accepted_connections == 1, "Each chunk must not consume a new TCP connection"


@pytest.mark.parametrize("path", ["/api/v1/users/me", "/v1/user/login"])
def test_unmocked_api_never_returns_spa_content(spa_server, path):
    _server, client = spa_server
    client.request("GET", path)
    response = client.getresponse()
    assert response.status == 503
    assert b"SPA fixture" not in response.read()


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("explicit_output", [False, True])
def test_runner_supplies_artifact_directory_and_build_identity_without_replacing_explicit_paths(tmp_path, monkeypatch, explicit, explicit_output):
    import subprocess

    runner = runpy.run_path(str(Path(__file__).resolve().parents[3] / "test/run_browser_regression.py"))
    (tmp_path / "index.html").write_text("<title>SPA fixture</title>", encoding="utf-8")
    extra = ["--", "--output", str(tmp_path / "explicit-output")] if explicit_output else []
    monkeypatch.setattr(sys, "argv", ["run_browser_regression.py", "--browser", "firefox", "--dist-root", str(tmp_path), "--suite", "test/playwright/e2e/test_sql_workspace_ui.py", *extra])
    if explicit:
        monkeypatch.setenv("PW_ARTIFACTS_DIR", str(tmp_path / "evidence"))
    else:
        monkeypatch.delenv("PW_ARTIFACTS_DIR", raising=False)
    captured = []

    def execute(command, *, cwd, env):
        captured.append({**env, "pytest_command": command})
        target = env["RAGFLOW_BASE_URL"]
        from urllib.request import urlopen
        with urlopen(target + "/sql-projects", timeout=3) as response:
            assert response.read() == b"<title>SPA fixture</title>"
        return 7

    monkeypatch.setattr(subprocess, "call", execute)
    assert runner["main"]() == 7
    assert captured[0]["PW_BROWSER"] == "firefox"
    assert captured[0]["SQL_BROWSER_BUILD_ROOT"] == str(tmp_path.resolve())
    expected = tmp_path / "evidence" if explicit else runner["ROOT"] / "test/playwright/artifacts/firefox"
    assert Path(captured[0]["PW_ARTIFACTS_DIR"]) == expected
    command = captured[0]["pytest_command"]
    assert command.count("--output") == 1
    assert Path(command[command.index("--output") + 1]) == (tmp_path / "explicit-output" if explicit_output else expected / "playwright")
