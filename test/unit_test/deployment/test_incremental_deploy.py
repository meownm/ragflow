"""Repeat deployment preparation without replaying unchanged work."""

import json
import importlib.util
from contextlib import nullcontext
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tarfile
from threading import Barrier
from types import SimpleNamespace

import pytest

from deployment.runner import feature_image, go_builder
from tools.quality import candidate, check_cache


@pytest.fixture(autouse=True)
def isolated_producer_context(monkeypatch):
    # Mock Docker boundaries belong to these tests, not a parent CLI producer.
    for name in ("RAGFLOW_ARTIFACT_RUN_ID", "RAGFLOW_ARTIFACT_OWNER_TOKEN", "RAGFLOW_ARTIFACT_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)


def test_successful_check_runs_once_and_changed_input_reruns(tmp_path):
    count = tmp_path / "count"
    source = tmp_path / "source"
    source.write_text("first")
    command = [sys.executable, "-c", "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]

    def inputs():
        return {"source": candidate.file_digest(source)}

    first = check_cache.run_check(command, tmp_path / "cache", inputs, cwd=tmp_path)
    repeat = check_cache.run_check(command, tmp_path / "cache", inputs, cwd=tmp_path)
    assert first["cache_hit"] is False and repeat["cache_hit"] is True
    assert count.read_text() == "x"
    source.write_text("second")
    assert check_cache.run_check(command, tmp_path / "cache", inputs, cwd=tmp_path)["cache_hit"] is False
    assert count.read_text() == "xx"


@pytest.mark.parametrize("failure", ["command", "changed-during-run", "interrupted"])
def test_failed_or_interrupted_check_cannot_leave_success(tmp_path, failure, monkeypatch):
    identity = {"source": "before"}
    command = [sys.executable, "-c", "pass"]
    cache = tmp_path / "cache"
    check_cache.run_check(command, cache, lambda: identity.copy(), cwd=tmp_path)

    def run(*args, **kwargs):
        if failure == "changed-during-run":
            identity["source"] = "after"
        elif failure == "interrupted":
            raise KeyboardInterrupt
        else:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises((subprocess.CalledProcessError, ValueError, KeyboardInterrupt)):
        check_cache.run_check(command, cache, lambda: identity.copy(), cwd=tmp_path, force=True)
    assert not list(cache.glob("*.json"))


def test_corrupt_receipt_reruns_and_command_change_invalidates(tmp_path):
    cache = tmp_path / "cache"
    command = [sys.executable, "-c", "pass"]
    first = check_cache.run_check(command, cache, lambda: {}, cwd=tmp_path)
    (cache / (first["key"] + ".json")).write_text("[]")
    assert not check_cache.run_check(command, cache, lambda: {}, cwd=tmp_path)["cache_hit"]
    assert not check_cache.run_check(command[:-1] + ["x=1"], cache, lambda: {}, cwd=tmp_path)["cache_hit"]


def test_checkout_identity_tracks_dependencies_environment_and_selected_files(tmp_path, monkeypatch):
    original = subprocess.check_output

    def git_files(command, *args, **kwargs):
        if command[0] == "git":
            return b"code.py\0test.py\0uv.lock\0"
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", git_files)
    for name in ("code.py", "test.py", "uv.lock"):
        (tmp_path / name).write_text("first")
    before = check_cache.checkout_inputs(tmp_path, [])
    (tmp_path / "test.py").write_text("changed assertion")
    assert check_cache.checkout_inputs(tmp_path, []) != before
    selected = check_cache.checkout_inputs(tmp_path, ["code.py", "uv.lock"])
    monkeypatch.setenv("CODEX_TOOL_CALL_ID", "another-invocation")
    assert check_cache.checkout_inputs(tmp_path, ["code.py", "uv.lock"]) == selected
    monkeypatch.setenv("RAGFLOW_ARTIFACT_RUN_ID", "another-owner-run")
    monkeypatch.setenv("RAGFLOW_ARTIFACT_OWNER_TOKEN", "another-capability")
    assert check_cache.checkout_inputs(tmp_path, ["code.py", "uv.lock"]) == selected
    (tmp_path / "test.py").write_text("other assertion")
    assert check_cache.checkout_inputs(tmp_path, ["code.py", "uv.lock"]) == selected
    monkeypatch.setenv("INCREMENTAL_TEST_FLAG", "changed")
    assert check_cache.checkout_inputs(tmp_path, ["code.py", "uv.lock"]) != selected
    with pytest.raises(ValueError, match="No check inputs"):
        check_cache.checkout_inputs(tmp_path, ["missing.py"])


def test_incomplete_distribution_metadata_runs_checks_without_reusing_or_saving_pass(tmp_path, monkeypatch):
    source = tmp_path / "code.py"
    source.write_text("source")
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: b"code.py\0")
    monkeypatch.setattr(check_cache.metadata, "distributions", lambda: [SimpleNamespace(metadata=None, locate_file=lambda _: tmp_path / "packages")])

    def inputs():
        return check_cache.checkout_inputs(tmp_path, [])

    count = tmp_path / "count"
    command = [sys.executable, "-c", "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]
    for _ in range(2):
        result = check_cache.run_check(command, tmp_path / "cache", inputs, cwd=tmp_path)
        assert result["status"] == "PASS" and not result["cache_hit"] and not result["cacheable"]
        assert result["incomplete_environment"] == [str(tmp_path / "packages")]
    assert count.read_text() == "xx"
    assert not list((tmp_path / "cache").glob("*.json"))


@pytest.mark.parametrize("direct_url", ['{"dir_info":{"editable":true}}', "{", "[]"])
def test_editable_or_unknown_dependency_is_not_cacheable(tmp_path, monkeypatch, direct_url):
    (tmp_path / "code.py").write_text("source")
    original = subprocess.check_output
    monkeypatch.setattr(subprocess, "check_output", lambda command, **kwargs: b"code.py\0" if command[0] == "git" else original(command, **kwargs))
    distribution = SimpleNamespace(
        metadata={"Name": "external-package", "Version": "1.0"}, read_text=lambda name: direct_url if name == "direct_url.json" else "unchanged-record", locate_file=lambda _: tmp_path / "packages"
    )
    monkeypatch.setattr(check_cache.metadata, "distributions", lambda: [distribution])
    command = [sys.executable, "-c", "from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"]
    for _ in range(2):
        result = check_cache.run_check(command, tmp_path / "cache", lambda: check_cache.checkout_inputs(tmp_path, []), cwd=tmp_path)
        assert result["cacheable"] is False
    assert (tmp_path / "count").read_text() == "xx"


@pytest.mark.parametrize("arguments", [["--artifacts"], ["--test", "./internal/parser/..."]])
def test_shared_builder_preserves_host_user_and_writable_cache_home(tmp_path, monkeypatch, arguments):
    source, libraries, cache = (tmp_path / name for name in ("source", "libraries", "cache"))
    source.mkdir()
    libraries.mkdir()
    monkeypatch.setenv("RAGFLOW_NATIVE_CACHE", str(libraries))
    monkeypatch.setenv("RAGFLOW_GO_CACHE_ROOT", str(cache))
    monkeypatch.setattr(go_builder.os, "getuid", lambda: 1234, raising=False)
    monkeypatch.setattr(go_builder.os, "getgid", lambda: 5678, raising=False)
    commands = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: commands.append(command))
    go_builder.run_builder(source, "sha256:" + "a" * 64, arguments)
    command = commands[0]
    assert command[command.index("--user") + 1] == "1234:5678"
    assert "HOME=/tmp/builder-home" in command
    assert f"type=bind,src={libraries},dst=/tmp/builder-home/ragflow-native-libs,readonly" in command
    assert all((cache / name).is_dir() for name in ("build", "modules", "artifacts"))


@pytest.fixture
def image_build(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    source = snapshot / "source"
    names = [
        "cmd/main.go",
        "internal/parser/parser.go",
        "go.mod",
        "go.sum",
        "build.sh",
        "deployment/runner/build-go-linux.sh",
        "deployment/runner/go_builder.py",
        "deployment/runner/Dockerfile.go-builder",
        "deployment/runner/native_artifacts.py",
        "ragflow_deps/prepare_native.py",
        "ragflow_deps/native-deps.json",
        "Dockerfile",
        "api/main.py",
    ]
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("input " + name)
    (source / "pyproject.toml").write_text('[project]\nversion="1.0.0"\n')
    identity = {"schema": 1, "head": "a" * 40, "dirty": True, "version": "1.0.0", "build": {"mode": "feature"}, "files": candidate.tree_files(source)}

    def manifest():
        identity["files"] = candidate.tree_files(source)
        data = {"identity": identity, "source_id": candidate.digest(candidate.canonical(identity))}
        (snapshot / "candidate.json").write_bytes(candidate.canonical(data))
        return data

    manifest()
    libraries = tmp_path / "libraries"
    libraries.mkdir()
    (libraries / "parser.a").write_text("native library")
    monkeypatch.setenv("RAGFLOW_NATIVE_CACHE", str(libraries))
    monkeypatch.setenv("RAGFLOW_GO_CACHE_ROOT", str(tmp_path / "native-cache"))
    builder_id = "sha256:" + "b" * 64
    monkeypatch.setattr(go_builder, "prepare_builder", lambda *args: builder_id)
    bases = {"ubuntu:24.04": "ubuntu@sha256:" + "c" * 64}
    monkeypatch.setattr(go_builder, "base_images", lambda *args: bases.copy())
    monkeypatch.setattr(check_cache.shutil, "which", lambda name: sys.executable)
    state = {"images": {}, "commands": [], "fail": False}

    def run(command, **kwargs):
        state["commands"].append(command)
        if state["fail"]:
            raise subprocess.CalledProcessError(1, command)
        if command[:2] == ["docker", "build"]:
            image_id = "sha256:" + candidate.digest(str(command).encode())
            source_id = next(value.split("=", 1)[1] for value in command if value.startswith("org.ragflow.source-id="))
            info = {"Id": image_id, "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {"org.ragflow.source-id": source_id, "org.ragflow.validation": "feature-build-only"}}}
            state["images"][image_id] = info
            state["images"][command[command.index("-t") + 1]] = info
            if "--iidfile" in command:
                Path(command[command.index("--iidfile") + 1]).write_text(image_id)

    def inspect(reference):
        if reference not in state["images"]:
            raise subprocess.CalledProcessError(1, ["docker", "inspect", reference])
        return state["images"][reference]

    def docker(*args):
        if args[0] == "info":
            return "test-engine"
        if args[-1] == "/ragflow/SOURCE_REVISION":
            return "unverified"
        if args[-1] == "/ragflow/VERSION":
            return "1.0.0-dev." + manifest()["source_id"][:12]
        raise AssertionError(args)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(go_builder, "inspect", inspect)
    monkeypatch.setattr(go_builder, "docker", docker)
    return snapshot, tmp_path / "cache", state, manifest, identity, bases, libraries


def test_repeat_build_and_commit_only_change_reuse_image_and_checks(image_build):
    snapshot, cache, state, manifest, identity, _, _ = image_build
    first = feature_image.build(snapshot, cache, local=True)
    commands = len(state["commands"])
    identity.update(head="d" * 40, dirty=False)
    manifest()
    repeat = feature_image.build(snapshot, cache, local=True)
    assert repeat["cache_hit"] is True
    assert repeat["source_id"] == first["source_id"]
    assert repeat["image_id"] == first["image_id"]
    assert len(state["commands"]) == commands


@pytest.mark.parametrize("changed", ["python", "native", "library", "base", "missing-image", "corrupt-receipt", "corrupt-fields", "force"])
def test_build_invalidates_only_when_its_inputs_or_artifact_change(image_build, changed):
    snapshot, cache, state, manifest, _, bases, libraries = image_build
    first = feature_image.build(snapshot, cache, local=True)
    if changed in ("python", "native"):
        name = "api/main.py" if changed == "python" else "cmd/main.go"
        (snapshot / "source" / name).write_text("changed")
        manifest()
    elif changed == "library":
        (libraries / "parser.a").write_text("changed")
    elif changed == "base":
        bases["ubuntu:24.04"] = "ubuntu@sha256:" + "e" * 64
    elif changed == "missing-image":
        state["images"].clear()
    elif changed == "corrupt-receipt":
        (cache / (first["key"] + ".json")).write_text("{")
    elif changed == "corrupt-fields":
        receipt = cache / (first["key"] + ".json")
        data = json.loads(receipt.read_bytes())
        data["image_id"] = "damaged"
        receipt.write_text(json.dumps(data))
    before = sum(command[:2] == ["docker", "build"] for command in state["commands"])
    result = feature_image.build(snapshot, cache, local=True, force=changed == "force")
    assert result["cache_hit"] is False
    assert sum(command[:2] == ["docker", "build"] for command in state["commands"]) == before + 1


def test_failed_force_build_invalidates_prior_build_receipt(image_build):
    snapshot, cache, state, *_ = image_build
    first = feature_image.build(snapshot, cache, local=True)
    state["fail"] = True
    with pytest.raises(subprocess.CalledProcessError):
        feature_image.build(snapshot, cache, local=True, force=True)
    assert not (cache / (first["key"] + ".json")).exists()


def test_feature_build_uses_own_iid_even_if_tag_moves(image_build, monkeypatch):
    snapshot, cache, state, *_ = image_build
    original = subprocess.run
    own_ids = []

    def run(command, **kwargs):
        original(command, **kwargs)
        if command[:2] == ["docker", "build"]:
            tag = command[command.index("-t") + 1]
            own_ids.append(state["images"][tag]["Id"])
            foreign = {**state["images"][tag], "Id": "sha256:" + "f" * 64}
            state["images"][tag] = foreign
            state["images"][foreign["Id"]] = foreign

    monkeypatch.setattr(subprocess, "run", run)
    result = feature_image.build(snapshot, cache, local=True)
    assert result["image_id"] == own_ids[0]
    assert state["images"][result["image"]]["Id"] != result["image_id"]
    assert not list(cache.glob("build-*"))


@pytest.mark.parametrize("outcome", ["missing", "invalid", "failure"])
def test_feature_build_requires_its_successful_iidfile(image_build, monkeypatch, outcome):
    snapshot, cache, *_ = image_build
    original = subprocess.run

    def run(command, **kwargs):
        original(command, **kwargs)
        if command[:2] == ["docker", "build"]:
            iidfile = Path(command[command.index("--iidfile") + 1])
            if outcome == "missing":
                iidfile.unlink()
            elif outcome == "invalid":
                iidfile.write_text("invalid")
            else:
                raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises((FileNotFoundError, ValueError, subprocess.CalledProcessError)):
        feature_image.build(snapshot, cache, local=True)
    assert not list(cache.glob("*.json"))
    assert not list(cache.glob("build-*"))


@pytest.mark.parametrize("failure", [False, True])
def test_feature_cli_result_file_is_separate_from_logs(tmp_path, monkeypatch, capsys, failure):
    output = tmp_path / "result.json"
    output.write_text('{"status":"PASS","stale":true}')
    expected = {"status": "PASS", "image_id": "sha256:" + "a" * 64}

    def build(*args, **kwargs):
        print("diagnostic output, not a result")
        if failure:
            raise ValueError("build failed")
        return expected

    monkeypatch.setattr(feature_image, "build", build)
    monkeypatch.setattr(sys, "argv", ["feature_image.py", "--snapshot", str(tmp_path), "--output", str(output)])
    if failure:
        with pytest.raises(ValueError, match="build failed"):
            feature_image.main.__wrapped__()
        assert not output.exists()
    else:
        feature_image.main.__wrapped__()
        assert json.loads(output.read_bytes()) == expected
        assert "diagnostic output" in capsys.readouterr().out


@pytest.fixture
def publication(monkeypatch):
    repository = feature_image.FEATURE_REPOSITORY
    result = {"status": "PASS", "image_id": "sha256:" + "b" * 64, "source_id": "a" * 64, "cache_hit": True}
    state = {"commands": [], "configs": [], "failure": None, "info": {"Id": result["image_id"], "RepoDigests": ["other.invalid/ragflow@sha256:" + "d" * 64, repository + "@sha256:" + "c" * 64]}}
    monkeypatch.setattr(go_builder, "docker", lambda *args: state["commands"].append(args))

    def output(command, **kwargs):
        if command[0] == "sudo":
            if state["failure"] == "credential":
                raise subprocess.CalledProcessError(1, command)
            return b"synthetic-password\n"
        return json.dumps([state["info"]])

    def run(command, **kwargs):
        state["commands"].append(command)
        config = Path(command[command.index("--config") + 1])
        assert config.is_dir()
        if "login" in command:
            state["configs"].append(config)
            assert kwargs["input"] == b"synthetic-password\n"
            assert all("synthetic-password" not in argument for argument in command)
            (config / "config.json").write_text("synthetic credentials")
        if state["failure"] == "interrupt" and "push" in command:
            raise KeyboardInterrupt
        if state["failure"] in command:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "check_output", output)
    monkeypatch.setattr(subprocess, "run", run)
    return result, state


def test_publish_cached_image_returns_exact_repository_digest_and_cleans_auth(publication, tmp_path, monkeypatch, capsys):
    result, state = publication
    for _ in range(2):
        published = feature_image.publish(result)
        assert published["digest"] == feature_image.FEATURE_REPOSITORY + "@sha256:" + "c" * 64
        assert published["image"].endswith(":dev-" + "b" * 64)
        assert published["source_id"] == result["source_id"]
        assert published["cache_hit"] is True
    assert len(state["configs"]) == 2 and len(set(state["configs"])) == 2
    assert all(not path.exists() for path in state["configs"])
    assert not any("build" in command for command in state["commands"])
    output = tmp_path / "published.json"
    monkeypatch.setattr(feature_image, "build", lambda *args, **kwargs: result)
    monkeypatch.setattr(sys, "argv", ["feature_image.py", "--snapshot", str(tmp_path), "--publish", "--output", str(output)])
    feature_image.main.__wrapped__()
    assert json.loads(output.read_bytes()) == published
    assert json.loads(capsys.readouterr().out) == published
    assert all(not path.exists() for path in state["configs"])


@pytest.mark.parametrize("failure", ["credential", "login", "push", "interrupt", "wrong-image", "wrong-repo", "ambiguous-digest"])
def test_publication_error_cleans_credentials_and_leaves_no_success_file(publication, tmp_path, monkeypatch, failure):
    result, state = publication
    state["failure"] = failure
    if failure == "wrong-image":
        state["info"]["Id"] = "sha256:" + "f" * 64
    elif failure == "wrong-repo":
        state["info"]["RepoDigests"] = ["other.invalid/ragflow@sha256:" + "d" * 64]
    elif failure == "ambiguous-digest":
        state["info"]["RepoDigests"].append(feature_image.FEATURE_REPOSITORY + "@sha256:" + "e" * 64)
    output = tmp_path / "result.json"
    output.write_text('{"status":"PASS","stale":true}')
    monkeypatch.setattr(feature_image, "build", lambda *args, **kwargs: result)
    monkeypatch.setattr(sys, "argv", ["feature_image.py", "--snapshot", str(tmp_path), "--publish", "--output", str(output)])
    with pytest.raises((subprocess.CalledProcessError, KeyboardInterrupt, ValueError)):
        feature_image.main.__wrapped__()
    assert not output.exists()
    assert all(not path.exists() for path in state["configs"])


@pytest.mark.parametrize("flags", [["--local", "--snapshot", "."], ["--image-import", "sha256:" + "a" * 64]])
def test_publication_is_rejected_for_local_or_import_only(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["feature_image.py", *flags, "--publish"])
    with pytest.raises(SystemExit) as error:
        feature_image.main.__wrapped__()
    assert error.value.code == 2


@pytest.mark.parametrize("local", [False, True])
def test_feature_launcher_uses_python_for_local_and_ssh(tmp_path, monkeypatch, local):
    path = Path(__file__).resolve().parents[3] / "deployment/local/build-feature.py"
    spec = importlib.util.spec_from_file_location("feature_launcher", path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    work = tmp_path / "work"
    work.mkdir()
    key = tmp_path / "key"
    key.write_text("synthetic key")
    commands = []

    def run(*args, **kwargs):
        commands.append(args)
        if "snapshot" in args:
            Path(args[args.index("--candidate") + 1]).mkdir()
        return "/tmp/ragflow-feature-ABC123" if kwargs.get("capture") else ""

    monkeypatch.setattr(launcher, "run", run)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: b"")
    args = SimpleNamespace(local=local, ssh_key=key, include=[], force=True, host="builder.invalid")
    assert launcher._build_run(args, tmp_path, lambda _: nullcontext(work)) == 0
    if local:
        assert any("--local" in command and "--force" in command for command in commands)
        assert not any(command[0] == "ssh" for command in commands)
    else:
        remote = next(command[-1] for command in commands if "--publish --snapshot" in command[-1])
        assert "python3 -B" in remote and "--force" in remote and "--output" in remote
        assert "$HOME/.local/state/ragflow-artifacts" in remote


@pytest.mark.parametrize("fail", [False, True])
def test_feature_snapshot_preserves_input_before_native_outputs_and_retires_worktree(tmp_path, monkeypatch, fail):
    from tools.quality import artifact_lifecycle as life
    from tools.quality.workspace import disposable_workspace

    path = Path(__file__).resolve().parents[3] / "deployment/local/build-feature.py"
    spec = importlib.util.spec_from_file_location("mutable_feature_snapshot", path)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(tmp_path / "ledger"))
    monkeypatch.setenv("RAGFLOW_ARTIFACT_ARCHIVES", str(tmp_path / "recovery"))
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: b"")
    work = tmp_path / "build-work"

    @contextmanager
    def workspace(_):
        with disposable_workspace(work):
            yield work

    def execute(*arguments, **_):
        if "snapshot" in arguments:
            destination = Path(arguments[arguments.index("--candidate") + 1]) / "source"
            destination.mkdir(parents=True)
            (destination / "source.py").write_bytes(b"exact dirty source")
        else:
            (work / "snapshot/source/native-output").write_bytes(b"generated native bytes")
            if fail:
                raise subprocess.CalledProcessError(7, arguments)

    monkeypatch.setattr(launcher, "run", execute)
    args = SimpleNamespace(local=True, include=[], force=False)
    with life.Run("feature-snapshot", category="cache") as owner:
        if fail:
            with pytest.raises(subprocess.CalledProcessError):
                launcher._build_run(args, tmp_path, workspace)
            owner.failed = True
        else:
            assert launcher._build_run(args, tmp_path, workspace) == 0
    assert not work.exists()
    archive = next(item for item in owner.data["resources"] if item.get("path", "").endswith("snapshot.tar.gz"))
    with tarfile.open(archive["preserved_evidence"]["archive"]["path"]) as bundle:
        assert bundle.extractfile("snapshot/source/source.py").read() == b"exact dirty source"
        assert "snapshot/source/native-output" not in bundle.getnames()


