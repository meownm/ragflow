"""Guard the live runner's log decoding and bounded child-process cleanup."""

import io
import json
import os
import sys
from contextlib import nullcontext
from time import monotonic
from types import SimpleNamespace

import pytest

from test.integration.live_ragflow import runner


@pytest.fixture(autouse=True)
def lifecycle_state(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(tmp_path / "lifecycle"))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)


def test_quality_setup_failure_returns_nonzero_and_cleans_own_project(tmp_path, monkeypatch):
    from api.utils import crypt as encryption

    logs = []

    def command(args, **kwargs):
        logs.append(kwargs["log"])
        if kwargs["log"] == "business-quality-dependency.log":
            raise RuntimeError("Synthetic SDK setup failure")
        return SimpleNamespace(returncode=0, stdout="")

    def api(path, *_args, **_kwargs):
        if path == "/api/v1/auth/login":
            return {"is_superuser": True}, "synthetic-token"
        return {"tenant_id": "synthetic-tenant"}, None

    def urlopen(request, **_kwargs):
        url = request if isinstance(request, str) else request.full_url
        data = (
            {"models": [{"name": name, "digest": "synthetic-weights", "size": 2_000_000} for name, _ in runner.MODELS]}
            if url.endswith("/api/tags")
            else {"model_info": {"general.architecture": "test", "test.context_length": 8192}}
        )
        return io.BytesIO(json.dumps(data).encode())

    def git_output(args, **kwargs):
        value = "" if "ls-files" in args else "synthetic-head"
        return value if kwargs.get("text") else value.encode()

    monkeypatch.setattr(
        sys,
        "argv",
        ["runner.py", "--source-root", str(runner.ROOT), "--dist-root", str(tmp_path / "dist"), "--evidence-dir", str(tmp_path / "evidence"), "--business-documents-quality", "--skip-browser-tests"],
    )
    monkeypatch.setattr(runner, "command", command)
    monkeypatch.setattr(runner, "api", api)
    monkeypatch.setattr(runner, "urlopen", urlopen)
    monkeypatch.setattr(runner, "SECRET_VALUES", [])
    monkeypatch.setattr(runner.subprocess, "check_output", git_output)
    monkeypatch.setattr(runner.socket, "socket", lambda *_: nullcontext(SimpleNamespace(bind=lambda *_: None)))
    monkeypatch.setattr(encryption, "crypt", lambda _: "synthetic-encrypted-password")
    from tools.quality import artifact_docker

    class Engine:
        identity = "synthetic-engine"

        def __init__(self, **_):
            pass

        def owned(self, *_):
            return []

        def stop_owned(self, *_):
            pass

    monkeypatch.setattr(artifact_docker, "Engine", Engine)

    assert runner.main() != 0
    assert (tmp_path / "evidence/failure.txt").read_text(encoding="utf-8") == "Synthetic SDK setup failure"
    assert logs[-4:] == ["app.log", "remaining-containers.log", "remaining-volumes.log", "remaining-networks.log"]
    assert json.loads((tmp_path / "evidence/cleanup.log").read_text())["errors"] == []
    from tools.quality.artifact_lifecycle import runs

    (saved,) = list(runs(tmp_path / "lifecycle"))
    assert saved["state"] == "failed"
    assert saved["teardown_request"]["evidence"] == str(tmp_path / "evidence")
    assert any(item.get("path") == str(tmp_path / "evidence") for item in saved["resources"])


@pytest.mark.parametrize("architecture,context", [("qwen3", 262144), ("bert", 8192)])
def test_native_model_context_is_preserved(architecture, context):
    metadata = {"model_info": {"general.architecture": architecture, f"{architecture}.context_length": context}}
    assert runner.model_context_length(metadata) == context


@pytest.mark.parametrize(
    "info", [{}, {"qwen3.context_length": 262144}, {"general.architecture": "qwen3"}, *({"general.architecture": "qwen3", "qwen3.context_length": value} for value in (True, "262144", 0, -1, 8192.5))]
)
def test_missing_or_invalid_native_context_fails_closed(info):
    with pytest.raises(ValueError, match="context_length"):
        runner.model_context_length({"model_info": info})


