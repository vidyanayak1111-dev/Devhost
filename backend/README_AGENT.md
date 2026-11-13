# SilentShift agent (customer side)

This folder is ONLY the agent. The server (provider.py + Ollama) runs on the teammate's laptop.
The agent never sees the server's files: it only talks to the server through the link.

## Install (once)
```powershell
cd "C:\path\to\silentshift-agent"
python -m pip install -r requirements.txt
```

## Run (3 terminals, all in this folder)

Terminal 1 - proxy, pointed at the server link from your teammate:
```powershell
$env:REAL_OLLAMA = "https://THE-LINK.trycloudflare.com"
python proxy.py
```

Terminal 2 - settings + dashboard:
```powershell
$env:GROQ_API_KEY = "gsk_your_key"
$env:JUDGE_PROVIDER = "groq"
$env:JUDGE_MODEL = "openai/gpt-oss-120b"
python harness.py          # should print an answer with 30
python harness.py groq     # should print 30 too (judge works)
python api.py
```
Open http://127.0.0.1:8001/

## Test
1. Dashboard: Fingerprint baseline, then Quality baseline (teammate changes NOTHING meanwhile).
2. Teammate secretly applies a change on the server (change_injector.py).
3. Dashboard: Quality check (label test1), then Diagnose.
4. Compare the agent's answer with the teammate's change_log.json.

Request-config changes (temperature, answer length, system prompt) are YOUR app's settings:
make them here by editing app_config.json, not on the server.

## Files
proxy.py (metadata-only proxy) · harness.py (talks to Ollama / Groq / Gemini via Google SDK) ·
canaries.json + runner.py + agent.py (fingerprints) · eval_set.json + judge.py + evaluator.py (quality) ·
diagnose.py (report) · api.py + dashboard.html (FastAPI backend + dashboard) · app_config.json (our settings)