def test_base_inputs_resolve_args_and_skip_prior_stages(tmp_path, monkeypatch):
    recipe = tmp_path / "Dockerfile"
    recipe.write_text("ARG DEPS=registry:5443/deps:latest\nFROM ${DEPS} AS base\nFROM scratch AS system\nCOPY --from=base / /\nFROM system AS runtime\nFROM ubuntu:24.04 AS frontend\n")
    references = {"registry:5443/deps:latest": "registry:5443/deps@sha256:" + "a" * 64, "ubuntu:24.04": "ubuntu@sha256:" + "b" * 64}
    monkeypatch.setattr(go_builder, "inspect", lambda image: {"RepoDigests": [references[image]]})
    assert go_builder.base_images(recipe) == references


def test_unchanged_builder_does_not_invoke_docker_build(tmp_path, monkeypatch):
    recipe = tmp_path / "deployment/runner/Dockerfile.go-builder"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("FROM ubuntu:24.04\n")
    monkeypatch.setattr(go_builder, "base_images", lambda path: {"ubuntu:24.04": "ubuntu@sha256:" + "a" * 64})
    key = candidate.digest(candidate.canonical({"schema": 2, "recipe": candidate.file_digest(recipe), "bases": go_builder.base_images(recipe), "platform": "linux/amd64"}))
    cache = tmp_path / "cache"
    (cache / "builders").mkdir(parents=True)
    check_cache.write_success(cache / "builders" / (key + ".json"), {"schema": 1, "key": key, "status": "PASS", "image_id": "sha256:" + "b" * 64})
    monkeypatch.setattr(go_builder, "inspect", lambda tag: {"Id": "sha256:" + "b" * 64, "Os": "linux", "Architecture": "amd64"})

    def unexpected(*args, **kwargs):
        raise AssertionError("An unchanged builder must not run docker build")

    monkeypatch.setattr(subprocess, "run", unexpected)
    assert go_builder.prepare_builder(tmp_path, cache) == "sha256:" + "b" * 64


