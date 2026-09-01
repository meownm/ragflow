param(
    [string]$OutputDirectory = $PSScriptRoot,
    [string]$BundleName = ('ragflow-pg-linux-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ($BundleName + '-' + [guid]::NewGuid().ToString('N'))
$stagingApp = Join-Path $stagingRoot 'ragflow-pg'
$archive = Join-Path $outputRoot ($BundleName + '.tar.gz')

New-Item -ItemType Directory -Path $stagingApp -Force | Out-Null
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null

$excludedDirectories = @(
    '.git', '.venv', '.codex_tmp', '.playwright-cli', 'node_modules', 'output',
    'ragflow-logs', '__pycache__', '.pytest_cache', '.ruff_cache', 'release'
)
$robocopyArgs = @($sourceRoot, $stagingApp, '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD') +
    $excludedDirectories + @('/XF', '*.tar.gz', '*.tar.gz.sha256')
& robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

$ignoredPaths = & git -C $sourceRoot ls-files --others --ignored --exclude-standard
if ($LASTEXITCODE -ne 0) {
    throw 'git ignored-file discovery failed'
}
foreach ($relativePath in $ignoredPaths) {
    $stagedPath = Join-Path $stagingApp $relativePath
    if (Test-Path -LiteralPath $stagedPath) {
        Remove-Item -LiteralPath $stagedPath -Recurse -Force
    }
}
Get-ChildItem -LiteralPath $stagingApp -Recurse -Directory |
    Sort-Object FullName -Descending |
    Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Force) } |
    Remove-Item -Force

Copy-Item -LiteralPath (Join-Path $stagingApp 'deployment\linux-pg\env.template') -Destination (Join-Path $stagingApp 'docker\.env') -Force
Remove-Item -LiteralPath (Join-Path $stagingApp 'docker\.env.local') -Force -ErrorAction SilentlyContinue

$manifest = Join-Path $stagingApp 'SHA256SUMS'
$manifestLines = Get-ChildItem -LiteralPath $stagingApp -Recurse -File |
    Where-Object { $_.FullName -ne $manifest } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [System.IO.Path]::GetRelativePath($stagingApp, $_.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
Set-Content -LiteralPath $manifest -Value $manifestLines -Encoding utf8NoBOM

Push-Location $stagingRoot
try {
    & tar -czf $archive 'ragflow-pg'
    if ($LASTEXITCODE -ne 0) { throw "tar failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($archive + '.sha256') -Value "$archiveHash  $([System.IO.Path]::GetFileName($archive))" -Encoding ascii

Write-Host "Bundle: $archive"
Write-Host "SHA256: $archiveHash"
Write-Host "Install: tar -xzf $([System.IO.Path]::GetFileName($archive)) && sudo bash ragflow-pg/deployment/linux-pg/install.sh"
