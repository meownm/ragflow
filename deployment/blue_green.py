"""App-only blue/green deployment on an existing Compose infrastructure.

The gateway and its listening socket survive deployments. Two immutable web
slots share the existing stores; only one unpublished worker runs schedulers.
Schema changes are deliberately refused: maintenance owns migrations.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request


SCHEMA_IDENTITY = """
import hashlib,json,pathlib
root=pathlib.Path('/ragflow')
paths=set()
for base in ('api','admin'):
    for p in (root/base).rglob('*.py'):
        if 'models' in p.stem or 'migration' in str(p.relative_to(root)):
            paths.add(p)
for name in ('api/db/init_data.py','api/db/db_utils.py','api/db/__init__.py'):
    paths.add(root/name)
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
print(hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest())
"""

IMAGE_CONTRACT = (
    """
import pathlib
root=pathlib.Path('/ragflow')
for name in ('entrypoint.sh','api/ragflow_server.py','admin/server/admin_server.py'):
    text=(root/name).read_text()
    assert 'RAGFLOW_SKIP_DATABASE_INIT' in text, 'Image lacks blue/green startup support'
    if name.endswith('.py'):
        assert 'RAGFLOW_BACKGROUND_ENABLED' in text, 'Image lacks web-only role'
"""
    + SCHEMA_IDENTITY
)

COPY_ASSETS = """
import hashlib,pathlib,shutil,tempfile
root=pathlib.Path('/ragflow/web/dist')
target=pathlib.Path('/release-assets')
for name in ('assets','entry','chunk'):
    source=root/name
    assert source.is_dir() and not source.is_symlink(), 'Image has no regular Vite '+name+' directory'
    destination=target if name=='assets' else target/name
    for p in source.rglob('*'):
        assert not p.is_symlink(), 'Asset links are forbidden'
        if p.is_file():
            dest=destination/p.relative_to(source)
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                assert hashlib.sha256(dest.read_bytes()).digest()==hashlib.sha256(p.read_bytes()).digest(), 'Asset name collision'
            else:
                # Stage on the same filesystem; readers only see complete assets.
                with tempfile.NamedTemporaryFile(prefix='.ragflow-asset-',suffix='.tmp',dir=dest.parent,delete=False) as stream:
                    temporary=pathlib.Path(stream.name)
                try:
                    shutil.copyfile(p,temporary)
                    temporary.chmod(0o644)
                    temporary.replace(dest)
                finally:
                    temporary.unlink(missing_ok=True)
"""

DRAINED = """
import pathlib,sys
for p in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try: value=p.read_bytes()
    except (FileNotFoundError,ProcessLookupError,PermissionError): continue
    if value.startswith(b'nginx: worker process is shutting down'): sys.exit(1)
"""


def command(*args: str, data: bytes | None = None, timeout: int = 120) -> str:
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        # Compose config and environment can contain credentials. Never echo them.
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout.decode().strip()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def deployment_lock(root: Path):
    """OS releases the exclusive lock on failure, including abrupt termination."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "deployment.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def gateway_config(slot: str, token: str, address: str) -> str:
    if slot not in {"web-blue", "web-green"} or not re.fullmatch("[a-f0-9]{32}", token):
        raise ValueError("Invalid route identity")
    address = str(IPv4Address(address))
    return f"""worker_processes 2;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events {{ worker_connections 2048; }}
http {{
    include /etc/nginx/mime.types;
    access_log off;
    sendfile on;
    gzip on;
    gzip_min_length 1024;
    gzip_comp_level 5;
    gzip_types text/plain text/css application/javascript application/json application/xml image/svg+xml;
    gzip_vary on;
    map $http_upgrade $connection_upgrade {{ default upgrade; '' ''; }}
    server {{
        listen 80;
        client_max_body_size 0;
        add_header X-Ragflow-Deployment {token} always;
        location ^~ /assets/ {{
            alias /release-assets/;
            expires 1y;
        }}
        location ^~ /entry/ {{
            alias /release-assets/entry/;
            expires 1y;
        }}
        location ^~ /chunk/ {{
            alias /release-assets/chunk/;
            expires 1y;
        }}
        location / {{
            proxy_pass http://{address}:80;
            proxy_http_version 1.1;
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
            proxy_next_upstream off;
        }}
    }}
}}
"""


