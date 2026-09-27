param(
    [string]$TaskName = "MachineAgentLocalBridge",
    [string]$PythonExe = "python",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8811
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root "local_machine_agent.py"
$arguments = "`"$scriptPath`" --host $BindHost --port $Port"

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Runs the local machine-agent bridge at login." `
    -Force | Out-Null

Write-Host "Installed startup task '$TaskName'."
Write-Host "It will run: $PythonExe $arguments"
