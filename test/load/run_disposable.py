"""Provision a disposable Docker contour and run the Business Documents load."""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml
from Cryptodome.Cipher import PKCS1_v1_5
from Cryptodome.PublicKey import RSA


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from test.load.business_documents import MODEL, parser as load_parser, run_load
from tools.quality.candidate import snapshot, verify


def lifecycle_error_count(log: str) -> int:
    """Count records, including JSON stack traces, rather than repeated phrases."""
    return sum(any(marker in line for marker in ("Event loop is closed", "was never awaited", "bound to a different event loop", "attached to a different loop")) for line in log.splitlines())


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--evidence-dir", type=Path, required=True)
    cli.add_argument("--app-image", required=True, help="Existing compatible application image; resolved and recorded by immutable image ID")
    cli.add_argument("--users", type=int, default=50)
    cli.add_argument("--worker-concurrency", type=int, choices=range(1, 9), default=4)
    cli.add_argument("--compare-single-worker", action="store_true", help="Run 50 users with 1 worker, then the selected pool on identical source/data services")
    args = cli.parse_args()
    if args.users < 1:
        cli.error("users must be positive")
    out = args.evidence_dir.resolve()
    if out.is_relative_to(ROOT) or out.exists():
        cli.error("Use a fresh evidence directory outside the source repository")
    out.mkdir(parents=True)
    project = "bd-load-" + secrets.token_hex(5)
    base, mock_url = "http://127.0.0.1:19582", "http://127.0.0.1:19581"
    for port in (19581, 19582):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    secret_values = []
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    env.update(QA_STORAGE_PASSWORD=secrets.token_urlsafe(32), QA_ADMIN_PASSWORD=secrets.token_urlsafe(32), QA_ADMIN_EMAIL=f"load-admin-{project}@example.org")
    secret_values.extend([env["QA_STORAGE_PASSWORD"], env["QA_ADMIN_PASSWORD"]])

    def sanitized(value):
        for secret in secret_values:
            value = value.replace(secret, "[REDACTED]")
        return value

    def command(argv, log, *, input=None, check=True, timeout=180):
        result = subprocess.run(argv, input=input, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env, cwd=ROOT, timeout=timeout)
        (out / log).write_text(sanitized(result.stdout + result.stderr), encoding="utf-8")
        if check and result.returncode:
            raise RuntimeError(f"Command failed: {log} (exit {result.returncode})")
        return result

    def image(reference):
        return subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", reference], text=True).strip()

    # Preserve all current source inputs, including the untracked prompt used by
    # the dirty application. The candidate rejects private/generated paths.
    included = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    manifest = snapshot(ROOT, out / "candidate", included, True, {"platform": "linux/amd64"})
    source = out / "candidate/source"
    compose = yaml.safe_load((source / "test/integration/live_ragflow/compose.yml").read_text(encoding="utf-8"))
    compose["name"] = project
    services = compose["services"]
    selected = {
        "app": args.app_image,
        "postgres": "postgres:16-alpine",
        "redis": "valkey/valkey:8",
        "minio": "pgsty/minio:RELEASE.2026-08-04T00-00-00Z",
        "es01": "docker.elastic.co/elasticsearch/elasticsearch:8.19.23",
        "plantuml-server": "plantuml/plantuml-server:jetty-v1.2026.6",
    }
    images = {name: image(reference) for name, reference in selected.items()}
    for name, identity in images.items():
        services[name]["image"] = identity
    app = services["app"]
    app["command"] = ["--init-superuser", "--disable-taskexecutor", "--disable-datasync"]
    app["ports"] = ["127.0.0.1:19582:80"]
    app["environment"].update(
        DEFAULT_SUPERUSER_EMAIL="${QA_ADMIN_EMAIL:?required}",
        DEFAULT_SUPERUSER_PASSWORD="${QA_ADMIN_PASSWORD:?required}",
        MANAGED_RESOURCE_OWNER_EMAIL="${QA_ADMIN_EMAIL:?required}",
        BUSINESS_DOCUMENT_WORKER_CONCURRENCY=str(1 if args.compare_single_worker else args.worker_concurrency),
    )
    app["volumes"] = [f"{source.as_posix()}/{item[6:]}" if item.startswith("../../") else item for item in app["volumes"]]
    dist = out / "empty-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>API load test</title>", encoding="utf-8")
    app["volumes"] = [f"{dist.as_posix()}:/ragflow/web/dist:ro" if ":/ragflow/web/dist:" in item else item for item in app["volumes"]]
    for name in manifest["identity"]["files"]:
        if (name.startswith("conf/") and name != "conf/service_conf.yaml") or name.startswith("rag/res/"):
            app["volumes"].append(f"{(source / name).as_posix()}:/ragflow/{name}:ro")
    app["volumes"].append(f"{(source / 'test/load').as_posix()}:/ragflow/load-test:ro")
    services["mock-llm"] = {
        "image": images["app"],
        "entrypoint": ["/ragflow/.venv/bin/python", "-B", "/ragflow/load-test/business_documents.py"],
        "command": ["mock-llm", "--host", "0.0.0.0"],
        "ports": ["127.0.0.1:19581:18081"],
        "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        "volumes": [f"{(source / 'test/load').as_posix()}:/ragflow/load-test:ro"],
    }
    compose_path = out / "compose.yml"
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    cmd = ["docker", "compose", "-p", project, "-f", str(compose_path)]
    (out / "identity.json").write_text(
        json.dumps(
            {
                "project": project,
                "source_id": manifest["source_id"],
                "head": manifest["identity"]["head"],
                "dirty": manifest["identity"]["dirty"],
                "images": images,
                "users": args.users,
                "api": base,
                "mock": mock_url,
                "app_processes": 1,
                "worker_concurrency": args.worker_concurrency,
                "compare_single_worker": args.compare_single_worker,
                "mode": "disposable test; not deployment",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    cipher = PKCS1_v1_5.new(RSA.import_key((source / "conf/public.pem").read_bytes(), "Welcome"))

    def encrypted(password):
        result = base64.b64encode(cipher.encrypt(base64.b64encode(password.encode()))).decode()
        secret_values.append(result)
        return result

    def api(path, method="GET", payload=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = token
        request = Request(base + path, method=method, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(request, timeout=90) as response:
                body = json.load(response)
                if body.get("code") != 0:
                    raise RuntimeError(sanitized(f"{method} {path}: {body}"))
                return body.get("data"), response.headers.get("Authorization")
        except HTTPError as error:
            raise RuntimeError(sanitized(f"{method} {path}: HTTP {error.code} {error.read().decode()[:1000]}")) from None

    stop_monitor = threading.Event()
    monitor = None
    exit_code = 1
    try:
        print(f"Starting {project}; source_id={manifest['source_id']}", flush=True)
        command([*cmd, "config", "--quiet"], "compose-validation.log")
        command([*cmd, "up", "-d", "--pull", "never"], "startup.log", timeout=300)
        deadline = time.monotonic() + 600
        while True:
            try:
                api("/api/v1/auth/login/channels")
                with urlopen(mock_url + "/stats", timeout=5) as response:
                    json.load(response)
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise RuntimeError("Readiness timeout")
                time.sleep(3)
        print("API and LLM emulator ready; seeding model catalog", flush=True)
        command([*cmd, "exec", "-T", "app", "/ragflow/.venv/bin/python", "-B", "-"], "catalog.log", input=(source / "test/integration/live_ragflow/seed_catalog.py").read_text(encoding="utf-8"))
        admin, admin_token = api("/api/v1/auth/login", "POST", {"email": env["QA_ADMIN_EMAIL"], "password": encrypted(env["QA_ADMIN_PASSWORD"])})
        assert admin_token and admin.get("is_superuser")
        secret_values.append(admin_token)
        provider = "OpenAI-API-Compatible"
        api("/api/v1/providers", "PUT", {"provider_name": provider}, admin_token)
        api(
            f"/api/v1/providers/{provider}/instances",
            "POST",
            {
                "instance_name": "LoadTest",
                "api_key": "synthetic",
                "base_url": "http://mock-llm:18081/v1",
                "region": "default",
                "model_info": [{"model_name": MODEL, "model_type": ["chat"], "max_tokens": 65536}],
            },
            admin_token,
        )
        api("/api/v1/models/default", "PATCH", {"model_provider": provider, "model_instance": "LoadTest", "model_name": MODEL, "model_type": "chat"}, admin_token)
        config, _ = api("/api/v1/users/me/models", token=admin_token)
        api(
            "/api/v1/users/me/models",
            "PATCH",
            {"tenant_id": config["tenant_id"], "llm_id": f"{MODEL}@LoadTest@{provider}", "embd_id": "", "img2txt_id": "", "asr_id": "", "rerank_id": "", "tts_id": ""},
            admin_token,
        )
        tokens, accounts = [], []
        for index in range(args.users):
            password = secrets.token_urlsafe(24)
            secret_values.append(password)
            email = f"load-{index:02d}-{project}@example.org"
            credential = encrypted(password)
            accounts.append((email, credential))
            api("/api/v1/users", "POST", {"email": email, "password": credential, "nickname": f"Load user {index + 1}"})
            _, token = api("/api/v1/auth/login", "POST", {"email": email, "password": credential})
            assert token
            secret_values.append(token)
            tokens.append(token)
            if (index + 1) % 10 == 0:
                print(f"Registered and authenticated {index + 1}/{args.users}", flush=True)
        (out / "bootstrap.json").write_text(
            json.dumps({"users_registered": len(tokens), "users_authenticated": len(set(tokens)), "admin_authenticated": True, "model": MODEL, "real_llm": False}), encoding="utf-8"
        )
        phase = "ready"

        def collect_metrics():
            sql = "SELECT json_build_object('jobs',(SELECT coalesce(json_agg(j), '[]') FROM (SELECT id,status,create_time,update_time,lease_owner,attempt FROM business_document_job) j),'connections',(SELECT json_agg(c) FROM (SELECT state,count(*) FROM pg_stat_activity WHERE datname='rag_flow' GROUP BY state)c));"
            while not stop_monitor.is_set():
                sampled = datetime.now(timezone.utc).isoformat()
                sampled_phase = phase
                ids = subprocess.check_output([*cmd, "ps", "-q"], text=True, env=env).split()
                for key, argv in (
                    ("containers", ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids]),
                    ("database", [*cmd, "exec", "-T", "postgres", "psql", "-U", "rag_flow", "-d", "rag_flow", "-At", "-c", sql]),
                ):
                    try:
                        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env, timeout=20)
                        data = {
                            "at": sampled,
                            "phase": sampled_phase,
                            "kind": key,
                            "exit_code": result.returncode,
                            "data": (json.loads(result.stdout) if key == "database" else [json.loads(line) for line in result.stdout.splitlines() if line.strip()])
                            if result.returncode == 0
                            else sanitized(result.stderr[:1000]),
                        }
                        with (out / "metrics.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(data) + "\n")
                    except Exception as error:
                        with (out / "metrics.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps({"at": sampled, "phase": sampled_phase, "kind": key, "error": type(error).__name__}) + "\n")
                stop_monitor.wait(3)

        monitor = threading.Thread(target=collect_metrics, daemon=True)
        monitor.start()
        load_args = load_parser().parse_args(["run", "--base-url", base, "--mock-url", mock_url, "--users-file", "unused", "--users", str(args.users), "--report", "unused"])
        counts = [1, args.worker_concurrency] if args.compare_single_worker and args.worker_concurrency != 1 else [args.worker_concurrency]
        for index, concurrency in enumerate(counts):
            if index:
                phase = "restart"
                print(f"Recreating the single API container with {concurrency} worker threads", flush=True)
                app["environment"]["BUSINESS_DOCUMENT_WORKER_CONCURRENCY"] = str(concurrency)
                compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
                command([*cmd, "up", "-d", "--no-deps", "--force-recreate", "app"], f"restart-workers-{concurrency}.log", timeout=180)
                deadline = time.monotonic() + 600
                while True:
                    try:
                        api("/api/v1/auth/login/channels")
                        break
                    except Exception:
                        if time.monotonic() > deadline:
                            raise RuntimeError("API readiness timeout after pool resize")
                        time.sleep(3)
                tokens = []
                for email, credential in accounts:
                    _, token = api("/api/v1/auth/login", "POST", {"email": email, "password": credential})
                    assert token
                    secret_values.append(token)
                    tokens.append(token)
                _, admin_token = api("/api/v1/auth/login", "POST", {"email": env["QA_ADMIN_EMAIL"], "password": encrypted(env["QA_ADMIN_PASSWORD"])})
                secret_values.append(admin_token)
            phase = f"load-{concurrency}"
            print(f"Running {args.users} users: 1 API process, {concurrency} worker threads", flush=True)
            report = asyncio.run(run_load(load_args, tokens, admin_token))
            report.update(source_id=manifest["source_id"], app_processes=1, worker_concurrency=concurrency)
            logs = command([*cmd, "logs", "--no-color", "app"], f"app-workers-{concurrency}.log")
            report["runtime_lifecycle_errors"] = lifecycle_error_count(logs.stdout + logs.stderr)
            if report["runtime_lifecycle_errors"]:
                report["status"] = "FAIL"
            (out / f"load-workers-{concurrency}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            with urlopen(mock_url + "/stats", timeout=10) as response:
                (out / f"llm-stats-workers-{concurrency}.json").write_text(json.dumps(json.load(response), ensure_ascii=False, indent=2), encoding="utf-8")
            command([*cmd, "ps", "--format", "json"], f"containers-workers-{concurrency}.json")
            print(json.dumps({key: report[key] for key in ("status", "elapsed_seconds", "failures", "scenario_seconds")}), flush=True)
            exit_code = 0 if report["status"] == "PASS" else 1
            if exit_code:
                break
    except Exception as error:
        (out / "failure.txt").write_text(sanitized(str(error)), encoding="utf-8")
        print(sanitized(str(error)), flush=True)
    finally:
        stop_monitor.set()
        if monitor:
            monitor.join(timeout=45)
        command([*cmd, "logs", "--no-color"], "containers.log", check=False)
        command([*cmd, "ps", "--format", "json"], "containers-final.json", check=False)
        command([*cmd, "down", "--volumes", "--remove-orphans"], "cleanup.log", check=False, timeout=180)
        remaining = {}
        for name, argv in (("containers", ["ps", "-aq"]), ("volumes", ["volume", "ls", "-q"]), ("networks", ["network", "ls", "-q"])):
            remaining[name] = subprocess.check_output(["docker", *argv, "--filter", f"label=com.docker.compose.project={project}"], text=True).split()
        (out / "cleanup.json").write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        assert not any(remaining.values()), "Disposable resources remain"
        verify(out / "candidate")
        print(f"Own resources removed; frozen source verified; evidence: {out}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
