# Manage one dedicated Chrome profile for the optional ChatGPT bridge.
# First sign-in: .\scripts\pack-browser.ps1 -Action open -Visible
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Name = $(if ($env:ANAM_CHATGPT_IDENTITY) { $env:ANAM_CHATGPT_IDENTITY } else { 'ChatGPT' }),
    [ValidateSet('open','close','status')][string]$Action = 'status',
    [switch]$Visible
)
$ErrorActionPreference = 'Stop'
$profileRoot = if ($env:ANAM_BROWSER_PROFILES_DIR) { $env:ANAM_BROWSER_PROFILES_DIR } else { Join-Path $env:LOCALAPPDATA 'Anam\BrowserProfiles' }
$profilePath = [IO.Path]::GetFullPath((Join-Path $profileRoot $Name))
$port = if ($env:ANAM_CHATGPT_CDP_PORT) { [int]$env:ANAM_CHATGPT_CDP_PORT } else { 9225 }
if ($port -lt 1024 -or $port -gt 65535) { throw 'ANAM_CHATGPT_CDP_PORT must be between 1024 and 65535.' }
function Get-ProfileProcesses {
    Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine -match ('--user-data-dir=(?:"' + [regex]::Escape($profilePath) + '"|' + [regex]::Escape($profilePath) + '(?=\s|$))')
    }
}
function Test-DebugPort {
    try { $null = Invoke-RestMethod "http://127.0.0.1:$port/json/version" -TimeoutSec 2; return $true } catch { return $false }
}
$owned = @(Get-ProfileProcesses)
if ($Action -eq 'status') { [pscustomobject]@{ Profile=$Name; Running=($owned.Count -gt 0); DebugPort=$port; Ready=(Test-DebugPort) } | ConvertTo-Json; exit 0 }
if ($Action -eq 'close') {
    foreach ($proc in $owned) { Stop-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue }
    exit 0
}
if (Test-DebugPort) {
    if ($owned.Count -eq 0) { throw "Debug port $port is already owned by another browser; choose a different ANAM_CHATGPT_CDP_PORT." }
    exit 0
}
if ($owned.Count -gt 0) { throw 'This profile is already running without its debug port. Close it before starting the bridge.' }
$chrome = $env:CHROME_EXE
if (-not $chrome) { $chrome = Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe' }
if (-not (Test-Path -LiteralPath $chrome -PathType Leaf)) { throw 'Chrome was not found. Set CHROME_EXE to your Chrome executable.' }
$null = New-Item -ItemType Directory -Path $profilePath -Force
$style = if ($Visible) { 'Normal' } else { 'Hidden' }
$chromeArgs = @("--user-data-dir=`"$profilePath`"", "--remote-debugging-port=$port", '--remote-debugging-address=127.0.0.1', '--no-first-run', '--no-default-browser-check', 'https://chatgpt.com')
Start-Process -FilePath $chrome -ArgumentList $chromeArgs -WindowStyle $style
for ($i=0; $i -lt 25; $i++) { if (Test-DebugPort) { exit 0 }; Start-Sleep -Seconds 1 }
throw 'Chrome opened but its local debug port did not become ready.'
