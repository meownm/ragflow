"""Deployment boundaries which cannot safely rely on a live shared stack."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from deployment.blue_green import COPY_ASSETS, BlueGreen, deployment_config, deployment_lock, gateway_config, shared_runtime

IMAGE = "sha256:" + "a" * 64


def base_config():
    return {
        "services": {
            "ragflow-cpu": {
                "image": IMAGE,
                "command": ["--enable-adminserver"],
                "networks": {"ragflow": {}},
                "environment": {"API_PROXY_SCHEME": "python"},
                "healthcheck": {"test": ["CMD", "curl", "-f", "http://localhost/"]},
                "ports": ["9380:80"],
                "depends_on": {"postgres": {}},
                "volumes": [
                    {"type": "bind", "source": "/config/template", "target": "/ragflow/conf/service_conf.yaml.template", "read_only": True},
                    {"type": "volume", "source": "quality", "target": "/ragflow/quality-evidence"},
                ],
            }
        },
        "networks": {"ragflow": {"name": "existing-ragflow"}},
        "volumes": {"quality": {"name": "existing-quality"}},
    }


def test_slots_have_immutable_code_existing_storage_and_single_background_owner():
    base = base_config()
    original = copy.deepcopy(base)
    config = deployment_config(base, "qa-bg", IMAGE)
    assert base == original
    assert set(config["services"]) == {"web-blue", "web-green", "worker", "gateway"}
    assert config["networks"] == {"ragflow": {"external": True, "name": "existing-ragflow"}}
    assert config["volumes"]["quality"] == {"external": True, "name": "existing-quality"}
    for role in ("web-blue", "web-green", "worker"):
        service = config["services"][role]
        assert "ports" not in service
        assert "depends_on" not in service
        assert service["environment"]["RAGFLOW_SKIP_DATABASE_INIT"] == "true"
        assert service["environment"]["RAGFLOW_BACKGROUND_ENABLED"] == ("true" if role == "worker" else "false")
    for role in ("web-blue", "web-green"):
        assert "--disable-taskexecutor" in config["services"][role]["command"]
        assert "--disable-datasync" in config["services"][role]["command"]
    assert config["services"]["worker"]["command"] == ["--enable-adminserver"]


@pytest.mark.parametrize("mutation", ["source-bind", "spa-bind", "go", "mutable-image", "no-health"])
def test_unsafe_inputs_fail_before_any_runtime_mutation(mutation):
    base = base_config()
    image = IMAGE
    app = base["services"]["ragflow-cpu"]
    if mutation in {"source-bind", "spa-bind"}:
        app["volumes"].append({"type": "bind", "source": "/checkout", "target": "/ragflow/api" if mutation == "source-bind" else "/ragflow/web/dist"})
    elif mutation == "go":
        app["environment"]["API_PROXY_SCHEME"] = "hybrid"
    elif mutation == "mutable-image":
        image = "ragflow:latest"
    else:
        app.pop("healthcheck")
    with pytest.raises(ValueError):
        deployment_config(base, "qa-bg", image)


def test_exclusive_lock_rejects_another_deployer_and_releases_after_failure(tmp_path):
    with pytest.raises(RuntimeError, match="simulated"):
        with deployment_lock(tmp_path):
            with pytest.raises(OSError):
                with deployment_lock(tmp_path):
                    pytest.fail("Concurrent deploy acquired an exclusive lock")
            raise RuntimeError("simulated")
    with deployment_lock(tmp_path):
        pass


def test_initial_gateway_needs_explicit_maintenance_authorization(tmp_path):
    deployer = BlueGreen("qa-bg", tmp_path)
    with pytest.raises(ValueError, match="maintenance window"):
        deployer.deploy(deployment_config(base_config(), "qa-bg", IMAGE))
    assert not deployer.journal.exists()


def test_schema_change_keeps_old_route_and_saved_state(tmp_path, monkeypatch):
    state = {"schema_sha256": "b" * 64, "slot": "web-blue", "token": "c" * 32}
    (tmp_path / "state.json").write_text(json.dumps(state))
    monkeypatch.setattr("deployment.blue_green.command", lambda *args, **kwargs: "a" * 64)
    deployer = BlueGreen("qa-bg", tmp_path)
    with pytest.raises(ValueError, match="Schema/initializer"):
        deployer.deploy(deployment_config(base_config(), "qa-bg", IMAGE))
    assert json.loads(deployer.state_path.read_text()) == state
    assert not deployer.journal.exists()


def test_coexisting_versions_must_keep_storage_and_authentication_configuration():
    previous = deployment_config(base_config(), "qa-bg", IMAGE)
    candidate = deployment_config(base_config(), "qa-bg", "sha256:" + "b" * 64)
    assert shared_runtime(previous, "web-blue") == shared_runtime(candidate, "web-green")
    candidate["services"]["web-green"]["environment"]["RAGFLOW_SECRET_KEY"] = "different"
    assert shared_runtime(previous, "web-blue") != shared_runtime(candidate, "web-green")


def test_first_handover_does_not_skip_a_required_migration(tmp_path, monkeypatch):
    def docker(*args, **kwargs):
        if args[1] == "run":
            return "a" * 64
        if args[1] == "inspect":
            return json.dumps([{"Config": {"Labels": {"com.docker.compose.project": "qa-bg"}}}])
        if args[1] == "exec":
            return "b" * 64
        pytest.fail("Initial schema mismatch must fail before runtime mutation")

    monkeypatch.setattr("deployment.blue_green.command", docker)
    deployer = BlueGreen("qa-bg", tmp_path)
    with pytest.raises(ValueError, match="migrate separately"):
        deployer.deploy(deployment_config(base_config(), "qa-bg", IMAGE), initialize=True)
    assert not deployer.journal.exists()


def test_proxy_does_not_retry_writes_or_buffer_streams_or_force_shutdown():
    config = gateway_config("web-blue", "a" * 32, "qa-bg-web-blue")
    assert "proxy_next_upstream off;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_request_buffering off;" in config
    assert "worker_shutdown_timeout" not in config
    assert "resolver 127.0.0.11 valid=2s ipv6=off;" in config
    assert "set $ragflow_backend qa-bg-web-blue:80;" in config
    assert "proxy_pass http://$ragflow_backend;" in config


@pytest.mark.parametrize("hostname", ["web-blue", "172.20.0.2", "qa-bg-web-green", "qa-bg-web-blue:81", "qa-bg-web-blue;", "../qa-bg-web-blue"])
def test_gateway_rejects_generic_aliases_addresses_and_foreign_slot_names(hostname):
    with pytest.raises(ValueError):
        gateway_config("web-blue", "a" * 32, hostname)


@pytest.mark.parametrize("directory", ["assets", "entry", "chunk"])
def test_versioned_files_are_retained_and_collisions_preserve_existing_bytes(tmp_path, directory):
    source, target = tmp_path / "dist", tmp_path / "retained"
    for name in ("assets", "entry", "chunk"):
        (source / name).mkdir(parents=True)
        (source / name / "v1.js").write_bytes(b"v1")
    script = COPY_ASSETS.replace("'/ragflow/web/dist'", repr(str(source))).replace("'/release-assets'", repr(str(target)))
    exec(script, {})
    destination = target if directory == "assets" else target / directory
    assert (destination / "v1.js").read_bytes() == b"v1"
    (source / directory / "v2.js").write_bytes(b"v2")
    exec(script, {})
    assert (destination / "v1.js").read_bytes() == b"v1"
    assert (destination / "v2.js").read_bytes() == b"v2"
    (source / directory / "v1.js").write_bytes(b"different")
    with pytest.raises(AssertionError, match="Asset name collision"):
        exec(script, {})
    assert (destination / "v1.js").read_bytes() == b"v1"


@pytest.mark.parametrize("directory", ["assets", "entry", "chunk"])
def test_partial_asset_copy_can_be_retried_after_write_failure(tmp_path, directory, monkeypatch):
    import shutil

    source, target = tmp_path / "dist", tmp_path / "retained"
    for name in ("assets", "entry", "chunk"):
        (source / name).mkdir(parents=True)
    data = b"complete JavaScript;" * 1000
    (source / directory / "retry.js").write_bytes(data)
    script = COPY_ASSETS.replace("'/ragflow/web/dist'", repr(str(source))).replace("'/release-assets'", repr(str(target)))
    destination = (target if directory == "assets" else target / directory) / "retry.js"

    def partial_copy(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes()[:100])
        raise OSError("simulated ENOSPC")

    with monkeypatch.context() as fault:
        fault.setattr(shutil, "copyfile", partial_copy)
        with pytest.raises(OSError, match="ENOSPC"):
            exec(script, {})
    assert not destination.exists()
    assert not any(path.is_file() for path in target.rglob("*"))
    exec(script, {})
    assert destination.read_bytes() == data
    exec(script, {})
    assert destination.read_bytes() == data


@pytest.mark.parametrize("directory", ["assets", "entry", "chunk"])
def test_partial_asset_copy_can_be_retried_after_process_kill(tmp_path, directory):
    source, target = tmp_path / "dist", tmp_path / "retained"
    for name in ("assets", "entry", "chunk"):
        (source / name).mkdir(parents=True)
    data = b"complete JavaScript;" * 1000
    (source / directory / "retry.js").write_bytes(data)
    script = COPY_ASSETS.replace("'/ragflow/web/dist'", repr(str(source))).replace("'/release-assets'", repr(str(target)))
    destination = (target if directory == "assets" else target / directory) / "retry.js"
    marker = tmp_path / "partial-copy-ready"
    interrupted = (
        f"""
