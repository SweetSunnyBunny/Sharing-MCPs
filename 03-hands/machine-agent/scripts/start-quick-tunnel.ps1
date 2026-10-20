param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8811,
    [string]$CloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $CloudflaredExe)) {
    throw "cloudflared not found at '$CloudflaredExe'"
}

$url = "http://${BindHost}:$Port"
Write-Host "Starting Cloudflare quick tunnel to $url"
& $CloudflaredExe tunnel --url $url
