# =============================================================================
# start.ps1 — Crypto_Momentum bootstrapper (Windows / PowerShell)
#
# 1. Creates the .venv virtual environment if it does not exist.
# 2. Installs the Windows requirements into it.
# 3. Copies .env.example -> .env on first run (fill in your credentials).
# 4. Launches Jupyter Lab in the notebooks folder so you can run the notebooks.
#
# Usage:  Right-click -> "Run with PowerShell", or from a terminal:
#            powershell -ExecutionPolicy Bypass -File .\start.ps1
# =============================================================================

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'

# Resolve a base Python interpreter to build the venv with.
function Get-BasePython {
    foreach ($cmd in @('python', 'py')) {
        $p = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($p) { return $p.Source }
    }
    throw 'Python was not found on PATH. Install Python 3.11+ and try again.'
}

# 1 + 2. Create the venv and install dependencies if needed.
if (-not (Test-Path $venvPython)) {
    Write-Host '[start] Creating virtual environment (.venv)...' -ForegroundColor Cyan
    $basePython = Get-BasePython
    & $basePython -m venv .venv
    Write-Host '[start] Upgrading pip / setuptools / wheel...' -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip setuptools wheel
    Write-Host '[start] Installing requirements (this can take a few minutes)...' -ForegroundColor Cyan
    & $venvPython -m pip install -r requirements-windows.txt
} else {
    Write-Host '[start] Virtual environment already present.' -ForegroundColor Green
}

# 3. First-run .env scaffold.
if ((Test-Path '.env.example') -and (-not (Test-Path '.env'))) {
    Copy-Item '.env.example' '.env'
    Write-Host '[start] Created .env from .env.example — fill in your API keys for live/paper trading.' -ForegroundColor Yellow
}

# 4. Launch Jupyter Lab.
Write-Host '[start] Launching Jupyter Lab...' -ForegroundColor Cyan
& $venvPython -m jupyter lab --notebook-dir "$root"