def test_concurrent_builder_recipes_cache_their_own_image_ids(tmp_path, monkeypatch):
    sources = [tmp_path / name for name in ("A", "B")]
    expected = {str(source): "sha256:" + digit * 64 for source, digit in zip(sources, ("a", "b"))}
    for source in sources:
        recipe = source / "deployment/runner/Dockerfile.go-builder"
        recipe.parent.mkdir(parents=True)
        recipe.write_text("FROM scratch\nLABEL recipe=" + source.name)
    monkeypatch.setattr(go_builder, "base_images", lambda path: {})
    completed = Barrier(2)
    iidfiles = []
    build_tags = []

    def build(command, **kwargs):
        source = Path(command[command.index("-f") + 1]).parents[2]
        iidfile = Path(command[command.index("--iidfile") + 1])
        iidfiles.append(iidfile)
        build_tags.append([command[index + 1] for index, value in enumerate(command) if value == "-t"])
        iidfile.write_text(expected[str(source)] + "\n")
        # Both builds complete before either caller reads its iidfile.
        completed.wait(timeout=10)

    def inspect(reference):
        assert reference in expected.values(), "The shared mutable tag must never identify a build result"
        return {"Id": reference, "Os": "linux", "Architecture": "amd64"}

    monkeypatch.setattr(subprocess, "run", build)
    monkeypatch.setattr(go_builder, "inspect", inspect)
    cache = tmp_path / "cache"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda source: go_builder.prepare_builder(source, cache), sources))
    assert results == list(expected.values())
    assert len(set(iidfiles)) == 2 and all(not path.parent.exists() for path in iidfiles)
    assert all(len(tags) == 1 for tags in build_tags)
    assert len({tags[-1] for tags in build_tags}) == 2
    receipts = [json.loads(path.read_text()) for path in (cache / "builders").glob("*.json")]
    assert {receipt["image_id"] for receipt in receipts} == set(expected.values())
    # Cache hits must retain the recipe-to-image mapping, too.
    assert [go_builder.prepare_builder(source, cache) for source in sources] == results


