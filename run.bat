@echo off
title Launching DedupeFlow SaaS
echo ==========================================================
echo           DEDUPEFLOW SAAS AUTO-INSTALLER & RUNNER
echo ==========================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check the box "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Create virtual environment if it doesn't exist
if not exist .venv (
    echo [INFO] Creating Python virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate virtual environment and install dependencies
echo [INFO] Activating virtual environment...
call .venv\Scripts\activate

echo [INFO] Installing required packages...
python -m pip install --upgrade pip
pip install -r backend/requirements.txt

:: Check if app.py exists
if not exist app.py (
    echo [ERROR] app.py not found in the current directory!
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo           STARTING SAAS SERVER ON PORT 8000
echo ==========================================================
echo.
echo Launching server...
echo Once started, open your browser and visit:
echo -> http://127.0.0.1:8000/
echo.
echo Press Ctrl+C in this terminal window to stop the server.
echo ==========================================================
echo.

python app.py
pause
