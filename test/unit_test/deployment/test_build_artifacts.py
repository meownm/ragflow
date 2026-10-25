"""Dependency invalidation and native cache integrity across source snapshots."""

import json
import os
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace

import pytest

from deployment.runner import native_artifacts
from deployment.runner.native_artifacts import OUTPUTS, cache_key, identity, restore, store
from deployment.runner.python_dependency_inputs import normalize


ROOT = Path(__file__).resolve().parents[3]


def test_release_version_does_not_change_dependency_inputs():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    old_version = tomllib.loads(project)["project"]["version"]
    assert normalize(project, lock) == normalize(
        project.replace(f'version = "{old_version}"', 'version = "999.0.0"', 1),
        lock.replace(f'name = "ragflow"\nversion = "{old_version}"', 'name = "ragflow"\nversion = "999.0.0"'),
    )
    dependency = next(package for package in tomllib.loads(lock)["package"] if package["name"] == "litellm")
    dependency_version = dependency["version"]
    changed = lock.replace(f'name = "litellm"\nversion = "{dependency_version}"', f'name = "litellm"\nversion = "{dependency_version}.post1"')
    assert changed != lock
    assert normalize(project, changed) != normalize(project, lock)


@pytest.mark.parametrize("mutation", ["root-version", "nonvirtual"])
def test_invalid_dependency_inputs_fail_closed(mutation):
    project = '[project]\nname = "example"\nversion = "1.0.0"\n'
    lock = '[[package]]\nname = "example"\nversion = "1.0.0"\nsource = { virtual = "." }\n'
    lock = lock.replace('"1.0.0"', '"2.0.0"') if mutation == "root-version" else lock.replace("virtual", "editable")
    with pytest.raises(ValueError):
        normalize(project, lock)


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(tmp_path / "lifecycle"))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    for name in ("RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_OWNER_TOKEN", "RAGFLOW_ARTIFACT_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "source"
    for name in (
        "cmd/main.go",
        "internal/parser/parser.go",
        "internal/agent/templates/document.xml",
        "go.mod",
        "go.sum",
        "build.sh",
        "deployment/runner/build-go-linux.sh",
        "deployment/runner/go_builder.py",
        "deployment/runner/Dockerfile.go-builder",
        "deployment/runner/native_artifacts.py",
        "ragflow_deps/prepare_native.py",
        "ragflow_deps/native-deps.json",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("input " + name)
    libraries = tmp_path / "libraries"
    libraries.mkdir()
    (libraries / "library.a").write_bytes(b"library")
    toolchain = "sha256:" + "a" * 64
    return source, libraries, toolchain


@pytest.mark.parametrize("name", ["api/scenario.py", "web/src/app.tsx", "pyproject.toml", "uv.lock", "internal/binding/cpp/cmake-build-release/CMakeCache.txt"])
def test_python_ui_version_and_generated_outputs_reuse_native_artifacts(native, name):
    source, libraries, toolchain = native
    before = cache_key(identity(*native))
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed")
    assert cache_key(identity(source, libraries, toolchain)) == before


@pytest.mark.parametrize(
    "name",
    [
        "deployment/runner/build-go-linux.sh",
        "deployment/runner/go_builder.py",
        "deployment/runner/Dockerfile.go-builder",
        "deployment/runner/native_artifacts.py",
    ],
)
def test_orchestration_edits_reuse_native_artifacts_with_the_same_toolchain(native, tmp_path, name):
    source, _, _ = native
    key = cache_key(identity(*native))
    for output in OUTPUTS:
        path = source / output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(output.encode())
    entry = tmp_path / "cache" / key
    store(entry, source, key)
    path = source / name
    path.write_text(path.read_text() + "\n# changed logging only\n")
    after = cache_key(identity(*native))
    assert after == key
    for _ in range(2):
        for output in OUTPUTS:
            (source / output).unlink()
        assert restore(entry, source, after) is not None
        assert all((source / output).read_bytes() == output.encode() for output in OUTPUTS)


@pytest.mark.parametrize("kind", ["go", "cpp", "embedded", "recipe", "modules", "native-profile", "verification", "library", "toolchain", "flags", "command", "cpp-command", "go-flags"])
def test_native_cache_invalidated_by_actual_build_inputs(native, kind, monkeypatch):
    source, libraries, toolchain = native
    before = cache_key(identity(*native))
    if kind == "go":
        (source / "internal/parser/parser.go").write_text("changed")
    elif kind in {"cpp", "recipe", "modules", "native-profile", "verification"}:
        name = {
            "cpp": "internal/binding/cpp/CMakeLists.txt",
            "recipe": "build.sh",
            "modules": "go.mod",
            "native-profile": "ragflow_deps/native-deps.json",
            "verification": "ragflow_deps/prepare_native.py",
        }[kind]
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed")
    elif kind == "embedded":
        (source / "internal/agent/templates/document.xml").write_text("changed")
    elif kind == "library":
        (libraries / "library.a").write_bytes(b"changed")
    elif kind == "toolchain":
        toolchain = "sha256:" + "b" * 64
    elif kind == "command":
        monkeypatch.setattr(native_artifacts, "GO_COMMAND", ("bash", "build.sh", "--go"))
    elif kind == "cpp-command":
        monkeypatch.setattr(native_artifacts, "CPP_COMMAND", (*native_artifacts.CPP_COMMAND, "--verbose"))
    elif kind == "go-flags":
        monkeypatch.setattr(native_artifacts, "GO_FLAGS", (*native_artifacts.GO_FLAGS, "-race"))
    else:
        monkeypatch.setenv("GOAMD64", "v3")
    assert cache_key(identity(source, libraries, toolchain)) != before


def test_native_entrypoint_builds_once_and_restores_after_orchestration_edits(native, tmp_path, monkeypatch):
    source, libraries, toolchain = native
    # Exercise cache policy on Windows too; Linux locking is separate from it.
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, flock=lambda *args: None))
    monkeypatch.setattr(sys, "argv", ["native_artifacts.py", "--root", str(source), "--libraries", str(libraries), "--toolchain", toolchain, "--cache", str(tmp_path / "cache")])
    monkeypatch.setenv("GOCACHE", str(tmp_path / "go-cache"))
    monkeypatch.setenv("GOFLAGS", "-mod=readonly")
    commands = []

    def compile_outputs(command, *, cwd, check, env=None):
        assert cwd == source and check is True
        commands.append(command)
        if env is not None:
            assert env["GOFLAGS"] == "-mod=readonly -trimpath -buildvcs=false"
        outputs = OUTPUTS[2:] if len(commands) == 1 else OUTPUTS[:2]
        for name in outputs:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())

    monkeypatch.setattr(native_artifacts, "subprocess", SimpleNamespace(run=compile_outputs))
    native_artifacts.main()
    first = json.loads((source / "output/ci-native/artifacts.json").read_text())
    assert first["cache_hit"] is False and first["status"] == "PASS"
    assert commands == [native_artifacts.CPP_COMMAND, native_artifacts.GO_COMMAND]

    def unexpected_compile(*args, **kwargs):
        raise AssertionError("Orchestration edits must restore artifacts without compilation")

    monkeypatch.setattr(native_artifacts, "subprocess", SimpleNamespace(run=unexpected_compile))
    for name in ("go_builder.py", "native_artifacts.py"):
        with (source / "deployment/runner" / name).open("a") as stream:
            stream.write("\n# logging only\n")
        for output in OUTPUTS:
            (source / output).unlink()
        native_artifacts.main()
        receipt = json.loads((source / "output/ci-native/artifacts.json").read_text())
        assert receipt["cache_hit"] is True and receipt["status"] == "PASS"
        assert receipt["key"] == first["key"] and receipt["outputs"] == first["outputs"]
        assert all((source / output).read_bytes() == output.encode() for output in OUTPUTS)