@pytest.mark.parametrize("failure", ["build", "missing-iid", "invalid-iid"])
def test_builder_failure_discards_iidfile_and_cannot_reuse_old_tag_receipt(tmp_path, monkeypatch, failure):
    recipe = tmp_path / "deployment/runner/Dockerfile.go-builder"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("FROM scratch\n")
    monkeypatch.setattr(go_builder, "base_images", lambda path: {})
    cache = tmp_path / "cache"
    builders = cache / "builders"
    builders.mkdir(parents=True)
    old_key = candidate.digest(candidate.canonical({"recipe": candidate.file_digest(recipe), "bases": {}, "platform": "linux/amd64"}))
    old_receipt = builders / (old_key + ".json")
    check_cache.write_success(old_receipt, {"schema": 1, "key": old_key, "status": "PASS", "image_id": "sha256:" + "a" * 64})

    def unexpected_inspect(reference):
        raise AssertionError("Receipts populated through the shared tag must not be reused")

    monkeypatch.setattr(go_builder, "inspect", unexpected_inspect)

    def build(command, **kwargs):
        iidfile = Path(command[command.index("--iidfile") + 1])
        if failure == "build":
            iidfile.write_text("sha256:" + "b" * 64)
            raise subprocess.CalledProcessError(1, command)
        if failure == "invalid-iid":
            iidfile.write_text("not-an-image-id")

    monkeypatch.setattr(subprocess, "run", build)
    with pytest.raises((subprocess.CalledProcessError, FileNotFoundError, ValueError)):
        go_builder.prepare_builder(tmp_path, cache)
    assert list(builders.glob("*.json")) == [old_receipt]
    assert not list(builders.glob("build-*"))


