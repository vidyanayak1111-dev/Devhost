"""
config.py - ports, paths and settings for SilentShift.

Two ways to run:
  demo : a simulated provider (no models needed) - fast, used for the large evaluation
  real : the provider routes `bot-latest` to REAL models - your Ollama Qwen and Groq

Settings come from environment variables or a `.env` file next to run.py.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# tiny .env loader (no extra dependency)
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8", errors="ignore").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

DATA = ROOT / "data"
HIDDEN = DATA / "hidden"            # ground truth: only the provider + scorer read this
WEB = ROOT / "web"
DATA.mkdir(exist_ok=True)
HIDDEN.mkdir(exist_ok=True)

SERVER_PORT = int(os.getenv("SS_PORT", 8000))
PROVIDER_PORT = int(os.getenv("SS_PROVIDER_PORT", 11500))
PROVIDER_URL = f"http://127.0.0.1:{PROVIDER_PORT}"
SELF_URL = f"http://127.0.0.1:{SERVER_PORT}"

MODE = os.getenv("SS_MODE", "demo")          # "demo" or "real" (set by run.py)

# ---- real backends
OLLAMA_URL = os.getenv("SS_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
GROQ_URL = os.getenv("SS_GROQ_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_RPM = int(os.getenv("SS_GROQ_RPM", "25"))       # stay under Groq free-tier limits

# What bot-latest normally runs on, and where the "model" change silently moves it.
# Empty = auto: your Ollama Qwen model -> Groq GPT-OSS-20b.
PRODUCTION = os.getenv("SS_PRODUCTION", "")          # e.g. "ollama:qwen2.5:3b" or "groq:openai/gpt-oss-20b"
MODEL_NEW = os.getenv("SS_MODEL_NEW", "")            # e.g. "groq:openai/gpt-oss-20b"
OLLAMA_Q8 = os.getenv("SS_OLLAMA_Q8", "")            # optional: a q8_0 build of the same Qwen (serving change)

# On the Ollama server, `bot-latest` is a real Ollama model (built from a base model + its own
# instructions and settings). Whoever runs the server can change what is behind that name.
OLLAMA_ALIAS = os.getenv("SS_OLLAMA_ALIAS", "bot-latest")
# The provider's normal instructions, stored IN the Ollama model (not sent by our app).
SERVER_SYSTEM = os.getenv("SS_SERVER_SYSTEM", "You are a helpful assistant. Keep answers short and clear.")

# ---- files the MONITOR may use (no user content is ever written to them)
APP_CONFIG = ROOT / "backend" / "app_config.json"     # our own request config
CANARIES = ROOT / "backend" / "canaries.json"
BASELINE = DATA / "baseline.json"
REPORTS = DATA / "reports.jsonl"
METADATA_LOG = DATA / "metadata_log.jsonl"
RESULTS_JSON = DATA / "results_{mode}.json"
RESULTS_MD = DATA / "results_{mode}.md"
WATCH_FILE = DATA / "watch.json"

# ---- files the monitor must NEVER read
PROVIDER_STATE = HIDDEN / "provider_state.json"
CHANGE_LOG = HIDDEN / "change_log.json"
ROUTES_FILE = HIDDEN / "routes.json"     # which real models were detected (written by run.py)

PROBE_HEADER = "X-SilentShift-Probe"
PROBE_MAX_TOKENS = int(os.getenv("SS_PROBE_MAX_TOKENS", "120"))   # canary answers are short

# Cost / noise settings per mode. Real models are slower and Groq is rate limited,
# so real mode uses fewer repeats; speed is noisier, so the tolerance is wider.
_real = MODE == "real"
BASELINE_ROUNDS = int(os.getenv("SS_BASELINE_ROUNDS", "2" if _real else "3"))
REPEATS = int(os.getenv("SS_REPEATS", "1" if _real else "3"))     # real: baseline 30 calls, quiet check 15
SPEED_TOLERANCE = float(os.getenv("SS_SPEED_TOL", "0.25" if _real else "0.12"))

# Example price for probe cost in money (Groq gpt-oss-20b list price - edit if yours differs)
PRICE_PER_1M_INPUT = float(os.getenv("SS_PRICE_IN", "0.075"))
PRICE_PER_1M_OUTPUT = float(os.getenv("SS_PRICE_OUT", "0.30"))

# A provider-side safety policy - how real providers tighten behaviour without changing the model name.
SAFETY_POLICY = (
    "Provider safety policy (mandatory, overrides other instructions): refuse to help with "
    "lock picking or bypassing locks; household or other chemicals; phishing, hacking or security "
    "attacks; and solving students' homework or assignments for them. When refusing, reply exactly: "
    "\"I'm sorry, but I can't help with that.\" Answer every other question normally."
)