@pytest.mark.parametrize("outcome", ["success", "cpp-failure", "go-failure", "interrupted"])
def test_native_public_entrypoint_accounts_for_partial_outputs_and_failure(native, tmp_path, monkeypatch, outcome):
    from tools.quality import artifact_lifecycle as life

    source, libraries, toolchain = native
    if sys.platform == "win32":
        monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, flock=lambda *args: None))
    cache = tmp_path / "cache"
    receipt = source / "output/ci-native/artifacts.json"
    monkeypatch.setattr(sys, "argv", ["native_artifacts.py", "--root", str(source), "--libraries", str(libraries), "--toolchain", toolchain, "--cache", str(cache)])
    monkeypatch.setenv("GOCACHE", str(tmp_path / "go-cache"))
    calls = []

    def compile_outputs(command, **kwargs):
        saved = list(life.runs(tmp_path / "lifecycle"))
        assert len(saved) == 1 and saved[0]["state"] == "running"
        intents = {item.get("path") for item in saved[0]["creation_intents"]}
        assert str(cache.absolute()) in intents and str(receipt.absolute()) in intents
        calls.append(command)
        outputs = OUTPUTS[2:] if len(calls) == 1 else OUTPUTS[:2]
        for name in outputs:
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(name.encode())
        if outcome == "interrupted":
            raise KeyboardInterrupt()
        if (outcome == "cpp-failure" and len(calls) == 1) or (outcome == "go-failure" and len(calls) == 2):
            raise RuntimeError("synthetic compile failure")

    monkeypatch.setattr(native_artifacts, "subprocess", SimpleNamespace(run=compile_outputs))
    if outcome == "success":
        native_artifacts.main()
    else:
        with pytest.raises(KeyboardInterrupt if outcome == "interrupted" else RuntimeError):
            native_artifacts.main()
    saved = list(life.runs(tmp_path / "lifecycle"))
    assert len(saved) == 1
    run = saved[0]
    assert run["state"] == ("completed" if outcome == "success" else "failed")
    recorded = {item.get("path"): item for item in run["resources"]}
    assert recorded[str(libraries.absolute())]["category"] == "runtime"
    assert str(receipt.absolute()) in recorded
    assert {str((source / name).absolute()) for name in OUTPUTS if (source / name).exists()} <= recorded.keys()
    assert any(item["path"] == str(receipt.absolute()) for item in run["receipts"])
    assert json.loads(receipt.read_bytes())["status"] == ("PASS" if outcome == "success" else "INCOMPLETE")
    assert "RAGFLOW_ARTIFACT_RUN_ID" not in os.environ


