"""Contracts for the branch-independent local Docker Desktop deploy entrypoint."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deployment/local/deploy.ps1"
SOURCE = SCRIPT.read_text(encoding="utf-8")


def test_local_deploy_has_one_fixed_local_target_and_no_git_mutation():
    assert '$ProjectName = "ragflow-local"' in SOURCE
    assert '"http://127.0.0.1:9380/api/v1/system/healthz"' in SOURCE
    assert "git switch" not in SOURCE
    assert "git checkout" not in SOURCE
    assert "git worktree" not in SOURCE
    assert "git branch" not in SOURCE
    assert "git commit" not in SOURCE
    assert "git tag" not in SOURCE
    assert "git push" not in SOURCE


def test_preflight_and_backup_precede_runtime_mutation():
    config = SOURCE.index('Invoke-Compose -Arguments @("config", "--quiet")')
    backup = SOURCE.index('"pg_dump -U')
    recreate = SOURCE.index('$upArguments = @("up", "-d", "--force-recreate", "--no-deps")')
    health = SOURCE.index("$health = Wait-RagflowHealth")
    assert config < backup < recreate < health
    assert "pg_restore --list" in SOURCE
    assert '"--no-build", "--pull", "never"' in SOURCE


def test_deploy_records_source_backup_runtime_and_health_evidence():
    for field in ("source = $source", "backup = $backup", "containers = $containerEvidence", "health = $health"):
        assert field in SOURCE
    assert '"deployment.json"' in SOURCE
    assert '"compose-config.yml"' in SOURCE
    assert '"compose-ps.txt"' in SOURCE


def test_powershell_script_parses():
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is not installed")
    command = (
        "$errors=$null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}', [ref]$null, [ref]$errors); "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
