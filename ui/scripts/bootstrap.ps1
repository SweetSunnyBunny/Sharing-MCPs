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

# The only tracked env template is .env.paths.example (machine path overrides).
# Secrets have no template: config.py documents every variable in .env.
if (-not (Test-Path ".env.paths.local") -and (Test-Path ".env.paths.example")) {
    Copy-Item ".env.paths.example" ".env.paths.local"
    Write-Host "Created .env.paths.local from .env.paths.example; edit the paths for this machine."
}
if (-not (Test-Path ".env")) {
    New-Item -ItemType File ".env" | Out-Null
    Write-Host "Created an empty .env; fill in keys by hand (config.py documents every variable)."
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
