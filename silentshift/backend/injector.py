"""
injector.py - CONTROLLED CHANGES for the demo and the evaluation.

Plays the provider (or a teammate) silently changing something, and writes the
truth to data/hidden/change_log.json. The monitor never reads that file; only the
scorer (evaluation.py) does.

    python injector.py list | apply <name> | reset
"""

import copy
import json
import sys
import time

import config

NORMAL_STATE = {"route": "production", "num_ctx": 4096, "slow": False, "safety": False}
NORMAL_CONFIG = {
    "model": "bot-latest",
    "system": "You are a helpful assistant. Keep answers short and clear.",
    "options": {"temperature": 0, "seed": 42, "top_p": 0.9, "num_predict": 512},
    "format": None,
}

# name: (layer, provider-state change, our-config change)
CHANGES = {
    "none":               ("none",           {}, {}),
    "model_swap":         ("model",          {"route": "new"}, {}),
    "serving_quant":      ("serving",        {"route": "q8"}, {}),
    "serving_context":    ("serving",        {"num_ctx": 512}, {}),
    "serving_slow":       ("serving",        {"slow": True}, {}),
    "safety_policy":      ("safety",         {"safety": True}, {}),
    "config_temperature": ("request_config", {}, {"options": {"temperature": 0.9}}),
    "config_max_tokens":  ("request_config", {}, {"options": {"num_predict": 8}}),
    "config_system":      ("request_config", {}, {"system": "You are a pirate. Always talk like a pirate."}),
}


