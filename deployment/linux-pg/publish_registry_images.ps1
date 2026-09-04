param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._:-]+(/[A-Za-z0-9._-]+)*$')]
    [string]$RegistryPrefix,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z._-]*$')]
    [string]$ReleaseVersion,
    [string]$OutputPath = (Join-Path $PSScriptRoot "registry-images-$ReleaseVersion.env"),
    [switch]$Push
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$projectVersionMatch = [regex]::Match(
    (Get-Content -LiteralPath (Join-Path $sourceRoot 'pyproject.toml') -Raw),
    '(?m)^version\s*=\s*"([^"]+)"'
)
if (-not $projectVersionMatch.Success -or $projectVersionMatch.Groups[1].Value -ne $ReleaseVersion.TrimStart('v')) {
    throw "ReleaseVersion $ReleaseVersion does not match pyproject.toml version."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI is required.'
}

$images = @(
    [pscustomobject]@{ Key = 'POSTGRES_IMAGE'; Source = 'postgres:16-alpine'; Target = "$RegistryPrefix/library/postgres:16-alpine" },
    [pscustomobject]@{ Key = 'RAGFLOW_IMAGE'; Source = 'infiniflow/ragflow:v0.26.4'; Target = "$RegistryPrefix/infiniflow/ragflow:v0.26.4" },
    [pscustomobject]@{ Key = 'VALKEY_IMAGE'; Source = 'valkey/valkey:8'; Target = "$RegistryPrefix/valkey/valkey:8" },
    [pscustomobject]@{ Key = 'ELASTICSEARCH_IMAGE'; Source = 'elasticsearch:8.11.3'; Target = "$RegistryPrefix/library/elasticsearch:8.11.3" },
    [pscustomobject]@{ Key = 'PLANTUML_IMAGE'; Source = 'plantuml/plantuml-server:jetty-v1.2026.6'; Target = "$RegistryPrefix/plantuml/plantuml-server:jetty-v1.2026.6" },
    [pscustomobject]@{ Key = 'MINIO_IMAGE'; Source = 'pgsty/minio:RELEASE.2026-03-25T00-00-00Z'; Target = "$RegistryPrefix/pgsty/minio:RELEASE.2026-03-25T00-00-00Z" }
)

foreach ($image in $images) {
    & docker image inspect $image.Source *> $null
    if ($LASTEXITCODE -ne 0) {
        & docker pull --platform linux/amd64 $image.Source
        if ($LASTEXITCODE -ne 0) { throw "Failed to pull $($image.Source)" }
    }
    $platform = (& docker image inspect $image.Source --format '{{.Os}}/{{.Architecture}}').Trim()
    if ($LASTEXITCODE -ne 0 -or $platform -ne 'linux/amd64') {
        throw "Expected linux/amd64 image, found ${platform}: $($image.Source)"
    }
    & docker tag $image.Source $image.Target
    if ($LASTEXITCODE -ne 0) { throw "Failed to tag $($image.Source)" }
}

if ($Push) {
    foreach ($target in @($images.Target)) {
        & docker push $target
        if ($LASTEXITCODE -ne 0) { throw "Failed to push $target" }
    }
}

$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Path (Split-Path -Parent $outputFullPath) -Force | Out-Null
$lines = @($images | ForEach-Object { "$($_.Key)=$($_.Target)" })
[System.IO.File]::WriteAllText($outputFullPath, (($lines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))

Write-Host "Images environment: $outputFullPath"
if ($Push) {
    Write-Host 'All six images were pushed.'
}
else {
    Write-Host 'Images were prepared locally only. Re-run with -Push after docker login.'
}
