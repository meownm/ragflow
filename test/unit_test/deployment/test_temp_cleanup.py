"""Exercise actual deletion only inside disposable temporary roots."""

import os
import json
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deployment/local/temp.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not PWSH or os.name != "nt", reason="Windows PowerShell and CIM are required")


@pytest.fixture(autouse=True)
def isolated_lifecycle(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("RAGFLOW_ARTIFACT_STATE", str(tmp_path_factory.mktemp("lifecycle-state")))
    monkeypatch.delenv("RAGFLOW_ARTIFACT_RUN_ID", raising=False)


def run(root, *args):
    # Other chats may create/remove live containers during this unit lane.
    # Docker mount/failure behavior has dedicated fixtures below; CIM stays real.
    options = {}
    arguments = iter(args)
    for argument in arguments:
        options[argument.removeprefix("-")] = next(arguments) if argument in {"-Name", "-KeepDays", "-MaxGiB"} else True
    command = """
function global:docker { $global:LASTEXITCODE = 0 }
$options = ConvertFrom-Json -AsHashtable $env:TEST_OPTIONS
& $env:TEST_SCRIPT -Root $env:TEST_ROOT @options
"""
    return subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        env={**os.environ, "TEST_SCRIPT": str(SCRIPT), "TEST_ROOT": str(root), "TEST_OPTIONS": json.dumps(options)},
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


def managed(task):
    (task / ".ragflow-temp").write_text("ragflow-disposable-v2\n", encoding="utf-8")
    (task / ".process-job").write_text("Global\\RAGFlow-build-" + uuid.uuid4().hex, encoding="utf-8")
    (task / ".lease").touch()


def test_cleanup_expires_only_inactive_unprotected_trees(tmp_path):
    root = tmp_path / "tmp/ragflow"
    for name in ("expired", "recent", "active", "backup", "repository", "nested-pin"):
        task = root / name
        task.mkdir(parents=True)
        (task / "log.txt").write_text("disposable", encoding="utf-8")
        managed(task)
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


def test_completion_removes_workspace_but_old_manual_pins_remain_protected(tmp_path):
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
    assert (root / "task/.keep").exists()


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
    assert (root / "backup/.keep").exists()


def test_quota_removes_oldest_inactive_workspace(tmp_path):
    root = tmp_path / "tmp/ragflow"
    oldest = root / "oldest"
    oldest.mkdir(parents=True)
    managed(oldest)
    with (oldest / "large-cache").open("wb") as stream:
        stream.truncate(2 * 1024**3)
    newer = root / "newer"
    newer.mkdir()
    managed(newer)
    (newer / "log").touch()
    result = run(root, "-MaxGiB", "1")
    assert result.returncode == 0, result.stderr
    assert not oldest.exists()
    assert newer.exists()


def test_old_pin_and_unmanaged_results_survive(tmp_path):
    root = tmp_path / "tmp/ragflow"
    for name in ("paused", "unknown"):
        task = root / name
        task.mkdir(parents=True)
        (task / "result").write_text("unique", encoding="utf-8")
    (root / "paused/.keep").touch()
    age(root / "paused")
    age(root / "unknown")
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert (root / "paused/result").read_text() == "unique"
    assert (root / "unknown/result").read_text() == "unique"


def test_relative_process_holds_build_lease_and_crashed_build_expires(tmp_path):
    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    root.mkdir(parents=True)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import time\nfrom tools.quality.workspace import disposable_workspace\n"
                "with disposable_workspace(Path('build')):\n Path('build/result').write_text('keep while running'); Path('build/ready').touch(); time.sleep(120)"
            ),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[2]), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        for _ in range(100):
            if (task / "ready").exists():
                break
            time.sleep(0.05)
        assert (task / "ready").exists()
        age(task)
        result = run(root)
        assert result.returncode == 0, result.stderr
        assert child.poll() is None
        assert (task / "result").exists()
    finally:
        child.kill()
        child.wait(timeout=10)
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert not task.exists()


