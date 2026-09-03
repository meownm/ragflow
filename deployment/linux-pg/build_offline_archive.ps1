param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$ReleaseVersion,
    [string]$OutputDirectory = $PSScriptRoot,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$archiveName = "ragflow-linux-pg-$ReleaseVersion-offline.tar.gz"
$archivePath = Join-Path $outputRoot $archiveName
$checksumPath = $archivePath + '.sha256'
$sourceArchiveName = "ragflow-linux-pg-$ReleaseVersion.tar.gz"

if (-not $Overwrite -and ((Test-Path -LiteralPath $archivePath) -or (Test-Path -LiteralPath $checksumPath))) {
    throw "Refusing to overwrite an existing offline release: $archivePath"
}

foreach ($commandName in @('docker', 'pnpm.cmd', 'tar')) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required build command is missing: $commandName"
    }
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

    $content = ($Lines -join "`n") + "`n"
    [System.IO.File]::WriteAllText($Path, $content, [System.Text.UTF8Encoding]::new($false))
}

$projectVersionMatch = [regex]::Match(
    (Get-Content -LiteralPath (Join-Path $sourceRoot 'pyproject.toml') -Raw),
    '(?m)^version\s*=\s*"([^"]+)"'
)
if (-not $projectVersionMatch.Success -or $projectVersionMatch.Groups[1].Value -ne $ReleaseVersion.TrimStart('v')) {
    throw "ReleaseVersion $ReleaseVersion does not match pyproject.toml version."
}

$dockerImages = @(
    'postgres:16-alpine',
    'infiniflow/ragflow:v0.26.4',
    'valkey/valkey:8',
    'elasticsearch:8.11.3',
    'plantuml/plantuml-server:jetty-v1.2026.6',
    'pgsty/minio:RELEASE.2026-03-25T00-00-00Z',
    'ragflow-pg-t-one-asr:latest'
)

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRoot = Join-Path $tempBase ('ragflow-linux-pg-offline-' + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $tempRoot 'package'
$payloadRoot = Join-Path $packageRoot 'payload'
$sourceArchivePath = Join-Path $payloadRoot $sourceArchiveName
$sourceChecksumPath = $sourceArchivePath + '.sha256'
$dockerArchivePath = Join-Path $payloadRoot 'docker-images.tar'
$frontendArchivePath = Join-Path $payloadRoot 'web-dist.tar.gz'

New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

try {
    & (Join-Path $PSScriptRoot 'build_archive.ps1') `
        -ReleaseVersion $ReleaseVersion `
        -OutputDirectory $payloadRoot `
        -Overwrite

    Push-Location (Join-Path $sourceRoot 'web')
    try {
        & pnpm.cmd install --frozen-lockfile --ignore-scripts
        if ($LASTEXITCODE -ne 0) {
            throw 'Frontend dependency installation failed.'
        }
        & pnpm.cmd run build
        if ($LASTEXITCODE -ne 0) {
            throw 'Frontend production build failed.'
        }
    }
    finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'web\dist\index.html') -PathType Leaf)) {
        throw 'Frontend build did not create web/dist/index.html.'
    }

    Push-Location (Join-Path $sourceRoot 'docker')
    try {
        & docker compose `
            --env-file .env `
            -p ragflow-pg `
            -f docker-compose.yml `
            -f docker-compose.local.yml `
            -f docker-compose.linux.local.yml `
            build t-one-asr
        if ($LASTEXITCODE -ne 0) {
            throw 'T-One image build failed.'
        }
    }
    finally {
        Pop-Location
    }

    foreach ($imageName in $dockerImages) {
        $platform = (& docker image inspect $imageName --format '{{.Os}}/{{.Architecture}}').Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Required Docker image is missing: $imageName"
        }
        if ($platform -ne 'linux/amd64') {
            throw "Docker image has unsupported platform ${platform}: $imageName"
        }
    }

    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install_offline.sh') -Destination (Join-Path $packageRoot 'install_offline.sh') -Force

    Invoke-Tar -ArgumentList @('-czf', $frontendArchivePath, '-C', (Join-Path $sourceRoot 'web'), 'dist') `
        -ErrorMessage 'Frontend archive creation failed.'

    & docker image save --output $dockerArchivePath @dockerImages
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker image archive creation failed.'
    }
    Write-LfText -Path (Join-Path $payloadRoot 'docker-images.txt') -Lines $dockerImages

    $manifestLines = @(
        "RELEASE_VERSION=$ReleaseVersion"
        'PACKAGE_MODE=offline'
        'PACKAGE_FORMAT=tar.gz'
        'TARGET_OS=rocky'
        'TARGET_VERSION=9'
        'TARGET_ARCH=amd64'
        'DOCKER_DNF_REPO=cifra-docker'
        "SOURCE_ARCHIVE=$sourceArchiveName"
        'FRONTEND_ARCHIVE=web-dist.tar.gz'
        'DOCKER_IMAGES_ARCHIVE=docker-images.tar'
        "DOCKER_IMAGE_COUNT=$($dockerImages.Count)"
        "PACKAGED_AT_UTC=$([DateTime]::UtcNow.ToString('o'))"
    )
    Write-LfText -Path (Join-Path $packageRoot 'OFFLINE-PACKAGE.env') -Lines $manifestLines

    $checksumLines = @(
        Get-ChildItem -LiteralPath $payloadRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
            $relativePath = [System.IO.Path]::GetRelativePath($packageRoot, $_.FullName).Replace('\', '/')
            $fileHash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$fileHash  $relativePath"
        }
    )
    $checksumsPath = Join-Path $packageRoot 'SHA256SUMS'
    Write-LfText -Path $checksumsPath -Lines $checksumLines

    foreach ($metadataPath in @(
        (Join-Path $packageRoot 'OFFLINE-PACKAGE.env'),
        (Join-Path $payloadRoot 'docker-images.txt'),
        $sourceChecksumPath,
        $checksumsPath
    )) {
        if ([System.IO.File]::ReadAllText($metadataPath).Contains("`r")) {
            throw "Linux metadata contains a CR character: $metadataPath"
        }
    }
    foreach ($checksumLine in $checksumLines) {
        if ($checksumLine -notmatch '^([0-9a-f]{64})  (.+)$') {
            throw "Invalid checksum line: $checksumLine"
        }
        $payloadPath = Join-Path $packageRoot $Matches[2].Replace('/', [System.IO.Path]::DirectorySeparatorChar)
        $actualPayloadHash = (Get-FileHash -LiteralPath $payloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualPayloadHash -ne $Matches[1]) {
            throw "Payload checksum verification failed: $($Matches[2])"
        }
    }

    Invoke-Tar -ArgumentList @('-czf', $archivePath, '-C', $packageRoot, '.') `
        -ErrorMessage 'Offline archive creation failed.'
    Invoke-Tar -ArgumentList @('-tzf', $archivePath) `
        -ErrorMessage 'Offline archive integrity check failed.' `
        -DiscardOutput

    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-LfText -Path $checksumPath -Lines @("$archiveHash  $archiveName")

    Write-Host "Offline archive: $archivePath"
    Write-Host "Checksum: $checksumPath"
    Write-Host "SHA256: $archiveHash"
    Write-Host "Size: $((Get-Item -LiteralPath $archivePath).Length) bytes"
    Write-Host "Docker images: $($dockerImages.Count)"
    Write-Host 'Target: Rocky Linux 9.x with Docker packages from cifra-docker'
}
finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like 'ragflow-linux-pg-offline-*') {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
