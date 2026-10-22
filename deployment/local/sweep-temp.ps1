[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$reportDirectory = Join-Path $repo '.local\maintenance\temp-cleanup'
$report = @{checked_at = [DateTime]::UtcNow.ToString('o'); succeeded = $false}
try {
    $messages = @(& (Join-Path $PSScriptRoot 'temp.ps1') -CleanCache)
    $report.succeeded = $true
    $report.removed = @($messages | Where-Object { $_ -is [string] -and $_.StartsWith('Removed: ') } | Select-Object -First 50)
} catch {
    $report.error = $_.Exception.Message
} finally {
    # One atomically replaced report, without accumulating per-run log folders.
    foreach ($path in @($repo, (Join-Path $repo '.local'), (Join-Path $repo '.local\maintenance'), $reportDirectory)) {
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Linked report directory: $path" }
    }
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    $temporaryReport = Join-Path $reportDirectory 'last-run.tmp'
    foreach ($name in @('last-run.tmp','last-run.json')) {
        $path = Join-Path $reportDirectory $name
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Linked report: $path" }
    }
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryReport -Encoding utf8
    [IO.File]::Move($temporaryReport, (Join-Path $reportDirectory 'last-run.json'), $true)
}
if (-not $report.succeeded) { Write-Error $report.error; exit 1 }
