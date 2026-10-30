"""Disposable live regression runner; generated secrets remain in the process environment."""

import argparse
from copy import deepcopy
import hashlib
from ipaddress import IPv4Network
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from test.playwright.helpers.redaction import redact_browser_secrets
from tools.quality.artifact_lifecycle import annotate_compose, current, docker_args, managed, note, note_all

OUT = Path(__file__).resolve().parent
PROJECT = "ragflow-t1-live-20260906-b-" + secrets.token_hex(4)
BASE = "http://127.0.0.1:19382"
SECRET_VALUES = []
MODELS = (("qwen3.8:latest", "chat"), ("qwen3.6:27b", "chat"), ("bge-m3:latest", "embedding"))
OLLAMA_HOST_URL = "http://127.0.0.1:11435"
OLLAMA_CONTAINER_URL = "http://host.docker.internal:11435"
BROWSER_PROFILE = """import os
import pytest

class Profile:
    @pytest.fixture(scope="session")
    def browser_context_args(self):
        base = os.environ["RAGFLOW_BASE_URL"]
        return {"viewport": {"width": 1920, "height": 1080}, "locale": "en-US", "storage_state": {"cookies": [], "origins": [{"origin": base, "localStorage": [{"name": "lng", "value": "en"}]}]}}

def pytest_configure(config):
    config.pluginmanager.register(Profile(), "t1-english")
"""


def models_for_run(tests: list[str], skip_browser_tests: bool, chat_models: list[str] | None = None) -> tuple[tuple[str, str], ...]:
    """Debug on qwen3.8; additional chat models require explicit selection."""
    chats = tuple((name, "chat") for name in dict.fromkeys(chat_models or [MODELS[0][0]]))
    if any(Path(path).name == "test_live_document_roundtrip.py" for path in tests) and len(chats) < 2:
        raise ValueError("The two-model roundtrip requires two explicitly authorized --chat-model values")
    retrieval = {"test_source_workbench_quality_live.py", "test_dataset_upload_parse.py", "test_next_apps_chat.py", "test_next_apps_search.py", "test_live_document_roundtrip.py"}
    embedding = not skip_browser_tests and (not tests or any(Path(path).name in retrieval for path in tests))
    return chats + ((MODELS[-1],) if embedding else ())


def model_context_length(metadata: dict) -> int:
    """Use the model's native context window, never a guessed QA default."""
    info = metadata.get("model_info", {})
    architecture = info.get("general.architecture")
    context = info.get(f"{architecture}.context_length")
    if not architecture or type(context) is not int or context <= 0:
        raise ValueError("Ollama /api/show must declare a positive architecture context_length")
    return context


def sanitized(text):
    for value in SECRET_VALUES:
        if value:
            text = text.replace(value, "[REDACTED]")
    return redact_browser_secrets(text)


