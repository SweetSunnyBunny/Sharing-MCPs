param(
    [string]$TaskName = "MachineAgentLocalBridge"
)

$ErrorActionPreference = "Stop"

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed startup task '$TaskName'."
