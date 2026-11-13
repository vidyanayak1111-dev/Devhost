@echo off
title SilentShift - public link
cd /d "%~dp0"
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo Installing cloudflared - one time...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  echo Installed. Close this window and double-click share.bat again.
  pause
  exit /b
)
echo Look for the https://....trycloudflare.com link below.
echo Chatbot = that link     Console = that link + /monitor.html
cloudflared tunnel --url http://localhost:8000
pause