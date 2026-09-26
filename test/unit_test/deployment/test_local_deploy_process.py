"""Contracts for the branch-independent local Docker Desktop deploy entrypoint."""

from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deployment/local/deploy.ps1"
SOURCE = SCRIPT.read_text(encoding="utf-8")
DOCKERFILE_SOURCE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
OBSERVABILITY_SOURCE = (ROOT / "docker/docker-compose.observability.yml").read_text(encoding="utf-8")


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


def test_fast_deploy_fails_closed_when_changed_runtime_is_not_bind_mounted():
    assert '[ValidateSet("Auto", "Fast", "Candidate", "Release")]' in SOURCE
    assert "unmounted_runtime = $unmountedRuntime" in SOURCE
    assert "Fast deploy is unsafe" in SOURCE
    assert "docker-compose.candidate.yml" in SOURCE


def test_candidate_image_is_pulled_and_verified_before_runtime_mutation():
    pull = SOURCE.index('Invoke-Native -FilePath "docker" -Arguments @("pull", $candidateImage)')
    revision = SOURCE.index("cat $candidateImage /ragflow/SOURCE_REVISION", pull)
    import_gate = SOURCE.index("import business_documents", revision)
    recreate = SOURCE.index('$upArguments = @("up", "-d", "--force-recreate", "--no-deps")', import_gate)
    assert pull < revision < import_gate < recreate
    assert "COPY business_documents business_documents" in DOCKERFILE_SOURCE


def test_release_receipt_revision_digest_and_jobs_are_checked_before_runtime_mutation():
    receipt_required = SOURCE.index("Release mode requires the candidate receipt")
    jobs_checked = SOURCE.index("-not (Test-CandidateReceiptJobs -CI $candidateReceiptData.ci)")
    digest_checked = SOURCE.index("Pulled candidate image digest does not match the CI receipt")
    backup = SOURCE.index('"pg_dump -U')
    recreate = SOURCE.index('$upArguments = @("up", "-d", "--force-recreate", "--no-deps")')
    assert receipt_required < jobs_checked < digest_checked < backup < recreate
    assert 'candidate_receipt_status = if ($candidateReceiptData) { "verified" }' in SOURCE


def test_backup_reuse_requires_recent_verified_artifact():
    assert "Get-FreshVerifiedBackup" in SOURCE
    assert "Get-FileHash" in SOURCE
    assert "Backup cannot be skipped" in SOURCE
    assert '$resolvedMode -eq "Release" -or $changePlan.database_sensitive.Count -gt 0' in SOURCE


def test_observability_services_are_opt_in():
    assert OBSERVABILITY_SOURCE.count("profiles: [observability]") == 5
    assert "if ($Observability)" in SOURCE


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


@pytest.mark.parametrize(
    "required,reason,engine_result,valid",
    [
        (True, "engine-boundary-changed", "success", True),
        (False, "no-engine-changes", "skipped", True),
        (False, "no-engine-changes", "failure", False),
        (False, "no-engine-changes", "cancelled", False),
        (True, "engine-boundary-changed", "skipped", False),
        (False, "baseline-unavailable", "skipped", False),
        ("false", "no-engine-changes", "skipped", False),
    ],
)
def test_deploy_evaluates_receipt_engine_policy_without_touching_docker(required, reason, engine_result, valid):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is not installed")
    ci = {
        "engine_tests_required": required,
        "engine_test_reason": reason,
        "jobs": {"ragflow_preflight": "success", "ragflow_tests_infinity": engine_result, "ragflow_tests_elasticsearch": engine_result},
    }
    command = (
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}', [ref]$null, [ref]$null); "
        "$fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Test-CandidateReceiptJobs'}, $true); "
        ". ([scriptblock]::Create($fn.Extent.Text)); Set-StrictMode -Version Latest; "
        "Test-CandidateReceiptJobs -CI ($env:RECEIPT_TEST_CI | ConvertFrom-Json)"
    )
    result = subprocess.run([pwsh, "-NoProfile", "-Command", command], env={**os.environ, "RECEIPT_TEST_CI": json.dumps(ci)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().lower() == str(valid).lower()


@pytest.mark.parametrize("known", [True, False])
def test_empty_change_plan_preserves_unknown_revision_gate(known):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is not installed")
    command = (
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}', [ref]$null, [ref]$null); "
        "$fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Get-ChangePlan'}, $true); "
        ". ([scriptblock]::Create($fn.Extent.Text)); Set-StrictMode -Version Latest; "
        f"Get-ChangePlan -ChangedPaths @() -BindSources @() -DeployedRevisionKnown ${str(known).lower()} | ConvertTo-Json -Compress"
    )
    result = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["requires_candidate"] is (not known)
    assert plan["changed_paths"] == []


def test_change_detection_includes_deployed_only_changes(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is not installed")

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True, stderr=subprocess.PIPE).strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "base").write_text("base")
    git("add", "base")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "deployed.py").write_text("deployed")
    git("add", "deployed.py")
    git("commit", "-qm", "deployed")
    deployed = git("rev-parse", "HEAD")
    git("checkout", "--detach", base)
    (tmp_path / "candidate.py").write_text("candidate")
    git("add", "candidate.py")
    git("commit", "-qm", "candidate")
    command = (
        f"$repoRoot='{tmp_path}'; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPT}', [ref]$null, [ref]$null); "
        "$functions=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -in @('Get-GitLines','Get-ChangedPaths')}, $true); "
        "$functions | ForEach-Object { . ([scriptblock]::Create($_.Extent.Text)) }; "
        f"@(Get-ChangedPaths -DeployedRevision '{deployed}') | ConvertTo-Json -Compress"
    )
    result = subprocess.run([pwsh, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == {"candidate.py", "deployed.py"}
