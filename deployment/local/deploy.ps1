[CmdletBinding()]
param(
    [string]$ProjectName = "ragflow-local",
    [ValidateSet("ragflow-cpu", "t-one-asr")]
    [string[]]$Services = @("ragflow-cpu"),
    [ValidateSet("Auto", "Fast", "Candidate", "Release")]
    [string]$Mode = "Auto",
    [string]$CandidateRegistry = "192.168.1.175:5443",
    [string]$CandidateRevision,
    [string]$CandidateImageReference,
    [string]$CandidateReceipt,
    [switch]$Observability,
    [switch]$Build,
    [switch]$BuildFrontend,
    [switch]$SkipBackup,
    [switch]$CheckOnly,
    [ValidateRange(1, 168)]
    [int]$BackupFreshHours = 24,
    [ValidateRange(30, 1800)]
    [int]$HealthTimeoutSeconds = 300,
    [string]$EvidenceDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dockerDirectory = Join-Path $repoRoot "docker"
$composeFiles = [System.Collections.Generic.List[string]]@(
    "docker-compose.yml",
    "docker-compose.local.yml",
    "docker-compose.linux.local.yml"
)
if ($Observability) {
    $composeFiles.Add("docker-compose.observability.yml")
}
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

function Update-ComposeArguments {
    $script:composeArguments = @("compose", "-p", $ProjectName, "--env-file", ".env", "--env-file", ".env.local")
    foreach ($composeFile in $composeFiles) {
        $script:composeArguments += @("-f", $composeFile)
    }
}
Update-ComposeArguments

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

function Get-DeployedRevision {
    $containerId = ((Invoke-Compose -Arguments @("ps", "-q", "ragflow-cpu") -Capture) -join "").Trim()
    if (-not $containerId) {
        return $null
    }
    $revision = ((& docker exec $containerId sh -lc "cat /ragflow/SOURCE_REVISION 2>/dev/null || true") -join "").Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') {
        return $null
    }
    return $revision
}

