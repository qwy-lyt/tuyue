@echo off
REM ---------------------------------------------------------------------------
REM Demo mode: start the server and open a temporary public tunnel.
REM
REM A console window appears showing the public URL. Send that URL to whoever
REM needs to see the site. CLOSING THIS WINDOW cuts the tunnel -- the address
REM stops working immediately, and the site is unreachable from the internet.
REM
REM Keep the messages in this file ASCII: cmd.exe reads .bat files using the
REM system code page, so UTF-8 Chinese here would come out as garbage. The
REM Chinese output you see comes from demo.py, which handles its own encoding.
REM ---------------------------------------------------------------------------

cd /d "%~dp0"

title Tuyue - demo mode

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   [!] Virtual environment not found.
    echo       Run run.bat once first to create it.
    echo.
    pause
    exit /b 1
)

if not exist "bin\cloudflared.exe" (
    echo.
    echo   [!] Tunnel program not downloaded yet.
    echo       Run this once, then try again:
    echo.
    echo       .venv\Scripts\python.exe scripts\install-cloudflared.py
    echo.
    pause
    exit /b 1
)

REM -u keeps output unbuffered, so the URL appears as soon as it is known.
".venv\Scripts\python.exe" -u demo.py

echo.
echo Tunnel closed. The public address no longer works.
echo.
pause
