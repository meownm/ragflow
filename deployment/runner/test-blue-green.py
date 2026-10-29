"""Qualify the production blue/green controller with real Docker and nginx.

Only HTTP application responses are synthetic. No shared application stores,
ports or source mounts are used. The base image is the candidate under test.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import argparse
import copy
import gzip
import hashlib
from ipaddress import ip_network
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deployment.blue_green import COPY_ASSETS, BlueGreen, IMAGE_CONTRACT, command, deployment_config, deployment_lock, write_json
from tools.quality.artifact_lifecycle import current, docker_args, managed, note

CONTROLLER_SHA256 = hashlib.sha256((ROOT / "deployment/blue_green.py").read_bytes()).hexdigest()


@contextmanager
def traffic_pool(stop):
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        yield pool
    finally:
        stop.set()
        pool.shutdown(wait=True)


def _available_subnet():
    """Keep QA independent of exhausted default pools, without touching other networks."""
    identities = command("docker", "network", "ls", "-q").split()
    networks = json.loads(command("docker", "network", "inspect", *identities))
    used = [ip_network(row["Subnet"]) for network in networks for row in (network.get("IPAM", {}).get("Config") or []) if row.get("Subnet") and ":" not in row["Subnet"]]
    start = int(uuid4().hex[:2], 16)
    for offset in range(256):
        candidate = ip_network(f"10.254.{(start + offset) % 256}.0/24")
        if not any(candidate.overlaps(existing) for existing in used):
            return str(candidate)
    raise ValueError("No non-overlapping QA subnet available in 10.254.0.0/16")


def check_dns_recovery(controller, network, occupying_image, request):
    """Force real address churn and a cold gateway boot, without controller reload."""
    original_state = controller.state_path.read_bytes()
    state = json.loads(original_state)
    slot = state["slot"]
    gateway = controller.inspect("gateway")
    worker_id = controller.inspect("worker")["Id"]
    configuration = controller.execute("gateway", "sha256sum", "/routing/nginx.conf")
    changes = []
    for index, restart_gateway in enumerate((True, False)):
        active = controller.inspect(slot)
        old_address = active["NetworkSettings"]["Networks"][network]["IPAddress"]
        controller.compose("stop", slot)
        command("docker", "rm", f"{controller.project}-{slot}")
        # Occupy the old IP with a different version and the generic service alias.
        # Routing by a cached IP or ambiguous 'web-green' must not pass this check.
        command(
            "docker",
            "run",
            "-d",
            "--name",
            f"{controller.project}-old-ip-{index}",
            "--network",
            network,
            "--network-alias",
            slot,
            "--ip",
            old_address,
            occupying_image,
        )
        if restart_gateway:
            command("docker", "restart", f"{controller.project}-gateway")
            assert controller.inspect("gateway")["State"]["Status"] == "running"
            try:
                request()
            except urllib.error.HTTPError as error:
                assert error.code == 502, "Missing selected DNS target must be unavailable"
            else:
                raise AssertionError("Gateway served another container while the selected name was absent")
        started = controller.inspect("gateway")["State"]["StartedAt"]
        controller.up(slot)
        ready = controller.wait_ready(slot)
        new_address = ready["NetworkSettings"]["Networks"][network]["IPAddress"]
        assert old_address != new_address, "Recreation must actually change the IP"
        deadline = time.monotonic() + 20
        began = time.monotonic()
        stable = 0
        while stable < 12:
            try:
                stable = stable + 1 if request() == b"v2" else 0
            except (OSError, urllib.error.URLError):
                stable = 0
            if time.monotonic() >= deadline:
                raise AssertionError("DNS route did not stabilize automatically after address change")
            time.sleep(0.2)
        with urllib.request.urlopen(f"http://127.0.0.1:{controller.port}/api/v1/system/healthz", timeout=5) as response:
            assert response.headers["X-Ragflow-Deployment"] == state["token"]
        assert controller.inspect("gateway")["Id"] == gateway["Id"]
        assert controller.inspect("gateway")["State"]["StartedAt"] == started
        assert controller.execute("gateway", "sha256sum", "/routing/nginx.conf") == configuration
        assert controller.state_path.read_bytes() == original_state
        changes.append({"old_address": old_address, "new_address": new_address, "gateway_restarted_before_target": restart_gateway, "recovered_seconds": round(time.monotonic() - began, 3)})
    assert controller.inspect("worker")["Id"] == worker_id
    uri = "/request-target/a%2Fb?x=a%2Fb&x=two%20words"
    assert request(uri) == uri.encode(), "Variable proxy_pass changed the original URI/query"
    assert request("/echo", b"write-after-address-change") == b"write-after-address-change"
    return changes


@managed("blue-green-qa", days=0)
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
    base_tag = f"{project}:base"
    images = [base_tag]
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
        subnet = _available_subnet()
        command("docker", "network", "create", "--subnet", subnet, "--label", f"org.ragflow.blue-green={project}", network)
        report["network_subnet"] = subnet
        # BuildKit interprets a bare sha256 image ID as a registry repository.
        # A disposable local tag keeps FROM tied to the already inspected image.
        base_id = command("docker", "image", "inspect", "--format", "{{.Id}}", image)
        command("docker", "image", "tag", base_id, base_tag)
        with tempfile.TemporaryDirectory(prefix="blue-green-context-", dir=output) as directory:
            context = Path(directory)
            shutil.copy2(ROOT / "test/integration/fixtures/blue_green_http.py", context / "server.py")
            for version in ("v1", "v2"):
                (context / "Dockerfile").write_text(
                    f"FROM {base_tag}\nCOPY server.py /fixture/server.py\nENV FIXTURE_VERSION={version}\n"
                    f"RUN mkdir -p /release-assets /routing /ragflow/logs /ragflow/web/dist/assets /ragflow/web/dist/entry/js /ragflow/web/dist/chunk/js && "
                    f"printf {version} > /ragflow/web/dist/assets/fixture-{version}.js && "
                    f"printf {version} > /ragflow/web/dist/entry/js/fixture-{version}.js && "
                    f"printf {version} > /ragflow/web/dist/chunk/js/fixture-{version}.js && "
                    "python3 -c \"from pathlib import Path; Path('/ragflow/web/dist/assets/compressible.css').write_text('body { color: black; }\\n' * 1000)\"\n"
                    'ENTRYPOINT ["python3", "/fixture/server.py"]\n',
                    encoding="utf-8",
                )
                tag = f"{project}:{version}"
                images.append(tag)
                build = subprocess.run(docker_args(["docker", "build", "--network", "none", "-t", tag, str(context)]), capture_output=True, timeout=300)
                (output / f"build-{version}.log").write_bytes(build.stdout + build.stderr)
                if build.returncode:
                    raise RuntimeError(f"Fixture build failed; see build-{version}.log")
        ids = [command("docker", "image", "inspect", "--format", "{{.Id}}", name) for name in images[1:]]
        health = {"test": ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1/api/v1/system/healthz')"], "interval": "1s", "timeout": "2s", "retries": 2}
        base = {"services": {"ragflow-cpu": {"image": ids[0], "networks": {"app": {}}, "environment": {}, "healthcheck": health}}, "networks": {"app": {"name": network}}, "volumes": {}}
        config = deployment_config(base, project, ids[0])
        # Mount targets are built into the fixture; its process scratch is ephemeral.
        # Empty mount-point directories must not look like unique writable evidence.
        config["services"]["gateway"]["tmpfs"] = ["/tmp"]
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
        # Simulate a volume created before retention covered entry/chunk JavaScript.
        controller.execute("web-blue", "python3", "-c", "import shutil; shutil.rmtree('/release-assets/entry'); shutil.rmtree('/release-assets/chunk')")
        with traffic_pool(stop) as pool:
            polling = pool.submit(traffic)
            candidate = deployment_config(base, project, ids[1])
            active_before_copy = controller.state_path.read_bytes()
            real_execute = controller.execute
            copy_interrupted = False

            def interrupt_asset_copy(role, *args):
                nonlocal copy_interrupted
                if role == "web-green" and args == ("python3", "-c", COPY_ASSETS):
                    copy_interrupted = True
                    interrupted = (
                        """
