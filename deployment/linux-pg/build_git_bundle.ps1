param(
    [string]$OutputDirectory = $PSScriptRoot,
    [string]$BundleName,
    [string]$DeploymentTag
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
$sourceStatus = @(& git -C $sourceRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the source worktree.'
}
if ($sourceStatus.Count -gt 0) {
    throw "Refusing to build a release from a dirty worktree.`n$($sourceStatus -join "`n")"
}

$sourceCommit = (& git -C $sourceRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to resolve the source commit.'
}
$sourceRef = & git -C $sourceRoot describe --tags --exact-match HEAD 2>$null
if ($LASTEXITCODE -eq 0) {
    $sourceRef = $sourceRef.Trim()
}
else {
    $sourceRef = $sourceCommit.Substring(0, 12)
}

$safeRef = $sourceRef -replace '[^0-9A-Za-z._-]', '-'
if (-not $BundleName) {
    $BundleName = "ragflow-linux-pg-$safeRef"
}
if (-not $DeploymentTag) {
    $DeploymentTag = $sourceRef
}
if ($DeploymentTag -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]*$') {
    throw "DeploymentTag contains unsupported characters: $DeploymentTag"
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$bundlePath = Join-Path $outputRoot ($BundleName + '.bundle')
$checksumPath = $bundlePath + '.sha256'
if ((Test-Path -LiteralPath $bundlePath) -or (Test-Path -LiteralPath $checksumPath)) {
    throw "Refusing to overwrite an existing release artifact: $bundlePath"
}

$tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$tempRoot = Join-Path $tempBase ('ragflow-linux-pg-' + [guid]::NewGuid().ToString('N'))
$deploymentRepo = Join-Path $tempRoot 'deployment-repo'
$validationClone = Join-Path $tempRoot 'validation-clone'
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    & (Join-Path $PSScriptRoot 'export_git_tree.ps1') -TargetDirectory $deploymentRepo
    if ($LASTEXITCODE -ne 0) {
        throw 'Deployment tree export failed.'
    }

    & git -C $deploymentRepo init -b main
    if ($LASTEXITCODE -ne 0) { throw 'git init failed.' }
    & git -C $deploymentRepo config user.name 'RAGFlow Release Builder'
    & git -C $deploymentRepo config user.email 'release-builder@ragflow.local'
    & git -C $deploymentRepo config core.autocrlf true
    & git -C $deploymentRepo config core.safecrlf false
    & git -C $deploymentRepo config commit.gpgSign false
    & git -C $deploymentRepo config tag.gpgSign false
    & git -C $deploymentRepo add --all 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'git add failed.' }

    $executablePaths = @(& git -C $sourceRoot ls-files --stage | ForEach-Object {
        if ($_ -match '^100755\s+[0-9a-f]+\s+\d+\t(.+)$') { $Matches[1] }
    })
    foreach ($relativePath in $executablePaths) {
        if (Test-Path -LiteralPath (Join-Path $deploymentRepo $relativePath) -PathType Leaf) {
            & git -C $deploymentRepo update-index --chmod=+x -- $relativePath
            if ($LASTEXITCODE -ne 0) { throw "Unable to preserve executable mode: $relativePath" }
        }
    }

    $requiredPaths = @(
        'deployment/linux-pg/install.sh',
        'deployment/linux-pg/docker-compose.release.yml',
        'deployment/linux-pg/seed_admin_asr.py',
        'deployment/linux-pg/env.template',
        'services/asr-online-service/Dockerfile',
        'DEPLOYMENT-SOURCE.env'
    )
    foreach ($relativePath in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $deploymentRepo $relativePath) -PathType Leaf)) {
            throw "Required deployment file is missing: $relativePath"
        }
    }
    $forbiddenPaths = @(& git -C $deploymentRepo ls-files | Where-Object {
        $_ -eq 'docker/.env' -or
        $_ -eq 'docker/.env.local' -or
        $_ -match '(^|/)(node_modules|__pycache__|ragflow-logs)(/|$)' -or
        $_ -match '\.(tar\.gz|bundle)(\.sha256)?$'
    })
    if ($forbiddenPaths.Count -gt 0) {
        throw "Forbidden release files were staged:`n$($forbiddenPaths -join "`n")"
    }

    & git -C $deploymentRepo diff --cached --check -- deployment/linux-pg DEPLOYMENT-SOURCE.env
    if ($LASTEXITCODE -ne 0) { throw 'Deployment-owned files failed git diff --check.' }
    & git -C $deploymentRepo commit --quiet -m "RAGFlow Linux PostgreSQL deployment from $sourceRef"
    if ($LASTEXITCODE -ne 0) { throw 'Deployment repository commit failed.' }
    & git -C $deploymentRepo tag -a $DeploymentTag -m "Linux PostgreSQL deployment from $sourceRef"
    if ($LASTEXITCODE -ne 0) { throw 'Deployment repository tag failed.' }
    & git -C $deploymentRepo bundle create $bundlePath --all
    if ($LASTEXITCODE -ne 0) { throw 'Git bundle creation failed.' }
    & git -C $deploymentRepo bundle verify $bundlePath
    if ($LASTEXITCODE -ne 0) { throw 'Git bundle verification failed.' }

    & git clone --quiet $bundlePath $validationClone
    if ($LASTEXITCODE -ne 0) { throw 'Validation clone from the Git bundle failed.' }
    & git -C $validationClone checkout --quiet $DeploymentTag
    if ($LASTEXITCODE -ne 0) { throw 'Validation checkout of the deployment tag failed.' }
    $validationStatus = @(& git -C $validationClone status --porcelain=v1 --untracked-files=all)
    if ($LASTEXITCODE -ne 0 -or $validationStatus.Count -gt 0) {
        throw "Validation clone is not clean.`n$($validationStatus -join "`n")"
    }
    foreach ($relativePath in $requiredPaths) {
        if (-not (Test-Path -LiteralPath (Join-Path $validationClone $relativePath) -PathType Leaf)) {
            throw "Required file is absent from the bundle: $relativePath"
        }
    }

    $archiveHash = (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksumPath -Value "$archiveHash  $([System.IO.Path]::GetFileName($bundlePath))" -Encoding ascii

    Write-Host "Git bundle: $bundlePath"
    Write-Host "Checksum: $checksumPath"
    Write-Host "SHA256: $archiveHash"
    Write-Host "Deployment tag: $DeploymentTag"
    Write-Host "Source: $sourceRef ($sourceCommit)"
}
finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like 'ragflow-linux-pg-*') {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
