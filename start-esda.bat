@echo off
setlocal

set "APP_HOST=127.0.0.1"
set "APP_PORT=8080"
set "APP_MODULE=backend.app.main:app"
set "ROOT_DIR=%~dp0"

cd /d "%ROOT_DIR%"

echo Starting BOS Genesis ESDA from:
echo %CD%
echo.

call "%ROOT_DIR%kill-esda.bat"
echo.

echo Launching http://%APP_HOST%:%APP_PORT%
start "BOS Genesis ESDA" cmd /k python -m uvicorn %APP_MODULE% --host %APP_HOST% --port %APP_PORT%

echo.
echo ESDA is starting in a new terminal window.
echo Open: http://%APP_HOST%:%APP_PORT%
timeout /t 4 /nobreak >nul
start "" "http://%APP_HOST%:%APP_PORT%"

endlocal