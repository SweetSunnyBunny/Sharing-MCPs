$ErrorActionPreference = "Stop"

$token = Read-Host -Prompt "Enter your MCP auth token (same token for all Workers)"
if (-not $token) { throw "Token cannot be empty" }

$servers = @(
    "clipboard-mcp",
    "desktop-control-mcp",
    "terminal-mcp",
    "filesystem-mcp",
    "krita-mcp",
    "muse-tts-mcp",
    "books-tools-mcp",
    "obsidian-mcp"
)

foreach ($name in $servers) {
    Write-Host "Setting MCP_AUTH_TOKEN on $name..."
    $token | npx wrangler secret put MCP_AUTH_TOKEN --name $name 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Done." -ForegroundColor Green
    } else {
        Write-Host "  Failed!" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "All done! Add this header when registering MCP servers with Claude:" -ForegroundColor Cyan
Write-Host "  -H `"Authorization: Bearer $token`""
