param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("clipboard", "desktop-control", "terminal", "filesystem", "krita", "muse-tts", "books-tools", "obsidian")]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$MachineAgentBaseUrl,

    [string]$MachineAgentApiKey = "",
    [string]$WorkerName = "",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"

$here = $PSScriptRoot
$projectRoot = Split-Path -Parent $here
$templates = Join-Path $here "templates"

if (-not $WorkerName) {
    $WorkerName = "$Server-cloud"
}

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $here "generated"
}

$outputDir = Join-Path $OutputRoot $Server
$srcDir = Join-Path $outputDir "src"

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
New-Item -ItemType Directory -Force -Path $srcDir | Out-Null

function Write-Template {
    param(
        [string]$TemplatePath,
        [string]$DestinationPath
    )

    $content = Get-Content $TemplatePath -Raw
    $content = $content.Replace("__SERVER_NAME__", $Server)
    $content = $content.Replace("__WORKER_NAME__", $WorkerName)
    $content = $content.Replace("__MACHINE_AGENT_BASE_URL__", $MachineAgentBaseUrl)
    $content = $content.Replace("__MACHINE_AGENT_API_KEY__", $MachineAgentApiKey)
    Set-Content -Path $DestinationPath -Value $content -NoNewline
}

Write-Template (Join-Path $templates "package.json") (Join-Path $outputDir "package.json")
Write-Template (Join-Path $templates "wrangler.jsonc") (Join-Path $outputDir "wrangler.jsonc")
Write-Template (Join-Path $templates "Dockerfile") (Join-Path $outputDir "Dockerfile")
Write-Template (Join-Path $templates ".dockerignore") (Join-Path $outputDir ".dockerignore")
Write-Template (Join-Path $templates "worker-index.js") (Join-Path $srcDir "index.js")

Copy-Item (Join-Path $projectRoot "requirements.txt") (Join-Path $outputDir "requirements.txt") -Force
Copy-Item (Join-Path $projectRoot "proxy_client.py") (Join-Path $outputDir "proxy_client.py") -Force
Copy-Item (Join-Path $projectRoot "server_factory.py") (Join-Path $outputDir "server_factory.py") -Force
Copy-Item (Join-Path $projectRoot "tool_specs.py") (Join-Path $outputDir "tool_specs.py") -Force
Copy-Item (Join-Path $projectRoot "run_server.py") (Join-Path $outputDir "run_server.py") -Force

Write-Host "Generated Cloudflare project at: $outputDir"
Write-Host "Worker name: $WorkerName"
Write-Host "Server: $Server"
Write-Host "Machine agent URL: $MachineAgentBaseUrl"
