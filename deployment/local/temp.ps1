[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._-]*$')]
    [string]$Name,
    [switch]$Complete,
    [ValidateRange(1, 365)]
    [int]$KeepDays = 2,
    [ValidateRange(1, 1024)]
    [int]$MaxGiB = 10,
    [string]$Root = (Join-Path ([System.IO.Path]::GetPathRoot($PSScriptRoot)) 'tmp\ragflow')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
if ((Split-Path -Leaf $Root) -ne 'ragflow' -or (Split-Path -Leaf (Split-Path -Parent $Root)) -ne 'tmp') {
    throw 'Temporary root must end in tmp\ragflow.'
}
if ($Complete -and -not $Name) { throw '-Complete requires -Name.' }

# Check every ancestor before creating, enumerating or removing anything.
$ancestor = $Root
while ($ancestor) {
    if (Test-Path -LiteralPath $ancestor) {
        $entry = Get-Item -LiteralPath $ancestor -Force
        if (-not $entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Temporary root has a non-directory or linked ancestor: $ancestor"
        }
    }
    $ancestor = Split-Path -Parent $ancestor
}
if (-not (Test-Path -LiteralPath $Root)) {
    if ($WhatIfPreference) { return }
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
}

function Get-TemporaryTree {
    param([System.IO.DirectoryInfo]$Directory)
    # Do not traverse junctions, symlinks, repositories or protected data.
    foreach ($item in Get-ChildItem -LiteralPath $Directory.FullName -Force) {
        $item
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            ($item.Name -eq '.keep' -and $item.LastWriteTimeUtc -ge [DateTime]::UtcNow.AddDays(-$KeepDays)) -or
            $item.Name -in @('.git', 'backup', 'backups') -or
            $item.Name -match '\.(dump|bak)(\.(gz|zip|zst))?$|\.sql\.(gz|zip|zst)$' -or
            $item.Name -match '(backup|database|postgres|mysql).*\.sql$') {
            throw "Protected entry: $($item.FullName)"
        }
        if ($item.PSIsContainer) { Get-TemporaryTree -Directory $item }
    }
}

$activePaths = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine
} | ForEach-Object { $_.CommandLine.Replace('/', '\') })
$bindPaths = @()
if (Get-Command docker -ErrorAction SilentlyContinue) {
    $containerIds = @(& docker ps -aq)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify Docker consumers before cleanup.' }
    if ($containerIds.Count) {
        $mounts = @(& docker inspect --format '{{range .Mounts}}{{if eq .Type "bind"}}{{println .Source}}{{end}}{{end}}' @containerIds)
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Docker bind mounts before cleanup.' }
        $bindPaths = @($mounts | Where-Object { $_ } | ForEach-Object {
            ($_ -replace '^/run/desktop/mnt/host/([a-z])/', '$1:/').Replace('/', '\').TrimEnd('\')
        })
    }
}

function Test-TaskInUse {
    param([string]$Path)
    foreach ($command in $activePaths) {
        if ($command.IndexOf($Path, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    foreach ($bind in $bindPaths) {
        if ($bind.Equals($Path, [StringComparison]::OrdinalIgnoreCase) -or
            $bind.StartsWith($Path + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $Path.StartsWith($bind + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

if ($Name) {
    $taskPath = [System.IO.Path]::GetFullPath((Join-Path $Root $Name))
    if ((Split-Path -Parent $taskPath) -ne $Root) { throw 'Task must be a direct child of the temporary root.' }
    if (Test-Path -LiteralPath $taskPath) {
        $task = Get-Item -LiteralPath $taskPath -Force
        if (-not $task.PSIsContainer -or ($task.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Task is not a regular directory: $taskPath"
        }
    } elseif ($Complete) {
        throw "Task does not exist: $taskPath"
    }
    if ($PSCmdlet.ShouldProcess($taskPath, $(if ($Complete) { 'Complete temporary task' } else { 'Reserve temporary task' }))) {
        if (-not (Test-Path -LiteralPath $taskPath)) { New-Item -ItemType Directory -Path $taskPath | Out-Null }
        $pin = Join-Path $taskPath '.keep'
        if (Test-Path -LiteralPath $pin) {
            $pinItem = Get-Item -LiteralPath $pin -Force
            if ($pinItem.PSIsContainer -or ($pinItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Task pin is not a regular file: $pin"
            }
        }
        if ($Complete) {
            if (Test-TaskInUse -Path $taskPath) { throw "Task still has an active process or Docker consumer: $taskPath" }
            if (Test-Path -LiteralPath $pin) { Remove-Item -LiteralPath $pin -Force }
            # Results and backups belong outside the disposable workspace.
            try { @(Get-TemporaryTree -Directory (Get-Item -LiteralPath $taskPath)) | Out-Null }
            catch { throw "Cannot complete task until protected data is saved outside it: $($_.Exception.Message)" }
            Remove-Item -LiteralPath $taskPath -Recurse -Force
        } else {
            Set-Content -LiteralPath $pin -Value 'Active task. Save results outside this workspace, then complete it for immediate removal.' -Encoding utf8
        }
    }
}

$cutoff = [DateTime]::UtcNow.AddDays(-$KeepDays)
$candidates = @()
foreach ($directory in Get-ChildItem -LiteralPath $Root -Directory -Force) {
    if ($Name -and $directory.Name -eq $Name) { continue }
    $target = [System.IO.Path]::GetFullPath($directory.FullName)
    if ((Split-Path -Parent $target) -ne $Root) { throw "Target escaped temporary root: $target" }
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
    if (Test-TaskInUse -Path $target) { continue }
    $pinPath = Join-Path $target '.keep'
    if ((Test-Path -LiteralPath $pinPath) -and (Get-Item -LiteralPath $pinPath -Force).LastWriteTimeUtc -ge $cutoff) { continue }
    try {
        $entries = @(Get-TemporaryTree -Directory $directory)
        $latest = $directory.LastWriteTimeUtc
        $size = 0L
        foreach ($entry in $entries) {
            if ($entry.LastWriteTimeUtc -gt $latest) { $latest = $entry.LastWriteTimeUtc }
            if (-not $entry.PSIsContainer) { $size += $entry.Length }
        }
    } catch {
        Write-Warning "Keeping ${target}: $($_.Exception.Message)"
        continue
    }
    $candidates += [pscustomobject]@{Path=$target; Latest=$latest; Size=$size}
}
$total = 0L
foreach ($candidate in $candidates) { $total += $candidate.Size }
foreach ($candidate in ($candidates | Sort-Object Latest)) {
    if ($candidate.Latest -ge $cutoff -and $total -le ($MaxGiB * 1GB)) { continue }
    # A task may have been reserved while its contents were inspected.
    $candidatePin = Join-Path $candidate.Path '.keep'
    if ((Test-Path -LiteralPath $candidatePin) -and (Get-Item -LiteralPath $candidatePin -Force).LastWriteTimeUtc -ge $cutoff) { continue }
    if ($PSCmdlet.ShouldProcess($candidate.Path, 'Remove expired task or enforce temporary workspace quota')) {
        Remove-Item -LiteralPath $candidate.Path -Recurse -Force
        $total -= $candidate.Size
        Write-Host "Removed: $($candidate.Path)"
    }
}
if ($Name -and -not $Complete) { Write-Output $taskPath }
