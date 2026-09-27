param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("clipboard", "desktop-control", "terminal", "filesystem", "krita", "muse-tts", "books-tools", "obsidian")]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$MachineAgentBaseUrl,

    [string]$MachineAgentApiKey = "",
    [string]$WorkerName = "",
    [switch]$SetAuthToken
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

& (Join-Path $here "render-cloudflare-project.ps1") `
    -Server $Server `
    -MachineAgentBaseUrl $MachineAgentBaseUrl `
    -MachineAgentApiKey $MachineAgentApiKey `
    -WorkerName $WorkerName

$target = Join-Path (Join-Path $here "generated") $Server
Push-Location $target

try {
    npm install
    npx wrangler deploy

    if ($SetAuthToken) {
        if (-not $WorkerName) { $WorkerName = "$Server-cloud" }
        Write-Host ""
        Write-Host "Now set the MCP auth token secret for the Worker."
        Write-Host "You will be prompted to enter the token value:"
        npx wrangler secret put MCP_AUTH_TOKEN
    }
}
finally {
    Pop-Location
}
