@echo off
title Material Inward Server
setlocal enabledelayedexpansion
chcp 65001 >nul

:: ── Log file setup ────────────────────────────────────────────────────────────
set APP_DIR=C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward
set LOG_DIR=C:\material_inward\logs
set LOG_FILE=%LOG_DIR%\startup_%date:~-4,4%%date:~-7,2%%date:~0,2%.log
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Dual output: console + log file
:: All subsequent echoes use CALL :log
set RESTART_COUNT=0

call :log "============================================================"
call :log "  Material Inward Process — Production Server"
call :log "  Started at: %date% %time%"
call :log "  Host      : http://0.0.0.0:5003"
call :log "  Log file  : %LOG_FILE%"
call :log "============================================================"
call :log ""

:: ── PRE-FLIGHT CHECKS ─────────────────────────────────────────────────────────
call :log "[CHECK] Running pre-flight checks..."
call :log ""

:: 1. G: drive mapping
call :log "[1/5] Mapping G: drive (NAS)..."
net use G: >nul 2>&1
if %errorlevel%==0 (
    call :log "      G: already mapped — OK"
) else (
    net use G: \\srv-nas\spl /persistent:yes >nul 2>&1
    if errorlevel 1 (
        call :log "      WARNING: G: drive mapping FAILED — folder watcher may not work"
    ) else (
        call :log "      G: drive mapped — OK"
    )
)

:: 2. G: drive accessibility
if exist "G:\" (
    call :log "      G: drive accessible — OK"
) else (
    call :log "      WARNING: G: drive not accessible — check NAS connection"
)
call :log ""

:: 3. PostgreSQL service
call :log "[2/5] Checking PostgreSQL service..."
sc query postgresql* 2>nul | find /I "RUNNING" >nul
if %errorlevel%==0 (
    call :log "      PostgreSQL RUNNING — OK"
) else (
    call :log "      WARNING: PostgreSQL service not detected — DB connections may fail"
    call :log "      Tip: Run 'services.msc' and start the PostgreSQL service manually"
)
call :log ""

:: 4. Port 5003 availability
call :log "[3/5] Checking port 5003..."
netstat -ano | find ":5003 " | find "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    call :log "      WARNING: Port 5003 already in use — another instance may be running"
    call :log "      Tip: Run 'netstat -ano | find \":5003\"' to identify the process"
) else (
    call :log "      Port 5003 free — OK"
)
call :log ""

:: 5. Nginx
call :log "[4/5] Checking Nginx..."
tasklist /FI "IMAGENAME eq nginx.exe" 2>nul | find /I "nginx.exe" >nul
if %errorlevel%==1 (
    call :log "      Starting Nginx..."
    start /D "C:\Program Files\nginx\nginx-1.28.3" nginx.exe
    timeout /t 2 /nobreak >nul
    tasklist /FI "IMAGENAME eq nginx.exe" 2>nul | find /I "nginx.exe" >nul
    if %errorlevel%==0 (
        call :log "      Nginx started — OK"
    ) else (
        call :log "      WARNING: Nginx failed to start — check nginx path"
    )
) else (
    call :log "      Nginx already running — OK"
)
call :log ""

:: 6. Python venv
call :log "[5/5] Activating Python virtual environment..."
cd /d "%APP_DIR%"
if not exist "venv\Scripts\activate.bat" (
    call :log "      ERROR: venv not found at %APP_DIR%\venv"
    call :log "      Run: python -m venv venv  then  pip install -r requirements.txt"
    pause
    exit /b 1
)
call venv\Scripts\activate
if errorlevel 1 (
    call :log "      ERROR: venv activation failed"
    pause
    exit /b 1
)
call :log "      venv activated — OK"
call :log ""

:: ── Show key .env config values (non-sensitive) ───────────────────────────────
call :log "[CONFIG] Active environment settings:"
for /f "tokens=1,* delims==" %%A in ('findstr /i "SERVER_PORT INTAKE_METHOD WATCH_FOLDER UPLOAD_FOLDER DMS_STAGING ENABLED_STEPS" "%APP_DIR%\.env" 2^>nul') do (
    call :log "         %%A = %%B"
)
call :log ""

:: ── All checks done ───────────────────────────────────────────────────────────
call :log "============================================================"
call :log "  Pre-flight complete. Starting application..."
call :log "============================================================"
call :log ""

:: ── RESTART LOOP ──────────────────────────────────────────────────────────────
:restart
set /a RESTART_COUNT+=1
call :log "[START #%RESTART_COUNT%] Launching run_server.py at %date% %time%"
python run_server.py
set EXIT_CODE=%errorlevel%
call :log "[STOP  #%RESTART_COUNT%] Server exited (code=%EXIT_CODE%) at %date% %time%"

:: Exit code 0       = clean shutdown
:: Exit code 3221225786 = Ctrl+C (Windows 0xC000013A)
if %EXIT_CODE%==0           goto :done
if %EXIT_CODE%==3221225786  goto :done

call :log "  Crash detected (code=%EXIT_CODE%). Restarting in 5 seconds..."
call :log "  Close this window to abort restart."
timeout /t 5 /nobreak >nul
goto restart

:done
call :log "  Server stopped cleanly (Ctrl+C or intentional shutdown)."
call :log "  Restart count: %RESTART_COUNT%"
call :log "============================================================"


:: ── Subroutine: write to console AND log file ─────────────────────────────────
:log
echo %~1
echo %~1 >> "%LOG_FILE%"
exit /b 0
