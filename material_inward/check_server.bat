@echo off
echo ============================================================
echo  Material Inward -- Server Health Check
echo ============================================================

echo.
echo [1] Checking Waitress (port 5003)...
netstat -an | findstr ":5003" | findstr "LISTENING"
if errorlevel 1 (echo    FAIL - Waitress not running) else (echo    OK - Waitress running)

echo.
echo [2] Checking Nginx (port 80)...
netstat -an | findstr ":80" | findstr "LISTENING"
if errorlevel 1 (echo    FAIL - Nginx not on port 80) else (echo    OK - Nginx on port 80)

echo.
echo [3] Checking Nginx (port 443)...
netstat -an | findstr ":443" | findstr "LISTENING"
if errorlevel 1 (echo    FAIL - Nginx not on port 443) else (echo    OK - Nginx on port 443)

echo.
echo [4] Checking G: drive...
if exist G:\ (echo    OK - G: drive mapped) else (echo    FAIL - G: drive not mapped)

echo.
echo [5] Checking Nginx process...
tasklist | findstr /i "nginx.exe" >nul 2>&1
if errorlevel 1 (echo    FAIL - Nginx not running) else (echo    OK - Nginx process found)

echo.
echo ============================================================
echo  Quick Fix Commands:
echo  Restart Nginx : cd "C:\Program Files\nginx\nginx-1.28.3" and nginx.exe -s reload
echo  Stop Nginx    : nginx.exe -s stop
echo  Map G: drive  : net use G: \\srv-nas\spl /persistent:yes
echo ============================================================
pause