@pytest.mark.parametrize("consumer", ["bind", "unavailable"])
def test_docker_mount_or_failed_inspection_prevents_deletion(tmp_path, consumer):
    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    managed(task)
    (task / "result").write_text("keep", encoding="utf-8")
    age(task)
    command = """
function global:docker {
    $global:LASTEXITCODE = 0
    if ($env:TEST_CONSUMER -eq 'unavailable') { $global:LASTEXITCODE = 1; return }
    if ($args[0] -eq 'ps') { 'fixture-container' } else { $env:TEST_BIND }
}
& $env:TEST_SCRIPT -Root $env:TEST_ROOT
"""
    mount = "/run/desktop/mnt/host/" + task.drive[0].lower() + str(task)[2:].replace("\\", "/")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        env={**os.environ, "TEST_CONSUMER": consumer, "TEST_BIND": mount, "TEST_SCRIPT": str(SCRIPT), "TEST_ROOT": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (task / "result").read_text() == "keep"
    assert (result.returncode == 0) == (consumer == "bind"), result.stderr


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


def test_orphaned_grandchild_protects_workspace_and_shared_cache(tmp_path):
    root = tmp_path / "tmp/ragflow"
    root.mkdir(parents=True)
    task, cache = root / "build", tmp_path / "cache/pnpm"
    cache.mkdir(parents=True)
    (cache / "package").write_bytes(b"keep")
    release, ready = tmp_path / "release", tmp_path / "ready"
    grandchild = "import os,time; from pathlib import Path; Path(os.environ['READY']).write_text(str(os.getpid())); exec(\"while not Path(os.environ['RELEASE']).exists(): time.sleep(.05)\")"
    intermediate = (
        "import os,subprocess,sys; subprocess.Popen([sys.executable,'-c',os.environ['GRANDCHILD']],close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    owner = (
        "import os,subprocess,sys,time; from pathlib import Path\n"
        "from tools.quality.frontend_artifact import _cache_lease\n"
        "from tools.quality.workspace import disposable_workspace, start_process\n"
        "with _cache_lease(Path(os.environ['CACHE'])), disposable_workspace(Path('build')):\n"
        " Path('build/result').write_text('keep')\n"
        " p=start_process([sys.executable,'-c',os.environ['INTERMEDIATE']],cwd='build',close_fds=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        " p.wait(timeout=10)\n"
        " while not Path(os.environ['READY']).exists(): time.sleep(.05)\n"
        " os._exit(0)\n"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(SCRIPT.parents[2]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CACHE": str(cache),
        "READY": str(ready),
        "RELEASE": str(release),
        "GRANDCHILD": grandchild,
        "INTERMEDIATE": intermediate,
    }
    parent = subprocess.Popen([sys.executable, "-c", owner], cwd=root, env=env)
    try:
        assert parent.wait(timeout=20) == 0
        assert ready.exists()
        age(task)
        age(cache)
        stamp = cache.parent / "pnpm.last-used"
        old = time.time() - 30 * 86400
        os.utime(stamp, (old, old))
        result = run(root)
        assert result.returncode == 0, result.stderr
        assert (task / "result").read_text() == "keep"
        cleanup = "from pathlib import Path; import os; from tools.quality import frontend_artifact as f; f.CACHE_ROOT=Path(os.environ['CACHE']); f.clean_cache()"
        result = subprocess.run([sys.executable, "-B", "-c", cleanup], env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode != 0
        assert "active build processes" in result.stderr
        assert (cache / "package").read_bytes() == b"keep"
    finally:
        release.touch()
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=10)
        # The named job is the production lifetime check, including orphan exit.
        if (task / ".process-job").exists():
            from tools.quality.workspace import job_active

            job = (task / ".process-job").read_text()
            deadline = time.monotonic() + 10
            while job_active(job) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not job_active(job)
    assert run(root).returncode == 0
    assert not task.exists()
    result = subprocess.run([sys.executable, "-B", "-c", cleanup], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not (cache / "package").exists()


@pytest.mark.parametrize("root_failure", [False, True])
def test_partial_delete_is_reported_and_retried_without_internal_markers(tmp_path, root_failure):
    from tools.quality.workspace import disposable_workspace
    from tools.quality import workspace

    root = tmp_path / "tmp/ragflow"
    root.mkdir(parents=True)
    task = root / "build"
    original = workspace._remove_tree

    def partial(path, **_):
        for name in (".ragflow-temp", ".lease", ".process-job", *(["result"] if root_failure else [])):
            (Path(path) / name).unlink()
        raise PermissionError("injected deletion failure")

    # A real owner-side cleanup error must leave an external retry receipt.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workspace, "_remove_tree", partial)
        with pytest.raises(PermissionError), disposable_workspace(task):
            (task / "result").write_bytes(b"keep until cleanup succeeds")
    receipts = list(root.glob(".ragflow-cleanup-*.json"))
    assert len(receipts) == 1
    claimed = task
    assert not (claimed / ".ragflow-temp").exists()
    record = json.loads(receipts[0].read_text())
    record["job"] = "Global\\RAGFlow-build-" + uuid.uuid4().hex
    receipts[0].write_text(json.dumps(record))
    locked = (claimed / "result").open("rb") if not root_failure else None
    try:
        result = run(root)
        if not root_failure:
            assert result.returncode != 0, result.stdout
            assert receipts[0].exists()
            assert (claimed / "result").exists()
    finally:
        if locked:
            locked.close()
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert not claimed.exists()
    assert not receipts[0].exists()
    assert not list(root.iterdir())
    assert workspace._remove_tree is original


def test_readonly_disposable_file_does_not_block_cleanup(tmp_path):
    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    managed(task)
    payload = task / "readonly-package"
    payload.write_bytes(b"disposable")
    payload.chmod(stat.S_IREAD)
    age(task)
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert not list(root.iterdir())


def test_weekly_wrapper_reports_failure_and_reuses_one_report_on_retry(tmp_path):
    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    managed(task)
    (task / "locked.bin").write_bytes(b"busy")
    age(task)
    scripts = tmp_path / "checkout/deployment/local"
    scripts.mkdir(parents=True)
    wrapper = scripts / "sweep-temp.ps1"
    shutil.copyfile(SCRIPT.with_name("sweep-temp.ps1"), wrapper)
    # Exercise the wrapper's native Python exit handling without inspecting live Docker.
    checkout = scripts.parents[1]
    python = checkout / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    shutil.copyfile(sys.executable, python)
    (python.parent.parent / "pyvenv.cfg").write_text(f"home = {sys.base_prefix}\ninclude-system-site-packages = false\n")
    inventory = checkout / "tools/quality/artifact_registry.py"
    inventory.parent.mkdir(parents=True)
    inventory.write_text(
        'import os, sys\nfrom tools.quality.artifact_lifecycle import inventory_marker\nassert inventory_marker()\nprint(\'{"complete": true, "counts": {}}\')\nsys.exit(int(os.environ.get("TEST_INVENTORY_EXIT", "0")))\n'
    )
    inventory.with_name("artifact_lifecycle.py").write_text(
        'import os, sys\ndef inventory_marker(): return True\nif __name__ == "__main__":\n print(\'{"removed": 0, "errors": 0}\')\n sys.exit(int(os.environ.get("TEST_LIFECYCLE_EXIT", "0")))\n'
    )
    (scripts / "temp.ps1").write_text("param([switch]$CleanCache)\n& $env:TEST_SCRIPT -Root $env:TEST_ROOT", encoding="utf-8")
    command = "function global:docker { $global:LASTEXITCODE=0 }; & $env:TEST_WRAPPER"
    env = {**os.environ, "TEST_SCRIPT": str(SCRIPT), "TEST_ROOT": str(root), "TEST_WRAPPER": str(wrapper)}
    env.pop("PYTHONPATH", None)

    def sweep():
        return subprocess.run([PWSH, "-NoProfile", "-Command", command], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)

    report_directory = tmp_path / "checkout/.local/maintenance/temp-cleanup"
    with (task / "locked.bin").open("rb"):
        result = sweep()
        assert result.returncode != 0
        report = json.loads((report_directory / "last-run.json").read_text(encoding="utf-8-sig"))
        assert report["succeeded"] is False
        assert "Cleanup" in report["error"]
    result = sweep()
    assert result.returncode == 0, result.stderr
    report = json.loads((report_directory / "last-run.json").read_text(encoding="utf-8-sig"))
    assert report["succeeded"] is True
    assert report["inventory"]["complete"] is True
    assert len(report["removed"]) == 1
    assert [path.name for path in report_directory.iterdir()] == ["last-run.json"]
    assert not list(root.iterdir())
    env["TEST_INVENTORY_EXIT"] = "1"
    result = sweep()
    assert result.returncode != 0
    report = json.loads((report_directory / "last-run.json").read_text(encoding="utf-8-sig"))
    assert "Artifact inventory failed" in report["inventory_error"]
    env["TEST_INVENTORY_EXIT"] = "0"
    env["TEST_LIFECYCLE_EXIT"] = "1"
    result = sweep()
    assert result.returncode != 0
    report = json.loads((report_directory / "last-run.json").read_text(encoding="utf-8-sig"))
    assert "Artifact retirement failed" in report["docker_cleanup_error"]


def test_cleanup_installer_previews_s4u_limited_without_registering_task(tmp_path):
    script = SCRIPT.with_name("install-temp-cleanup.ps1")
    command = r"""
Import-Module ScheduledTasks
function global:Get-ScheduledTask { param($TaskName, $ErrorAction) return $null }
function global:New-ScheduledTaskPrincipal {
    param($UserId, $LogonType, $RunLevel)
    $global:principal = @{user=$UserId;logon=$LogonType;level=$RunLevel}
    ScheduledTasks\New-ScheduledTaskPrincipal -UserId $UserId -LogonType $LogonType -RunLevel $RunLevel
}
function global:Register-ScheduledTask { throw 'WhatIf must not register a task' }
& $env:TEST_INSTALLER -WhatIf
@{principal=$global:principal;user=[Security.Principal.WindowsIdentity]::GetCurrent().Name}|ConvertTo-Json -Compress
"""
    result = subprocess.run([PWSH, "-NoProfile", "-Command", command], cwd=tmp_path, env={**os.environ, "TEST_INSTALLER": str(script)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout.splitlines()[-1])
    assert value["principal"] == {"user": value["user"], "logon": "S4U", "level": "Limited"}


@pytest.mark.parametrize("complete", [False, True])
def test_sweeper_delete_failure_retries_after_locked_file_is_released(tmp_path, complete):
    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    managed(task)
    if complete:
        (task / ".keep").touch()
    (task / "a-result").write_bytes(b"disposable")
    (task / "locked.bin").write_bytes(b"busy")
    age(task)
    with (task / "locked.bin").open("rb"):
        result = run(root, *(["-Name", "build", "-Complete"] if complete else []))
        assert result.returncode != 0, result.stdout
        assert (task / "locked.bin").read_bytes() == b"busy"
        assert not (task / ".ragflow-temp").exists()
        assert len(list(root.glob(".ragflow-cleanup-*.json"))) == 1
        repeated = run(root, *(["-Name", "build", "-Complete"] if complete else []))
        assert repeated.returncode != 0
        assert len(list(root.glob(".ragflow-cleanup-*.json"))) == 1
    result = run(root, *(["-Name", "build", "-Complete"] if complete else []))
    assert result.returncode == 0, result.stderr
    assert not list(root.iterdir())


@pytest.mark.parametrize("protected", [".keep", "postgres.dump", ".git"])
def test_retry_still_protects_pins_backups_and_repositories(tmp_path, protected):
    from tools.quality.workspace import _directory_identity

    root = tmp_path / "tmp/ragflow"
    root.mkdir(parents=True)
    receipt = root / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
    claimed = root / "build"
    claimed.mkdir()
    payload = claimed / protected
    payload.write_bytes(b"keep")
    receipt.write_text(json.dumps({"version": 1, "original": "build", "identity": _directory_identity(claimed), "job": "Global\\RAGFlow-build-" + uuid.uuid4().hex}))
    result = run(root)
    assert result.returncode != 0
    assert payload.read_bytes() == b"keep"
    assert receipt.exists()


def test_stale_receipt_does_not_delete_reused_original_name(tmp_path):
    from tools.quality.workspace import _directory_identity

    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    (task / "unique").write_bytes(b"keep")
    receipt = root / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
    previous_identity = _directory_identity(task)
    previous_identity[1] += 1
    receipt.write_text(json.dumps({"version": 1, "original": "build", "identity": previous_identity, "job": "Global\\RAGFlow-build-" + uuid.uuid4().hex}))
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert (task / "unique").read_bytes() == b"keep"
    assert not receipt.exists()


@pytest.mark.parametrize("action", ["retire", "recover"])
@pytest.mark.parametrize("protected", [".keep", "postgres.dump"])
def test_final_helper_preserves_protection_added_after_sweep_scan(tmp_path, action, protected):
    from tools.quality.workspace import _directory_identity

    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    managed(task)
    (task / "result").write_bytes(b"keep")
    age(task)
    if action == "recover":
        receipt = root / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
        receipt.write_text(json.dumps({"version": 1, "original": "build", "identity": _directory_identity(task), "job": (task / ".process-job").read_text()}))
        needle = "Invoke-Workspace recover $receipt.FullName"
    else:
        needle = "Invoke-Workspace retire $candidate.Path"
    line = next(i for i, value in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1) if needle in value)
    proof = tmp_path / "injected"
    command = """
function global:docker { $global:LASTEXITCODE=0 }
$null=Set-PSBreakpoint -Script $env:TEST_SCRIPT -Line ([int]$env:TEST_LINE) -Action {
    if ($env:TEST_PROTECTED -eq '.keep') {
        & $env:TEST_SCRIPT -Root $env:TEST_ROOT -Name build | Out-Null
    } else {
        Set-Content -LiteralPath (Join-Path $env:TEST_ROOT 'build/postgres.dump') -Value 'fixture backup'
    }
    Set-Content -LiteralPath $env:TEST_PROOF -Value 'protected entry exists before final helper'
}
& $env:TEST_SCRIPT -Root $env:TEST_ROOT
"""
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        env={**os.environ, "TEST_SCRIPT": str(SCRIPT), "TEST_ROOT": str(root), "TEST_LINE": str(line), "TEST_PROTECTED": protected, "TEST_PROOF": str(proof)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proof.exists()
    assert result.returncode != 0
    assert (task / protected).exists()
    assert (task / "result").read_bytes() == b"keep"
    if action == "recover":
        assert receipt.exists()


def test_owner_crash_immediately_after_createprocess_keeps_child_in_job(tmp_path):
    import ctypes
    from tools.quality.workspace import _kernel, job_active

    root = tmp_path / "tmp/ragflow"
    root.mkdir(parents=True)
    task, pidfile = root / "build", tmp_path / "child.pid"
    owner = """
import os,sys,subprocess
from pathlib import Path
from tools.quality import workspace as w
from tools.quality import _windows_process as native
original=native._create_process
def crash_after_creation(*args):
    result=original(*args)
    Path(os.environ['PID_FILE']).write_text(str(result[2]))
    os._exit(0)
native._create_process=crash_after_creation
with w.disposable_workspace(Path('build')):
    Path('build/result').write_text('keep')
    w.start_process([sys._base_executable,'-B','-c','import time; time.sleep(120)'],cwd='build',creationflags=4,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
"""
    parent = subprocess.Popen(
        [sys.executable, "-B", "-c", owner],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[2]), "PYTHONDONTWRITEBYTECODE": "1", "PID_FILE": str(pidfile)},
    )
    kernel, handle = _kernel(), None
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateProcess.restype = ctypes.c_int
    try:
        assert parent.wait(timeout=20) == 0
        handle = kernel.OpenProcess(0x101001, False, int(pidfile.read_text()))
        assert handle
        assert kernel.WaitForSingleObject(handle, 0) == 258
        job = (task / ".process-job").read_text()
        assert job_active(job)
        age(task)
        result = run(root)
        assert result.returncode == 0, result.stderr
        assert (task / "result").read_text() == "keep"
        assert kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=10)
        if handle:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                assert kernel.TerminateProcess(handle, 2)
                assert kernel.WaitForSingleObject(handle, 10000) == 0
            kernel.CloseHandle(handle)
    result = run(root)
    assert result.returncode == 0, result.stderr
    assert not task.exists()


def test_crashed_reservation_lock_does_not_block_next_reservation(tmp_path):
    root = tmp_path / "tmp/ragflow"
    root.mkdir(parents=True)
    code = "import os; from pathlib import Path; from tools.quality.workspace import _task_lock; exec(\"with _task_lock(Path('build').absolute()): os._exit(0)\")"
    child = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[2]), "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=15,
    )
    assert child.returncode == 0
    result = run(root, "-Name", "build")
    assert result.returncode == 0, result.stderr
    assert (root / "build/.keep").exists()