def test_retrieval_only_run_does_not_require_unused_chat_model():
    minimal = runner.models_for_run(["test/playwright/e2e/test_source_workbench_quality_live.py"], False)
    assert minimal == (runner.MODELS[0], runner.MODELS[-1])
    assert runner.models_for_run(["test/playwright/e2e/test_source_workbench_quality_live.py", "other.py"], False) == minimal


def test_four_workbench_live_checks_never_validate_an_unrequested_model():
    assert runner.models_for_run(["test/playwright/e2e/test_workbench_integrations_live.py"], False) == (("qwen3.8:latest", "chat"),)
    assert runner.models_for_run([], True) == (("qwen3.8:latest", "chat"),)


def test_generated_browser_profile_imports_and_registers(monkeypatch):
    monkeypatch.setenv("RAGFLOW_BASE_URL", runner.BASE)
    namespace = {}
    exec(compile(runner.BROWSER_PROFILE, "live_profile.py", "exec"), namespace)
    registered = []
    config = SimpleNamespace(pluginmanager=SimpleNamespace(register=lambda plugin, name: registered.append((plugin, name))))
    namespace["pytest_configure"](config)
    assert registered[0][1] == "t1-english"
    assert registered[0][0].browser_context_args.__wrapped__(registered[0][0])["storage_state"]["origins"][0]["origin"] == runner.BASE


def test_additional_chat_model_requires_explicit_selection():
    assert runner.models_for_run(["other.py"], False, ["qwen3.8:latest", "qwen3.6:27b"]) == (("qwen3.8:latest", "chat"), ("qwen3.6:27b", "chat"))


def test_two_model_journey_fails_before_setup_without_explicit_models():
    with pytest.raises(ValueError, match="explicitly authorized"):
        runner.models_for_run(["test_live_document_roundtrip.py"], False)


def test_command_decodes_utf8_and_tolerates_invalid_log_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "OUT", tmp_path)
    result = runner.command(
        [sys.executable, "-c", "import os;os.write(1, 'Привет 😀'.encode('utf-8') + b'\\xff')"],
        env=dict(os.environ),
        log="unicode.log",
    )
    assert result.returncode == 0
    assert (tmp_path / "unicode.log").read_text(encoding="utf-8") == "Привет 😀\ufffd"


def test_compose_instrumentation_uses_child_qa_environment_without_leaking_it(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "OUT", tmp_path)
    monkeypatch.delenv("QA_STORAGE_PASSWORD", raising=False)

    def adapt(args, **_):
        assert os.environ["QA_STORAGE_PASSWORD"] == "synthetic-secret"
        return args

    monkeypatch.setattr(runner, "docker_args", adapt)
    result = runner.command([sys.executable, "-c", "print('ok')"], env={**os.environ, "QA_STORAGE_PASSWORD": "synthetic-secret"}, log="environment.log")
    assert result.returncode == 0
    assert "QA_STORAGE_PASSWORD" not in os.environ
    assert "synthetic-secret" not in (tmp_path / "environment.log").read_text()


def test_command_timeout_terminates_children_holding_output_pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "OUT", tmp_path)
    script = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print('child started',flush=True);time.sleep(60)"
    started = monotonic()
    result = runner.command([sys.executable, "-c", script], env=dict(os.environ), log="timeout.log", timeout=1, check=False)
    assert result.returncode == 124
    assert "child started" in result.stdout
    assert monotonic() - started < 15, "A child kept the pipe open after the timeout"
    assert "Command timeout" in (tmp_path / "timeout.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("budget", ["0", "59", "7201", "invalid"])
def test_invalid_browser_budget_is_rejected_before_runtime_access(tmp_path, monkeypatch, budget):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner.py", "--source-root", str(runner.ROOT), "--dist-root", str(tmp_path / "dist"), "--evidence-dir", str(tmp_path / "evidence"), "--browser-timeout-seconds", budget],
    )
    monkeypatch.setattr(runner, "command", lambda *_args, **_kwargs: pytest.fail("Invalid budget must not access Docker"))
    monkeypatch.setattr(runner, "urlopen", lambda *_args, **_kwargs: pytest.fail("Invalid budget must not access a provider"))
    with pytest.raises(SystemExit) as stopped:
        runner.main()
    assert stopped.value.code == 2
