param(
    [switch]$ForceApiMode
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

$python = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Virtualenv python not found at $python"
}

& $python -m pip install --upgrade pip
& $python -m pip install --no-build-isolation -e ".[dev]"

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
}

if (-not $ForceApiMode) {
    $env:ANAM_USE_DIRECT_API = "false"
}

& $python -m pytest -q

Write-Host ""
Write-Host "Bootstrap complete."
if ($ForceApiMode) {
    Write-Host "Runtime preference left unchanged for direct API testing."
} else {
    Write-Host "Claude Code CLI remains the default runtime (ANAM_USE_DIRECT_API=false for this bootstrap run)."
}
