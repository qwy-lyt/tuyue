@echo off
REM ---------------------------------------------------------------------------
REM Start the server so other devices on your Radmin VPN can reach it.
REM
REM Two things happen here:
REM   1. a firewall rule that opens port 8000 ONLY to the Radmin network
REM      (26.0.0.0/8, the range every Radmin VPN uses). Your normal LAN address
REM      stays closed, so someone on the same campus or office network cannot
REM      reach the app even if they scan for it.
REM   2. the server binds to 0.0.0.0 instead of 127.0.0.1, so it is reachable
REM      from outside this machine.
REM
REM Adding a firewall rule needs Administrator rights -- right-click this file
REM and choose "Run as administrator".
REM ---------------------------------------------------------------------------

cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Administrator rights are required to add the firewall rule.
    echo       Right-click run_radmin.bat and pick "Run as administrator".
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   [!] Virtual environment not found. Run run.bat once first.
    echo.
    pause
    exit /b 1
)

REM Find this machine's own Radmin address instead of hard-coding one, so the
REM script keeps working on a different machine or a re-created network.
set "RADMIN_IP="
for /f "usebackq delims=" %%a in (`powershell -NoProfile -Command "$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceAlias -like '*Radmin*' } | Select-Object -First 1).IPAddress; if ($ip) { $ip }"`) do set "RADMIN_IP=%%a"

if not defined RADMIN_IP (
    echo.
    echo   [!] No Radmin VPN adapter found. Is the Radmin VPN client running?
    echo       The firewall rule will still be added; the address below is unknown.
    echo.
)

echo [1/2] Opening port 8000 to the Radmin network only...
netsh advfirewall firewall delete rule name="Yue Chat (Radmin)" >nul 2>&1
netsh advfirewall firewall add rule name="Yue Chat (Radmin)" dir=in action=allow protocol=TCP localport=8000 remoteip=26.0.0.0/8 profile=any >nul

if errorlevel 1 (
    echo       Failed to add the firewall rule.
    pause
    exit /b 1
)
echo       Rule "Yue Chat (Radmin)" is in place.

echo.
echo [2/2] Starting the server on 0.0.0.0:8000
echo.
echo   On this machine      : http://127.0.0.1:8000
if defined RADMIN_IP echo   From other devices   : http://%RADMIN_IP%:8000
echo.
echo   Keep this window open. Press Ctrl+C to stop.
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000

pause
