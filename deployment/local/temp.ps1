[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._-]*$')]
    [string]$Name,
    [switch]$Complete,
    [switch]$CleanCache,
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

$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $repo '.venv\Scripts\python.exe'
$workspace = Join-Path $repo 'tools\quality\workspace.py'
function Invoke-Workspace {
    param([string]$Action, [string]$Path, [string[]]$Options = @())
    $messages = @(& $python -B $workspace $Action --root $Root --path $Path @Options 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "Workspace ${Action} failed: $($messages -join [Environment]::NewLine)" }
    if ($messages.Count) { ($messages -join [Environment]::NewLine) | ConvertFrom-Json }
}

# Reservations never sweep other tasks or wait for Docker.
if ($Name -and -not $Complete) {
    $taskPath = [IO.Path]::GetFullPath((Join-Path $Root $Name))
    if ((Split-Path -Parent $taskPath) -ne $Root) { throw 'Task must be a direct child of the temporary root.' }
    if ($PSCmdlet.ShouldProcess($taskPath, 'Reserve temporary task')) {
        Invoke-Workspace reserve $taskPath
    }
    Write-Output $taskPath
    return
}

function Test-ManagedBuild {
    param([string]$Path)
    $marker = Join-Path $Path '.ragflow-temp'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $marker -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
    return (Get-Content -LiteralPath $marker -Raw).Trim() -eq 'ragflow-disposable-v2'
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

$failures = @()
$pendingPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$completedPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$replacedPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
# Receipts live outside the tree being removed, including after partial rmtree.
# A receipt authorizes retry only for the same filesystem directory identity.
foreach ($receipt in Get-ChildItem -LiteralPath $Root -Filter '.ragflow-cleanup-*.json' -File -Force) {
    $receiptOptions = @()
    if ($Complete) {
        # Foreign or unidentifiable receipts are outside this named operation.
        if ($receipt.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        try {
            $claim = Get-Content -LiteralPath $receipt.FullName -Raw | ConvertFrom-Json
            if ($claim.original -isnot [string] -or $claim.original -ne $Name) { continue }
        } catch { continue }
        $receiptOptions = @('--expected-task', $Name)
    }
    try {
        $pending = Invoke-Workspace inspect $receipt.FullName -Options $receiptOptions
        if ($pending.matches) { $null = $pendingPaths.Add($pending.path) }
        if ($pending.active -or (Test-TaskInUse $pending.path)) { continue }
        if ($PSCmdlet.ShouldProcess($pending.path, 'Retry incomplete temporary cleanup')) {
            $options = $receiptOptions
            if ($Complete -and $pending.matches) { $options += '--manual' }
            $recovered = Invoke-Workspace recover $receipt.FullName -Options $options
            if ($recovered.completed) { $null = $completedPaths.Add($pending.path) }
            if ($recovered.replaced) { $null = $replacedPaths.Add($pending.path) }
            if ($recovered.removed) { Write-Output "Removed: $($pending.path)" }
        }
    } catch {
        $failures += "Retry $($receipt.FullName): $($_.Exception.Message)"
        Write-Warning $failures[-1]
    }
}

if ($Name) {
    $taskPath = [System.IO.Path]::GetFullPath((Join-Path $Root $Name))
    if ((Split-Path -Parent $taskPath) -ne $Root) { throw 'Task must be a direct child of the temporary root.' }
    if ($replacedPaths.Contains($taskPath)) { throw 'Task directory was replaced; preserve the new generation.' }
    if ($pendingPaths.Contains($taskPath) -and -not $completedPaths.Contains($taskPath)) {
        throw "Pending cleanup is active or failed; resolve the blocker and retry its existing receipt: $taskPath"
    }
    if ($completedPaths.Contains($taskPath)) {
        # Recovery already completed this exact directory generation.
    } elseif (Test-Path -LiteralPath $taskPath) {
        $task = Get-Item -LiteralPath $taskPath -Force
        if (-not $task.PSIsContainer -or ($task.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Task is not a regular directory: $taskPath"
        }
    } else {
        throw "Task does not exist: $taskPath"
    }
    if (-not $completedPaths.Contains($taskPath) -and $PSCmdlet.ShouldProcess($taskPath, 'Complete temporary task')) {
        if (Test-TaskInUse -Path $taskPath) { throw "Task still has an active process or Docker consumer: $taskPath" }
        # Results and backups belong outside the disposable workspace.
        try {
            Invoke-Workspace retire $taskPath -Options @('--manual')
        }
        catch { throw "Cannot complete task until protected data is saved outside it: $($_.Exception.Message)" }
    }
    if ($failures.Count) { throw ($failures -join [Environment]::NewLine) }
    # Named completion never sweeps other tasks or cleans the shared cache.
    return
}

$cutoff = [DateTime]::UtcNow.AddDays(-$KeepDays)
$candidates = @()
foreach ($directory in Get-ChildItem -LiteralPath $Root -Directory -Force) {
    if ($Name -and $directory.Name -eq $Name) { continue }
    $target = [System.IO.Path]::GetFullPath($directory.FullName)
    if ($pendingPaths.Contains($target)) { continue }
    if ((Split-Path -Parent $target) -ne $Root) { throw "Target escaped temporary root: $target" }
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
    if (Test-TaskInUse -Path $target) { continue }
    $pinPath = Join-Path $target '.keep'
    if (Test-Path -LiteralPath $pinPath) { continue }
    if (-not (Test-ManagedBuild $target)) { continue }
    try {
        $scan = Invoke-Workspace scan $target
        if ($scan.active) { continue }
        $latest = [DateTimeOffset]::FromUnixTimeMilliseconds([long]($scan.latest * 1000)).UtcDateTime
        $size = $scan.size
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
    if (Test-Path -LiteralPath $candidatePin) { continue }
    try {
        if ($PSCmdlet.ShouldProcess($candidate.Path, 'Remove abandoned build or enforce temporary workspace quota')) {
            Invoke-Workspace retire $candidate.Path
            $total -= $candidate.Size
            Write-Output "Removed: $($candidate.Path)"
        }
    } catch {
        $failures += "Cleanup $($candidate.Path): $($_.Exception.Message)"
        Write-Warning $failures[-1]
    }
}
if ($CleanCache -and $PSCmdlet.ShouldProcess('Shared frontend package cache', 'Expire idle cache and enforce quota under its lock')) {
    & (Join-Path $repo '.venv\Scripts\python.exe') -B (Join-Path $repo 'tools\quality\frontend_artifact.py') clean-cache
    if ($LASTEXITCODE -ne 0) { throw 'Shared cache is busy or could not be safely cleaned.' }
}
if ($failures.Count) { throw ($failures -join [Environment]::NewLine) }
