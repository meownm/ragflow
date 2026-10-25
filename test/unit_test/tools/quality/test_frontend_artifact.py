"""Fake package executables exercise real child processes without network/builds."""

import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from tools.quality import candidate
from tools.quality import frontend_artifact as frontend

PROFILE = {"platform": "linux/amd64", "node": "22.20.0", "package_manager": "pnpm@10.33.0", "minify": "esbuild", "sourcemap": False, "mode": "production"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend, "CACHE_ROOT", tmp_path / "cache")
    source = tmp_path / "source-repo"
    (source / "web/src").mkdir(parents=True)
    (source / "pyproject.toml").write_text('[project]\nversion="1.0.0"\n', encoding="utf-8")
    (source / "web/package.json").write_text('{"private":true}', encoding="utf-8")
    (source / "web/pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (source / "web/src/index.ts").write_text("export const value = 1;", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Synthetic"],
        ["config", "user.email", "qa@example.invalid"],
        ["config", "core.autocrlf", "false"],
        ["add", "."],
        ["commit", "-qm", "baseline"],
    ):
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    snapshot = tmp_path / "candidate"
    candidate.snapshot(source, snapshot, [], False, PROFILE.copy())
    helper = tmp_path / "fake_tools.py"
    helper.write_text(
        """
import json, os, pathlib, sys
kind, *args = sys.argv[1:]
if args == ["--version"]:
    print("v22.20.0" if kind == "node" else "10.33.0")
elif args[0] == "install":
    assert "--frozen-lockfile" in args and "--ignore-scripts" in args and "--prod=false" in args
    assert "SYNTHETIC_SECRET" not in os.environ
    assert os.environ["VITE_MINIFY"] == "esbuild"
    assert os.environ["VITE_BUILD_SOURCEMAP"] == "false"
    pathlib.Path("node_modules").mkdir()
    pathlib.Path("node_modules/install-proof").write_text("installed")
    placeholder = pathlib.Path("node_modules/.pnpm/example@1/node_modules/example/lib/.keep")
    placeholder.parent.mkdir(parents=True)
    placeholder.touch()
    store = pathlib.Path(args[args.index("--store-dir") + 1])
    (store / "synthetic-package").write_bytes(b"package bytes")
elif args == ["exec", "vite", "build", "--mode", "production"]:
    assert pathlib.Path("node_modules/install-proof").exists()
    assert not pathlib.Path("node_modules/.pnpm/example@1/node_modules/example/lib/.keep").exists()
    pathlib.Path("dist/assets").mkdir(parents=True)
    pathlib.Path("dist/index.html").write_text("<html>built</html>")
    pathlib.Path("dist/assets/app.js").write_text("console.log('built');")
else:
    raise AssertionError(args)
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(frontend, "_tools", lambda pnpm_cli=None: ([sys.executable, str(helper), "node"], [sys.executable, str(helper), "pnpm"]))
    monkeypatch.setenv("SYNTHETIC_SECRET", "must-not-reach-build")
    return snapshot, tmp_path / "work", tmp_path / "frontend.tar.gz", helper


def test_build_isolated_artifact_and_cli_verification(setup):
    snapshot, work, archive, _ = setup
    before = candidate.verify(snapshot)
    receipt = frontend.build(snapshot, work, archive)
    assert receipt["source_id"] == before["source_id"]
    assert not work.exists()
    assert Path(str(archive) + ".logs.zip").is_file()
    assert receipt["validation"] == "NOT_ASSERTED"
    assert set(receipt["files"]) == {"index.html", "assets/app.js"}
    assert receipt["toolchain"] == {"node": "22.20.0", "pnpm": "10.33.0"}
    assert not (snapshot / "source/web/dist").exists()
    assert candidate.verify(snapshot) == before
    result = subprocess.run(
        [sys.executable, "-m", "tools.quality.frontend_artifact", "verify", "--candidate", str(snapshot), "--archive", str(archive)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["validation"] == "NOT_ASSERTED"


def test_shared_cache_is_bounded_and_workspaces_are_removed(setup, monkeypatch):
    snapshot, work, archive, _ = setup
    monkeypatch.setattr(frontend, "MAX_CACHE_BYTES", 1)
    frontend.build(snapshot, work, archive)
    assert not work.exists()
    assert not list(frontend.CACHE_ROOT.iterdir())
    assert frontend.verify(snapshot, archive, Path(str(archive) + ".json"))["source_id"]


def test_package_placeholders_never_remove_pins_or_follow_dependency_links(tmp_path, request):
    web = tmp_path / "private-build/web"
    package = web / "node_modules/.pnpm/example@1/node_modules/example"
    package.mkdir(parents=True)
    placeholder = package / "lib/.keep"
    placeholder.parent.mkdir()
    placeholder.touch()
    pins = [web / ".keep", web / "node_modules/.keep", package / ".keep"]
    for path in pins:
        path.write_bytes(b"keep this task or package")
    outside = tmp_path / "working-data"
    outside.mkdir()
    sentinel = outside / ".keep"
    sentinel.touch()
    _directory_link(package / "external", outside, request)
    assert frontend._remove_package_placeholders(web) == 1
    assert not placeholder.exists()
    assert sentinel.exists() and all(path.read_bytes() == b"keep this task or package" for path in pins)


def test_cache_expires_and_rejects_concurrent_users(tmp_path):
    cache = tmp_path / "pnpm"
    cache.mkdir()
    (cache / "old-package").write_bytes(b"old")
    stamp = tmp_path / "pnpm.last-used"
    stamp.touch()
    old = time.time() - 8 * 86400
    os.utime(stamp, (old, old))
    with frontend._cache_lease(cache):
        assert not list(cache.iterdir())
        with pytest.raises(OSError), frontend._cache_lease(cache):
            pytest.fail("concurrent cache lease succeeded")
    with frontend._cache_lease(cache):
        assert stamp.stat().st_mtime > old


def _directory_link(link, target, request):
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)

    def remove():
        if link.is_junction():
            link.rmdir()
        else:
            link.unlink(missing_ok=True)

    request.addfinalizer(remove)


@pytest.mark.parametrize("cleanup", ["fresh", "expired", "quota"])
@pytest.mark.parametrize("missing", [False, True])
def test_cache_project_registration_never_traverses_or_removes_target(tmp_path, monkeypatch, request, cleanup, missing):
    cache, project = tmp_path / "pnpm", tmp_path / "project"
    cache.mkdir()
    project.mkdir()
    link = cache / "v10/projects" / ("a" * 32)
    _directory_link(link, project, request)
    if missing:
        project.rmdir()
    else:
        (project / "must-survive").write_bytes(b"x" * 4096)
    (cache / "package").write_bytes(b"data")
    stamp = tmp_path / "pnpm.last-used"
    stamp.touch()
    if cleanup == "expired":
        old = time.time() - 8 * 86400
        os.utime(stamp, (old, old))
    monkeypatch.setattr(frontend, "MAX_CACHE_BYTES", 1 if cleanup == "quota" else 1024)
    original = frontend.os.scandir

    def scan(path):
        # POSIX rmtree traverses directories through file descriptors.
        # Compare directory identity before any target traversal can start.
        if isinstance(path, int):
            if project.exists():
                assert not os.path.samestat(os.fstat(path), project.stat())
        else:
            assert not Path(path).is_relative_to(project)
            assert Path(path) != link
        return original(path)

    monkeypatch.setattr(frontend.os, "scandir", scan)
    with frontend._cache_lease(cache, used=False):
        pass
    if cleanup == "fresh":
        assert (cache / "package").read_bytes() == b"data"
        assert link.is_symlink() or link.is_junction()
    else:
        assert not list(cache.iterdir())
    if missing:
        assert not project.exists()
    else:
        assert (project / "must-survive").read_bytes() == b"x" * 4096


@pytest.mark.parametrize("relative", ["v10/files/" + "a" * 32, "v10/projects/other", "projects/" + "a" * 32, "v10/projects"])
@pytest.mark.parametrize("expired", [False, True])
def test_cache_rejects_unexpected_links_before_eviction(tmp_path, monkeypatch, request, relative, expired):
    cache, project = tmp_path / "pnpm", tmp_path / "project"
    cache.mkdir()
    project.mkdir()
    (project / "must-survive").write_bytes(b"keep")
    (cache / "package").write_bytes(b"data")
    _directory_link(cache / relative, project, request)
    stamp = tmp_path / "pnpm.last-used"
    stamp.touch()
    if expired:
        old = time.time() - 8 * 86400
        os.utime(stamp, (old, old))
    monkeypatch.setattr(frontend, "MAX_CACHE_BYTES", 1)
    with pytest.raises(ValueError, match="linked package cache entry"), frontend._cache_lease(cache, used=False):
        pass
    assert (project / "must-survive").read_bytes() == b"keep"
    assert (cache / "package").read_bytes() == b"data"


def test_cache_lease_rejects_linked_root(tmp_path, request):
    project, cache = tmp_path / "project", tmp_path / "pnpm"
    project.mkdir()
    (project / "must-survive").write_bytes(b"keep")
    _directory_link(cache, project, request)
    with pytest.raises(ValueError, match="linked frontend"), frontend._cache_lease(cache, used=False):
        pytest.fail("linked cache root accepted")
    assert (project / "must-survive").read_bytes() == b"keep"


def test_unreadable_expired_cache_is_not_evicted(tmp_path, monkeypatch):
    cache = tmp_path / "pnpm"
    cache.mkdir()
    (cache / "package").write_bytes(b"data")
    old = time.time() - 8 * 86400
    os.utime(cache, (old, old))

    def fail_scan(_):
        raise PermissionError("cache inaccessible")

    monkeypatch.setattr(frontend.os, "scandir", fail_scan)
    with pytest.raises(PermissionError, match="cache inaccessible"), frontend._cache_lease(cache, used=False):
        pytest.fail("unreadable cache accepted")
    assert (cache / "package").read_bytes() == b"data"


@pytest.mark.parametrize("overlap", ["candidate", "artifact"])
def test_cache_cannot_mutate_candidate_or_remove_artifact(setup, monkeypatch, overlap):
    snapshot, work, archive, _ = setup
    before = candidate.verify(snapshot)
    cache = snapshot / "source/.local/cache" if overlap == "candidate" else archive.parent / "store"
    monkeypatch.setattr(frontend, "CACHE_ROOT", cache)
    if overlap == "artifact":
        archive = cache / "frontend.tar.gz"
    with pytest.raises(ValueError, match="separate"):
        frontend.build(snapshot, work, archive)
    assert candidate.verify(snapshot) == before
    assert not cache.exists()
    assert not work.exists()


def test_workspace_creation_failure_does_not_leave_a_tree(setup, monkeypatch):
    snapshot, work, archive, _ = setup
    original = Path.write_text

    def fail_marker(path, *args, **kwargs):
        if path.name == ".ragflow-temp":
            raise OSError("synthetic write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_marker)
    with pytest.raises(OSError, match="synthetic"):
        frontend.build(snapshot, work, archive)
    assert not work.exists()
    candidate.verify(snapshot)


def test_unavailable_log_destination_still_removes_workspace(setup):
    snapshot, work, archive, _ = setup
    blocked = archive.parent / "blocked-output"
    blocked.write_text("ordinary file", encoding="utf-8")
    with pytest.raises(OSError):
        frontend.build(snapshot, work, blocked / archive.name)
    assert not work.exists()
    candidate.verify(snapshot)


def test_scheduled_cache_cleanup_does_not_renew_idle_timestamp(tmp_path, monkeypatch):
    cache = tmp_path / "pnpm"
    cache.mkdir()
    (cache / "package").write_bytes(b"data")
    stamp = tmp_path / "pnpm.last-used"
    stamp.touch()
    before = time.time() - 6 * 86400
    os.utime(stamp, (before, before))
    monkeypatch.setattr(frontend, "CACHE_ROOT", cache)
    frontend.clean_cache()
    assert stamp.stat().st_mtime == before
    assert (cache / "package").exists()
    old = time.time() - 8 * 86400
    os.utime(stamp, (old, old))
    frontend.clean_cache()
    assert not list(cache.iterdir())
    assert stamp.stat().st_mtime == old
    with frontend._cache_lease(cache), pytest.raises(OSError):
        frontend.clean_cache()


def test_scheduled_cache_cleanup_handles_first_build_crash_without_timestamp(tmp_path, monkeypatch):
    cache = tmp_path / "pnpm"
    cache.mkdir()
    (cache / "orphan-package").write_bytes(b"data")
    old = time.time() - 8 * 86400
    os.utime(cache, (old, old))
    monkeypatch.setattr(frontend, "CACHE_ROOT", cache)
    frontend.clean_cache()
    assert not list(cache.iterdir())
    assert not (tmp_path / "pnpm.last-used").exists()


@pytest.mark.parametrize("change", ["inputs", "new-input", "version", "empty-output", "failure"])
def test_failed_or_mutating_build_has_no_completion_receipt(setup, change):
    snapshot, work, archive, helper = setup
    text = helper.read_text()
    if change == "inputs":
        text += '\nif args and args[0] == "install": pathlib.Path("src/index.ts").write_text("mutated")\n'
    elif change == "new-input":
        text += '\nif args and args[0] == "install": pathlib.Path(".env.local").write_text("injected")\n'
    elif change == "version":
        text = text.replace("v22.20.0", "v20.0.0")
    elif change == "empty-output":
        text = text.replace("<html>built</html>", "")
    else:
        text += '\nif args and args[0] == "install": raise SystemExit(12)\n'
    helper.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        frontend.build(snapshot, work, archive)
    assert not work.exists()
    assert Path(str(archive) + ".logs.zip").is_file()
    assert not Path(str(archive) + ".json").exists()
    candidate.verify(snapshot)


@pytest.mark.parametrize("tamper", ["archive", "receipt", "candidate", "profile", "file-map", "toolchain"])
def test_reuse_rejects_tamper(setup, tamper):
    snapshot, work, archive, _ = setup
    frontend.build(snapshot, work, archive)
    receipt = Path(str(archive) + ".json")
    if tamper == "archive":
        archive.write_bytes(archive.read_bytes() + b"unexpected")
    elif tamper == "candidate":
        (snapshot / "source/web/src/index.ts").write_text("changed")
    else:
        data = json.loads(receipt.read_bytes())
        if tamper == "receipt":
            data["source_id"] = "0" * 64
        elif tamper == "profile":
            data["profile"]["minify"] = "terser"
        elif tamper == "toolchain":
            data["toolchain"]["node"] = "0.0.0"
        else:
            del data["files"]["assets/app.js"]
        if tamper != "receipt":
            data["receipt_id"] = candidate.digest(candidate.canonical({k: v for k, v in data.items() if k != "receipt_id"}))
        receipt.write_bytes(candidate.canonical(data))
    with pytest.raises(ValueError):
        frontend.verify(snapshot, archive, receipt)


def test_fresh_work_and_separate_output_required(setup):
    snapshot, work, archive, _ = setup
    work.mkdir()
    with pytest.raises(ValueError, match="fresh"):
        frontend.build(snapshot, work, archive)
    with pytest.raises(ValueError, match="separate"):
        frontend.build(snapshot, snapshot / "work", archive)


@pytest.mark.parametrize("field,value", [("package_manager", "npm@10.0.0"), ("node", "latest"), ("sourcemap", "false"), ("mode", "development"), ("minify", "other")])
def test_profile_is_explicit_and_supported(field, value):
    profile = {**PROFILE, field: value}
    with pytest.raises(ValueError):
        frontend._profile(profile)


def test_timeout_stops_owned_child(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        frontend._run([sys.executable, "-c", "import time;time.sleep(60)"], tmp_path, {}, tmp_path / "timeout.log", 1)


def test_explicit_pnpm_cli_uses_selected_node_without_wrapper(tmp_path, monkeypatch):
    entrypoint = tmp_path / "pnpm.cjs"
    entrypoint.write_text("// synthetic entrypoint", encoding="utf-8")
    monkeypatch.setattr(frontend.shutil, "which", lambda name: "selected-node" if name == "node" else None)
    node, pnpm = frontend._tools(entrypoint)
    assert node == ["selected-node"]
    assert pnpm == ["selected-node", str(entrypoint.resolve())]


@pytest.mark.parametrize("name,exists", [("missing.cjs", False), ("pnpm.cmd", True), ("directory.js", True)])
def test_invalid_explicit_pnpm_cli_rejected(tmp_path, monkeypatch, name, exists):
    entrypoint = tmp_path / name
    if name == "directory.js":
        entrypoint.mkdir()
    elif exists:
        entrypoint.write_text("invalid", encoding="utf-8")
    monkeypatch.setattr(frontend.shutil, "which", lambda _: "selected-node")
    with pytest.raises(ValueError, match="regular"):
        frontend._tools(entrypoint)


@pytest.mark.parametrize("entry", ["../escape", "duplicate", "symlink"])
def test_archive_parser_rejects_unsafe_entries_even_with_updated_archive_hash(tmp_path, entry):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        member = tarfile.TarInfo("../escape" if entry == "../escape" else "index.html")
        if entry == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "outside"
            bundle.addfile(member)
        else:
            member.size = 1
            bundle.addfile(member, io.BytesIO(b"x"))
            if entry == "duplicate":
                bundle.addfile(member, io.BytesIO(b"x"))
    receipt = {"archive": {"sha256": candidate.file_digest(archive), "size": archive.stat().st_size}, "files": {}}
    with pytest.raises(ValueError):
        frontend._verify_archive(archive, receipt)
