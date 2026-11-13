"""
config.py - one place for ports, paths and modes.

Architecture (all on your laptop):

    Browser (chat UI + monitor dashboard)
          |
          v
    server.py  :8000   <- our app. /api/chat for the chatbot, /ollama/* proxy,
          |                /monitor/* for the dashboard. Logs METADATA ONLY.
          v
    provider.py :11500  <- the "AI provider" (black box to the monitor).
                           mode "mock": pretend models, no GPU needed.
                           mode "real": wraps your real Ollama (:11434) and
                           applies hidden serving/safety changes for the demo.
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# tiny .env loader (no extra dependency): KEY=value lines in silentshift/.env
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
DATA = ROOT / "data"
HIDDEN = DATA / "hidden"          # only the provider + scorer may read this
WEB = ROOT / "web"
DATA.mkdir(exist_ok=True)
HIDDEN.mkdir(exist_ok=True)

SERVER_PORT = int(os.getenv("SS_PORT", 8000))
PROVIDER_PORT = int(os.getenv("SS_PROVIDER_PORT", 11500))
PROVIDER_URL = os.getenv("SS_PROVIDER_URL", f"http://127.0.0.1:{PROVIDER_PORT}")
SELF_URL = f"http://127.0.0.1:{SERVER_PORT}"

# "mock" = no models needed. "real" = a real local Ollama. "api" = any OpenAI-compatible cloud API.
PROVIDER_MODE = os.getenv("SS_PROVIDER_MODE", "mock")
REAL_OLLAMA_URL = os.getenv("SS_OLLAMA_URL", "http://127.0.0.1:11434")

# --- API mode (OpenAI-compatible: Groq, OpenAI, Gemini, OpenRouter, ...)
API_BASE = os.getenv("SS_API_BASE", "https://api.groq.com/openai/v1").rstrip("/")
API_KEY = os.getenv("SS_API_KEY", "")
API_MODEL = os.getenv("SS_API_MODEL", "")                  # what bot-latest normally points to
API_MODEL_NEW = os.getenv("SS_API_MODEL_NEW", "")          # where the alias is silently moved (model change)
API_MODEL_SERVING = os.getenv("SS_API_MODEL_SERVING", "")  # optional: same model, different serving variant
API_EXTRA = json.loads(os.getenv("SS_API_EXTRA", "{}") or "{}")  # extra request fields, e.g. {"reasoning_effort":"low"}

# Speed tolerance: network APIs are much noisier than a local model
SPEED_TOLERANCE = float(os.getenv("SS_SPEED_TOL", {"mock": 0.12, "real": 0.20}.get(PROVIDER_MODE, 0.35)))

# --- files the MONITOR may use (no user content is ever written to them)
APP_CONFIG = ROOT / "backend" / "app_config.json"     # our own request config
CANARIES = ROOT / "backend" / "canaries.json"
BASELINE = DATA / "baseline.json"
REPORTS = DATA / "reports.jsonl"
METADATA_LOG = DATA / "metadata_log.jsonl"
RESULTS_MD = DATA / "results.md"
RESULTS_JSON = DATA / "results.json"

# --- files the monitor must NEVER read (ground truth for the demo/scoring)
PROVIDER_STATE = HIDDEN / "provider_state.json"
CHANGE_LOG = HIDDEN / "change_log.json"

PROBE_HEADER = "X-SilentShift-Probe"   # marks canary traffic vs live traffic