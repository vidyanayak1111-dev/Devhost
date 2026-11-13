@echo off
title SilentShift
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist venv (
  echo First run: installing... this takes about a minute.
  python -m venv venv
)
call venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - open it in Notepad and paste your GROQ_API_KEY.
)

echo.
echo   SilentShift
echo     1 = Demo mode  (simulated provider - instant, for the big evaluation)
echo     2 = Real mode  (your Ollama Qwen + Groq - Ollama must be running)
echo.
set /p MODE="Type 1 or 2 and press Enter: "

rem open the chatbot and the dashboard a few seconds after the server starts
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start "" http://localhost:8000/ & start "" http://localhost:8000/monitor.html"
if "%MODE%"=="2" (python run.py --real) else (python run.py)
pause
