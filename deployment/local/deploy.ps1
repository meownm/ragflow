[CmdletBinding()]
param(
    [string]$ProjectName = "ragflow-local",
    [ValidateSet("ragflow-cpu", "t-one-asr")]
    [string[]]$Services = @("ragflow-cpu"),
    [switch]$Build,
    [switch]$BuildFrontend,
    [switch]$SkipBackup,
    [switch]$CheckOnly,
    [ValidateRange(30, 1800)]
    [int]$HealthTimeoutSeconds = 300,
    [string]$EvidenceDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dockerDirectory = Join-Path $repoRoot "docker"
$composeFiles = @(
    "docker-compose.yml",
    "docker-compose.local.yml",
    "docker-compose.linux.local.yml",
    "docker-compose.observability.yml"
)
$requiredFiles = @(".env", ".env.local") + $composeFiles

foreach ($requiredFile in $requiredFiles) {
    $path = Join-Path $dockerDirectory $requiredFile
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required local deployment file is missing: $path"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is not available."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git CLI is not available."
}

$composeArguments = @("compose", "-p", $ProjectName, "--env-file", ".env", "--env-file", ".env.local")
foreach ($composeFile in $composeFiles) {
    $composeArguments += @("-f", $composeFile)
}

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$Capture
    )

    Push-Location $dockerDirectory
    try {
        if ($Capture) {
            $result = & docker @composeArguments @Arguments 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "docker compose failed ($LASTEXITCODE): $($result -join [Environment]::NewLine)"
            }
            return $result
        }

        & docker @composeArguments @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE."
    }
}

function Get-GitLines {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $result = & git -C $repoRoot @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git failed with exit code $LASTEXITCODE."
    }
    return @($result)
}

function Wait-RagflowHealth {
    param([int]$TimeoutSeconds)

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:9380/api/v1/system/healthz" -TimeoutSec 10
            if ($response.status -eq "ok") {
                return $response
            }
            $lastError = "health status is '$($response.status)'"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 5
    }
    throw "RAGFlow health did not become ready in $TimeoutSeconds seconds. Last error: $lastError"
}

function Wait-ContainerHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [int]$TimeoutSeconds
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $containerId = ((Invoke-Compose -Arguments @("ps", "-q", $Service) -Capture) -join "").Trim()
        if ($containerId) {
            $state = (& docker inspect --format "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" $containerId).Trim()
            if ($LASTEXITCODE -ne 0) {
                throw "Could not inspect container for service '$Service'."
            }
            if ($state -in @("running|healthy", "running|none")) {
                return $containerId
            }
        }
        Start-Sleep -Seconds 5
    }
    throw "Service '$Service' did not become healthy in $TimeoutSeconds seconds."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path $repoRoot "output\local-deploy\$timestamp"
}
$EvidenceDirectory = [System.IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Force -Path $EvidenceDirectory | Out-Null

$head = (Get-GitLines -Arguments @("rev-parse", "HEAD") | Select-Object -First 1).Trim()
$branch = (Get-GitLines -Arguments @("branch", "--show-current") | Select-Object -First 1).Trim()
if (-not $branch) {
    $branch = "DETACHED"
}
$worktreeStatus = @(Get-GitLines -Arguments @("status", "--short", "--untracked-files=all"))
$source = [ordered]@{
    repository = $repoRoot
    head = $head
    branch = $branch
    dirty = $worktreeStatus.Count -gt 0
    changes = $worktreeStatus
}

Write-Host "Candidate: $head from current checkout ($branch); dirty=$($source.dirty)"
Write-Host "Local target: Docker Desktop Compose project '$ProjectName'"

# Preflight is deliberately before backup or container mutation.
Invoke-Compose -Arguments @("config", "--quiet")
$renderedConfig = Invoke-Compose -Arguments @("config") -Capture
$renderedConfig | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "compose-config.yml") -Encoding utf8

if ($CheckOnly) {
    $preflight = [ordered]@{
        schema = 1
        mode = "check-only"
        created_at = [DateTimeOffset]::Now.ToString("o")
        project = $ProjectName
        services = $Services
        source = $source
        compose_files = $composeFiles
    }
    $preflight | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "deployment.json") -Encoding utf8
    Write-Host "Preflight passed. Evidence: $EvidenceDirectory"
    exit 0
}

if ($BuildFrontend) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        throw "npm is required by -BuildFrontend."
    }
    Push-Location (Join-Path $repoRoot "web")
    try {
        $previousNodeOptions = $env:NODE_OPTIONS
        $env:NODE_OPTIONS = "--max-old-space-size=8192"
        Invoke-Native -FilePath $npm.Source -Arguments @("run", "build")
    }
    finally {
        $env:NODE_OPTIONS = $previousNodeOptions
        Pop-Location
    }
}

$backup = $null
if (-not $SkipBackup) {
    $postgresContainer = ((Invoke-Compose -Arguments @("ps", "-q", "postgres") -Capture) -join "").Trim()
    if (-not $postgresContainer) {
        throw "PostgreSQL is not running; start the local stack or use -SkipBackup for an intentional first install."
    }
    $postgresState = (& docker inspect --format "{{.State.Status}}" $postgresContainer).Trim()
    if ($LASTEXITCODE -ne 0 -or $postgresState -ne "running") {
        throw "PostgreSQL container is not running; deployment stopped before mutation."
    }

    $backupName = "ragflow-postgres-$timestamp.dump"
    $containerBackup = "/tmp/$backupName"
    Invoke-Native -FilePath "docker" -Arguments @(
        "exec", $postgresContainer, "sh", "-lc",
        "pg_dump -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -Fc -f '$containerBackup' && pg_restore --list '$containerBackup' >/dev/null"
    )
    $backupPath = Join-Path $EvidenceDirectory $backupName
    try {
        Invoke-Native -FilePath "docker" -Arguments @("cp", "${postgresContainer}:$containerBackup", $backupPath)
    }
    finally {
        & docker exec $postgresContainer rm -f $containerBackup | Out-Null
    }
    $backup = [ordered]@{
        path = $backupPath
        sha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash
    }
}

$upArguments = @("up", "-d", "--force-recreate", "--no-deps")
if ($Build) {
    $upArguments += "--build"
}
else {
    $upArguments += @("--no-build", "--pull", "never")
}
$upArguments += $Services
Invoke-Compose -Arguments $upArguments

$containerEvidence = @()
foreach ($service in $Services) {
    $containerId = Wait-ContainerHealth -Service $service -TimeoutSeconds $HealthTimeoutSeconds
    $inspection = & docker inspect $containerId | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect deployed service '$service'."
    }
    $containerEvidence += [ordered]@{
        service = $service
        id = $containerId
        image = $inspection[0].Image
        status = $inspection[0].State.Status
        health = if ($inspection[0].State.Health) { $inspection[0].State.Health.Status } else { "none" }
        restart_count = $inspection[0].RestartCount
    }
}

$health = Wait-RagflowHealth -TimeoutSeconds $HealthTimeoutSeconds
$composePs = Invoke-Compose -Arguments @("ps") -Capture
$composePs | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "compose-ps.txt") -Encoding utf8

$evidence = [ordered]@{
    schema = 1
    mode = "deploy"
    created_at = [DateTimeOffset]::Now.ToString("o")
    project = $ProjectName
    services = $Services
    build = [bool]$Build
    build_frontend = [bool]$BuildFrontend
    source = $source
    compose_files = $composeFiles
    backup = $backup
    containers = $containerEvidence
    health = $health
}
$evidence | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "deployment.json") -Encoding utf8

Write-Host "Local deployment passed. Evidence: $EvidenceDirectory"
