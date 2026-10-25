[CmdletBinding(SupportsShouldProcess)]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskName = 'RAGFlow Temporary Cleanup'
$runner = Join-Path $PSScriptRoot 'sweep-temp.ps1'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$pwsh = (Get-Command pwsh -ErrorAction Stop).Source
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$runner`""
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays(7) -RepetitionInterval (New-TimeSpan -Days 7)
# S4U uses the same account without storing a password or requiring an interactive session.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    $existingUser = $existing.Principal.UserId
    $existingSid = if ($existingUser -match '^S-\d-') { $existingUser } else { ([Security.Principal.NTAccount]$existingUser).Translate([Security.Principal.SecurityIdentifier]).Value }
    if (@($existing.Actions).Count -ne 1 -or $existing.Actions.Execute -ne $pwsh -or $existing.Actions.Arguments -ne $arguments -or $existingSid -ne [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) {
        throw 'An unrelated scheduled task already uses the cleanup task name.'
    }
}
if ($PSCmdlet.ShouldProcess($taskName, 'Install weekly cleanup for this checkout under the current user with S4U logon')) {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this installer from an elevated PowerShell to register S4U logon. The cleanup itself runs as the current user with Limited privileges.'
    }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Reclaim abandoned managed RAGFlow builds and idle shared package cache; preserve task pins, leases, backups and Docker consumers.' -Force | Out-Null
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
}
