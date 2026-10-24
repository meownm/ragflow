"""Dependency invalidation and native cache integrity across source snapshots."""

import json
from pathlib import Path
import tomllib

import pytest

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
def native(tmp_path):
    source = tmp_path / "source"
    for name in (
        "cmd/main.go",
        "internal/parser/parser.go",
        "internal/agent/templates/document.xml",
        "go.mod",
        "go.sum",
        "build.sh",
        "deployment/runner/build-go-linux.sh",
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


@pytest.mark.parametrize("kind", ["go", "embedded", "library", "toolchain", "flags"])
def test_native_cache_invalidated_by_actual_build_inputs(native, kind, monkeypatch):
    source, libraries, toolchain = native
    before = cache_key(identity(*native))
    if kind == "go":
        (source / "internal/parser/parser.go").write_text("changed")
    elif kind == "embedded":
        (source / "internal/agent/templates/document.xml").write_text("changed")
    elif kind == "library":
        (libraries / "library.a").write_bytes(b"changed")
    elif kind == "toolchain":
        toolchain = "sha256:" + "b" * 64
    else:
        monkeypatch.setenv("GOAMD64", "v3")
    assert cache_key(identity(source, libraries, toolchain)) != before


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
