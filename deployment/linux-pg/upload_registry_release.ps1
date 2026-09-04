param(
    [Parameter(Mandatory = $true)][string]$PackagePath,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9._-]+$')][string]$HostName,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9._-]+$')][string]$UserName,
    [Parameter(Mandatory = $true)][string]$PrivateKeyPath,
    [ValidateRange(1, 65535)][int]$Port = 22,
    [ValidatePattern('^/[A-Za-z0-9._/-]+$')][string]$RemoteDirectory = '/tmp/ragflow-registry-release',
    [string]$PuttyDirectory = 'C:\Program Files\PuTTY',
    [string]$SavedSession,
    [string]$HostKey
)

$ErrorActionPreference = 'Stop'
$resolvedPackage = [System.IO.Path]::GetFullPath($PackagePath)
$resolvedChecksum = $resolvedPackage + '.sha256'
$resolvedKey = [System.IO.Path]::GetFullPath($PrivateKeyPath)
$pscp = Join-Path $PuttyDirectory 'pscp.exe'
$plink = Join-Path $PuttyDirectory 'plink.exe'

foreach ($requiredFile in @($resolvedPackage, $resolvedChecksum, $resolvedKey, $pscp, $plink)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}
if ([System.IO.Path]::GetExtension($resolvedKey) -ne '.ppk') {
    throw 'PrivateKeyPath must point to a PuTTY .ppk key.'
}

$checksumLine = (Get-Content -LiteralPath $resolvedChecksum -TotalCount 1).Trim()
$checksumParts = $checksumLine -split '\s+', 2
if ($checksumParts.Count -ne 2 -or $checksumParts[0] -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Invalid checksum file: $resolvedChecksum"
}
$actualHash = (Get-FileHash -LiteralPath $resolvedPackage -Algorithm SHA256).Hash
if ($actualHash -ne $checksumParts[0]) {
    throw 'Package SHA256 does not match its checksum file.'
}

$commonArgs = @('-batch', '-P', $Port.ToString(), '-i', $resolvedKey)
if ($SavedSession) { $commonArgs += @('-load', $SavedSession) }
if ($HostKey) { $commonArgs += @('-hostkey', $HostKey) }
$destination = "${UserName}@${HostName}"

& $plink @commonArgs $destination "mkdir -p -- '$RemoteDirectory'"
if ($LASTEXITCODE -ne 0) { throw 'Failed to create the remote release directory.' }

& $pscp @commonArgs $resolvedPackage $resolvedChecksum "${destination}:$RemoteDirectory/"
if ($LASTEXITCODE -ne 0) { throw 'Package upload failed.' }

$packageName = [System.IO.Path]::GetFileName($resolvedPackage)
Write-Host "Uploaded: ${destination}:$RemoteDirectory/$packageName"
Write-Host "Server command: cd '$RemoteDirectory' && sha256sum -c '$packageName.sha256' && tar -xzf '$packageName'"
