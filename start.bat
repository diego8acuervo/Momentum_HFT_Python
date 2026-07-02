@echo off
REM ==========================================================================
REM start.bat - Crypto_Momentum bootstrapper (Windows / cmd.exe)
REM
REM 1. Creates the .venv virtual environment if it does not exist.
REM 2. Installs the Windows requirements into it.
REM 3. Copies .env.example -> .env on first run (fill in your credentials).
REM 4. Launches Jupyter Lab so you can run the notebooks.
REM
REM Usage:  double-click start.bat, or run it from a terminal.
REM ==========================================================================

setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [start] Creating virtual environment ^(.venv^)...
    python -m venv .venv
    if errorlevel 1 (
        echo [start] ERROR: could not create the venv. Is Python on PATH?
        exit /b 1
    )
    echo [start] Upgrading pip / setuptools / wheel...
    "%VENV_PY%" -m pip install --upgrade pip setuptools wheel
    echo [start] Installing requirements ^(this can take a few minutes^)...
    "%VENV_PY%" -m pip install -r requirements-windows.txt
) else (
    echo [start] Virtual environment already present.
)

if exist ".env.example" if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [start] Created .env from .env.example - fill in your API keys for live/paper trading.
)

echo [start] Launching Jupyter Lab...
"%VENV_PY%" -m jupyter lab --notebook-dir "%~dp0"

endlocal
