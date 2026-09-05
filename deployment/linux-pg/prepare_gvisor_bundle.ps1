param(
    [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = 'Stop'
$release = '20260817.0'
$baseUrl = "https://storage.googleapis.com/gvisor/releases/release/$release/x86_64"
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ragflow-gvisor-' + [guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $tempRoot 'gvisor.tar.bz2'
$checksumPath = $archivePath + '.sha512'

try {
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null
    & curl.exe -fsSL --retry 3 --max-time 300 -o $archivePath "$baseUrl/gvisor.tar.bz2"
    if ($LASTEXITCODE -ne 0) { throw 'Failed to download the pinned gVisor bundle.' }
    & curl.exe -fsSL --retry 3 --max-time 60 -o $checksumPath "$baseUrl/gvisor.tar.bz2.sha512"
    if ($LASTEXITCODE -ne 0) { throw 'Failed to download the pinned gVisor checksum.' }
    $expected = ((Get-Content -LiteralPath $checksumPath -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
    $actual = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA512).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw 'Pinned gVisor bundle checksum mismatch.' }
    & tar -xjf $archivePath -C $destinationPath
    if ($LASTEXITCODE -ne 0) { throw 'Failed to extract the pinned gVisor bundle.' }
    foreach ($relativePath in @('runsc', 'containerd-shim-runsc-v1', 'gvisor-bin/checkpointgofer', 'gvisor-bin/gvisor_sentry')) {
        if (-not (Test-Path -LiteralPath (Join-Path $destinationPath $relativePath) -PathType Leaf)) {
            throw "gVisor bundle is missing $relativePath"
        }
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $destinationPath 'GVISOR-BUNDLE.env'),
        "GVISOR_RELEASE=$release`nGVISOR_ARCHIVE_SHA512=$actual`n",
        [System.Text.UTF8Encoding]::new($false)
    )
}
finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    $tempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedTemp.StartsWith($tempBase, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTemp) -like 'ragflow-gvisor-*') {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
