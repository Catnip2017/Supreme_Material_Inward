@echo off
title Material Inward Server
echo ============================================================
echo  Material Inward -- Production Server
echo  Starting on http://0.0.0.0:5003
echo ============================================================

:: Map Z: drive
net use Z: \\srv-nas\spl\Material_inward /user:spl_rpa Notepad#2566 /persistent:yes 2>nul

:: Start Nginx if not already running
tasklist /FI "IMAGENAME eq nginx.exe" 2>nul | find /I "nginx.exe" >nul
if %errorlevel%==1 (
    echo Starting Nginx...
    start /D "C:\Program Files\nginx\nginx-1.28.3" nginx.exe
    echo Nginx started.
) else (
    echo Nginx already running.
)

cd /d "C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward"
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