import pathlib,shutil,time
def partial_copy(src,dst):
    pathlib.Path(dst).write_bytes(pathlib.Path(src).read_bytes()[:100])
    pathlib.Path({str(marker)!r}).write_text('ready')
    time.sleep(60)
shutil.copyfile=partial_copy
"""
        + script
    )
    process = subprocess.Popen([sys.executable, "-B", "-c", interrupted], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "Copy process did not reach the partial write"
        process.kill()
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert not destination.exists()
    # Abrupt termination bypasses finally: leftover staging files cannot poison retry.
    assert any(path.is_file() and path.stat().st_size == 100 for path in target.rglob("*"))
    exec(script, {})
    assert destination.read_bytes() == data
    exec(script, {})
    assert destination.read_bytes() == data


def test_runtime_bootstrap_separates_initialization_and_background_services():
    # Inspect actual guards without importing application bootstrap or its stores.
    import ast

    root = Path(__file__).resolve().parents[3]
    source = ast.parse((root / "api/ragflow_server.py").read_text(encoding="utf-8"))
    guards = [node for node in ast.walk(source) if isinstance(node, ast.If)]
    init = next(node for node in guards if "RAGFLOW_SKIP_DATABASE_INIT" in ast.unparse(node.test))
    assert {"init_web_db", "init_web_data"} <= {node.func.id for node in ast.walk(init) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    background = next(node for node in guards if "RAGFLOW_BACKGROUND_ENABLED" in ast.unparse(node.test))
    assert all(name in ast.unparse(background.orelse) for name in ("start_chat_channels", "start_business_documents", "start_linked_documents"))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith("start_") for node in ast.walk(ast.Module(body=background.body, type_ignores=[])))
