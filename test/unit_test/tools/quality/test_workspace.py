import os
import shutil
import runpy
import subprocess
import sys
import json
from pathlib import Path

import pytest

from tools.quality.workspace import disposable_workspace
from tools.quality import workspace


def test_workspace_error_removes_tree_without_touching_sibling(tmp_path):
    sibling = tmp_path / "unique-result"
    sibling.write_text("keep", encoding="utf-8")
    work = tmp_path / "work"
    with pytest.raises(KeyboardInterrupt), disposable_workspace(work):
        (work / "dependencies").mkdir()
        (work / "dependencies/package").write_bytes(b"data")
        raise KeyboardInterrupt
    assert not work.exists()
    assert sibling.read_text() == "keep"


def test_existing_workspace_is_not_removed(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "result").write_bytes(b"keep")
    with pytest.raises(FileExistsError), disposable_workspace(work):
        pytest.fail("existing directory accepted")
    assert (work / "result").read_bytes() == b"keep"


@pytest.mark.skipif(os.name != "nt" or not shutil.which("pwsh"), reason="Windows sharing modes")
def test_lease_stays_exclusive_until_removal_is_finished(tmp_path, monkeypatch):
    work = tmp_path / "work"
    original = workspace._remove_tree

    def remove(path, **options):
        command = "try { $f=[IO.File]::Open($env:TEST_LEASE,'Open','ReadWrite','Delete'); $f.Dispose(); exit 0 } catch { exit 33 }"
        result = subprocess.run(
            [shutil.which("pwsh"), "-NoProfile", "-Command", command],
            env={**os.environ, "TEST_LEASE": str(Path(path) / ".lease")},
            timeout=15,
            check=False,
        )
        assert result.returncode == 33
        original(path, **options)

    monkeypatch.setattr(workspace, "_remove_tree", remove)
    with disposable_workspace(work):
        (work / "source").mkdir()
    assert not work.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic child enrollment")
def test_failed_job_assignment_stops_child_before_it_can_write(tmp_path, monkeypatch):
    workspace.process_job()
    monkeypatch.setattr(workspace, "_job_handle", -1)
    destination = tmp_path / "must-not-be-created"
    with pytest.raises(OSError):
        workspace.start_process([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(destination)])
    assert not destination.exists()


def test_feature_builder_uses_guarded_local_commands():
    script = Path(__file__).resolve().parents[4] / "deployment/local/build-feature.py"
    run = runpy.run_path(str(script))["run"]
    assert run(sys.executable, "-c", "print('captured')", capture=True).strip() == "captured"
    with pytest.raises(subprocess.CalledProcessError) as error:
        run(sys.executable, "-c", "raise SystemExit(7)")
    assert error.value.returncode == 7


@pytest.mark.skipif(os.name != "nt", reason="Windows shared reservation mutex")
def test_reservation_cannot_succeed_during_deletion(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[4] / "deployment/local/temp.ps1"
    work = tmp_path / "tmp/ragflow/build"
    original = workspace._remove_tree

    def remove(path):
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script), "-Root", str(work.parent), "-Name", work.name],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode != 0
        assert "mutation is busy" in result.stderr
        original(path)

    monkeypatch.setattr(workspace, "_remove_tree", remove)
    with disposable_workspace(work):
        (work / "result").touch()
    assert not work.exists()


@pytest.mark.parametrize("protected", [".keep", "postgres.dump"])
def test_protected_entry_created_after_preflight_is_never_deleted(tmp_path, monkeypatch, protected):
    work = tmp_path / "work"
    original = workspace._remove_tree

    def remove(path):
        (path / protected).write_bytes(b"keep")
        original(path)

    monkeypatch.setattr(workspace, "_remove_tree", remove)
    with pytest.raises(OSError, match="Protected entry"), disposable_workspace(work):
        (work / "result").touch()
    assert (work / protected).read_bytes() == b"keep"
    assert len(list(tmp_path.glob(".ragflow-cleanup-*.json"))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess Job list")
def test_atomic_launch_preserves_popen_io_unicode_and_global_api(tmp_path):
    import _winapi

    before = _winapi.CreateProcess
    directory = tmp_path / "Кириллица with spaces"
    directory.mkdir()
    code = "import os,sys,json; print(json.dumps([os.getcwd(),sys.argv[1],os.environ['CUSTOM'],len(sys.stdin.read())],ensure_ascii=False))"
    with workspace.start_process(
        [sys.executable, "-B", "-c", code, 'quoted " argument'],
        cwd=directory,
        env={**os.environ, "CUSTOM": "ёж", "PYTHONIOENCODING": "utf-8"},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    ) as process:
        output, _ = process.communicate("x" * 100000, timeout=20)
        assert process.returncode == 0
    assert json.loads(output) == [str(directory), 'quoted " argument', "ёж", 100000]
    assert _winapi.CreateProcess is before


@pytest.mark.skipif(os.name != "nt", reason="Windows isolated CreateProcess adapter")
def test_atomic_launch_does_not_replace_concurrent_stdlib_creation(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from tools.quality import _windows_process as native

    workspace.process_job()
    entered, release = threading.Event(), threading.Event()
    original = native._create_process
    calls = []

    def paused(*args):
        calls.append(args)
        entered.set()
        assert release.wait(timeout=10)
        return original(*args)

    def guarded():
        with workspace.start_process([sys.executable, "-B", "-c", "print('guarded')"], stdout=subprocess.PIPE, text=True) as process:
            return process.communicate(timeout=10)[0].strip()

    monkeypatch.setattr(native, "_create_process", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(guarded)
        try:
            assert entered.wait(timeout=10)
            ordinary = subprocess.run([sys.executable, "-B", "-c", "print('ordinary')"], capture_output=True, text=True, timeout=10, check=True)
            assert ordinary.stdout.strip() == "ordinary"
            assert len(calls) == 1
        finally:
            release.set()
        assert future.result(timeout=15) == "guarded"
