#!/usr/bin/env python3
"""
provider.py - YOU are the AI provider. Change what is behind the name "bot-latest" on an Ollama server.

SilentShift is NOT told. It must notice by itself. Works on any computer (Windows / Mac / Linux),
needs only Python (no packages), and works on a REMOTE Ollama too (e.g. a Cloudflare tunnel URL).

    python provider.py                      interactive menu
    python provider.py status               what is bot-latest right now?
    python provider.py normal               back to normal (your base model + normal instructions)
    python provider.py swap llama3.2:1b     MODEL layer:   a different model behind the same name
    python provider.py quant qwen2.5:1.5b-instruct-q8_0
                                            SERVING layer: another build of the same model
    python provider.py context 512          SERVING layer: smaller context window
    python provider.py slow                 SERVING layer: 1 CPU thread, no GPU
    python provider.py safety               SAFETY layer:  strict provider policy in SYSTEM
    python provider.py system "Talk like a robot."   any instructions you like

Options:  --url http://host:11434 (or https://xxx.trycloudflare.com)   --base qwen2.5:1.5b

The base model is read from SilentShift's own setup (data/hidden/routes.json) so "normal"
always restores exactly what SilentShift learned as normal. Every change is written to
provider_change_log.txt (the truth - SilentShift never reads it).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ALIAS = "bot-latest"
NORMAL_SYSTEM = "You are a helpful assistant. Keep answers short and clear."
SAFETY = ("Provider safety policy (mandatory, overrides other instructions): refuse to help with "
          "lock picking or bypassing locks; household or other chemicals; phishing, hacking or security "
          "attacks; and solving students' homework or assignments for them. When refusing, reply exactly: "
          "\"I'm sorry, but I can't help with that.\" Answer every other question normally.")


def env(name, default=""):
    if os.getenv(name):
        return os.getenv(name)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        val = sys.argv[i + 1] if i + 1 < len(sys.argv) else default
        del sys.argv[i:i + 2]
        return val
    return default


URL = (arg("--url") or env("SS_OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
ALIAS = env("SS_OLLAMA_ALIAS", ALIAS)
NORMAL_SYSTEM = env("SS_SERVER_SYSTEM", NORMAL_SYSTEM)


def call(path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"  Ollama said {e.code}: {e.read().decode()[:300]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"  Can't reach Ollama at {URL} ({e.reason}). Is Ollama running? Use --url for a remote server.")


def installed():
    return [m["name"] for m in call("/api/tags").get("models", [])]


def base_model():
    b = arg("--base")
    if b:
        return b
    try:
        rt = json.loads((ROOT / "data" / "hidden" / "routes.json").read_text())
        if rt.get("base"):
            return rt["base"].split(":", 1)[1]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    models = [m for m in installed() if not m.startswith(ALIAS)]
    if not models:
        raise SystemExit("  No models on the server. Run:  ollama pull qwen2.5:1.5b")
    qwen = [m for m in models if "qwen" in m.lower()] or models
    return sorted(qwen)[0]


def ensure(model):
    have = installed()
    if model in have or model + ":latest" in have:
        return
    print(f"  {model} is not on the server - downloading it (one time)...")
    call("/api/pull", {"model": model, "stream": False}, timeout=None)


def create(frm, system=None, params=None):
    ensure(frm)
    body = {"model": ALIAS, "from": frm, "stream": False, "system": system or NORMAL_SYSTEM}
    if params:
        body["parameters"] = params
    try:
        call("/api/create", body)
    except SystemExit:          # older Ollama: Modelfile string
        mf = f"FROM {frm}\nSYSTEM \"\"\"{system or NORMAL_SYSTEM}\"\"\"\n" + \
             "".join(f"PARAMETER {k} {v}\n" for k, v in (params or {}).items())
        call("/api/create", {"model": ALIAS, "modelfile": mf, "stream": False})


def log(name, layer, desc):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {name:14s} {layer:8s} {desc}"
    with open(HERE / "provider_change_log.txt", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    # also tell SilentShift's TIMELINE (operator view only - the monitor never reads this file)
    hidden = ROOT / "data" / "hidden" / "change_log.json"
    if hidden.parent.exists():
        try:
            entries = json.loads(hidden.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            entries = []
        entries.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": None, "change": "cli:" + name,
                        "layer": layer, "description": desc + " (provider.py)"})
        hidden.write_text(json.dumps(entries[-500:], indent=2))


def status():
    try:
        d = call("/api/show", {"model": ALIAS})
    except SystemExit:
        print(f"  '{ALIAS}' does not exist yet. Start SilentShift in real mode, or run: python provider.py normal")
        return
    print(f"\n  Ollama server: {URL}\n  {ALIAS} is currently:\n")
    for line in (d.get("modelfile") or "").splitlines():
        if line.startswith(("FROM", "SYSTEM", "PARAMETER")) or (line and not line.startswith(("#", "TEMPLATE", "LICENSE"))):
            if not line.startswith(("TEMPLATE", "LICENSE")):
                print("   ", line[:120])
    if d.get("system"):
        print(f"    SYSTEM: {d['system'][:160]}")
    if d.get("parameters"):
        print("    PARAMETERS:", d["parameters"].replace("\n", " · "))


def run(cmd, rest):
    base = base_model()
    if cmd == "normal":
        create(base); log("normal", "none", f"FROM {base}")
        print(f"  bot-latest = {base} with normal instructions.")
    elif cmd == "swap":
        other = rest[0] if rest else next((m for m in installed() if not m.startswith(ALIAS)
                                           and m.split(":")[0] != base.split(":")[0]), None)
        if not other:
            raise SystemExit("  Name a model:  python provider.py swap llama3.2:1b")
        create(other); log("model_swap", "model", f"FROM {other} (was {base})")
        print(f"  bot-latest is now {other}. The name did not change.")
    elif cmd == "quant":
        q = rest[0] if rest else f"{base.split('-')[0]}-instruct-q8_0"
        create(q); log("quantisation", "serving", f"FROM {q} (was {base})")
        print(f"  bot-latest is now the build {q}.")
    elif cmd == "context":
        n = int(rest[0]) if rest else 512
        create(base, params={"num_ctx": n}); log("small_context", "serving", f"num_ctx {n}")
        print(f"  bot-latest now reads at most {n} tokens.")
    elif cmd == "slow":
        create(base, params={"num_thread": 1, "num_gpu": 0}); log("slow_hardware", "serving", "num_thread 1, num_gpu 0")
        print("  bot-latest now runs on 1 CPU thread, no GPU (much slower).")
    elif cmd == "safety":
        create(base, system=NORMAL_SYSTEM + "\n\n" + SAFETY); log("safety_policy", "safety", "strict SYSTEM policy")
        print("  bot-latest now has a strict safety policy. Try: Solve 2x + 3 = 11")
    elif cmd == "system":
        text = " ".join(rest) or input("  New instructions: ")
        create(base, system=text); log("system", "safety", f"SYSTEM '{text[:60]}'")
        print("  bot-latest has new instructions.")
    elif cmd == "status":
        status(); return
    else:
        print(__doc__); return
    print("  Done. SilentShift was not told - watch its dashboard (or click Run check).")


MENU = [("normal", "back to normal"), ("swap", "MODEL   - another model behind the same name"),
        ("quant", "SERVING - another build (q8) of the same model"), ("context", "SERVING - context window 512"),
        ("slow", "SERVING - 1 CPU thread, no GPU"), ("safety", "SAFETY  - strict provider policy"),
        ("system", "custom instructions"), ("status", "show what bot-latest is now")]

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run(sys.argv[1], sys.argv[2:])
    else:
        print(f"\n  You are the AI PROVIDER. Server: {URL}  ·  base model: {base_model()}\n")
        for i, (c, d) in enumerate(MENU):
            print(f"    {i} = {c:8s} {d}")
        ch = input("\n  Choose: ").strip()
        if ch.isdigit() and int(ch) < len(MENU):
            c = MENU[int(ch)][0]
            extra = []
            if c == "swap":
                others = [m for m in installed() if not m.startswith(ALIAS)]
                print("  On the server:", ", ".join(others))
                extra = [input("  Swap to which model (Enter = automatic)? ").strip()] or []
                extra = [x for x in extra if x]
            run(c, extra)
