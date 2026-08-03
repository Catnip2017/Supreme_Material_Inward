@echo off
title Material Inward Process — Setup
color 0A

echo ============================================================
echo  MATERIAL INWARD PROCESS — AUTOMATED SETUP
echo  This script will install and configure everything.
echo  Run as Administrator.
echo ============================================================
echo.

:: ============================================================
:: STEP 0 — Check Administrator
:: ============================================================
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Please run this script as Administrator.
    echo Right-click setup.bat and select "Run as administrator"
    pause
    exit /b 1
)

:: ============================================================
:: STEP 1 — Check Python
:: ============================================================
echo [1/8] Checking Python installation...
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.11 from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER% found.
echo.

:: ============================================================
:: STEP 2 — Check PostgreSQL
:: ============================================================
echo [2/8] Checking PostgreSQL...
psql --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] PostgreSQL is not installed or psql not in PATH.
    echo Please install PostgreSQL 16 from https://postgresql.org
    echo and ensure the bin folder is added to PATH.
    pause
    exit /b 1
)
echo [OK] PostgreSQL found.
echo.

:: ============================================================
:: STEP 3 — Check .env file
:: ============================================================
echo [3/8] Checking configuration...
if not exist ".env" (
    if exist ".env.template" (
        echo [WARN] .env file not found. Copying from template...
        copy ".env.template" ".env" >nul
        echo.
        echo ============================================================
        echo  ACTION REQUIRED: Open .env in a text editor and fill in:
        echo  - DB_PASSWORD
        echo  - FLASK_SECRET_KEY (run: python -c "import secrets; print(secrets.token_hex(32))")
        echo  - JWT_SECRET_KEY   (run same command again)
        echo  - SAP_PASSWORD
        echo  - EMAIL_PASSWORD / IMAP_PASSWORD
        echo  - WATSONX_API_KEY and WATSONX_PROJECT_ID
        echo  - ALLOWED_ORIGIN (production URL)
        echo ============================================================
        echo.
        echo After editing .env, run setup.bat again to continue.
        pause
        exit /b 0
    ) else (
        echo [ERROR] Neither .env nor .env.template found.
        echo Please ensure you are running setup.bat from the project folder.
        pause
        exit /b 1
    )
)
echo [OK] .env file found.
echo.

:: ============================================================
:: STEP 4 — Install Python dependencies
:: ============================================================
echo [4/8] Installing Python dependencies...
pip install -r requirements.txt --quiet
if %errorLevel% neq 0 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.
echo.

:: ============================================================
:: STEP 5 — Create required folders
:: ============================================================
echo [5/8] Creating required folders...
if not exist "uploads" mkdir uploads
if not exist "uploads\processed" mkdir uploads\processed
if not exist "uploads\failed" mkdir uploads\failed
if not exist "logs" mkdir logs
if not exist "logs\rf_output" mkdir logs\rf_output
echo [OK] Folders created.
echo.

:: ============================================================
:: STEP 6 — Create PostgreSQL database and user
:: ============================================================
echo [6/8] Setting up PostgreSQL database...
echo Please enter your PostgreSQL superuser (postgres) password when prompted.
echo.

:: Read DB settings from .env
for /f "tokens=1,* delims==" %%a in (.env) do (
    if "%%a"=="DB_USER"     set DB_USER=%%b
    if "%%a"=="DB_PASSWORD" set DB_PASSWORD=%%b
    if "%%a"=="DB_NAME"     set DB_NAME=%%b
    if "%%a"=="DB_HOST"     set DB_HOST=%%b
    if "%%a"=="DB_PORT"     set DB_PORT=%%b
)

:: Create user and database
psql -U postgres -h localhost -c "CREATE USER %DB_USER% WITH PASSWORD '%DB_PASSWORD%';" 2>nul
psql -U postgres -h localhost -c "CREATE DATABASE %DB_NAME% OWNER %DB_USER%;" 2>nul
psql -U postgres -h localhost -c "GRANT ALL PRIVILEGES ON DATABASE %DB_NAME% TO %DB_USER%;" 2>nul

:: Run schema
echo Running database schema...
set PGPASSWORD=%DB_PASSWORD%
psql -U %DB_USER% -h %DB_HOST% -p %DB_PORT% -d %DB_NAME% -f database\schema.sql
if %errorLevel% neq 0 (
    echo [ERROR] Schema execution failed. Check PostgreSQL is running and credentials are correct.
    pause
    exit /b 1
)
echo [OK] Database and tables created.
echo.

:: ============================================================
:: STEP 7 — Test database connection
:: ============================================================
echo [7/8] Testing database connection...
python -c "from database.connection import test_connection; test_connection()"
if %errorLevel% neq 0 (
    echo [ERROR] Database connection test failed.
    echo Check DB_HOST, DB_PORT, DB_USER, DB_PASSWORD in .env
    pause
    exit /b 1
)
echo [OK] Database connection verified.
echo.

:: ============================================================
:: STEP 8 — Install as Windows Service using NSSM
:: ============================================================
echo [8/8] Setting up Windows Service...
where nssm >nul 2>&1
if %errorLevel% neq 0 (
    echo [SKIP] NSSM not found. Skipping Windows Service setup.
    echo To run as a service later, download NSSM from https://nssm.cc
    echo and run: nssm install MaterialInward python app.py
    echo.
) else (
    set PROJ_DIR=%~dp0
    set PROJ_DIR=%PROJ_DIR:~0,-1%
    nssm install MaterialInward python "%PROJ_DIR%\app.py" >nul 2>&1
    nssm set MaterialInward AppDirectory "%PROJ_DIR%" >nul
    nssm set MaterialInward DisplayName "Material Inward Process" >nul
    nssm set MaterialInward Description "Material Inward SAP Automation Portal" >nul
    nssm set MaterialInward Start SERVICE_AUTO_START >nul
    nssm start MaterialInward >nul 2>&1
    echo [OK] Windows Service installed and started.
)
echo.

:: ============================================================
:: DONE
:: ============================================================
echo ============================================================
echo  SETUP COMPLETE
echo ============================================================
echo.
echo  Application URL:    http://localhost:5000
echo  Default Admin:      admin@catnip.com
echo  Default Password:   Admin@123
echo.
echo  IMPORTANT — Change default admin password on first login!
echo.
echo ============================================================
echo  MANUAL STEP REQUIRED — Mail Poller (Task Scheduler)
echo ============================================================
echo.
echo  The mail poller must be set up manually:
echo  1. Open Task Scheduler (search in Start menu)
echo  2. Click "Create Basic Task"
echo  3. Name: MaterialInward_MailPoller
echo  4. Trigger: Daily
echo  5. Action: Start a program
echo     Program: python.exe
echo     Arguments: %~dp0services\mail_poller.py
echo     Start in: %~dp0
echo  6. In Triggers, edit to repeat every 5 minutes indefinitely
echo.
echo ============================================================
echo  FIREWALL — Whitelist the following URL on your server:
echo  Add this to your firewall/reverse proxy configuration:
echo.
echo  Production URL: [UPDATE ALLOWED_ORIGIN IN .env]
echo  Port to expose: 5000 (or configure reverse proxy to 80/443)
echo ============================================================
echo.
echo Press any key to exit setup.
pause >nul