def _routes():
    try:
        return json.loads(config.ROUTES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _name(spec):
    return spec.split(":", 1)[1] if spec else "?"


def describe(name):
    rt = _routes() if config.MODE == "real" else {}
    prod = _name(rt.get("base") or rt.get("production")) if rt else "Qwen (simulated)"
    new = _name(rt.get("new")) if rt else "GPT-OSS (simulated)"
    q8 = _name(rt.get("q8")) if rt else "q8 build (simulated)"
    on_ollama = (rt.get("production") or "ollama:").startswith("ollama:")
    return {
        "model_swap": f"alias bot-latest silently moved {prod} -> {new}",
        "serving_quant": f"same model, different build: {prod} -> {q8}",
        "serving_context": "server context window 4096 -> 512 tokens"
                           + ("" if on_ollama or not rt else " (simulated by the wrapper on a cloud API)"),
        "serving_slow": "model moved to weaker hardware (1 CPU thread, no GPU)",
        "safety_policy": "provider adds a stricter safety policy (refuses homework & borderline topics)",
        "config_temperature": "our temperature 0 -> 0.9",
        "config_max_tokens": "our max tokens 512 -> 8",
        "config_system": "our system prompt changed (pirate)",
    }.get(name, "nothing changed")


def effect(name):
    """Plain-language: where the change acts, and what you will notice."""
    rt = _routes() if config.MODE == "real" else {}
    prod = _name(rt.get("production")) if rt else "the simulated model"
    new = _name(rt.get("new")) if rt else "another simulated model"
    where = "Ollama on your laptop" if (rt.get("production") or "ollama:").startswith("ollama:") else "Groq"
    if _server_mode():
        base, srv = _name(rt.get("base")), "the Ollama server"
        return {
            "model_swap": f"On {srv}, 'bot-latest' is now built from {new} instead of {base}"
                          + (" (on Groq)." if (rt.get("new") or "").startswith("groq:") else ".")
                          + " The app still asks for 'bot-latest'. Ask the chatbot 'who are you?'.",
            "serving_quant": f"On {srv}, 'bot-latest' is now built from the q8 build of {base}.",
            "serving_context": f"On {srv}, 'bot-latest' now has PARAMETER num_ctx 512 - long inputs lose their beginning.",
            "serving_slow": f"On {srv}, 'bot-latest' now has PARAMETER num_thread 1 and num_gpu 0 - everything is slower.",
            "safety_policy": f"On {srv}, 'bot-latest' now has a strict SYSTEM policy. Ask the chatbot 'Solve 2x + 3 = 11'.",
            "config_temperature": "OUR app now asks for temperature 0.9.",
            "config_max_tokens": "OUR app now limits answers to 8 tokens.",
            "config_system": "OUR app now sends its own pirate system prompt (it overrides the server's instructions).",
        }.get(name, "")
    swap_to = f"{new} on Groq" if rt else "a different simulated model (GPT-OSS-like)"
    return {
        "model_swap": f"Every request for bot-latest (your chats AND the canaries) now goes to {swap_to} instead of "
                      f"{prod}. The app still says 'bot-latest'. Try asking the chatbot 'who are you?'.",
        "serving_quant": f"Requests now go to a different build of {prod} (q8 instead of q4) on {where}. Same model, different numbers inside.",
        "serving_context": f"The provider now tells {where} to read at most 512 tokens per request (num_ctx=512). "
                           "Long messages lose their beginning. Short chats look normal.",
        "serving_slow": f"The provider now tells {where} to run {prod} on 1 CPU thread with no GPU "
                        "(num_thread=1, num_gpu=0). EVERYTHING becomes several times slower - including the chatbot. "
                        "Click Reset all when you are done.",
        "safety_policy": "The provider now silently adds a strict safety policy in front of every request. "
                         "Ask the chatbot 'Solve 2x + 3 = 11' - it will refuse.",
        "config_temperature": "OUR app config (backend/app_config.json) now asks for temperature 0.9 - answers vary more.",
        "config_max_tokens": "OUR app config now limits answers to 8 tokens - chatbot replies get cut off.",
        "config_system": "OUR app config now has a pirate system prompt - the chatbot talks like a pirate.",
    }.get(name, "")


def available():
    """Changes that are actually possible with the models you have."""
    names = [k for k in CHANGES if k != "none"]
    if config.MODE != "real":
        return names
    rt = _routes()
    if not rt.get("new"):
        names.remove("model_swap")
    if not rt.get("q8"):
        names.remove("serving_quant")
    if not (rt.get("production") or "").startswith("ollama:"):
        names.remove("serving_slow")          # only a local Ollama can be given fewer CPU threads
    return names


def _save(path, data):
    path.write_text(json.dumps(data, indent=2))


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


ACTIVE_FILE = config.HIDDEN / "active_change.json"


def _server_mode():
    """True when bot-latest is a real model ON the Ollama server (real mode with Ollama)."""
    return config.MODE == "real" and bool(_routes().get("alias"))


def normal_config():
    cfg = copy.deepcopy(NORMAL_CONFIG)
    if _server_mode():
        cfg["system"] = None     # the provider's instructions live in the Ollama model itself
    return cfg


def _tag(spec):
    return spec.split(":", 1)[1]


def _build(base=None, system=None, parameters=None):
    """Redefine bot-latest on the Ollama server (a REAL server-side change)."""
    import backends
    rt = _routes()
    backends.create_alias(_tag(rt["alias"]), _tag(base or rt["base"]),
                          system if system is not None else config.SERVER_SYSTEM, parameters)


def reset():
    _save(config.PROVIDER_STATE, NORMAL_STATE)
    _save(config.APP_CONFIG, normal_config())
    if _server_mode():
        _build()                                    # bot-latest = base model + normal instructions
    _save(ACTIVE_FILE, {"active": "none"})


def apply(name, trial=None):
    if name != "none" and name not in available():
        raise ValueError(f"change '{name}' is not available with your current models")
    layer, st, cfg = CHANGES[name]
    reset()
    rt = _routes()
    if _server_mode() and name in ("model_swap", "serving_quant", "serving_context", "serving_slow", "safety_policy"):
        # make the change ON the Ollama server - SilentShift's provider just forwards requests
        if name == "model_swap" and rt["new"].startswith("ollama:"):
            _build(base=rt["new"])
        elif name == "model_swap":
            _save(config.PROVIDER_STATE, {**NORMAL_STATE, **st})     # cloud model: the wrapper reroutes
        elif name == "serving_quant":
            _build(base=rt["q8"])
        elif name == "serving_context":
            _build(parameters={"num_ctx": 512})
        elif name == "serving_slow":
            _build(parameters={"num_thread": 1, "num_gpu": 0})
        elif name == "safety_policy":
            _build(system=config.SERVER_SYSTEM + "\n\n" + config.SAFETY_POLICY)
    else:
        _save(config.PROVIDER_STATE, {**NORMAL_STATE, **st})
    _save(config.APP_CONFIG, _merge(normal_config(), cfg))
    _save(ACTIVE_FILE, {"active": name})
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": trial,
             "change": name, "layer": layer, "description": describe(name), "target": _target(name, rt)}
    log_change(entry)
    return entry


