"""
run.py - start everything with one command.

    python run.py            # simulated provider (no models needed)
    python run.py --real     # wrap your real Ollama on :11434
    python run.py --api      # any OpenAI-compatible cloud API (settings in .env)

Then open http://localhost:8000          (chatbot)
          http://localhost:8000/monitor.html  (monitor dashboard)
"""

import os
import sys
import threading
import time
from pathlib import Path

if "--real" in sys.argv:
    os.environ["SS_PROVIDER_MODE"] = "real"
if "--api" in sys.argv:
    os.environ["SS_PROVIDER_MODE"] = "api"
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn  # noqa: E402

import config  # noqa: E402
import injector  # noqa: E402

if not config.PROVIDER_STATE.exists():
    injector.reset()


def run_provider():
    import provider
    uvicorn.Server(uvicorn.Config(provider.app, host="127.0.0.1", port=config.PROVIDER_PORT,
                                  log_level="warning")).run()


if __name__ == "__main__":
    if config.PROVIDER_MODE == "api" and not (config.API_KEY and config.API_MODEL):
        sys.exit("API mode needs SS_API_KEY and SS_API_MODEL - copy .env.example to .env and fill it in.")
    threading.Thread(target=run_provider, daemon=True).start()
    time.sleep(0.5)
    print(f"\n  Provider ({config.PROVIDER_MODE}) on :{config.PROVIDER_PORT}")
    if config.PROVIDER_MODE == "api":
        print(f"  API {config.API_BASE}  model {config.API_MODEL}  (alias-move target: {config.API_MODEL_NEW or 'not set'})")
    print(f"  Chatbot   http://localhost:{config.SERVER_PORT}")
    print(f"  Monitor   http://localhost:{config.SERVER_PORT}/monitor.html\n")
    import server
    uvicorn.run(server.app, host="0.0.0.0", port=config.SERVER_PORT, log_level="warning")