@pytest.mark.parametrize("state", ["absent", "pinned", "replaced"])
def test_complete_retry_handles_finished_or_replaced_directory_generation(tmp_path, state):
    from tools.quality.workspace import _directory_identity

    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    (task / "result").write_bytes(b"keep")
    identity = _directory_identity(task)
    if state == "absent":
        (task / "result").unlink()
        task.rmdir()
    elif state == "pinned":
        (task / ".keep").touch()
    else:
        identity[1] += 1
        (task / ".keep").touch()
    receipt = root / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
    receipt.write_text(json.dumps({"version": 1, "original": "build", "identity": identity, "job": "Global\\RAGFlow-build-" + uuid.uuid4().hex}))
    result = run(root, "-Name", "build", "-Complete")
    if state == "replaced":
        assert result.returncode != 0
        assert "new generation" in result.stderr
        assert (task / ".keep").exists()
        assert (task / "result").read_bytes() == b"keep"
    else:
        assert result.returncode == 0, result.stderr
        assert not task.exists()
    assert not receipt.exists()


@pytest.mark.parametrize("dependency", [False, True])
def test_managed_dependency_links_never_traverse_external_data(tmp_path, dependency):
    root = tmp_path / "tmp/ragflow"
    task = root / "build"
    task.mkdir(parents=True)
    managed(task)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "postgres.dump").write_bytes(b"keep")
    parent = task / ("node_modules" if dependency else "ordinary")
    parent.mkdir()
    link = parent / "package"
    subprocess.run(
        [PWSH, "-NoProfile", "-Command", "$null=New-Item -ItemType Junction -Path $env:TEST_LINK -Target $env:TEST_TARGET"],
        env={**os.environ, "TEST_LINK": str(link), "TEST_TARGET": str(outside)},
        check=True,
        timeout=15,
    )
    outside_time = (outside / "postgres.dump").stat().st_mtime_ns
    outside_directory_time = outside.stat().st_mtime_ns
    result = run(root, "-Name", "build", "-Complete")
    assert (result.returncode == 0) is dependency, result.stderr
    assert task.exists() is not dependency
    assert (outside / "postgres.dump").read_bytes() == b"keep"
    assert (outside / "postgres.dump").stat().st_mtime_ns == outside_time
    assert outside.stat().st_mtime_ns == outside_directory_time