def _target(name, rt):
    """Ground truth for the identification score: which model is really behind the name now."""
    if name == "model_swap":
        return rt.get("new") if config.MODE == "real" else "sim:new"
    if name == "serving_quant":
        return rt.get("q8") if config.MODE == "real" else "sim:q8"
    return None


def log_change(entry):
    try:
        log = json.loads(config.CHANGE_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        log = []
    log.append(entry)
    _save(config.CHANGE_LOG, log[-500:])


def custom(base=None, system=None, parameters=None, note=""):
    """Provider console: redefine bot-latest on the Ollama server with ANY base model, instructions
    and server parameters - like typing `ollama create bot-latest -f Modelfile` yourself."""
    if not _server_mode():
        raise ValueError("The server console needs real mode with Ollama (bot-latest must live on the Ollama server).")
    rt = _routes()
    base = base or _tag(rt["base"])
    params = {k: v for k, v in (parameters or {}).items() if v not in (None, "")}
    for k in list(params):
        if k not in ("num_ctx", "num_thread", "num_gpu", "temperature", "top_p", "top_k", "repeat_penalty", "num_predict", "seed"):
            raise ValueError(f"unsupported PARAMETER {k}")
        params[k] = float(params[k]) if k in ("temperature", "top_p", "repeat_penalty") else int(params[k])
    _save(config.PROVIDER_STATE, NORMAL_STATE)
    _save(config.APP_CONFIG, normal_config())
    _build(base="ollama:" + base, system=system if system else None, parameters=params or None)
    # ground truth for the scorer / timeline (the monitor never reads it)
    import fingerprints
    base_now = _tag(rt["base"])
    if fingerprints.core("ollama:" + base) != fingerprints.core("ollama:" + base_now):
        layer = "model"
    elif base != base_now or params:
        layer = "serving"
    elif system and system.strip() != config.SERVER_SYSTEM.strip():
        layer = "safety"
    else:
        layer = "none"
    desc = f"FROM {base}" + (f" · SYSTEM {len(system)} chars" if system and system.strip() != config.SERVER_SYSTEM.strip() else "") \
        + "".join(f" · {k}={v}" for k, v in params.items())
    _save(ACTIVE_FILE, {"active": "custom" if layer != "none" else "none", "effect": "Server console: bot-latest = " + desc})
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": None, "change": "console", "layer": layer,
             "description": desc + (f" ({note})" if note else ""), "target": "ollama:" + base}
    log_change(entry)
    return entry


def current():
    """For the demo-operator panel only (the monitor never calls this)."""
    try:
        d = json.loads(ACTIVE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    name = d.get("active", "none")
    if name == "none":
        return {"active": "none"}
    return {"active": name, "effect": d.get("effect") or effect(name)}


def list_changes():
    return [{"name": k, "layer": CHANGES[k][0], "description": describe(k)} for k in available()]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for c in list_changes():
            print(f"  {c['name']:20s} {c['layer']:15s} {c['description']}")
    elif cmd == "reset":
        reset(); print("Back to normal.")
    elif cmd == "apply":
        print(apply(sys.argv[2]))
