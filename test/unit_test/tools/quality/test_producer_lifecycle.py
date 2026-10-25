"""Public producer paths retain intent, partial output and honest failure state."""

import importlib.util
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

from deployment.runner import go_builder, native_artifacts
from deployment.runner.native_artifacts import OUTPUTS
from test import run_browser_regression as browser
from test.unit_test.deployment.test_build_artifacts import native as native_fixture
from tools.quality import artifact_docker as docker
from tools.quality import artifact_lifecycle as life


@pytest.fixture
def native(tmp_path, monkeypatch):
    return native_fixture.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def state(tmp_path, monkeypatch):
    store = tmp_path / "lifecycle"
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(store))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    for name in ("RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_OWNER_TOKEN", "RAGFLOW_ARTIFACT_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)
    return store


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "real-native",
        "failed-start",
        "failed-partial",
        "interrupted",
        "stale",
        "wrong-toolchain",
        "array",
        "null",
        "array-success",
        "null-success",
        "bad-key",
        "corrupt-output",
        "missing-output",
        "accounting-failure",
    ],
)
def test_go_public_entrypoint_binds_native_receipt_to_invocation(state, native, tmp_path, monkeypatch, outcome):
    source, libraries, _ = native
    cache = tmp_path / "cache"
    builder_id = "sha256:" + "a" * 64
    receipt = source / "output/ci-native/artifacts.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"status": "PASS", "artifact_run_id": "b" * 32, "key": "c" * 64, "toolchain": builder_id}))
    monkeypatch.setenv("RAGFLOW_NATIVE_CACHE", str(libraries))
    monkeypatch.setenv("RAGFLOW_GO_CACHE_ROOT", str(cache))
    monkeypatch.setattr(sys, "argv", ["go_builder.py", "--root", str(source), "--cache", str(tmp_path / "builder-cache"), "--", "--artifacts"])
    monkeypatch.setattr(go_builder, "prepare_builder", lambda *_: builder_id)

    class Engine:
        identity = "synthetic-engine"
        prefix = ["docker"]
        created = []

        def __init__(self, **_):
            pass

        def inspect(self, kind, reference):
            assert kind == "image" and reference == builder_id
            return {"Id": builder_id}

        def owned(self, _):
            return self.created

    monkeypatch.setattr(docker, "Engine", Engine)
    original_note = life.note

    def record(path, **kwargs):
        if outcome == "accounting-failure" and path == source / OUTPUTS[0] and path.exists():
            raise PermissionError("confidential-accounting-canary")
        return original_note(path, **kwargs)

    monkeypatch.setattr(life, "note", record)

    def invoke(command, **_):
        run = life.current()
        durable = life.read_run(state, run.data["run_id"])
        labels = dict(value.split("=", 1) for previous, value in zip(command, command[1:]) if previous == "--label")
        token = labels[life.OBJECT]
        assert any(item.get("object") == token for item in durable["creation_intents"])
        initial = json.loads(receipt.read_bytes())
        assert initial["status"] == "INCOMPLETE" and initial["artifact_run_id"] == run.data["run_id"]
        assert f"RAGFLOW_NATIVE_INVOCATION={initial['invocation']}" in command
        if outcome == "failed-start":
            raise subprocess.CalledProcessError(7, ["synthetic-builder"])
        Engine.created.append({"key": "container:synthetic", "kind": "container", "identity": token, "token": token, "category": "disposable", "status": "present"})
        if outcome == "real-native":

            def compile_outputs(_command, **_kwargs):
                for name in OUTPUTS:
                    path = source / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(name.encode())

            with monkeypatch.context() as child:
                if sys.platform == "win32":
                    child.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, flock=lambda *args: None))
                child.setattr(sys, "argv", ["native_artifacts.py", "--root", str(source), "--cache", str(cache / "artifacts"), "--libraries", str(libraries), "--toolchain", builder_id])
                child.setenv("RAGFLOW_ARTIFACT_TRANSPORT", "receipt")
                child.setenv("RAGFLOW_NATIVE_INVOCATION", initial["invocation"])
                child.setenv("GOCACHE", str(cache / "build"))
                child.setattr(native_artifacts, "subprocess", SimpleNamespace(run=compile_outputs))
                native_artifacts.main()
            assert len(list(life.runs(state))) == 1
            assert json.loads(receipt.read_bytes())["invocation"] == initial["invocation"]
            return SimpleNamespace(returncode=0)
        for name in OUTPUTS if outcome not in {"failed-partial", "array", "null", "interrupted"} else OUTPUTS[:1]:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"partial native bytes")
        payload = {**initial, "status": "PASS", "key": "d" * 64, "outputs": {name: hashlib.sha256(b"partial native bytes").hexdigest() for name in OUTPUTS}}
        if outcome == "stale":
            payload["invocation"] = "e" * 32
        elif outcome == "wrong-toolchain":
            payload["toolchain"] = "sha256:" + "f" * 64
        elif outcome in {"array", "null", "array-success", "null-success"}:
            payload = [] if outcome.startswith("array") else None
        elif outcome == "bad-key":
            payload["key"] = []
        elif outcome == "corrupt-output":
            (source / OUTPUTS[0]).write_bytes(b"different bytes")
        elif outcome == "missing-output":
            (source / OUTPUTS[1]).unlink()
        receipt.write_text(json.dumps(payload))
        if outcome in {"failed-partial", "array", "null", "accounting-failure"}:
            raise subprocess.CalledProcessError(7, ["synthetic-builder"])
        if outcome == "interrupted":
            raise KeyboardInterrupt()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(go_builder, "subprocess", SimpleNamespace(run=invoke))
    expected_error = (
        KeyboardInterrupt if outcome == "interrupted" else subprocess.CalledProcessError if outcome in {"failed-start", "failed-partial", "array", "null", "accounting-failure"} else ValueError
    )
    if outcome in {"success", "real-native"}:
        go_builder.main()
    else:
        with pytest.raises(expected_error) as failure:
            go_builder.main()
        if outcome == "accounting-failure":
            assert failure.value.returncode == 7
            assert any("PermissionError" in note for note in failure.value.__notes__)
    (saved,) = list(life.runs(state))
    assert saved["state"] == ("completed" if outcome in {"success", "real-native"} else "failed")
    paths = {item.get("path") for item in saved["resources"]}
    assert str(receipt.absolute()) in paths
    if outcome != "failed-start":
        assert str((source / OUTPUTS[1] if outcome == "accounting-failure" else source / OUTPUTS[0]).absolute()) in paths
        assert any(item["kind"] == "container" for item in saved["resources"])
    assert all(item.get("path") != str(cache / "artifacts" / ("c" * 64)) for item in saved["resources"])
    if outcome in {"stale", "wrong-toolchain", "array", "null", "array-success", "null-success"}:
        assert saved["receipts"] == []


