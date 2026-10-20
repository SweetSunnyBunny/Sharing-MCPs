param(
    [Parameter(Mandatory = $true)]
    [string]$TunnelName,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8811,
    [string]$CloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    [string]$Hostname = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $CloudflaredExe)) {
    throw "cloudflared not found at '$CloudflaredExe'"
}

$url = "http://${BindHost}:$Port"

Write-Host "If this machine has not logged into Cloudflare Tunnel yet, run:"
Write-Host "  cloudflared tunnel login"
Write-Host ""
Write-Host "Target origin: $url"
Write-Host "Tunnel name: $TunnelName"

if ($Hostname) {
    Write-Host "Routing hostname $Hostname to tunnel $TunnelName"
    & $CloudflaredExe tunnel route dns $TunnelName $Hostname
}

Write-Host "Running named tunnel $TunnelName -> $url"
& $CloudflaredExe tunnel run --url $url $TunnelName