@pytest.mark.parametrize("recovery", [False, True])
def test_complete_only_removes_requested_task_and_its_receipt(tmp_path, recovery):
    from tools.quality.workspace import _directory_identity

    root = tmp_path / "tmp/ragflow"
    assert run(root, "-Name", "requested").returncode == 0
    task = root / "requested"
    receipts = {}
    for name in ("requested", "foreign-recovery", "foreign-expired"):
        other = root / name
        other.mkdir(exist_ok=True)
        (other / "result").write_bytes(name.encode())
        if name != "requested":
            managed(other)
            age(other)
        if name == "foreign-recovery" or (name == "requested" and recovery):
            record = root / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
            record.write_text(json.dumps({"version": 1, "original": name, "identity": _directory_identity(other), "job": "Global\\RAGFlow-build-" + uuid.uuid4().hex}))
            receipts[name] = record
    malformed = root / (".ragflow-cleanup-" + uuid.uuid4().hex + ".json")
    malformed.write_bytes(b"unidentifiable foreign record")
    preserved = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and not p.is_relative_to(task) and p != receipts.get("requested")}
    result = run(root, "-Name", "requested", "-Complete")
    assert result.returncode == 0, result.stderr
    assert not task.exists()
    if recovery:
        assert not receipts["requested"].exists()
    for path, (contents, modified) in preserved.items():
        assert path.read_bytes() == contents
        assert path.stat().st_mtime_ns == modified
    assert {p.name for p in root.iterdir() if p.is_dir()} == {"foreign-recovery", "foreign-expired"}