def deployment_config(base: dict, project: str, image: str) -> dict:
    """Preserve resolved infrastructure names; never import their service definitions."""
    if not re.fullmatch("[a-z0-9][a-z0-9_-]{0,45}", project):
        raise ValueError("Invalid project name")
    if not re.fullmatch(r"(?:sha256:|[A-Za-z0-9_.:/-]+@sha256:)[a-f0-9]{64}", image):
        raise ValueError("An immutable image ID or registry digest is required")
    app = copy.deepcopy(base["services"]["ragflow-cpu"])
    if not app.get("healthcheck", {}).get("test") or app["healthcheck"].get("disable"):
        raise ValueError("A readiness healthcheck is required")
    if app.get("environment", {}).get("API_PROXY_SCHEME", "python") != "python":
        raise ValueError("Blue/green currently supports the Python API profile only")
    allowed = {"/ragflow/logs", "/ragflow/conf/service_conf.yaml.template"}
    for mount in app.get("volumes", []):
        if mount["type"] == "bind" and mount["target"] not in allowed:
            raise ValueError("Blue/green requires immutable code and SPA, without development mounts")
    for key in ("container_name", "ports", "depends_on", "profiles", "build", "env_file", "hostname"):
        app.pop(key, None)
    app["image"] = image
    app.setdefault("environment", {})["RAGFLOW_SKIP_DATABASE_INIT"] = "true"
    app["environment"].setdefault("API_PROXY_SCHEME", "python")
    app["environment"]["WAIT_FOR_BACKEND_BEFORE_NGINX"] = "true"
    services = {}
    for slot in ("web-blue", "web-green", "worker"):
        service = copy.deepcopy(app)
        service["container_name"] = f"{project}-{slot}"
        service["labels"] = {"org.ragflow.blue-green": project, "org.ragflow.role": slot}
        service["environment"]["RAGFLOW_BACKGROUND_ENABLED"] = "true" if slot == "worker" else "false"
        if slot != "worker":
            service["command"] = ["--enable-adminserver", "--disable-taskexecutor", "--disable-datasync"]
        # Logs are per-instance. A shared bind would rotate another version's log.
        service["volumes"] = [m for m in service.get("volumes", []) if m["target"] != "/ragflow/logs"]
        service["volumes"].append({"type": "volume", "source": f"{slot}-logs", "target": "/ragflow/logs"})
        if slot != "worker":
            service["volumes"].append({"type": "volume", "source": "assets", "target": "/release-assets"})
        services[slot] = service
    networks = {name: {"external": True, "name": base["networks"][name]["name"]} for name in app["networks"]}
    volumes = {name: {"external": True, "name": spec["name"]} for name, spec in base.get("volumes", {}).items() if any(m.get("source") == name for m in app.get("volumes", []))}
    for name in ("assets", "routing", "web-blue-logs", "web-green-logs", "worker-logs"):
        volumes[name] = {"name": f"{project}-blue-green-{name}", "labels": {"org.ragflow.blue-green": project}}
    services["gateway"] = {
        "image": image,
        "container_name": f"{project}-gateway",
        "entrypoint": ["/usr/sbin/nginx"],
        "command": ["-c", "/routing/nginx.conf", "-g", "daemon off;"],
        "ports": [{"target": 80, "published": "9380", "host_ip": "0.0.0.0", "protocol": "tcp"}],
        "networks": list(networks),
        "restart": "unless-stopped",
        "labels": {"org.ragflow.blue-green": project, "org.ragflow.role": "gateway"},
        "volumes": [
            {"type": "volume", "source": "routing", "target": "/routing"},
            {"type": "volume", "source": "assets", "target": "/release-assets", "read_only": True},
        ],
    }
    return {"name": project, "services": services, "networks": networks, "volumes": volumes}


def shared_runtime(config: dict, role: str) -> dict:
    """Coexisting versions must address the same stores and authentication state."""
    app = config["services"][role]
    return {
        "environment": {key: value for key, value in app["environment"].items() if key not in {"RAGFLOW_IMAGE", "RAGFLOW_QA_SOURCE_DIRTY"}},
        "volumes": [mount for mount in app.get("volumes", []) if mount["target"] not in {"/ragflow/logs", "/release-assets"}],
        "networks": config["networks"],
        "external_volumes": {key: value for key, value in config["volumes"].items() if value.get("external")},
    }


