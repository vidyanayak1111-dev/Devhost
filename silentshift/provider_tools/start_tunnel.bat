@echo off
title Cloudflare tunnel to Ollama
echo.
echo  Shares THIS computer's Ollama over the internet with a temporary https link.
echo  WARNING: Ollama has no password - anyone with the link can use your models.
echo  Close this window when the demo is over.
echo.
where cloudflared >nul 2>nul
if errorlevel 1 (
  echo Installing cloudflared...
  winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  echo Close this window and double-click start_tunnel.bat again.
  pause
  exit /b
)
echo  Copy the https://....trycloudflare.com link it prints, then on the SilentShift computer put this
echo  line in .env:   SS_OLLAMA_URL=https://....trycloudflare.com
echo.
cloudflared tunnel --url http://localhost:11434 --http-host-header="localhost:11434"
pause
