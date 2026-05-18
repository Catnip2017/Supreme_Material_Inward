@echo off
title Material Inward Server
echo ============================================================
echo  Material Inward -- Production Server
echo  Starting on http://0.0.0.0:5003
echo ============================================================

call venv\Scripts\activate
if errorlevel 1 (
    echo ERROR: venv activation failed
    pause
    exit /b 1
)

:restart
echo [%date% %time%] Starting server...
python run_server.py
echo [%date% %time%] Server stopped. Restarting in 5 seconds...
timeout /t 5 /nobreak
goto restart