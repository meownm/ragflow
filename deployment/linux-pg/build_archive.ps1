param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]*$')]
    [string]$ReleaseVersion,
    [string]$OutputDirectory = $PSScriptRoot,
    [string]$ArchiveName,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$safeVersion = $ReleaseVersion -replace '[^0-9A-Za-z._-]', '-'
if (-not $ArchiveName) {
    $ArchiveName = "ragflow-linux-pg-$safeVersion"
}
if ($ArchiveName -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]*$') {
    throw "ArchiveName contains unsupported characters: $ArchiveName"
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

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$archivePath = Join-Path $outputRoot ($ArchiveName + '.tar.gz')
$checksumPath = $archivePath + '.sha256'
if (-not $Overwrite -and ((Test-Path -LiteralPath $archivePath) -or (Test-Path -LiteralPath $checksumPath))) {
    throw "Refusing to overwrite an existing release artifact: $archivePath"
}

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRoot = Join-Path $tempBase ('ragflow-linux-pg-archive-' + [guid]::NewGuid().ToString('N'))
$stageRoot = Join-Path $tempRoot 'source'
$validationRoot = Join-Path $tempRoot 'validation'
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    $excludedDirectoryNames = @(
        '.git', '.venv', '.codex_tmp', '.playwright-cli',
        '.cache', '.hypothesis', '.mypy_cache', '.pytest_cache', '.ruff_cache',
        'node_modules', '__pycache__', 'ragflow-logs', 'output'
    )
    $excludedDirectoryPaths = @(
        'build', 'dist', 'release', 'web/dist',
        'services/asr-online-service',
        'services/asr-online-service/uploads',
        'test/playwright/artifacts',
        'ragflow_deps/huggingface.co', 'ragflow_deps/nltk_data'
    )
    $exportedFileCount = 0
    function Copy-ReleaseDirectory {
        param(
            [Parameter(Mandatory = $true)][string]$SourceDirectory,
            [Parameter(Mandatory = $true)][string]$TargetDirectory,
            [string]$RelativeDirectory = ''
        )

        foreach ($item in Get-ChildItem -LiteralPath $SourceDirectory -Force) {
            $relativePath = if ($RelativeDirectory) {
                "$RelativeDirectory/$($item.Name)"
            }
            else {
                $item.Name
            }
            $normalizedPath = $relativePath.Replace('\', '/')

            if ($item.PSIsContainer) {
                if (
                    $excludedDirectoryNames -contains $item.Name -or
                    $excludedDirectoryPaths -contains $normalizedPath -or
                    $item.Name -like '*.egg-info'
                ) {
                    continue
                }
                if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Refusing to package a directory link: $normalizedPath"
                }
                Copy-ReleaseDirectory `
                    -SourceDirectory $item.FullName `
                    -TargetDirectory (Join-Path $TargetDirectory $item.Name) `
                    -RelativeDirectory $normalizedPath
                continue
            }

            if (
                $normalizedPath -match '(^|/)\.env\.local$' -or
                ($item.Name -eq '.env' -and $normalizedPath -ne 'web/.env') -or
                $normalizedPath -match '\.(tar\.gz|bundle)(\.sha256)?$' -or
                $item.Name -eq '.DS_Store' -or
                $normalizedPath -match '\.(log|dump|sqlite|sqlite3)$' -or
                $normalizedPath -match '^deployment/linux-pg/registry-images-.*\.env$' -or
                (
                    $normalizedPath -match '^ragflow_deps/' -and
                    $normalizedPath -notin @(
                        'ragflow_deps/Dockerfile',
                        'ragflow_deps/download_deps.py',
                        'ragflow_deps/download_go_deps.py'
                    )
                )
            ) {
                continue
            }
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to package a file link: $normalizedPath"
            }

            New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $TargetDirectory $item.Name) -Force
            $script:exportedFileCount++
        }
    }

    Copy-ReleaseDirectory -SourceDirectory $sourceRoot -TargetDirectory $stageRoot

    $manifestPath = Join-Path $stageRoot 'DEPLOYMENT-SOURCE.env'
    $manifest = @(
        "RELEASE_VERSION=$ReleaseVersion"
        'PACKAGE_FORMAT=tar.gz'
        'SOURCE_MODE=filesystem-snapshot'
        "PACKAGED_AT_UTC=$([DateTime]::UtcNow.ToString('o'))"
    )
    Write-LfText -Path $manifestPath -Lines $manifest

    $requiredPaths = @(
        'deployment/linux-pg/install.sh',
        'deployment/linux-pg/docker-compose.release.yml',
        'deployment/linux-pg/seed_admin.py',
        'deployment/linux-pg/env.template',
        'DEPLOYMENT-SOURCE.env'
    )
    foreach ($relativePath in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $stageRoot $relativePath) -PathType Leaf)) {
            throw "Required deployment file is missing: $relativePath"
        }
    }

    $forbiddenPaths = @(
        Get-ChildItem -LiteralPath $stageRoot -Recurse -Force -File | ForEach-Object {
            $relativePath = [System.IO.Path]::GetRelativePath($stageRoot, $_.FullName).Replace('\', '/')
            if (
                $relativePath -in @('docker/.env', 'docker/.env.local') -or
                $relativePath -match '(^|/)(\.git|\.venv|\.codex_tmp|\.playwright-cli|node_modules|__pycache__|ragflow-logs|output|build|dist|release)(/|$)' -or
                $relativePath -match '^services/asr-online-service/uploads/' -or
                $relativePath -match '^services/asr-online-service/' -or
                $relativePath -match '^test/playwright/artifacts/' -or
                $relativePath -match '^deployment/linux-pg/registry-images-.*\.env$' -or
                $relativePath -match '^ragflow_deps/(?!Dockerfile$|download_deps\.py$|download_go_deps\.py$)' -or
                $relativePath -match '\.(tar\.gz|bundle)(\.sha256)?$'
            ) {
                $relativePath
            }
        }
    )
    if ($forbiddenPaths.Count -gt 0) {
        throw "Forbidden release files were exported:`n$($forbiddenPaths -join "`n")"
    }

    Invoke-Tar -ArgumentList @('-czf', $archivePath, '-C', $stageRoot, '.') `
        -ErrorMessage 'Archive creation failed.'
    Invoke-Tar -ArgumentList @('-tzf', $archivePath) `
        -ErrorMessage 'Archive integrity check failed.' `
        -DiscardOutput

    New-Item -ItemType Directory -Path $validationRoot | Out-Null
    Invoke-Tar -ArgumentList @('-xzf', $archivePath, '-C', $validationRoot) `
        -ErrorMessage 'Archive validation extraction failed.'
    foreach ($relativePath in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $validationRoot $relativePath) -PathType Leaf)) {
            throw "Required file is absent from the archive: $relativePath"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $validationRoot '.git')) {
        throw 'Archive unexpectedly contains Git metadata.'
    }

    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-LfText -Path $checksumPath -Lines @(
        "$archiveHash  $([System.IO.Path]::GetFileName($archivePath))"
    )

    Write-Host "Archive: $archivePath"
    Write-Host "Checksum: $checksumPath"
    Write-Host "SHA256: $archiveHash"
    Write-Host "Release version: $ReleaseVersion"
    Write-Host "Packaged files: $exportedFileCount"
}
finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like 'ragflow-linux-pg-archive-*') {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
