@echo off
rem Double-click to start the screener. TWS must already be open and logged in.
rem Closing this window stops the screener.
title Options Screener
cd /d "%~dp0"

rem Already running (e.g. started from a terminal)? Just open the page.
netstat -ano | findstr /r /c:"127.0.0.1:8000 .*LISTENING" >nul
if not errorlevel 1 (
    echo Screener is already running -- opening http://localhost:8000
    start http://localhost:8000
    timeout /t 3 /nobreak >nul
    exit /b 0
)

rem Open the browser once the server has had a few seconds to come up.
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000"

.venv\Scripts\python -m uvicorn backend.app.main:app --port 8000
pause
