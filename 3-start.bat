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
    echo See README.md section "Install" for details.
    echo.
    pause
    exit /b 1
)

echo REAL seckill mode - the real page places the real order.
echo Start 5-10 minutes before the session opens. Do NOT minimize the window.
echo The script clicks the buy button and confirms the buy dialog.
echo YOU pay in that window.
".venv\Scripts\python.exe" snap_browser.py %*
echo.
pause
