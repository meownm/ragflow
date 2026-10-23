"""Qualify the production blue/green controller with real Docker and nginx.

Only HTTP application responses are synthetic. No shared application stores,
ports or source mounts are used. The base image is the candidate under test.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deployment.blue_green import BlueGreen, IMAGE_CONTRACT, command, deployment_config, deployment_lock, write_json

CONTROLLER_SHA256 = hashlib.sha256((ROOT / "deployment/blue_green.py").read_bytes()).hexdigest()


@contextmanager
def traffic_pool(stop):
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        yield pool
    finally:
        stop.set()
        pool.shutdown(wait=True)


def qualify(image: str, output: Path) -> dict:
    if os.environ.get("RAGFLOW_TEST_DISPOSABLE") != "1":
        raise ValueError("RAGFLOW_TEST_DISPOSABLE=1 is required")
    output.mkdir(parents=True, exist_ok=True)
    # Check the actual candidate supports split startup before using stand-ins.
    contract = command("docker", "run", "--rm", "--network", "none", "--entrypoint", "python3", image, "-c", IMAGE_CONTRACT)
    revision = command("docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", image, "/ragflow/SOURCE_REVISION")
    if os.getenv("SOURCE_SHA") and revision != os.environ["SOURCE_SHA"]:
        raise ValueError("Blue/green test base image has a foreign source SHA")
    project = "rf-bg-" + uuid4().hex[:16]
    network = project + "-network"
    images = []
    report = {
        "schema": 1,
        "purpose": "blue-green-http-transition",
        "status": "FAIL",
        "source_sha": revision,
        "image_digest": image,
        "schema_sha256": contract,
        "project": project,
        "scope": "Real production controller/Docker/nginx with synthetic HTTP, worker and health responses; no live app/DB/model acceptance",
    }
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    stop = threading.Event()
    requests, failures = [], []
    phase = "before-switch"
    controller = None
    legacy = project + "-ragflow-cpu-1"

    def request(path="/", data=None):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", data=data, timeout=10) as response:
            return response.read()

    def traffic():
        while not stop.is_set():
            try:
                body = request()
                assert body in (b"v1", b"v2")
                requests.append(body.decode())
            except Exception as error:
                failures.append({"phase": phase, "error": type(error).__name__, "status": getattr(error, "code", None)})
            stop.wait(0.03)

    try:
        command("docker", "network", "create", "--label", f"org.ragflow.blue-green={project}", network)
        with tempfile.TemporaryDirectory(prefix="blue-green-context-", dir=output) as directory:
            context = Path(directory)
            shutil.copy2(ROOT / "test/integration/fixtures/blue_green_http.py", context / "server.py")
            for version in ("v1", "v2"):
                (context / "Dockerfile").write_text(
                    f"FROM {image}\nCOPY server.py /fixture/server.py\nENV FIXTURE_VERSION={version}\n"
                    f"RUN mkdir -p /ragflow/web/dist/assets && printf {version} > /ragflow/web/dist/assets/fixture-{version}.js\n"
                    'ENTRYPOINT ["python3", "/fixture/server.py"]\n',
                    encoding="utf-8",
                )
                tag = f"{project}:{version}"
                images.append(tag)
                command("docker", "build", "--network", "none", "-t", tag, str(context), timeout=300)
        ids = [command("docker", "image", "inspect", "--format", "{{.Id}}", name) for name in images]
        health = {"test": ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1/api/v1/system/healthz')"], "interval": "1s", "timeout": "2s", "retries": 2}
        base = {"services": {"ragflow-cpu": {"image": ids[0], "networks": {"app": {}}, "environment": {}, "healthcheck": health}}, "networks": {"app": {"name": network}}, "volumes": {}}
        config = deployment_config(base, project, ids[0])
        config["services"]["gateway"]["ports"][0].update(published=str(port), host_ip="127.0.0.1")
        runtime = output / "runtime"
        controller = BlueGreen(project, runtime, timeout=40, port=port)
        # The first migration is deliberately outside the availability measurement.
        command("docker", "run", "-d", "--name", legacy, "--label", f"com.docker.compose.project={project}", "--network", network, ids[0])
        with deployment_lock(runtime):
            controller.deploy(config, initialize=True)
        gateway_id = controller.inspect("gateway")["Id"]
        worker_id = controller.inspect("worker")["Id"]
        assert request() == b"v1"
        with traffic_pool(stop) as pool:
            polling = pool.submit(traffic)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream", timeout=20) as stream:
                assert stream.readline() == b"data: v1:start\n"
                candidate = deployment_config(base, project, ids[1])
                with deployment_lock(runtime):
                    controller.deploy(candidate)
                phase = "candidate-active"
                assert b"data: v1:finish" in stream.read(), "In-flight SSE was truncated"
            assert request() == b"v2"
            assert controller.inspect("gateway")["Id"] == gateway_id, "Gateway was recreated"
            assert controller.inspect("worker")["Id"] != worker_id
            assert request("/assets/fixture-v1.js") == b"v1", "Old lazy-loaded assets disappeared"
            assert request("/assets/fixture-v2.js") == b"v2"
            assert request("/echo", b"unique-write") == b"unique-write"
            active = controller.state_path.read_bytes()
            unready = copy.deepcopy(candidate)
            unready["services"]["web-blue"]["healthcheck"]["test"] = ["CMD", "python3", "-c", "raise SystemExit(1)"]
            controller.timeout = 5
            phase = "reject-unready"
            try:
                with deployment_lock(runtime):
                    controller.deploy(unready)
            except (RuntimeError, TimeoutError):
                pass
            else:
                raise AssertionError("Unready candidate was accepted")
            assert controller.state_path.read_bytes() == active
            assert request() == b"v2"
            controller.timeout = 40
            previous_state = json.loads(active)
            real_wait_route = controller.wait_route
            injected = False

            def reject_post_switch(token):
                nonlocal injected
                if token != previous_state["token"] and not injected:
                    injected = True
                    raise TimeoutError("Injected post-switch readiness failure")
                return real_wait_route(token)

            controller.wait_route = reject_post_switch
            phase = "post-switch-rollback"
            try:
                with deployment_lock(runtime):
                    controller.deploy(config)
            except TimeoutError:
                pass
            else:
                raise AssertionError("Post-switch failure was not rejected")
            finally:
                controller.wait_route = real_wait_route
            assert injected
            assert request() == b"v2"
            assert controller.state_path.read_bytes() == active
            write_json(controller.journal, {"previous": previous_state, "stage": "interrupted-after-switch"})
            phase = "interrupted-recovery"
            controller.route("web-blue", uuid4().hex)
            with deployment_lock(runtime):
                controller.recover()
            assert request() == b"v2"
            assert controller.state_path.read_bytes() == active
            assert not controller.journal.exists()
            stop.set()
            polling.result(timeout=10)
            assert not failures, f"Traffic failed during deployment: {failures}"
            assert {"v1", "v2"} <= set(requests)
        if hashlib.sha256((ROOT / "deployment/blue_green.py").read_bytes()).hexdigest() != CONTROLLER_SHA256:
            raise ValueError("Controller source changed during qualification")
        report.update(
            status="PASS",
            requests=len(requests),
            failed_requests=len(failures),
            sse_drain=True,
            old_assets=True,
            unready_rejected=True,
            gateway_unchanged=True,
            post_switch_rollback=True,
            interrupted_recovery=True,
            controller_sha256=CONTROLLER_SHA256,
        )
    except BaseException as error:
        report.update(reason=type(error).__name__, requests=len(requests), failed_requests=len(failures), failures=failures)
        raise
    finally:
        stop.set()
        errors = []
        log = subprocess.run(["docker", "logs", "--tail", "100", f"{project}-gateway"], capture_output=True, timeout=30)
        (output / "gateway.log").write_bytes(log.stdout + log.stderr)
        # Resource identity is checked before every removal, even on test failure.
        for name in ("worker", "gateway", "web-blue", "web-green", "ragflow-cpu-1"):
            target = f"{project}-{name}"
            inspection = subprocess.run(["docker", "inspect", target], capture_output=True, text=True, timeout=30)
            if inspection.returncode:
                if "No such" not in inspection.stderr:
                    errors.append(target)
                continue
            info = json.loads(inspection.stdout)[0]
            labels = info["Config"].get("Labels", {})
            if labels.get("org.ragflow.blue-green") != project and not (target == legacy and labels.get("com.docker.compose.project") == project):
                errors.append(target)
                continue
            command("docker", "rm", "-f", target)
        for name in command("docker", "volume", "ls", "-q", "--filter", f"label=org.ragflow.blue-green={project}").splitlines():
            command("docker", "volume", "rm", name)
        if command("docker", "network", "inspect", "--format", '{{index .Labels "org.ragflow.blue-green"}}', network) != project:
            errors.append(network)
        else:
            command("docker", "network", "rm", network)
        for image_name in images:
            command("docker", "image", "rm", image_name)
        report["cleanup"] = "FAIL" if errors else "PASS"
        if errors:
            report["status"] = "FAIL"
        write_json(output / "blue-green.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if qualify(args.image, args.output)["status"] == "PASS" else 1)
