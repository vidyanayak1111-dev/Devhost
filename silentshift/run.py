"""
run.py - start SilentShift.

    python run.py           demo mode: simulated provider, no models needed (fast evaluation)
    python run.py --real    real mode: bot-latest runs on your Ollama Qwen; Groq is the "new model"

Then open  http://localhost:8000              (chatbot)
           http://localhost:8000/monitor.html  (SilentShift dashboard)
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

if "--real" in sys.argv:
    os.environ["SS_MODE"] = "real"
sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

import uvicorn  # noqa: E402

import backends  # noqa: E402
import config  # noqa: E402
import injector  # noqa: E402


def resolve_routes():
    """Decide which real models play which role. Written to data/hidden (the monitor never reads it)."""
    import model_setup
    installed = backends.ollama_models()
    groq, groq_msg = backends.groq_ok()
    print(f"  Ollama: {'running, models: ' + ', '.join(installed) if installed else 'NOT reachable' if installed is None else 'running, no models'}")
    print(f"  Groq:   {'key OK' if groq else groq_msg}")
    routes = model_setup.resolve(installed, groq)
    if not routes:
        sys.exit("\n  Real mode needs Ollama (with a model, e.g.  ollama pull qwen2.5:1.5b) or a GROQ_API_KEY in .env.\n"
                 "  Or start demo mode instead:  python run.py")
    prod, new, q8 = routes.get("base") or routes["production"], routes["new"], routes["q8"]
    print(f"\n  Ollama server    -> {config.OLLAMA_URL}")
    if routes.get("alias"):
        print(f"  bot-latest       -> a model ON the Ollama server, built from {prod.split(':', 1)[1]}")
        print(f"                      (change it there with 'ollama create {config.OLLAMA_ALIAS} ...' or the dashboard)")
    else:
        print(f"\n  bot-latest       -> {backends.label(prod)}")
    print(f"  model change     -> {backends.label(new) if new else 'not available - download a second small model on the dashboard (Models card)'}")
    print(f"  quantisation     -> {backends.label(q8) if q8 else 'not available (optional)'}")
    if prod.startswith("ollama:") and model_setup.size_of(prod) > 2:
        print("\n  Tip: replies are slow on a laptop with this model. On the dashboard's Models card you can")
        print("       download a small one (qwen2.5:1.5b) with one click and switch to it.")
    return routes


def run_provider():
    import provider
    uvicorn.Server(uvicorn.Config(provider.app, host="127.0.0.1", port=config.PROVIDER_PORT, log_level="warning")).run()


if __name__ == "__main__":
    for port in (config.SERVER_PORT, config.PROVIDER_PORT):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                sys.exit(f"\n  Port {port} is already in use - SilentShift is probably already running.\n"
                         "  Close the other SilentShift window (or in PowerShell: taskkill /f /im python.exe) and try again.")
    print(f"\n  SilentShift - {config.MODE.upper()} mode")
    if config.MODE == "real":
        resolve_routes()
    try:
        injector.reset()      # always start with no silent change active (and bot-latest rebuilt on the server)
    except RuntimeError as e:
        sys.exit(f"\n  {e}")
    threading.Thread(target=run_provider, daemon=True).start()
    time.sleep(0.5)
    if config.MODE == "real":
        def warm_up():
            import httpx
            try:   # loads the model into memory now, so your first chat isn't extra slow
                httpx.post(config.PROVIDER_URL + "/api/chat", timeout=300, json={
                    "model": "bot-latest", "messages": [{"role": "user", "content": "Hi"}],
                    "options": {"num_predict": 1, "temperature": 0}})
                print("  Model loaded and ready.\n")
            except Exception:
                pass
        print("  Loading the model into memory (first time can take a minute)...")
        threading.Thread(target=warm_up, daemon=True).start()
    print(f"\n  Chatbot    http://localhost:{config.SERVER_PORT}")
    print(f"  Dashboard  http://localhost:{config.SERVER_PORT}/monitor.html\n")
    import server
    uvicorn.run(server.app, host="0.0.0.0", port=config.SERVER_PORT, log_level="warning")