function Get-ChangedPaths {
    param([string]$DeployedRevision)

    $paths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    if ($DeployedRevision) {
        & git -C $repoRoot cat-file -e "${DeployedRevision}^{commit}" 2>$null
        if ($LASTEXITCODE -eq 0) {
            foreach ($path in @(Get-GitLines -Arguments @("diff", "--name-only", "$DeployedRevision...HEAD"))) {
                if ($path) { [void]$paths.Add($path.Replace('\', '/')) }
            }
        }
    }
    foreach ($arguments in @(
        @("diff", "--name-only"),
        @("diff", "--cached", "--name-only"),
        @("ls-files", "--others", "--exclude-standard")
    )) {
        foreach ($path in @(Get-GitLines -Arguments $arguments)) {
            if ($path) { [void]$paths.Add($path.Replace('\', '/')) }
        }
    }
    return @($paths | Sort-Object)
}

function Get-RagflowBindSources {
    param([Parameter(Mandatory = $true)]$RenderedCompose)

    $service = $RenderedCompose.services.'ragflow-cpu'
    if (-not $service) {
        throw "Rendered Compose config does not contain ragflow-cpu."
    }
    $sources = @()
    foreach ($volume in @($service.volumes)) {
        if ($volume.type -eq "bind" -and $volume.source) {
            $sources += [System.IO.Path]::GetFullPath([string]$volume.source).TrimEnd('\', '/')
        }
    }
    return $sources
}

function Test-PathCoveredByBindMount {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryPath,
        [Parameter(Mandatory = $true)][string[]]$BindSources
    )

    $absolutePath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $RepositoryPath.Replace('/', '\')))
    foreach ($source in $BindSources) {
        if ($absolutePath.Equals($source, [System.StringComparison]::OrdinalIgnoreCase) -or
            $absolutePath.StartsWith("$source\", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Get-ChangePlan {
    param(
        [Parameter(Mandatory = $true)][string[]]$ChangedPaths,
        [Parameter(Mandatory = $true)][string[]]$BindSources,
        [bool]$DeployedRevisionKnown
    )

    $frontend = @($ChangedPaths | Where-Object { $_ -like "web/*" })
    $asr = @($ChangedPaths | Where-Object { $_ -like "services/asr-online-service/*" })
    $database = @($ChangedPaths | Where-Object {
        $_ -eq "api/db/db_models.py" -or $_ -like "*/migrations/*" -or
        $_ -like "docker/*postgres*" -or $_ -like "docker/*init*sql*"
    })
    $imageInputs = @($ChangedPaths | Where-Object {
        $_ -eq "Dockerfile" -or $_ -in @("pyproject.toml", "uv.lock", "go.mod", "go.sum", "build.sh") -or
        $_ -like "cmd/*" -or $_ -like "internal/*" -or $_ -like "ragflow_deps/*" -or
        $_ -like "deepdoc/*" -or $_ -like "docker/entrypoint.sh"
    })
    $composeConfig = @($ChangedPaths | Where-Object {
        $_ -like "docker/*.yml" -or $_ -like "docker/*.yaml" -or $_ -like "docker/*.env*"
    })
    $runtimeRoots = @("api/", "rag/", "agent/", "common/", "admin/", "conf/", "business_documents/", "mcp/", "memory/")
    $runtime = @($ChangedPaths | Where-Object {
        $candidate = $_
        @($runtimeRoots | Where-Object { $candidate.StartsWith($_, [System.StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
    })
    $unmountedRuntime = @($runtime | Where-Object { -not (Test-PathCoveredByBindMount -RepositoryPath $_ -BindSources $BindSources) })
    $requiresCandidate = -not $DeployedRevisionKnown -or $imageInputs.Count -gt 0 -or $unmountedRuntime.Count -gt 0

    return [ordered]@{
        changed_paths = $ChangedPaths
        frontend = $frontend
        asr = $asr
        database_sensitive = $database
        image_inputs = $imageInputs
        compose_config = $composeConfig
        runtime = $runtime
        unmounted_runtime = $unmountedRuntime
        requires_candidate = $requiresCandidate
    }
}

function Get-FreshVerifiedBackup {
    param([int]$FreshHours)

    $deployRoot = Join-Path $repoRoot "output\local-deploy"
    if (-not (Test-Path -LiteralPath $deployRoot -PathType Container)) {
        return $null
    }
    $cutoff = [DateTimeOffset]::Now.AddHours(-$FreshHours)
    foreach ($evidenceFile in @(Get-ChildItem -LiteralPath $deployRoot -Filter deployment.json -Recurse -File |
            Sort-Object LastWriteTime -Descending)) {
        try {
            $evidence = Get-Content -LiteralPath $evidenceFile.FullName -Raw | ConvertFrom-Json
            if (-not $evidence.backup.path -or -not $evidence.backup.sha256) { continue }
            if ($evidenceFile.LastWriteTime -lt $cutoff.LocalDateTime) { continue }
            if (-not (Test-Path -LiteralPath $evidence.backup.path -PathType Leaf)) { continue }
            $actualHash = (Get-FileHash -LiteralPath $evidence.backup.path -Algorithm SHA256).Hash
            if ($actualHash -eq $evidence.backup.sha256) {
                return [ordered]@{ path = $evidence.backup.path; sha256 = $actualHash; reused = $true }
            }
        }
        catch {
            continue
        }
    }
    return $null
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
$renderedConfigJson = ((Invoke-Compose -Arguments @("config", "--format", "json") -Capture) -join [Environment]::NewLine) | ConvertFrom-Json
$bindSources = @(Get-RagflowBindSources -RenderedCompose $renderedConfigJson)
$deployedRevision = Get-DeployedRevision
$changedPaths = @(Get-ChangedPaths -DeployedRevision $deployedRevision)
$changePlan = Get-ChangePlan -ChangedPaths $changedPaths -BindSources $bindSources -DeployedRevisionKnown ([bool]$deployedRevision)

$dirtyPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($arguments in @(
    @("diff", "--name-only"),
    @("diff", "--cached", "--name-only"),
    @("ls-files", "--others", "--exclude-standard")
)) {
    foreach ($path in @(Get-GitLines -Arguments $arguments)) {
        if ($path) { [void]$dirtyPaths.Add($path.Replace('\', '/')) }
    }
}
$candidateOnlyDirtyPaths = @($changePlan.image_inputs + $changePlan.unmounted_runtime |
    Where-Object { $dirtyPaths.Contains($_) } | Sort-Object -Unique)

$resolvedMode = $Mode
if ($resolvedMode -eq "Auto") {
    $resolvedMode = if ($changePlan.requires_candidate) { "Candidate" } else { "Fast" }
}
if ($resolvedMode -eq "Fast" -and $changePlan.requires_candidate) {
    $details = @($changePlan.image_inputs + $changePlan.unmounted_runtime | Sort-Object -Unique) -join ", "
    if (-not $details) { $details = "deployed SOURCE_REVISION is unknown" }
    throw "Fast deploy is unsafe: runtime changes require a CI candidate image. Paths: $details"
}
if ($resolvedMode -in @("Candidate", "Release") -and $candidateOnlyDirtyPaths.Count -gt 0 -and -not $CandidateImageReference) {
    throw "Candidate deploy cannot include uncommitted image/runtime changes. Commit and pass CI first: $($candidateOnlyDirtyPaths -join ', ')"
}
if ($CandidateImageReference -and $candidateOnlyDirtyPaths.Count -gt 0) {
    Write-Warning "Explicit validation image override is being used with dirty image inputs: $($candidateOnlyDirtyPaths -join ', ')"
}

if (-not $CandidateRevision) {
    $CandidateRevision = $head
}
$candidateImage = $null
$candidateReceiptData = $null
$candidateReceiptPath = $null
$candidateReceiptHash = $null
if ($resolvedMode -in @("Candidate", "Release")) {
    if ($CandidateRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CandidateRevision must be a full 40-character Git SHA."
    }
    $candidateImage = if ($CandidateImageReference) {
        $CandidateImageReference
    }
    else {
        "${CandidateRegistry}/ragflow:${CandidateRevision}"
    }
    if ($resolvedMode -eq "Release" -and -not $CandidateReceipt) {
        throw "Release mode requires the candidate receipt from the successful CI run."
    }
    if ($CandidateReceipt) {
        $candidateReceiptPath = [System.IO.Path]::GetFullPath($CandidateReceipt)
        if (-not (Test-Path -LiteralPath $candidateReceiptPath -PathType Leaf)) {
            throw "Candidate receipt is unavailable: $candidateReceiptPath"
        }
        $candidateReceiptData = Get-Content -LiteralPath $candidateReceiptPath -Raw | ConvertFrom-Json
        $receiptJobs = $candidateReceiptData.ci.jobs
        $imageRepository = $candidateImage.Substring(0, $candidateImage.LastIndexOf(':'))
        if ($candidateReceiptData.schema -ne 1 -or
            $candidateReceiptData.source_revision -ne $CandidateRevision -or
            $candidateReceiptData.image.reference -ne $candidateImage -or
            $candidateReceiptData.image.digest -cnotmatch ('^' + [regex]::Escape($imageRepository) + '@sha256:[0-9a-f]{64}$') -or
            $candidateReceiptData.ci.run_id -notmatch '^[0-9]+$' -or
            -not $candidateReceiptData.ci.repository -or
            $receiptJobs.ragflow_preflight -ne "success" -or
            $receiptJobs.ragflow_tests_infinity -ne "success" -or
            $receiptJobs.ragflow_tests_elasticsearch -ne "success") {
            throw "Candidate receipt does not prove the requested revision, image and required CI jobs."
        }
        $candidateReceiptHash = (Get-FileHash -LiteralPath $candidateReceiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $env:RAGFLOW_IMAGE = $candidateImage
    if (-not $composeFiles.Contains("docker-compose.candidate.yml")) {
        $composeFiles.Add("docker-compose.candidate.yml")
        Update-ComposeArguments
    }
    $Services = @("ragflow-cpu")
    $Build = $false
    $BuildFrontend = $false
    Invoke-Compose -Arguments @("config", "--quiet")
}
elseif (-not $PSBoundParameters.ContainsKey("Services")) {
    $plannedServices = [System.Collections.Generic.List[string]]::new()
    if ($changePlan.asr.Count -gt 0) { $plannedServices.Add("t-one-asr") }
    if ($changePlan.runtime.Count -gt 0 -or $changePlan.compose_config.Count -gt 0) {
        $plannedServices.Add("ragflow-cpu")
    }
    $Services = @($plannedServices | Select-Object -Unique)
}

if ($resolvedMode -eq "Fast" -and $changePlan.frontend.Count -gt 0) {
    $BuildFrontend = $true
}
if ($resolvedMode -eq "Fast" -and $changePlan.asr.Count -gt 0) {
    $Build = $true
}

$renderedConfig = Invoke-Compose -Arguments @("config") -Capture
$renderedConfig | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "compose-config.yml") -Encoding utf8
Write-Host "Deploy plan: mode=$resolvedMode; services=$($Services -join ','); candidate=$candidateImage"
if ($changePlan.unmounted_runtime.Count -gt 0) {
    Write-Host "Unmounted runtime paths require candidate image: $($changePlan.unmounted_runtime -join ', ')"
}

if ($CheckOnly) {
    $preflight = [ordered]@{
        schema = 1
        mode = "check-only"
        planned_mode = $resolvedMode
        created_at = [DateTimeOffset]::Now.ToString("o")
        project = $ProjectName
        services = $Services
        source = $source
        deployed_revision = $deployedRevision
        candidate_image = $candidateImage
        candidate_receipt_path = $candidateReceiptPath
        candidate_receipt_sha256 = $candidateReceiptHash
        candidate_receipt_status = if ($candidateReceiptData) { "metadata_only" } elseif ($candidateImage) { "missing" } else { "not_applicable" }
        candidate_image_override = [bool]$CandidateImageReference
        change_plan = $changePlan
        compose_files = $composeFiles
    }
    $preflight | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "deployment.json") -Encoding utf8
    Write-Host "Preflight passed. Evidence: $EvidenceDirectory"
    exit 0
}

$candidateImageId = $null
$candidateRepoDigests = @()
if ($candidateImage) {
    Invoke-Native -FilePath "docker" -Arguments @("pull", $candidateImage)
    $actualRevision = (& docker run --rm --entrypoint cat $candidateImage /ragflow/SOURCE_REVISION).Trim()
    if ($LASTEXITCODE -ne 0 -or $actualRevision -ne $CandidateRevision) {
        throw "Candidate image revision mismatch: expected $CandidateRevision, got '$actualRevision'."
    }
    & docker run --rm --entrypoint /ragflow/.venv/bin/python $candidateImage -c "import business_documents"
    if ($LASTEXITCODE -ne 0) {
        throw "Candidate image is incomplete: business_documents cannot be imported."
    }
    $candidateInspection = & docker image inspect $candidateImage | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $candidateInspection -or -not $candidateInspection[0].Id) {
        throw "Could not inspect the pulled candidate image identity."
    }
    $candidateImageId = $candidateInspection[0].Id
    $candidateRepoDigests = @($candidateInspection[0].RepoDigests | Where-Object { $_ })
    if ($candidateReceiptData -and $candidateRepoDigests -cnotcontains $candidateReceiptData.image.digest) {
        throw "Pulled candidate image digest does not match the CI receipt."
    }
}

if ($BuildFrontend) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        throw "npm is required by -BuildFrontend."
    }
    Push-Location (Join-Path $repoRoot "web")
    try {
        $previousNodeOptions = $env:NODE_OPTIONS
        $env:NODE_OPTIONS = "--max-old-space-size=8192"
        $npmPath = $npm.Source
        Invoke-Native -FilePath $npmPath -Arguments @("run", "build")
    }
    finally {
        $env:NODE_OPTIONS = $previousNodeOptions
        Pop-Location
    }
}

$backup = $null
$backupRequired = $resolvedMode -in @("Candidate", "Release") -or
    $changePlan.database_sensitive.Count -gt 0 -or $changePlan.compose_config.Count -gt 0
if ($SkipBackup) {
    if ($resolvedMode -eq "Release" -or $changePlan.database_sensitive.Count -gt 0) {
        throw "Backup cannot be skipped for Release mode or database-sensitive changes."
    }
    $backup = Get-FreshVerifiedBackup -FreshHours $BackupFreshHours
    if (-not $backup) {
        throw "Backup can be skipped only when a fresh verified backup exists (max age: $BackupFreshHours hours)."
    }
    $backupRequired = $false
}
elseif (-not $backupRequired -and $Services.Count -gt 0) {
    $backup = Get-FreshVerifiedBackup -FreshHours $BackupFreshHours
    $backupRequired = -not [bool]$backup
}

if ($backupRequired) {
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
        reused = $false
    }
}

$deploymentAction = "none"
$restartOnly = $resolvedMode -eq "Fast" -and $Services.Count -eq 1 -and
    $Services[0] -eq "ragflow-cpu" -and $changePlan.runtime.Count -gt 0 -and
    $changePlan.frontend.Count -eq 0 -and $changePlan.compose_config.Count -eq 0 -and -not $Build
if ($restartOnly) {
    Invoke-Compose -Arguments (@("restart") + $Services)
    $deploymentAction = "restart"
}
elseif ($Services.Count -gt 0) {
    $upArguments = @("up", "-d", "--force-recreate", "--no-deps")
    if ($Build) {
        $upArguments += "--build"
    }
    else {
        $upArguments += @("--no-build", "--pull", "never")
    }
    $upArguments += $Services
    Invoke-Compose -Arguments $upArguments
    $deploymentAction = "recreate"
}

$containerEvidence = @()
foreach ($service in $Services) {
    $containerId = Wait-ContainerHealth -Service $service -TimeoutSeconds $HealthTimeoutSeconds
    $inspection = & docker inspect $containerId | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect deployed service '$service'."
    }
    if ($service -eq "ragflow-cpu" -and $candidateImageId -and $inspection[0].Image -ne $candidateImageId) {
        throw "Deployed ragflow-cpu image does not match the verified candidate image."
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
    mode = $resolvedMode.ToLowerInvariant()
    created_at = [DateTimeOffset]::Now.ToString("o")
    project = $ProjectName
    services = $Services
    action = $deploymentAction
    build = [bool]$Build
    build_frontend = [bool]$BuildFrontend
    observability = [bool]$Observability
    source = $source
    deployed_revision = $deployedRevision
    candidate_revision = if ($candidateImage) { $CandidateRevision } else { $null }
    candidate_image = $candidateImage
    candidate_image_id = $candidateImageId
    candidate_repo_digests = $candidateRepoDigests
    candidate_receipt_path = $candidateReceiptPath
    candidate_receipt_sha256 = $candidateReceiptHash
    candidate_receipt_status = if ($candidateReceiptData) { "verified" } elseif ($candidateImage) { "missing" } else { "not_applicable" }
    candidate_image_override = [bool]$CandidateImageReference
    change_plan = $changePlan
    compose_files = $composeFiles
    backup = $backup
    containers = $containerEvidence
    health = $health
}
$evidence | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $EvidenceDirectory "deployment.json") -Encoding utf8

Write-Host "Local deployment passed. Evidence: $EvidenceDirectory"
