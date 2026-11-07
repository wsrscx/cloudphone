@echo off
setlocal
cd /d "%~dp0"
title CloudPhone Server

echo ============================================================
echo                    CloudPhone Server
echo ============================================================
echo Admin page:     http://127.0.0.1:8080
echo Public service: HTTPS/WSS on the configured TCP port
echo.
echo Keep this window open while the server is running.
echo Press Ctrl+C to stop the server.
echo ============================================================
echo.

netstat -ano | findstr /R /C:":8080 .*LISTENING" >nul
if not errorlevel 1 (
    echo The server is already running.
    echo.
    pause
    exit /b 0
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-server.ps1"
set "CLOUDPHONE_EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%CLOUDPHONE_EXIT_CODE%"=="0" (
    echo Server startup or runtime failed. Exit code: %CLOUDPHONE_EXIT_CODE%
) else (
    echo The server has stopped.
)
echo.
pause
exit /b %CLOUDPHONE_EXIT_CODE%
