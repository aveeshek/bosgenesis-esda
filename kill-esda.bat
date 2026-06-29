@echo off
setlocal

set "PORT=8080"

echo Stopping BOS Genesis ESDA on port %PORT%...

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo Killing listener PID %%P
    taskkill /PID %%P /F >nul 2>&1
)

for /f "tokens=2 delims==" %%P in ('wmic process where "CommandLine like '%%backend.app.main%%' and Name like '%%python%%'" get ProcessId /value 2^>nul ^| findstr "ProcessId"') do (
    echo Killing ESDA python PID %%P
    taskkill /PID %%P /F >nul 2>&1
)

echo Done.
endlocal