def test_complete_never_enters_shared_cache_cleanup(tmp_path):
    root = tmp_path / "tmp/ragflow"
    assert run(root, "-Name", "requested").returncode == 0
    line = next(i for i, value in enumerate(SCRIPT.read_text(encoding="utf-8").splitlines(), 1) if value.startswith("if ($CleanCache -and"))
    proof = tmp_path / "shared-cache-cleanup-entered"
    command = """
function global:docker { $global:LASTEXITCODE=0 }
$null=Set-PSBreakpoint -Script $env:TEST_SCRIPT -Line ([int]$env:TEST_LINE) -Action {
    Set-Content -LiteralPath $env:TEST_PROOF -Value 'shared cleanup reached'
    exit 73
}
& $env:TEST_SCRIPT -Root $env:TEST_ROOT -Name requested -Complete -CleanCache
"""
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        env={**os.environ, "TEST_SCRIPT": str(SCRIPT), "TEST_ROOT": str(root), "TEST_LINE": str(line), "TEST_PROOF": str(proof)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not proof.exists()
    assert not (root / "requested").exists()


@pytest.mark.parametrize("alias", ["absolute", "relative", "stale", "external"])
def test_complete_handles_pytest_aliases_but_preserves_external_targets(tmp_path, alias):
    root = tmp_path / "tmp/ragflow"
    assert run(root, "-Name", "requested").returncode == 0
    task = root / "requested"
    outside = root / "foreign/test_result0"
    outside.mkdir(parents=True)
    payload = outside / "important"
    payload.write_bytes(b"keep")
    target = task / "test_result0"
    if alias == "external":
        target = outside
    elif alias != "stale":
        target.mkdir()
    (task / "test_resultcurrent").symlink_to(target.name if alias == "relative" else target, target_is_directory=True)
    result = run(root, "-Name", "requested", "-Complete")
    assert (result.returncode == 0) is (alias != "external"), result.stderr
    assert task.exists() is (alias == "external")
    if alias == "external":
        assert (task / ".keep").is_file()
        assert (task / "test_resultcurrent").is_symlink()
    assert payload.read_bytes() == b"keep"