@pytest.fixture
def qualified_image_collector(tmp_path, monkeypatch):
    script = (Path(__file__).resolve().parents[3] / "deployment/runner/build-qualified-image.sh").read_text()
    collector = script.rsplit("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    monkeypatch.chdir(tmp_path)
    for name in ("output/ci-native", "output/ci-image", "libraries"):
        (tmp_path / name).mkdir(parents=True)
    (tmp_path / "libraries/library.a").write_bytes(b"synthetic native library")
    monkeypatch.setenv("CI_DOCKER_CONFIG", str(tmp_path / "docker-config"))
    monkeypatch.setenv("IMAGE_DIGEST", "registry.invalid/ragflow@sha256:" + "c" * 64)
    monkeypatch.setenv("SOURCE_SHA", "d" * 40)
    monkeypatch.setenv("RAGFLOW_NATIVE_CACHE", str(tmp_path / "libraries"))
    return compile(collector, "build-qualified-image.sh:metadata", "exec")


def test_qualified_go_metadata_uses_native_receipt_after_a_b_cache_hit_a(tmp_path, monkeypatch, qualified_image_collector):
    ids = {"A": "sha256:" + "a" * 64, "B": "sha256:" + "b" * 64}
    shared_tag = "ragflow-go-builder:go1.26.9-ubuntu22"
    tags = {shared_tag: ids["B"]}  # A stale alias left by an earlier builder.
    for name in ids:
        recipe = tmp_path / name / "deployment/runner/Dockerfile.go-builder"
        recipe.parent.mkdir(parents=True)
        recipe.write_text("FROM scratch\nLABEL recipe=" + name)

    def build(command, **kwargs):
        name = Path(command[command.index("-f") + 1]).parents[2].name
        for index, value in enumerate(command):
            if value == "-t":
                tags[command[index + 1]] = ids[name]
        Path(command[command.index("--iidfile") + 1]).write_text(ids[name])

    monkeypatch.setattr(go_builder, "base_images", lambda path: {})
    monkeypatch.setattr(go_builder, "inspect", lambda reference: {"Id": tags.get(reference, reference), "Os": "linux", "Architecture": "amd64"})
    monkeypatch.setattr(subprocess, "run", build)
    cache = tmp_path / "cache"
    assert go_builder.prepare_builder(tmp_path / "A", cache) == ids["A"]
    assert go_builder.prepare_builder(tmp_path / "B", cache) == ids["B"]
    native_build = {"schema": 1, "status": "PASS", "toolchain": go_builder.prepare_builder(tmp_path / "A", cache), "cache_hit": True}
    assert native_build["toolchain"] == ids["A"] and tags[shared_tag] == ids["B"]
    (tmp_path / "output/ci-native/artifacts.json").write_text(json.dumps(native_build))
    go_commands = []

    def version(command, **kwargs):
        if command[command.index("--entrypoint") + 1] == "go":
            go_commands.append(command)
            return "Go for A" if tags.get(command[-2], command[-2]) == ids["A"] else "Go for B"
        return "version"

    monkeypatch.setattr(subprocess, "check_output", version)
    exec(qualified_image_collector, {})
    report = json.loads((tmp_path / "output/ci-image/image-build.json").read_text())
    assert report["toolchain"]["go"] == "Go for A"
    assert report["native_build"] == native_build
    assert len(go_commands) == 1 and go_commands[0][-2:] == (ids["A"], "version")


@pytest.mark.parametrize(
    "native_build",
    [
        {"status": "PASS", "toolchain": "ragflow-go-builder:go1.26.9-ubuntu22"},
        {"status": "PASS"},
        {"status": "INCOMPLETE", "toolchain": "sha256:" + "a" * 64},
    ],
)
def test_qualified_metadata_rejects_unverified_native_builder(tmp_path, monkeypatch, qualified_image_collector, native_build):
    (tmp_path / "output/ci-native/artifacts.json").write_text(json.dumps(native_build))

    def unexpected_version(*args, **kwargs):
        raise AssertionError("Invalid native identity must fail before querying tool versions")

    monkeypatch.setattr(subprocess, "check_output", unexpected_version)
    with pytest.raises(ValueError, match="immutable builder ID"):
        exec(qualified_image_collector, {})
    assert not (tmp_path / "output/ci-image/image-build.json").exists()
