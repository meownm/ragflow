[CmdletBinding()]
param(
    [string]$RegistryHost = "192.168.1.175",
    [ValidateRange(1, 65535)]
    [int]$RegistryPort = 5443,
    [string]$SshUser = "apt",
    [string]$IdentityFile = "$HOME\.ssh\ragflow_nuc8_ed25519"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$certificateDirectory = Join-Path $HOME ".docker\ragflow-registry"
$certificatePath = Join-Path $certificateDirectory "${RegistryHost}-${RegistryPort}-ca.crt"
New-Item -ItemType Directory -Force -Path $certificateDirectory | Out-Null

& scp -i $IdentityFile -o IdentitiesOnly=yes `
    "${SshUser}@${RegistryHost}:/opt/ragflow-registry/certs/registry.crt" `
    $certificatePath
if ($LASTEXITCODE -ne 0) {
    throw "Could not download the candidate registry CA certificate."
}

$certificate = Import-Certificate -FilePath $certificatePath -CertStoreLocation Cert:\CurrentUser\Root
if (-not $certificate) {
    throw "Could not import the candidate registry CA certificate."
}

Write-Host "Trusted candidate registry CA: $($certificate.Thumbprint)"
Write-Host "Restart Docker Desktop once so its Linux VM imports the updated Windows trust store."
