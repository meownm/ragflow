#requires -Version 7.0
<#
.SYNOPSIS
Installs pinned Windows x64 security CLIs after checking archive SHA-256.
#>
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE '.local/bin'),
    [Parameter(Mandatory)]
    [string]$WorkDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'This installer supports Windows x64. See README.md for other platforms.'
}

# Official GitHub release archives; update version and digest together.
$releases = @(
    @{ Name = 'trivy'; Version = '0.76.1'; Repo = 'aquasecurity/trivy'; Asset = 'trivy_0.76.1_windows-64bit.zip'; Sha256 = '1051f49cb22d5284f43425f72aad9cda63ca12ceb92e7caa06903490c82dc9bf' },
    @{ Name = 'syft'; Version = '1.55.0'; Repo = 'anchore/syft'; Asset = 'syft_1.55.0_windows_amd64.zip'; Sha256 = 'ada4c9b93af575f479ce80fce6629897272a2d41f300a14bc49bace020767ca2' },
    @{ Name = 'gitleaks'; Version = '8.30.1'; Repo = 'gitleaks/gitleaks'; Asset = 'gitleaks_8.30.1_windows_x64.zip'; Sha256 = 'd29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e' },
    @{ Name = 'grype'; Version = '0.121.0'; Repo = 'anchore/grype'; Asset = 'grype_0.121.0_windows_amd64.zip'; Sha256 = 'f867bbdc14bd8b0d99b3fc47d839af07480a540072b16896373a8ba32b76f588' }
)

$Destination = [IO.Path]::GetFullPath($Destination)
$WorkDirectory = [IO.Path]::GetFullPath($WorkDirectory)
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
New-Item -ItemType Directory -Path $WorkDirectory -Force | Out-Null
$installDirectory = Join-Path $WorkDirectory ('security-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $installDirectory | Out-Null
$receipt = @()
foreach ($release in $releases) {
    $url = "https://github.com/$($release.Repo)/releases/download/v$($release.Version)/$($release.Asset)"
    $archive = Join-Path $installDirectory $release.Asset
    Write-Host "Downloading $($release.Name) $($release.Version)"
    Invoke-WebRequest -Uri $url -OutFile $archive -TimeoutSec 300
    $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $release.Sha256) {
        throw "SHA-256 mismatch for $($release.Asset); executable was not installed."
    }
    $extracted = Join-Path $installDirectory $release.Name
    Expand-Archive -LiteralPath $archive -DestinationPath $extracted
    $binary = Join-Path $extracted "$($release.Name).exe"
    if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
        throw "Missing executable in $($release.Asset)."
    }
    # Execute only a verified archive's binary before replacing an installed tool.
    [string[]]$versionArguments = if ($release.Name -eq 'trivy') { @('--version') } else { @('version') }
    $versionOutput = @(& $binary @versionArguments 2>&1)
    if ($LASTEXITCODE -ne 0 -or ($versionOutput -join "`n") -notmatch [regex]::Escape($release.Version)) {
        throw "Version check failed for $($release.Name): $($versionOutput -join [Environment]::NewLine)"
    }
    $installed = Join-Path $Destination "$($release.Name).exe"
    Copy-Item -LiteralPath $binary -Destination $installed -Force
    $receipt += [ordered]@{
        name = $release.Name
        version = $release.Version
        url = $url
        archive_sha256 = $actualHash
        executable_sha256 = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash.ToLowerInvariant()
        executable = $installed
    }
    Write-Host "Installed $installed"
}
$receiptPath = Join-Path $WorkDirectory 'security-tools-install.json'
$receipt | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $receiptPath -Encoding utf8
Write-Host "Receipt: $receiptPath"
Write-Host "Add $Destination to your user PATH if it is not already present."
