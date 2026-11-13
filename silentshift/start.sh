#!/usr/bin/env bash
# SilentShift launcher for Mac / Linux:  bash start.sh
cd "$(dirname "$0")"
[ -d venv ] || { echo "First run: installing..."; python3 -m venv venv; }
source venv/bin/activate
pip install -q --disable-pip-version-check -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env - paste your GROQ_API_KEY into it."; }
echo; echo "  1 = Demo mode (simulated)   2 = Real mode (Ollama Qwen + Groq)"
read -p "Choose 1 or 2: " MODE
if [ "$MODE" = "2" ]; then python run.py --real; else python run.py; fi
