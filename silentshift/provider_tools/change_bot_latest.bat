@echo off
title Provider tools - change bot-latest on the Ollama server
cd /d "%~dp0"
rem Uses the SilentShift venv's Python if it exists, otherwise any Python on PATH.
set PY=python
if exist "..\venv\Scripts\python.exe" set PY=..\venv\Scripts\python.exe
%PY% provider.py %*
echo.
pause
