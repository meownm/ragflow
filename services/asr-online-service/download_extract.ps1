param(
  [Parameter(Mandatory = $true)][string]$Url,
  [Parameter(Mandatory = $true)][string]$OutZip,
  [Parameter(Mandatory = $true)][string]$TmpDir,
  [int]$MinBytes = 1048576
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-Zip([string]$Path) {
  Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
  try {
    $z = [System.IO.Compression.ZipFile]::OpenRead($Path)
    $z.Dispose()
    return $true
  }
  catch {
    return $false
  }
}
function Download-Once([string]$U, [string]$O) {
  if (Test-Path $O) {
    Remove-Item -Force $O
  }
  Invoke-WebRequest -Uri $U -OutFile $O -MaximumRedirection 10 -Headers @{ "User-Agent" = "Mozilla/5.0" }
}

try {
  Write-Host "download_extract start url=$Url out=$OutZip tmp=$TmpDir"

  $outDir = Split-Path -Parent $OutZip
  if ($outDir -and !(Test-Path $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
  }

  for ($i = 1; $i -le 3; $i++) {
    try {
      Write-Host "attempt=$i downloading"
      Download-Once $Url $OutZip

      if (!(Test-Path $OutZip)) { throw "Downloaded file missing" }

      $len = (Get-Item $OutZip).Length
      if ($len -lt $MinBytes) {
        $bytes = [IO.File]::ReadAllBytes($OutZip)
        $take = [Math]::Min(511, $bytes.Length - 1)
        if ($take -ge 0) {
          $head = [Text.Encoding]::UTF8.GetString($bytes[0..$take])
        }
        else {
          $head = ""
        }
        throw ("Downloaded file too small or HTML. Size=" + $len + " Head=" + $head.Replace("`r", " ").Replace("`n", " "))
      }

      if (!(Test-Zip $OutZip)) {
        throw "Invalid zip (central directory not found)"
      }

      if (Test-Path $TmpDir) {
        Remove-Item -Recurse -Force $TmpDir
      }
      New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null

      Expand-Archive -Path $OutZip -DestinationPath $TmpDir -Force
      Write-Host "download_extract done"
      exit 0
    }
    catch {
      Write-Host "attempt=$i failed error=$($_.Exception.Message)"
      if ($i -eq 3) {
        throw
      }
      Start-Sleep -Seconds 2
    }
  }

  Write-Error "Unexpected flow in download_extract"
  exit 40
}
catch {
  Write-Error ("download_extract failed: " + $_.Exception.Message)
  if ($_ -and $_.InvocationInfo -and $_.InvocationInfo.PositionMessage) {
    Write-Error $_.InvocationInfo.PositionMessage
  }
  exit 30
}
