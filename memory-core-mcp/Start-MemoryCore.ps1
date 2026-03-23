param(
    [ValidateSet("help", "status", "install", "server", "unified", "daemon")]
    [string]$Mode = "help",
    [string]$Python = "python",
    [string]$BindHost = "127.0.0.1",
    [int]$DaemonPort = 8766
)

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host "  $("-" * $Title.Length)" -ForegroundColor DarkCyan
}

function Test-Python {
    & $Python --version *> $null
    return $LASTEXITCODE -eq 0
}

function Show-Help {
    Write-Section "Memory Core Launcher"
    Write-Host "  Modes:"
    Write-Host "    help      Show this screen"
    Write-Host "    status    Check Python, scripts, and the database file"
    Write-Host "    install   Install Python dependencies"
    Write-Host "    server    Run memory_core_server.py"
    Write-Host "    unified   Run unified_memory_server.py"
    Write-Host "    daemon    Run memory_core_daemon.py"
    Write-Host ""
    Write-Host "  Examples:"
    Write-Host "    .\Start-MemoryCore.ps1 -Mode install"
    Write-Host "    .\Start-MemoryCore.ps1 -Mode unified"
    Write-Host "    .\Start-MemoryCore.ps1 -Mode daemon -BindHost 127.0.0.1 -DaemonPort 8766"
}

Set-Location $RepoRoot

if (-not (Test-Python)) {
    Write-Error "Python was not found with '$Python'. Pass -Python with an explicit path if needed."
    exit 1
}

switch ($Mode) {
    "help" {
        Show-Help
    }
    "status" {
        Write-Section "Environment Status"
        $pythonVersion = & $Python --version 2>&1
        Write-Host "  Python:   $pythonVersion"
        Write-Host "  Root:     $RepoRoot"
        Write-Host "  Database: $(Test-Path (Join-Path $RepoRoot 'memory.db'))"
        Write-Host "  Server:   $(Test-Path (Join-Path $RepoRoot 'memory_core_server.py'))"
        Write-Host "  Unified:  $(Test-Path (Join-Path $RepoRoot 'unified_memory_server.py'))"
        Write-Host "  Daemon:   $(Test-Path (Join-Path $RepoRoot 'memory_core_daemon.py'))"
    }
    "install" {
        Write-Section "Installing Dependencies"
        & $Python -m pip install -r (Join-Path $RepoRoot "requirements.txt")
        exit $LASTEXITCODE
    }
    "server" {
        Write-Section "Starting Memory Core Server"
        & $Python (Join-Path $RepoRoot "memory_core_server.py")
        exit $LASTEXITCODE
    }
    "unified" {
        Write-Section "Starting Unified Memory Server"
        & $Python (Join-Path $RepoRoot "unified_memory_server.py")
        exit $LASTEXITCODE
    }
    "daemon" {
        Write-Section "Starting Memory Core Daemon"
        & $Python (Join-Path $RepoRoot "memory_core_daemon.py") --host $BindHost --port $DaemonPort
        exit $LASTEXITCODE
    }
}