@pytest.mark.parametrize("outcome", ["success", "nonzero", "interrupted"])
def test_browser_public_entrypoint_closes_listener_and_accounts_evidence(state, tmp_path, monkeypatch, outcome):
    dist, evidence = tmp_path / "dist", tmp_path / "browser-output"
    dist.mkdir()
    (dist / "index.html").write_bytes(b"<!doctype html><title>synthetic SPA</title>")
    monkeypatch.setenv("PW_ARTIFACTS_DIR", str(evidence))
    monkeypatch.setattr(sys, "argv", ["run_browser_regression.py", "--dist-root", str(dist), "--suite", browser.SUITES[0]])
    endpoints = []

    def invoke(_command, **kwargs):
        endpoint = kwargs["env"]["RAGFLOW_BASE_URL"]
        endpoints.append(endpoint)
        with urlopen(endpoint + "/nested/route", timeout=5) as response:
            assert response.read() == (dist / "index.html").read_bytes()
        evidence.mkdir()
        (evidence / "result.log").write_text("partial browser evidence")
        if outcome == "interrupted":
            raise KeyboardInterrupt()
        return 0 if outcome == "success" else 7

    monkeypatch.setattr(browser, "subprocess", SimpleNamespace(call=invoke))
    if outcome == "interrupted":
        with pytest.raises(KeyboardInterrupt):
            browser.main()
    else:
        assert browser.main() == (0 if outcome == "success" else 7)
    port = int(endpoints[0].rsplit(":", 1)[1])
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    (saved,) = list(life.runs(state))
    assert saved["state"] == ("completed" if outcome == "success" else "failed")
    assert {item.get("path") for item in saved["resources"]} >= {str(dist), str(evidence)}
    assert (evidence / "result.log").read_text() == "partial browser evidence"


def test_browser_script_bootstraps_imports_from_another_working_directory(state, tmp_path):
    dist = tmp_path / "missing-dist"
    completed = subprocess.run([sys.executable, "-B", str(Path(browser.__file__)), "--dist-root", str(dist)], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 2
    assert "Build the frontend first" in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    (saved,) = life.runs(state)
    assert saved["state"] == "failed"
    assert any(item.get("path") == str(dist) for item in saved["creation_intents"])


def test_benchmark_public_entrypoint_accounts_early_setup_failure(state, tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[4] / "deployment/runner/benchmark-build.py"
    spec = importlib.util.spec_from_file_location("qa_build_benchmark", path)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    evidence = tmp_path / "benchmark"
    monkeypatch.setattr(sys, "argv", [str(path), "--before", str(tmp_path / "before"), "--after", str(tmp_path / "after"), "--output", str(evidence)])

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic daemon unavailable")

    monkeypatch.setattr(benchmark, "subprocess", SimpleNamespace(check_output=unavailable))
    with pytest.raises(RuntimeError, match="synthetic daemon"):
        benchmark.main()
    (saved,) = list(life.runs(state))
    assert saved["state"] == "failed"
    assert any(item.get("path") == str(evidence) for item in saved["resources"])
    assert any(item.get("path") == str(evidence) for item in saved["creation_intents"])
