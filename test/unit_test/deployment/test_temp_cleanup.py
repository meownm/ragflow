"""Exercise actual deletion only inside disposable temporary roots."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deployment/local/temp.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not PWSH or os.name != "nt", reason="Windows PowerShell and CIM are required")


def run(root, *args):
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(SCRIPT), "-Root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def age(path):
    old = time.time() - 30 * 86400
    for item in sorted(path.rglob("*"), reverse=True):
        os.utime(item, (old, old))
    os.utime(path, (old, old))


def test_cleanup_expires_only_inactive_unprotected_trees(tmp_path):
    root = tmp_path / "tmp/ragflow"
    for name in ("expired", "recent", "active", "backup", "repository", "nested-pin"):
        task = root / name
        task.mkdir(parents=True)
        (task / "log.txt").write_text("disposable", encoding="utf-8")
    (root / "active/.keep").touch()
    (root / "backup/database.sql.gz").touch()
    (root / "repository/.git").touch()
    (root / "nested-pin/nested").mkdir()
    (root / "nested-pin/nested/.keep").touch()
    for task in root.iterdir():
        age(task)
    (root / "active/.keep").touch()
    (root / "nested-pin/nested/.keep").touch()
    (root / "recent/log.txt").touch()
    preview = run(root, "-WhatIf")
    assert preview.returncode == 0, preview.stderr
    assert (root / "expired").is_dir()
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert not (root / "expired").exists()
    assert {p.name for p in root.iterdir()} == {"recent", "active", "backup", "repository", "nested-pin"}


def test_completion_removes_workspace_and_stale_reservations_expire(tmp_path):
    root = tmp_path / "tmp/ragflow"
    result = run(root, "-Name", "task")
    assert result.returncode == 0, result.stderr
    assert (root / "task/.keep").is_file()
    assert run(root).returncode == 0
    assert (root / "task").exists()
    result = run(root, "-Name", "task", "-Complete")
    assert result.returncode == 0, result.stderr
    assert not (root / "task").exists()
    assert run(root, "-Name", "task").returncode == 0
    age(root / "task")
    assert run(root).returncode == 0
    assert not (root / "task").exists()


def test_source_sql_does_not_block_cleanup_but_backups_do(tmp_path):
    root = tmp_path / "tmp/ragflow"
    assert run(root, "-Name", "source").returncode == 0
    (root / "source/init.sql").write_text("select 1;", encoding="utf-8")
    assert run(root, "-Name", "source", "-Complete").returncode == 0
    assert not (root / "source").exists()
    assert run(root, "-Name", "backup").returncode == 0
    (root / "backup/postgres.dump").write_bytes(b"backup")
    assert run(root, "-Name", "backup", "-Complete").returncode != 0
    assert (root / "backup/postgres.dump").read_bytes() == b"backup"


def test_quota_removes_oldest_inactive_workspace(tmp_path):
    root = tmp_path / "tmp/ragflow"
    oldest = root / "oldest"
    oldest.mkdir(parents=True)
    with (oldest / "large-cache").open("wb") as stream:
        stream.truncate(2 * 1024**3)
    newer = root / "newer"
    newer.mkdir()
    (newer / "log").touch()
    result = run(root, "-MaxGiB", "1")
    assert result.returncode == 0, result.stderr
    assert not oldest.exists()
    assert newer.exists()


def test_completion_refuses_a_running_consumer(tmp_path):
    root = tmp_path / "tmp/ragflow"
    assert run(root, "-Name", "task").returncode == 0
    task = root / "task"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(task)])
    try:
        result = run(root, "-Name", "task", "-Complete")
        assert result.returncode != 0
        assert task.exists()
    finally:
        child.kill()
        child.wait(timeout=10)
    result = run(root, "-Name", "task", "-Complete")
    assert result.returncode == 0, result.stderr
    assert not task.exists()


def test_wrong_root_and_path_escape_are_rejected(tmp_path):
    assert run(tmp_path, "-Name", "task").returncode != 0
    root = tmp_path / "tmp/ragflow"
    assert run(root, "-Name", "../outside").returncode != 0
    assert not root.exists()


def test_junction_and_its_external_data_survive(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction protection")
    root = tmp_path / "tmp/ragflow"
    task = root / "linked"
    task.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = outside / "important.txt"
    payload.write_text("keep", encoding="utf-8")
    junction = task / "junction"
    command = "$null=New-Item -ItemType Junction -Path $env:TEMP_TEST_LINK -Target $env:TEMP_TEST_TARGET"
    subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        env={**os.environ, "TEMP_TEST_LINK": str(junction), "TEMP_TEST_TARGET": str(outside)},
        check=True,
    )
    old = time.time() - 30 * 86400
    os.utime(task, (old, old))
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert task.exists()
    assert payload.read_text(encoding="utf-8") == "keep"
    result = run(junction / "tmp/ragflow", "-Name", "task")
    assert result.returncode != 0
    assert not (outside / "tmp").exists()
