param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("clipboard", "desktop-control", "terminal", "filesystem", "krita", "muse-tts", "books-tools", "obsidian")]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$MachineAgentUrl,

    [string]$MachineAgentKey = "",
    [switch]$SetAuthToken,
    [switch]$DeployAll
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

$workerName = "$Server-mcp"

# Generate wrangler.jsonc for this server
$config = @"
{
  "name": "$workerName",
  "main": "mcp-worker.js",
  "compatibility_date": "2025-01-01",
  "vars": {
    "SERVER_NAME": "$Server",
    "MACHINE_AGENT_URL": "$MachineAgentUrl"$(if ($MachineAgentKey) { ",`n    `"MACHINE_AGENT_KEY`": `"$MachineAgentKey`"" })
  }
}
"@

$configPath = Join-Path $here "wrangler.jsonc"
$config | Set-Content -Path $configPath -Encoding utf8

Push-Location $here
try {
    npx wrangler deploy --config $configPath
    if ($SetAuthToken) {
        Write-Host ""
        Write-Host "Set the MCP auth token for the Worker."
        Write-Host "Enter a strong secret - Claude will send this as a Bearer token:"
        npx wrangler secret put MCP_AUTH_TOKEN --name $workerName
    }
    Write-Host ""
    Write-Host "Deployed! MCP endpoint: https://$workerName.<your-subdomain>.workers.dev/mcp"
}
finally {
    Pop-Location
}
