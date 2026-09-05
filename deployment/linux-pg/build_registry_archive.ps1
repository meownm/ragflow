param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]*$')]
    [string]$ReleaseVersion,
    [string]$ImagesEnvPath,
    [string]$OutputDirectory = $PSScriptRoot,
    [switch]$UseExistingFrontend,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$safeVersion = $ReleaseVersion -replace '[^0-9A-Za-z._-]', '-'
$archiveName = "ragflow-linux-pg-$safeVersion-registry.tar.gz"
$archivePath = Join-Path $outputRoot $archiveName
$checksumPath = $archivePath + '.sha256'
$sourceArchiveBase = "ragflow-linux-pg-$safeVersion-source"
$sourceArchiveName = $sourceArchiveBase + '.tar.gz'

if (-not $Overwrite -and ((Test-Path -LiteralPath $archivePath) -or (Test-Path -LiteralPath $checksumPath))) {
    throw "Refusing to overwrite an existing registry release: $archivePath"
}
$requiredCommands = @('tar')
if (-not $UseExistingFrontend) { $requiredCommands += 'pnpm.cmd' }
foreach ($commandName in $requiredCommands) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required build command is missing: $commandName"
    }
}

$projectVersionMatch = [regex]::Match(
    (Get-Content -LiteralPath (Join-Path $sourceRoot 'pyproject.toml') -Raw),
    '(?m)^version\s*=\s*"([^"]+)"'
)
if (-not $projectVersionMatch.Success -or $projectVersionMatch.Groups[1].Value -ne $ReleaseVersion.TrimStart('v')) {
    throw "ReleaseVersion $ReleaseVersion does not match pyproject.toml version."
}

$tarCommand = Get-Command tar -ErrorAction Stop
function Invoke-Tar {
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$ErrorMessage,
        [switch]$DiscardOutput
    )
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $tarCommand.Source
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $DiscardOutput.IsPresent
    foreach ($argument in $ArgumentList) {
        [void]$startInfo.ArgumentList.Add($argument)
    }
    $process = [System.Diagnostics.Process]::Start($startInfo)
    if ($DiscardOutput) {
        $null = $process.StandardOutput.ReadToEnd()
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw $ErrorMessage
    }
}

