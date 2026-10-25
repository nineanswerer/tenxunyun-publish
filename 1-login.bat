@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [ERROR] .venv not found in this folder.
    echo Run these commands once to set up the environment:
    echo     py -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo     .venv\Scripts\python.exe -m playwright install chromium
    echo.
    echo See README.md section "快速开始" for details.
    echo.
    pause
    exit /b 1
)

echo Opening the browser for the QR login...
".venv\Scripts\python.exe" snap_login.py
echo.
pause
