param(
    [string]$TargetDirectory = (Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) 'ragflow-linux-pg'),
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$targetRoot = [System.IO.Path]::GetFullPath($TargetDirectory)

if ($targetRoot -eq $sourceRoot -or $targetRoot.StartsWith($sourceRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    throw 'TargetDirectory must be outside the source repository to avoid recursive copies.'
}
if (Test-Path -LiteralPath $targetRoot) {
    if ((Get-ChildItem -LiteralPath $targetRoot -Force | Select-Object -First 1)) {
        throw "Target directory is not empty: $targetRoot"
    }
}
else {
    New-Item -ItemType Directory -Path $targetRoot | Out-Null
}

$sourceCommit = (& git -C $sourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to resolve the source commit'
}
$sourceStatus = @(& git -C $sourceRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the source worktree'
}
$sourceDirty = $sourceStatus.Count -gt 0
if ($sourceDirty -and -not $AllowDirty) {
    throw "Refusing to export a dirty worktree. Commit the deployment increment or pass -AllowDirty explicitly.`n$($sourceStatus -join "`n")"
}
$sourceRef = & git -C $sourceRoot describe --tags --exact-match HEAD 2>$null
if ($LASTEXITCODE -eq 0) {
    $sourceRef = $sourceRef.Trim()
}
else {
    $sourceRef = $sourceCommit.Substring(0, 12)
}

$sourceFiles = @(& git -C $sourceRoot ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to enumerate source files'
}
foreach ($relativePath in $sourceFiles) {
    $normalizedPath = $relativePath.Replace('\', '/')
    if ($normalizedPath -eq 'docker/.env') {
        continue
    }
    $sourcePath = Join-Path $sourceRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        continue
    }
    $targetPath = Join-Path $targetRoot $relativePath
    $targetParent = Split-Path -Parent $targetPath
    New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
}

@(
    "SOURCE_COMMIT=$sourceCommit"
    "SOURCE_REF=$sourceRef"
    "SOURCE_DIRTY=$($sourceDirty.ToString().ToLowerInvariant())"
    "EXPORTED_AT_UTC=$([DateTime]::UtcNow.ToString('o'))"
) | Set-Content -LiteralPath (Join-Path $targetRoot 'DEPLOYMENT-SOURCE.env') -Encoding utf8NoBOM

Write-Host "Deployment Git tree: $targetRoot"
Write-Host "Enumerated source files: $($sourceFiles.Count)"
Write-Host "Source: $sourceRef ($sourceCommit), dirty=$($sourceDirty.ToString().ToLowerInvariant())"
Write-Host 'Next: review the folder, initialize a new Git repository, commit it, and push it to the deployment remote.'
