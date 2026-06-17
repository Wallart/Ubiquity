# Ubiquity — Windows auto-start setup (Task Scheduler)
# Run once as the user who should run the sync (no admin required).
#
# Usage:
#   .\install-windows.ps1 -Dir C:\your\folder -Peer 192.168.1.x -Port 5001

param(
    [Parameter(Mandatory)][string]$Dir,
    [Parameter(Mandatory)][string]$Peer,
    [int]$Port = 5001
)

$python  = (Get-Command python).Source
$script  = Join-Path $PSScriptRoot "..\main.py"
$script  = (Resolve-Path $script).Path
$workdir = Split-Path $script

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "`"$script`" --mode client --dir `"$Dir`" --peer $Peer --port $Port" `
    -WorkingDirectory $workdir

$trigger  = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName "UbiquitySync" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "Installed. Starting now..."
Start-ScheduledTask -TaskName "UbiquitySync"
Write-Host "Done. To remove: Unregister-ScheduledTask -TaskName UbiquitySync"