function Write-LfText {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Lines
    )
    [System.IO.File]::WriteAllText($Path, (($Lines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
}

function Copy-LfText {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $content = [System.IO.File]::ReadAllText($Source).
        Replace("`r`n", "`n").
        Replace("`r", "`n")
    [System.IO.File]::WriteAllText($Destination, $content, [System.Text.UTF8Encoding]::new($false))
}

$requiredImageKeys = @(
    'POSTGRES_IMAGE', 'RAGFLOW_IMAGE', 'VALKEY_IMAGE', 'ELASTICSEARCH_IMAGE',
    'PLANTUML_IMAGE', 'MINIO_IMAGE', 'T_ONE_ASR_IMAGE', 'OTEL_COLLECTOR_IMAGE',
    'TEMPO_IMAGE', 'LOKI_IMAGE', 'PROMETHEUS_IMAGE', 'GRAFANA_IMAGE',
    'SANDBOX_EXECUTOR_MANAGER_IMAGE', 'SANDBOX_BASE_NODEJS_IMAGE',
    'SANDBOX_BASE_PYTHON_IMAGE'
)

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRoot = Join-Path $tempBase ('ragflow-linux-pg-registry-' + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $tempRoot 'package'
$payloadRoot = Join-Path $packageRoot 'payload'
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

try {
    & (Join-Path $PSScriptRoot 'build_archive.ps1') `
        -ReleaseVersion $ReleaseVersion `
        -ArchiveName $sourceArchiveBase `
        -OutputDirectory $payloadRoot `
        -Overwrite

    if (-not $UseExistingFrontend) {
        Push-Location (Join-Path $sourceRoot 'web')
        try {
            & pnpm.cmd install --frozen-lockfile --ignore-scripts
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
            & pnpm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
        }
        finally {
            Pop-Location
        }
    }
    $frontendIndex = Join-Path $sourceRoot 'web\dist\index.html'
    if (-not (Test-Path -LiteralPath $frontendIndex -PathType Leaf)) {
        throw 'Frontend build did not create web/dist/index.html.'
    }

    $frontendArchiveName = 'web-dist.tar.gz'
    Invoke-Tar -ArgumentList @('-czf', (Join-Path $payloadRoot $frontendArchiveName), '-C', (Join-Path $sourceRoot 'web'), 'dist') `
        -ErrorMessage 'Frontend archive creation failed.'
    & (Join-Path $PSScriptRoot 'prepare_gvisor_bundle.ps1') -Destination (Join-Path $payloadRoot 'gvisor')

    if ($ImagesEnvPath) {
        $resolvedImagesEnv = [System.IO.Path]::GetFullPath($ImagesEnvPath)
        if (-not (Test-Path -LiteralPath $resolvedImagesEnv -PathType Leaf)) {
            throw "Images environment file does not exist: $resolvedImagesEnv"
        }
        $imageLines = @(
            Get-Content -LiteralPath $resolvedImagesEnv |
                ForEach-Object { $_.Trim() } |
                Where-Object { $_ -and -not $_.StartsWith('#') }
        )
        foreach ($imageLine in $imageLines) {
            if ($imageLine -notmatch '^([A-Z_]+)=([^\s]+)$') {
                throw "Invalid image environment line: $imageLine"
            }
        }
        $foundKeys = @($imageLines | ForEach-Object { ($_ -split '=', 2)[0] })
        $missingKeys = @($requiredImageKeys | Where-Object { $_ -notin $foundKeys })
        $unexpectedKeys = @($foundKeys | Where-Object { $_ -notin $requiredImageKeys })
        $duplicateKeys = @($foundKeys | Group-Object | Where-Object Count -gt 1 | ForEach-Object Name)
        if ($missingKeys.Count -gt 0 -or $unexpectedKeys.Count -gt 0 -or $duplicateKeys.Count -gt 0) {
            throw "Invalid images env. Missing: $($missingKeys -join ', '); unexpected: $($unexpectedKeys -join ', '); duplicates: $($duplicateKeys -join ', ')"
        }
        $imagesFileName = 'images.env'
        Write-LfText -Path (Join-Path $payloadRoot $imagesFileName) -Lines $imageLines
    }
    else {
        $imagesFileName = 'images.env.template'
        Write-LfText -Path (Join-Path $payloadRoot $imagesFileName) -Lines @(
            'POSTGRES_IMAGE=__REGISTRY_PREFIX__/library/postgres:16-alpine',
            'RAGFLOW_IMAGE=__REGISTRY_PREFIX__/infiniflow/ragflow:v0.26.4',
            'VALKEY_IMAGE=__REGISTRY_PREFIX__/valkey/valkey:8',
            'ELASTICSEARCH_IMAGE=__REGISTRY_PREFIX__/library/elasticsearch:8.11.3',
            'PLANTUML_IMAGE=__REGISTRY_PREFIX__/plantuml/plantuml-server:jetty-v1.2026.6',
            'MINIO_IMAGE=__REGISTRY_PREFIX__/pgsty/minio:RELEASE.2026-03-25T00-00-00Z'
            "T_ONE_ASR_IMAGE=__REGISTRY_PREFIX__/ragflow/t-one-asr:$($ReleaseVersion.TrimStart('v'))"
            'OTEL_COLLECTOR_IMAGE=__REGISTRY_PREFIX__/otel/opentelemetry-collector-contrib:0.160.0'
            'TEMPO_IMAGE=__REGISTRY_PREFIX__/grafana/tempo:2.10.5'
            'LOKI_IMAGE=__REGISTRY_PREFIX__/grafana/loki:3.7.0'
            'PROMETHEUS_IMAGE=__REGISTRY_PREFIX__/prom/prometheus:v3.11.0'
            'GRAFANA_IMAGE=__REGISTRY_PREFIX__/grafana/grafana:13.1.0'
            'SANDBOX_EXECUTOR_MANAGER_IMAGE=__REGISTRY_PREFIX__/infiniflow/sandbox-executor-manager:latest'
            'SANDBOX_BASE_NODEJS_IMAGE=__REGISTRY_PREFIX__/infiniflow/sandbox-base-nodejs:latest'
            'SANDBOX_BASE_PYTHON_IMAGE=__REGISTRY_PREFIX__/infiniflow/sandbox-base-python:latest'
        )
    }

    Copy-LfText -Source (Join-Path $PSScriptRoot 'install_registry.sh') -Destination (Join-Path $packageRoot 'install_registry.sh')
    Copy-LfText -Source (Join-Path $PSScriptRoot 'upgrade_registry.sh') -Destination (Join-Path $packageRoot 'upgrade_registry.sh')
    Write-LfText -Path (Join-Path $packageRoot 'REGISTRY-PACKAGE.env') -Lines @(
        "RELEASE_VERSION=$ReleaseVersion",
        'PACKAGE_MODE=registry',
        'PACKAGE_FORMAT=tar.gz',
        'TARGET_ARCH=amd64',
        'DOCKER_DNF_REPO=cifra-docker',
        "SOURCE_ARCHIVE=$sourceArchiveName",
        "FRONTEND_ARCHIVE=$frontendArchiveName",
        "IMAGES_FILE=$imagesFileName",
        'GVISOR_BUNDLE=gvisor',
        'DOCKER_IMAGE_COUNT=15',
        "PACKAGED_AT_UTC=$([DateTime]::UtcNow.ToString('o'))"
    )

    $checksumLines = @(
        Get-ChildItem -LiteralPath $payloadRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
            $relativePath = [System.IO.Path]::GetRelativePath($packageRoot, $_.FullName).Replace('\', '/')
            $fileHash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$fileHash  $relativePath"
        }
    )
    Write-LfText -Path (Join-Path $packageRoot 'SHA256SUMS') -Lines $checksumLines

    $archiveEntries = @(
        'REGISTRY-PACKAGE.env'
        'SHA256SUMS'
        'install_registry.sh'
        'upgrade_registry.sh'
    ) + @(
        Get-ChildItem -LiteralPath $payloadRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
            'payload/' + [System.IO.Path]::GetRelativePath($payloadRoot, $_.FullName).Replace('\', '/')
        }
    )
    Invoke-Tar -ArgumentList (@('-czf', $archivePath, '-C', $packageRoot) + $archiveEntries) `
        -ErrorMessage 'Registry release archive creation failed.'
    Invoke-Tar -ArgumentList @('-tzf', $archivePath) -ErrorMessage 'Registry release archive validation failed.' -DiscardOutput

    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-LfText -Path $checksumPath -Lines @("$archiveHash  $archiveName")

    Write-Host "Registry archive: $archivePath"
    Write-Host "Checksum: $checksumPath"
    Write-Host "SHA256: $archiveHash"
    Write-Host "Images file: $imagesFileName"
}
finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like 'ragflow-linux-pg-registry-*') {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