class BlueGreen:
    def __init__(self, project: str, root: Path, timeout: int = 1200, port: int = 9380):
        self.project, self.root, self.timeout = project, root, timeout
        self.port = port
        self.path = root / "compose.json"
        self.state_path = root / "state.json"
        self.journal = root / "pending.json"

    def compose(self, *args: str) -> str:
        return command("docker", "compose", "-p", self.project, "-f", str(self.path), *args, timeout=max(120, self.timeout))

    def inspect(self, role: str) -> dict:
        result = json.loads(command("docker", "inspect", f"{self.project}-{role}"))[0]
        if result["Config"]["Labels"].get("org.ragflow.blue-green") != self.project:
            raise ValueError("Refusing to operate on a foreign container")
        return result

    def execute(self, role: str, *args: str) -> str:
        self.inspect(role)
        return command("docker", "exec", f"{self.project}-{role}", *args)

    def wait_ready(self, role: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            info = self.inspect(role)
            if info["State"]["Status"] != "running":
                raise RuntimeError(f"{role} exited before readiness")
            if info["State"].get("Health", {}).get("Status") == "healthy":
                return info
            time.sleep(1)
        raise TimeoutError(f"{role} readiness timed out")

    def route(self, slot: str, token: str, *, reload: bool = True):
        gateway = self.inspect("gateway")
        target = self.inspect(slot)
        common = set(gateway["NetworkSettings"]["Networks"]) & set(target["NetworkSettings"]["Networks"])
        addresses = [target["NetworkSettings"]["Networks"][name]["IPAddress"] for name in sorted(common)]
        if not addresses or not addresses[0]:
            raise ValueError("Gateway and candidate have no shared IPv4 network")
        address = str(IPv4Address(addresses[0]))
        if reload:
            # Container-local health alone cannot prove the proxy can reach it.
            self.execute(
                "gateway",
                "python3",
                "-c",
                "import json,sys,urllib.request; "
                "response=urllib.request.urlopen('http://'+sys.argv[1]+'/api/v1/system/healthz',timeout=5); "
                "assert json.load(response).get('status')=='ok'; "
                "urllib.request.urlopen('http://'+sys.argv[1]+'/',timeout=5).close()",
                address,
            )
        path = self.root / "nginx.conf"
        path.write_text(gateway_config(slot, token, address), encoding="utf-8")
        command("docker", "cp", str(path), f"{self.project}-gateway:/routing/next.conf")
        if reload:
            self.execute("gateway", "/usr/sbin/nginx", "-t", "-c", "/routing/next.conf")
            self.execute("gateway", "mv", "/routing/next.conf", "/routing/nginx.conf")
            self.execute("gateway", "/usr/sbin/nginx", "-s", "reload", "-c", "/routing/nginx.conf")
        else:
            command("docker", "cp", str(path), f"{self.project}-gateway:/routing/nginx.conf")

    def wait_route(self, token: str):
        deadline = time.monotonic() + min(self.timeout, 60)
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/v1/system/healthz", headers={"Connection": "close"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.headers.get("X-Ragflow-Deployment") == token and json.load(response).get("status") == "ok":
                        return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        raise TimeoutError("Public route did not confirm candidate readiness")

    def wait_drained(self):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                self.execute("gateway", "python3", "-c", DRAINED)
                return
            except RuntimeError:
                time.sleep(1)
        raise TimeoutError("Old requests still active; refusing to reuse their web slot")

    def up(self, role: str):
        existing = command("docker", "ps", "-aq", "--filter", f"name=^/{self.project}-{role}$")
        if existing:
            self.inspect(role)
        self.compose("up", "-d", "--no-deps", "--no-build", "--pull", "never", role)

    def recover(self):
        """Replay the saved rollback after an interrupted deployment, under its lock."""
        pending = json.loads(self.journal.read_text())
        previous = pending["previous"]
        if previous:
            self.route(previous["slot"], previous["token"])
            self.wait_route(previous["token"])
            write_json(self.path, previous["config"])
            self.up("worker")
            self.wait_ready("worker")
            write_json(self.state_path, previous)
        elif pending.get("legacy"):
            # Restore the direct-port listener and singleton background owner.
            self.compose("stop", "worker", "gateway")
            legacy = pending["legacy"]
            info = json.loads(command("docker", "inspect", legacy))[0]
            if info["Config"]["Labels"].get("com.docker.compose.project") != self.project:
                raise ValueError("Refusing to recover a foreign legacy container")
            command("docker", "update", "--restart=" + pending["legacy_restart"], legacy)
            command("docker", "start", legacy)
        self.journal.unlink()

    def deploy(self, config: dict, *, initialize: bool = False) -> dict:
        from uuid import uuid4

        if self.journal.exists():
            raise ValueError("Interrupted deployment: resolve pending.json before another deployment")
        previous = json.loads(self.state_path.read_text()) if self.state_path.exists() else None
        if not previous and not initialize:
            raise ValueError("Gateway is not initialized. First port handover needs an explicit maintenance window (--initialize)")
        schema = command("docker", "run", "--rm", "--network", "none", "--entrypoint", "python3", config["services"]["web-blue"]["image"], "-c", IMAGE_CONTRACT)
        if not re.fullmatch("[a-f0-9]{64}", schema):
            raise ValueError("Image schema identity is unavailable")
        if previous and previous["schema_sha256"] != schema:
            raise ValueError("Schema/initializer inputs changed; use a separately verified maintenance migration")
        if not previous:
            legacy = f"{self.project}-ragflow-cpu-1"
            info = json.loads(command("docker", "inspect", legacy))[0]
            if info["Config"]["Labels"].get("com.docker.compose.project") != self.project:
                raise ValueError("Legacy container belongs to another project")
            legacy_schema = command("docker", "exec", legacy, "python3", "-c", SCHEMA_IDENTITY)
            if legacy_schema != schema:
                raise ValueError("Initial handover requires matching legacy schema inputs; migrate separately first")
        if previous:
            if shared_runtime(config, "web-blue") != shared_runtime(previous["config"], previous["slot"]):
                raise ValueError("Shared configuration changed; stores/authentication require a maintenance deployment")
            self.wait_route(previous["token"])
            self.wait_drained()
            # The running gateway must retain its exact image and definition.
            config["services"]["gateway"] = previous["config"]["services"]["gateway"]
        slot = "web-green" if previous and previous["slot"] == "web-blue" else "web-blue"
        token = uuid4().hex
        write_json(self.path, config)
        self.compose("config", "--quiet")
        # Persist intent before mutation so a killed deploy cannot be reported green.
        write_json(self.journal, {"previous": previous, "slot": slot, "token": token, "stage": "warming"})
        switched = False
        try:
            self.up(slot)
            ready = self.wait_ready(slot)
            if previous:
                # Preserve JS from the active image even when its volume predates JS retention.
                self.execute(previous["slot"], "python3", "-c", COPY_ASSETS)
            self.execute(slot, "python3", "-c", COPY_ASSETS)
            if not previous:
                # Existing direct-port service is stopped only after candidate warmup.
                # This is the one-time maintenance handover, never a zero-downtime claim.
                self.compose("create", "--no-build", "--pull", "never", "gateway")
                self.route(slot, token, reload=False)
                legacy = f"{self.project}-ragflow-cpu-1"
                legacy_info = json.loads(command("docker", "inspect", legacy))[0]
                if legacy_info["Config"]["Labels"].get("com.docker.compose.project") != self.project:
                    raise ValueError("Legacy container belongs to another project")
                policy = legacy_info["HostConfig"]["RestartPolicy"]
                restart = policy["Name"]
                if restart == "on-failure" and policy["MaximumRetryCount"]:
                    restart += ":" + str(policy["MaximumRetryCount"])
                write_json(self.journal, {"previous": None, "slot": slot, "token": token, "legacy": legacy, "legacy_restart": restart, "stage": "initial-handover"})
                command("docker", "stop", legacy, timeout=self.timeout)
                command("docker", "update", "--restart=no", legacy)
                self.compose("start", "gateway")
            else:
                switched = True
                self.route(slot, token)
            switched = True
            self.wait_route(token)
            # Keep both web slots alive until the next deployment. No force-drain.
            # Worker processing can pause/retry during replacement; it owns no public port.
            self.up("worker")
            worker = self.wait_ready("worker")
            if ready["Image"] != worker["Image"]:
                raise ValueError("Web and worker image identities differ")
            result = {
                "schema": 1,
                "slot": slot,
                "token": token,
                "schema_sha256": schema,
                "config": config,
                "web_image_id": ready["Image"],
                "worker_image_id": worker["Image"],
                "initial_handover": not bool(previous),
            }
            write_json(self.state_path, result)
            self.journal.unlink()
            return {key: value for key, value in result.items() if key != "config"}
        except BaseException:
            if previous:
                # Restore traffic first, then the singleton worker. No database restore.
                if switched:
                    self.route(previous["slot"], previous["token"])
                    self.wait_route(previous["token"])
                write_json(self.path, previous["config"])
                self.up("worker")
                self.wait_ready("worker")
                write_json(self.state_path, previous)
                self.journal.unlink()
            else:
                self.recover()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Resolved immutable candidate Compose JSON")
    parser.add_argument("--project", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--recover", action="store_true", help="Restore the pre-deployment route and worker after interruption")
    args = parser.parse_args()
    with deployment_lock(args.runtime):
        deployer = BlueGreen(args.project, args.runtime, args.timeout)
        if args.recover:
            deployer.recover()
            return 0
        if not args.config or not args.output:
            parser.error("--config and --output are required for deployment")
        base = json.loads(args.config.read_text(encoding="utf-8-sig"))
        config = deployment_config(base, args.project, base["services"]["ragflow-cpu"]["image"])
        result = deployer.deploy(config, initialize=args.initialize)
    write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