def command(args, *, env, log, input=None, check=True, cwd=None, timeout=180):
    # The lifecycle adapter resolves Compose before Popen receives its env.
    # Expose generated QA values only during that resolution, without files.
    previous = {key: os.environ.get(key) for key in env if key.startswith("QA_")}
    try:
        os.environ.update({key: env[key] for key in previous})
        args = docker_args(args, cwd=cwd or ROOT)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    process = subprocess.Popen(
        args,
        env=env,
        cwd=cwd or ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=30, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate(timeout=30)
    result = subprocess.CompletedProcess(args, 124 if timed_out else process.returncode, stdout, stderr)
    (OUT / log).write_text(sanitized((stdout or "") + (stderr or "") + ("\nCommand timeout\n" if timed_out else "")), encoding="utf-8")
    with (OUT / "commands.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(sanitized(json.dumps({"argv": args, "cwd": str(cwd or ROOT), "timeout_seconds": timeout, "exit_code": result.returncode, "log": log})) + "\n")
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); see {log}")
    return result


def api(path, method="GET", payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    with urlopen(Request(BASE + path, method=method, headers=headers, data=json.dumps(payload).encode() if payload is not None else None), timeout=240) as response:
        result = json.load(response)
        assert result.get("code") == 0, f"{method} {path}: code={result.get('code')} message={result.get('message')}"
        return result.get("data"), response.headers.get("Authorization")


def verify_source(candidate, source, env, phase):
    script = "import sys,json;sys.path.insert(0,sys.argv[1]);from candidate import verify;m=verify(__import__('pathlib').Path(sys.argv[2]));print(json.dumps({'source_id':m['source_id'],'integrity':'PASS'}))"
    command([sys.executable, "-B", "-c", script, str(source / "tools/quality"), str(candidate)], env=env, log=f"candidate-{phase}.log")


@managed("live-regression-qa", days=0)
def main():
    global OUT, BASE
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-image", help="Explicit disposable QA image; recorded by immutable image ID")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--dist-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--frontend-archive", type=Path, help="Verified frontend archive and adjacent .json receipt; required for frozen browser candidates")
    parser.add_argument("--chat-model", action="append", help="Explicitly authorized chat model; repeat for an explicitly requested multi-model run. Default: qwen3.8:latest")
    parser.add_argument("--worker-service", action="store_true", help="Run background work in a separate unpublished container")
    parser.add_argument("--openmetadata-network", help="Existing private Docker network for the real OpenMetadata server")
    parser.add_argument("--network-subnet", type=IPv4Network, help="Explicit unused QA IPv4 CIDR when Docker's automatic address pools are exhausted")
    parser.add_argument("--base-port", type=int, choices=(19382, 19482), default=19382, help="Dedicated loopback QA HTTP port; admin uses the next port")
    parser.add_argument("--browser-timeout-seconds", type=int, default=1800, help="Overall browser batch budget, 60..7200 seconds; individual test/model limits stay unchanged")
    parser.add_argument("--business-documents-quality", action="store_true", help="Run the real-model Business Documents suite in this disposable app")
    parser.add_argument("--business-documents-case-id", help="Run one Business Documents case for diagnosis; the quality report remains incomplete")
    parser.add_argument("--business-documents-nightly", action="store_true", help="Run one full nightly series across the disposable QA tenant's Ollama catalog")
    parser.add_argument("--skip-browser-tests", action="store_true", help="Use only with Business Documents model quality; no built SPA or browser evidence")
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    if not 60 <= args.browser_timeout_seconds <= 7200:
        parser.error("--browser-timeout-seconds must be between 60 and 7200")
    BASE = f"http://127.0.0.1:{args.base_port}"
    if args.skip_browser_tests and not args.business_documents_quality:
        parser.error("--skip-browser-tests requires --business-documents-quality")
    if args.business_documents_case_id and not args.business_documents_quality:
        parser.error("--business-documents-case-id requires --business-documents-quality")
    if args.business_documents_nightly and (not args.business_documents_quality or args.business_documents_case_id or not args.skip_browser_tests):
        parser.error("--business-documents-nightly requires quality and skip-browser-tests, without a case id")
    if args.skip_browser_tests and args.tests:
        parser.error("--skip-browser-tests cannot select browser tests")
    models = models_for_run(args.tests, args.skip_browser_tests, args.chat_model)
    source = args.source_root.resolve()
    dist = args.dist_root.resolve()
    candidate = source.parent if (source.parent / "candidate.json").is_file() else None
    OUT = args.evidence_dir.resolve()
    if candidate and (OUT.is_relative_to(source) or (args.skip_browser_tests and dist.is_relative_to(source))):
        parser.error("Evidence and generated dist must stay outside the frozen source snapshot")
    note(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    failure = None
    try:
        if args.business_documents_quality:
            (OUT / "business-documents-quality.json").write_text(
                json.dumps({"schema_version": "2", "status": "INCOMPLETE", "failure": "Live model run did not complete"}, indent=2) + "\n",
                encoding="utf-8",
            )
        if any(Path(name).name == "test_source_workbench_quality_live.py" for name in args.tests):
            (OUT / "source-workbench-quality.json").write_text(
                json.dumps({"status": "incomplete", "failure": "Live retrieval run did not complete"}, indent=2) + "\n",
                encoding="utf-8",
            )
        assert (source / "api/ragflow_server.py").is_file()
        if not args.skip_browser_tests:
            assert (dist / "index.html").is_file()
        else:
            dist.mkdir(parents=True, exist_ok=True)
        for port in (args.base_port, args.base_port + 1):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        env = dict(os.environ)
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        env.update(QA_STORAGE_PASSWORD=secrets.token_urlsafe(32), QA_ADMIN_PASSWORD=secrets.token_urlsafe(32), QA_ADMIN_EMAIL=f"regression-probe-{secrets.token_hex(5)}@example.org")
        SECRET_VALUES.extend([env["QA_STORAGE_PASSWORD"], env["QA_ADMIN_PASSWORD"]])
        SECRET_VALUES.extend(env.get(key, "") for key in ("QA_OPENMETADATA_PASSWORD", "QA_OPENMETADATA_JWT_TOKEN"))
        if env.get("QA_EVA_CONNECTOR_CONFIG"):
            credentials = json.loads(env["QA_EVA_CONNECTOR_CONFIG"]).get("credentials", {})
            SECRET_VALUES.extend(value for value in credentials.values() if isinstance(value, str) and value)
        for resource, arguments in (("containers", ["ps", "-a"]), ("volumes", ["volume", "ls"]), ("networks", ["network", "ls"])):
            existing = command(["docker", *arguments, "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"], env=env, log=f"preflight-{resource}.log")
            assert not existing.stdout.strip(), "Disposable project name is already in use"
        compose = yaml.safe_load(Path(__file__).with_name("compose.yml").read_text(encoding="utf-8"))
        if args.network_subnet:
            compose.setdefault("networks", {})["default"] = {"ipam": {"config": [{"subnet": str(args.network_subnet)}]}}
        compose["name"] = PROJECT
        app = compose["services"]["app"]
        if args.app_image:
            app["image"] = args.app_image
        app["ports"] = [f"127.0.0.1:{args.base_port}:80", f"127.0.0.1:{args.base_port + 1}:9381"]
        app["command"].append("--workers=1")
        app["environment"].update(
            DEFAULT_SUPERUSER_EMAIL="${QA_ADMIN_EMAIL:?required}", DEFAULT_SUPERUSER_PASSWORD="${QA_ADMIN_PASSWORD:?required}", MANAGED_RESOURCE_OWNER_EMAIL="${QA_ADMIN_EMAIL:?required}"
        )
        if args.openmetadata_network:
            compose.setdefault("networks", {})["openmetadata"] = {"external": True, "name": args.openmetadata_network}
            app["networks"] = ["default", "openmetadata"]
            app["environment"].update(
                OPENMETADATA_URL="http://openmetadata-server:8585",
                OPENMETADATA_USERNAME="${QA_OPENMETADATA_USERNAME:-}",
                OPENMETADATA_PASSWORD="${QA_OPENMETADATA_PASSWORD:-}",
                OPENMETADATA_JWT_TOKEN="${QA_OPENMETADATA_JWT_TOKEN:-}",
                OPENMETADATA_DATASET_ID="",
            )
        app["volumes"] = [f"{source.as_posix()}/{item[6:]}" if item.startswith("../../") else item for item in app["volumes"]]
        app["volumes"] = [f"{dist.as_posix()}:/ragflow/web/dist:ro" if ":/ragflow/web/dist:" in item else item for item in app["volumes"]]
        if args.business_documents_quality:
            app["volumes"].extend((f"{(source / 'test').as_posix()}:/ragflow/test:ro", f"{OUT.as_posix()}:/ragflow/qa-evidence"))
        if candidate:
            input_files = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))["identity"]["files"]
        else:
            input_files = subprocess.check_output(["git", "ls-files", "conf", "rag/res"], cwd=source, text=True).splitlines()
        mounted_assets = {}
        for name in input_files:
            if (name.startswith("conf/") and name != "conf/service_conf.yaml") or name.startswith("rag/res/"):
                app["volumes"].append(f"{(source / name).as_posix()}:/ragflow/{name}:ro")
                mounted_assets["/ragflow/" + name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
        if args.worker_service:
            app["environment"]["RAGFLOW_BACKGROUND_ENABLED"] = "false"
            # BT/TZ/SQL do not enqueue dataset-ingestion tasks. Keep that separate
            # executor out of both containers; durable document jobs still run.
            app["command"].append("--disable-taskexecutor")
            app.update(mem_limit="3g", cpus=2.0)
            for name, limit in {"postgres": "512m", "redis": "256m", "minio": "512m", "es01": "1g", "plantuml-server": "512m"}.items():
                compose["services"][name]["mem_limit"] = limit
            app["healthcheck"] = {
                "test": ["CMD", "python3", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9380/api/v1/system/healthz',timeout=5)"],
                "interval": "5s",
                "timeout": "8s",
                "retries": 90,
            }
            worker = deepcopy(app)
            worker.pop("ports", None)
            worker["command"] = ["--disable-datasync", "--disable-taskexecutor", "--workers=1"]
            worker["environment"].update(RAGFLOW_BACKGROUND_ENABLED="true", RAGFLOW_SKIP_DATABASE_INIT="true")
            worker["depends_on"] = {"app": {"condition": "service_healthy"}}
            # The entrypoint starts nginx after the API; API readiness alone can
            # precede a fatal nginx startup error and a stopped worker container.
            worker["healthcheck"]["test"][-1] = "import urllib.request;urllib.request.urlopen('http://127.0.0.1/api/v1/system/healthz',timeout=5)"
            compose["services"]["worker"] = worker
        compose_path = OUT / "compose.yml"
        compose_path.write_text(yaml.safe_dump(annotate_compose(compose), sort_keys=False), encoding="utf-8")
        cmd = ["docker", "compose", "-p", PROJECT, "-f", str(compose_path)]
        if candidate:
            verify_source(candidate, source, env, "before")
            source_identity = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))["source_id"]
        else:
            source_identity = "development:" + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        identity = {
            "project": PROJECT,
            "browser_timeout_seconds": args.browser_timeout_seconds,
            "source": str(source),
            "dist": str(dist),
            "source_id": source_identity,
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "mounted_assets_sha256": mounted_assets,
            "dist_sha256": {path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(dist.rglob("*")) if path.is_file()},
            "targets": [BASE, f"http://127.0.0.1:{args.base_port + 1}", OLLAMA_CONTAINER_URL],
            "images": {
                name: command(["docker", "image", "inspect", "--format", "{{.Id}}", svc["image"]], env=env, log=f"image-{name}.log").stdout.strip() for name, svc in compose["services"].items()
            },
        }
        with urlopen(f"{OLLAMA_HOST_URL}/api/tags", timeout=30) as response:
            tags = {model["name"]: model for model in json.load(response)["models"]}
        identity["ollama_models"] = {name: {key: tags[name][key] for key in ("digest", "size")} for name, _ in models}
        assert all(model["size"] > 1_000_000 for model in identity["ollama_models"].values()), "Only installed local model weights are allowed"
        for model, _ in models:
            request = Request(f"{OLLAMA_HOST_URL}/api/show", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=30) as response:
                identity["ollama_models"][model]["context_length"] = model_context_length(json.load(response))
        if candidate and not args.skip_browser_tests:
            assert args.frontend_archive, "Frozen live proof requires the matching frontend artifact"
            archive = args.frontend_archive.resolve()
            command(
                [sys.executable, "-B", str(source / "tools/quality/frontend_artifact.py"), "verify", "--candidate", str(candidate), "--archive", str(archive)], env=env, log="frontend-verification.log"
            )
            receipt = json.loads(Path(str(archive) + ".json").read_text(encoding="utf-8"))
            if receipt["kind"] == "frontend-image":
                assert receipt["image_id"] == identity["images"]["app"], "Frontend belongs to another runtime image"
            assert identity["dist_sha256"] == {name: value["sha256"] for name, value in receipt["files"].items()}, "Mounted dist differs from the verified candidate build"
            identity["frontend_receipt_id"] = receipt["receipt_id"]
        (OUT / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
        current().request_teardown(OUT)
        exit_code = 1
        try:
            print("Starting disposable regression stack", flush=True)
            command([*cmd, "up", "-d", "--pull", "never", "--wait", "--wait-timeout", "480"], env=env, log="startup.log", timeout=600)
            deadline = time.monotonic() + 480
            while True:
                try:
                    api("/api/v1/auth/login/channels")
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise RuntimeError("Backend readiness timeout")
                    time.sleep(3)
            print("Backend ready; initializing canonical empty QA model catalog", flush=True)
            check_assets = (
                "import json,hashlib;from pathlib import Path;expected="
                + repr(mounted_assets)
                + ";assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in expected.items());print(json.dumps({'candidate_assets_verified':len(expected)}))"
            )
            command([*cmd, "exec", "-T", "app", "/ragflow/.venv/bin/python", "-"], env=env, log="runtime-assets.log", input=check_assets)
            seed = Path(__file__).with_name("seed_catalog.py").read_text(encoding="utf-8")
            command([*cmd, "exec", "-T", "app", "/ragflow/.venv/bin/python", "-"], env=env, log="catalog.log", input=seed)
            sys.path.insert(0, str(source))
            from api.utils.crypt import crypt

            encrypted = crypt(env["QA_ADMIN_PASSWORD"])
            SECRET_VALUES.append(encrypted)
            user, token = api("/api/v1/auth/login", "POST", {"email": env["QA_ADMIN_EMAIL"], "password": encrypted})
            assert token and user.get("is_superuser"), "Synthetic managed-resource admin login failed"
            SECRET_VALUES.append(token)
            print("Synthetic superuser authenticated; configuring real local Ollama", flush=True)
            for model, kind in models:
                api(
                    "/v1/llm/add_llm",
                    "POST",
                    {"llm_factory": "Ollama", "llm_name": model, "model_type": kind, "api_base": OLLAMA_CONTAINER_URL, "max_tokens": identity["ollama_models"][model]["context_length"]},
                    token,
                )
                print(f"Validated local {kind} model {model}", flush=True)
            api("/api/v1/providers", "PUT", {"provider_name": "Ollama"}, token)
            api(
                "/api/v1/providers/Ollama/instances",
                "POST",
                {
                    "instance_name": "QA",
                    "api_key": "",
                    "base_url": OLLAMA_CONTAINER_URL,
                    "region": "default",
                    "model_info": [{"model_name": model, "model_type": [kind], "max_tokens": identity["ollama_models"][model]["context_length"]} for model, kind in models],
                },
                token,
            )
            defaults = {}
            for model, kind in models:
                defaults.setdefault(kind, model)
            for kind, model in defaults.items():
                api("/api/v1/models/default", "PATCH", {"model_provider": "Ollama", "model_instance": "QA", "model_name": model, "model_type": kind}, token)
            tenant, _ = api("/api/v1/users/me/models", token=token)
            api(
                "/api/v1/users/me/models",
                "PATCH",
                {
                    "tenant_id": tenant["tenant_id"],
                    "llm_id": f"{defaults['chat']}@QA@Ollama",
                    "embd_id": f"{defaults['embedding']}@QA@Ollama" if "embedding" in defaults else "",
                    "img2txt_id": "",
                    "asr_id": "",
                    "rerank_id": "",
                    "tts_id": "",
                },
                token,
            )
            env.update(
                RAGFLOW_BASE_URL=BASE,
                SEEDED_USER_EMAIL=env["QA_ADMIN_EMAIL"],
                SEEDED_USER_PASSWORD=env["QA_ADMIN_PASSWORD"],
                PW_BROWSER="chromium",
                PW_TRACE="0",
                RUN_ID="t1-live-" + secrets.token_hex(4),
                PW_STEP_LOG="1",
                PYTHONPATH=str(source),
                QA_LIVE_TOKEN=token,
                QA_LIVE_EVIDENCE=str(OUT),
                PYTHONDONTWRITEBYTECODE="1",
                QA_DISPOSABLE_PROJECT=PROJECT,
                QA_LIVE_PORT=str(args.base_port),
                PW_ARTIFACTS_DIR=str(OUT / "artifacts"),
                QA_LIVE_APP_CONTAINER=command([*cmd, "ps", "-q", "app"], env=env, log="app-container.log").stdout.strip(),
                QA_LIVE_WORKER_CONTAINER=command([*cmd, "ps", "-q", "worker"], env=env, log="worker-container.log").stdout.strip() if args.worker_service else "",
            )
            (OUT / "bootstrap.json").write_text(json.dumps({"admin_authenticated": True, "real_models_validated": True}), encoding="utf-8")
            tests = args.tests or [
                str(source / "test/playwright/e2e" / filename)
                for filename in ("test_dataset_upload_parse.py", "test_next_apps_chat.py", "test_next_apps_search.py", "test_business_document_live_intake.py")
            ]
            # Registration through pytest_configure occurs after pytest-playwright,
            # so the explicit test profile wins without mutating production config.
            (OUT / "live_profile.py").write_text(BROWSER_PROFILE, encoding="utf-8")
            env["PYTHONPATH"] += os.pathsep + str(OUT)
            result = (
                command(
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "pytest",
                        "-p",
                        "live_profile",
                        *tests,
                        "--color=no",
                        "--tb=short",
                        "-rA",
                        "-s",
                        "-o",
                        f"cache_dir={OUT / 'pytest-cache'}",
                        f"--junitxml={OUT / 'browser.xml'}",
                    ],
                    env=env,
                    log="browser.log",
                    check=False,
                    cwd=source,
                    timeout=args.browser_timeout_seconds,
                )
                if not args.skip_browser_tests
                else None
            )
            exit_code = result.returncode if result else 0
            if result:
                print(f"Live browser exit={exit_code}; see browser.log", flush=True)
            if args.business_documents_quality:
                command(
                    [*cmd, "exec", "-T", "app", "/ragflow/.venv/bin/python", "-m", "pip", "install", "--disable-pip-version-check", "pytest==9.0.2"],
                    env=env,
                    log="business-quality-dependency.log",
                    timeout=240,
                )
                if args.business_documents_nightly:
                    nightly_script = """
    import json
    import sys
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    sys.path.insert(0, '/ragflow/admin/server')
    from api.db.db_models import DB
    from document_quality_runs import enqueue_nightly_campaign, get_quality_campaign, process_next_run

    progress = Path('/ragflow/qa-evidence/nightly-campaign.json')
    with DB.connection_context():
        day = datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
        campaign = enqueue_nightly_campaign(day)
        progress.write_text(json.dumps(campaign, ensure_ascii=False, indent=2), encoding='utf-8')
        while process_next_run():
            campaign = get_quality_campaign(campaign['id'])
            progress.write_text(json.dumps(campaign, ensure_ascii=False, indent=2), encoding='utf-8')
        campaign = get_quality_campaign(campaign['id'])
        assert campaign and all(run['status'] not in ('PENDING', 'RUNNING') for run in campaign['runs'])
        print(json.dumps({'status': campaign['status'], 'baseline': campaign['baseline_status'],
                          'models': len(campaign['runs']), 'completed': len(campaign['runs'])}))
    """
                    quality_result = command(
                        [*cmd, "exec", "-T", "-e", f"BUSINESS_DOCUMENT_QUALITY_TENANT_ID={tenant['tenant_id']}", "app", "/ragflow/.venv/bin/python", "-"],
                        env=env,
                        log="business-nightly.log",
                        input=nightly_script,
                        check=False,
                        timeout=20 * 60 * 12,
                    )
                    progress = OUT / "nightly-campaign.json"
                    if progress.is_file():
                        campaign = json.loads(progress.read_text(encoding="utf-8"))
                        (OUT / "business-documents-quality.json").write_text(
                            json.dumps(
                                {
                                    "schema_version": "2",
                                    "status": campaign["status"],
                                    "campaign_id": campaign["id"],
                                    "baseline_status": campaign["baseline_status"],
                                    "models": len(campaign["runs"]),
                                    "completed_models": sum(run["status"] not in ("PENDING", "RUNNING") for run in campaign["runs"]),
                                },
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                    exit_code = exit_code or quality_result.returncode
                    print(f"Business Documents nightly exit={quality_result.returncode}; see nightly-campaign.json", flush=True)
                    return exit_code
                if candidate:
                    source_manifest = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))
                    source_revision = source_manifest["identity"]["head"]
                    source_dirty = source_manifest["identity"]["dirty"]
                else:
                    source_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
                    source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=source))
                quality_result = command(
                    [
                        *cmd,
                        "exec",
                        "-T",
                        "-e",
                        "BUSINESS_DOCUMENT_LIVE_LLM=1",
                        "-e",
                        f"BUSINESS_DOCUMENT_LIVE_TENANT_ID={tenant['tenant_id']}",
                        "-e",
                        "BUSINESS_DOCUMENT_QUALITY_REPORT=/ragflow/qa-evidence/business-documents-quality.json",
                        "-e",
                        f"BUSINESS_DOCUMENT_MODEL_DIGEST={identity['ollama_models'][MODELS[0][0]]['digest']}",
                        "-e",
                        f"GITHUB_SHA={source_revision}",
                        "-e",
                        f"RAGFLOW_QA_SOURCE_DIRTY={int(source_dirty)}",
                        *(["-e", f"BUSINESS_DOCUMENT_QUALITY_CASE_ID={args.business_documents_case_id}"] if args.business_documents_case_id else []),
                        "app",
                        "/ragflow/.venv/bin/python",
                        "-m",
                        "pytest",
                        "-q",
                        "-c",
                        "/dev/null",
                        "--confcutdir=/ragflow/test/evals/business_documents",
                        "/ragflow/test/evals/business_documents/test_live_model_quality.py",
                    ],
                    env=env,
                    log="business-quality.log",
                    check=False,
                    timeout=1500 if args.business_documents_case_id else 3600,
                )
                exit_code = exit_code or quality_result.returncode or (2 if args.business_documents_case_id else 0)
                print(f"Business Documents real-model exit={quality_result.returncode}; see business-quality.log", flush=True)
        except Exception as exc:
            exit_code = exit_code or 1
            (OUT / "failure.txt").write_text(sanitized(str(exc)), encoding="utf-8")
            print(sanitized(str(exc)), flush=True)
        finally:
            try:
                if args.worker_service:
                    command([*cmd, "logs", "--no-color", "--tail", "500", "worker"], env=env, log="worker.log", check=False, timeout=30)
                command([*cmd, "logs", "--no-color", "--tail", "250", "app"], env=env, log="app.log", check=False)
            finally:
                cleanup = current().retire_owned()
                (OUT / "cleanup.log").write_text(json.dumps(cleanup, indent=2), encoding="utf-8")
                if cleanup["errors"] or cleanup["blocked"]:
                    raise RuntimeError("Owned QA cleanup incomplete; recovery preserved in lifecycle receipt")
            remain = command(["docker", "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"], env=env, log="remaining-containers.log").stdout.strip()
            volumes = command(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"], env=env, log="remaining-volumes.log").stdout.strip()
            network = command(["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"], env=env, log="remaining-networks.log").stdout.strip()
            assert not remain and not volumes and not network, "Disposable resources remain; see cleanup logs"
            if candidate:
                verify_source(candidate, source, env, "after")
            print("Own containers, volumes and network removed", flush=True)
            note(OUT / "identity.json", receipt=True)
            note(OUT)
            if exit_code == 0:
                current().close_consumption("live-regression-qa", exit_code=0, cleanup=cleanup, receipt=OUT / "identity.json")
        return exit_code
    except BaseException as error:
        failure = error
        raise
    finally:
        note_all([(OUT, "evidence", False)], primary=failure)


if __name__ == "__main__":
    raise SystemExit(main())
