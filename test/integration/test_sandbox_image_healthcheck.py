"""Run Compose and Linux healthchecks against disposable, isolated images."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from uuid import uuid4

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
ERROR_SERVER = """
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503)
        self.end_headers()
    def log_message(self, *_args):
        pass
HTTPServer(('127.0.0.1', 9385), Handler).serve_forever()
"""


def _docker(*arguments, env=None):
    result = subprocess.run(["docker", *arguments], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip()


@pytest.mark.p0
@pytest.mark.parametrize("source_path", ["docker/docker-compose-base.yml", "agent/sandbox/docker-compose.yml", "deployment/linux-pg/install.sh", "deployment/linux-pg/upgrade.sh"])
@pytest.mark.parametrize("endpoint", ["manager", "http-error", "closed"])
def test_manager_image_healthcheck(source_path, endpoint):
    if os.environ.get("RAGFLOW_SANDBOX_IMAGE_TEST") != "1":
        pytest.skip("Requires explicitly enabled disposable Docker sandbox image test")
    image = os.environ.get("RAGFLOW_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.fail("RAGFLOW_SANDBOX_TEST_IMAGE must identify the prepared image")
    _docker("image", "inspect", image, "--format", "{{.Id}}")
    source = (ROOT / source_path).read_text(encoding="utf-8")
    if source_path.endswith(".sh"):
        line = next(line for line in source.splitlines() if line.startswith('sudo docker exec "${SANDBOX_CONTAINER}" '))
        arguments = shlex.split(line)
        assert arguments[-1] == ">/dev/null", line
        healthcheck = ["CMD", *arguments[4:-1]]
    else:
        config = yaml.safe_load(source)
        healthcheck = config["services"]["sandbox-executor-manager"]["healthcheck"]["test"]
    if healthcheck[0] == "CMD":
        command = shlex.join(healthcheck[1:])
    else:
        assert healthcheck[0] == "CMD-SHELL", healthcheck
        command = healthcheck[1]
    name = f"ragflow-health-test-{uuid4().hex[:16]}"
    arguments = [
        "run",
        "-d",
        "--pull=never",
        "--name",
        name,
        "--label",
        "codex.task=sandbox-healthcheck",
        "--network",
        "none",
        "--env",
        "SANDBOX_EXECUTOR_MANAGER_POOL_SIZE=0",
        "--health-cmd",
        command,
        "--health-interval=1s",
        "--health-timeout=6s",
        "--health-retries=2",
    ]
    if endpoint != "manager":
        arguments += ["--entrypoint", "python"]
    arguments.append(image)
    if endpoint == "http-error":
        arguments += ["-c", ERROR_SERVER]
    elif endpoint == "closed":
        arguments += ["-c", "import time; time.sleep(90)"]
    expected = "healthy" if endpoint == "manager" else "unhealthy"
    try:
        _docker(*arguments)
        deadline = time.monotonic() + 35
        while True:
            state = json.loads(_docker("inspect", "--format", "{{json .State}}", name))
            assert state["Running"], _docker("logs", name)
            health = state["Health"]
            matches = health["Status"] == expected
            if endpoint == "http-error":
                matches = matches and bool(health["Log"]) and "503" in health["Log"][-1]["Output"]
            if matches:
                break
            assert time.monotonic() < deadline, health
            time.sleep(0.25)
        assert health["Log"][-1]["ExitCode"] == (0 if endpoint == "manager" else 1), health
    finally:
        _docker("rm", "-f", name)


@pytest.mark.parametrize("linux", [False, True])
def test_sandbox_image_overrides_reach_compose(linux):
    if os.environ.get("RAGFLOW_SANDBOX_IMAGE_TEST") != "1":
        pytest.skip("Requires explicitly enabled Docker Compose test")
    environment = {
        **os.environ,
        "SANDBOX_BASE_PYTHON_IMAGE": "registry.example.test/python:approved",
        "SANDBOX_BASE_NODEJS_IMAGE": "registry.example.test/node:approved",
    }
    arguments = ["compose", "--env-file", str(ROOT / "docker/.env")]
    paths = ["docker-compose.yml", "docker-compose.local.yml"]
    if linux:
        paths.append("docker-compose.linux.local.yml")
    for path in paths:
        arguments += ["-f", str(ROOT / "docker" / path)]
    arguments += ["--profile", "sandbox", "config", "--format", "json"]
    config = json.loads(_docker(*arguments, env=environment))
    manager = config["services"]["sandbox-executor-manager"]["environment"]
    for key in ("SANDBOX_BASE_PYTHON_IMAGE", "SANDBOX_BASE_NODEJS_IMAGE"):
        assert manager[key] == environment[key]