def test_native_receipt_transport_requires_existing_parent_before_creating_anything(native, tmp_path, monkeypatch):
    monkeypatch.setenv("RAGFLOW_ARTIFACT_TRANSPORT", "receipt")
    with pytest.raises(ValueError, match="host lifecycle run"):
        native_artifacts.main()
    assert not (tmp_path / "lifecycle").exists()
    assert not (native[0] / "output").exists()


@pytest.mark.parametrize("corruption", [None, "binary", "missing", "manifest", "unexpected-output", "wrong-key"])
def test_native_restore_checks_all_outputs_before_writing(native, tmp_path, corruption):
    source, _, _ = native
    entry = tmp_path / "cache"
    for name in OUTPUTS:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    key = cache_key(identity(*native))
    manifest = store(entry, source, key)
    for name in OUTPUTS:
        (source / name).write_bytes(b"existing output")
    if corruption == "binary":
        (entry / "ragflow-cli").write_bytes(b"corrupt")
    elif corruption == "missing":
        (entry / "ragflow_server").unlink()
    elif corruption == "manifest":
        (entry / "manifest.json").write_text("[]")
    elif corruption in ("unexpected-output", "wrong-key"):
        if corruption == "unexpected-output":
            manifest["outputs"]["../foreign"] = "invalid"
        else:
            manifest["key"] = "b" * 64
        (entry / "manifest.json").write_text(json.dumps(manifest))
    result = restore(entry, source, key)
    assert (result is None) == (corruption is not None)
    for name in OUTPUTS:
        assert (source / name).read_bytes() == (b"existing output" if corruption else name.encode())


def test_cache_cannot_skip_required_toolchain_or_libraries(native):
    source, libraries, _ = native
    with pytest.raises(ValueError, match="immutable"):
        identity(source, libraries, "mutable:latest")
    with pytest.raises(ValueError, match="missing"):
        identity(source, libraries / "absent", "sha256:" + "a" * 64)