import os,pathlib,shutil,signal
real_copy=shutil.copyfile
def partial_copy(src,dst):
    if str(src).endswith('/chunk/js/fixture-v2.js'):
        pathlib.Path(dst).write_bytes(b'v')
        os.kill(os.getpid(),signal.SIGKILL)
    return real_copy(src,dst)
shutil.copyfile=partial_copy
"""
                        + COPY_ASSETS
                    )
                    return real_execute(role, "python3", "-c", interrupted)
                return real_execute(role, *args)

            controller.execute = interrupt_asset_copy
            phase = "interrupted-asset-copy"
            try:
                with deployment_lock(runtime):
                    controller.deploy(candidate)
            except RuntimeError:
                pass
            else:
                raise AssertionError("Interrupted asset copy was accepted")
            finally:
                controller.execute = real_execute
            assert copy_interrupted
            assert controller.state_path.read_bytes() == active_before_copy
            assert not controller.journal.exists()
            assert request() == b"v1"
            try:
                request("/chunk/js/fixture-v2.js")
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("Partial JavaScript was published")
            controller.execute(
                "web-green",
                "python3",
                "-c",
                "from pathlib import Path; root=Path('/release-assets/chunk/js'); assert any(p.stat().st_size==1 for p in root.glob('.ragflow-asset-*.tmp'))",
            )
            phase = "retry-after-asset-copy"
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream", timeout=20) as stream:
                assert stream.readline() == b"data: v1:start\n"
                with deployment_lock(runtime):
                    controller.deploy(candidate)
                phase = "candidate-active"
                assert b"data: v1:finish" in stream.read(), "In-flight SSE was truncated"
            assert request() == b"v2"
            assert controller.inspect("gateway")["Id"] == gateway_id, "Gateway was recreated"
            assert controller.inspect("worker")["Id"] != worker_id
            for prefix in ("assets", "entry/js", "chunk/js"):
                for version in ("v1", "v2"):
                    path = f"/{prefix}/fixture-{version}.js"
                    assert request(path) == version.encode(), f"Versioned file disappeared: {path}"
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as response:
                        assert response.headers.get_content_type() == "application/javascript"
                        assert "max-age=31536000" in response.headers.get("Cache-Control", "")
                try:
                    request(f"/{prefix}/missing.js")
                except urllib.error.HTTPError as error:
                    assert error.code == 404
                else:
                    raise AssertionError(f"Missing {prefix} file received SPA fallback")
            compressed_request = urllib.request.Request(f"http://127.0.0.1:{port}/assets/compressible.css", headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(compressed_request, timeout=10) as response:
                compressed = response.read()
                assert response.headers.get("Content-Encoding") == "gzip"
                assert response.headers.get("Vary") == "Accept-Encoding"
                assert gzip.decompress(compressed) == b"body { color: black; }\n" * 1000
                assert len(compressed) < 1000
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
        dns_changes = check_dns_recovery(controller, network, ids[0], request)
        if hashlib.sha256((ROOT / "deployment/blue_green.py").read_bytes()).hexdigest() != CONTROLLER_SHA256:
            raise ValueError("Controller source changed during qualification")
        report.update(
            status="PASS",
            requests=len(requests),
            failed_requests=len(failures),
            sse_drain=True,
            old_assets=True,
            old_javascript=True,
            legacy_asset_volume=True,
            missing_assets_404=True,
            static_gzip=True,
            unready_rejected=True,
            gateway_unchanged=True,
            post_switch_rollback=True,
            interrupted_recovery=True,
            interrupted_asset_copy_retry=True,
            automatic_dns_recovery=True,
            gateway_boot_without_web=True,
            exact_project_dns=True,
            request_target_preserved=True,
            dns_address_changes=dns_changes,
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
        try:
            cleanup = current().retire_owned()
            errors.extend(cleanup["errors"])
            errors.extend(cleanup["blocked"])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            errors.append(type(error).__name__)
        report["cleanup"] = "FAIL" if errors else "PASS"
        if errors:
            report["cleanup_errors"] = errors
            report["status"] = "FAIL"
        write_json(output / "blue-green.json", report)
        note(output / "blue-green.json", receipt=True)
        note(output)
        if report["status"] == "PASS" and not errors:
            current().close_consumption("blue-green-qa", exit_code=0, cleanup=cleanup, receipt=output / "blue-green.json")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if qualify(args.image, args.output)["status"] == "PASS" else 1)
