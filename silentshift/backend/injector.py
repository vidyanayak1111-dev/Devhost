"""
injector.py - makes CONTROLLED CHANGES for the demo and writes the truth to
data/hidden/change_log.json. The monitor never reads that file; only score.py does.

    python injector.py list
    python injector.py apply safety_on
    python injector.py reset
"""

import copy
import json
import sys
import time

import config

NORMAL_STATE = {"alias": "llama", "quant": "q4", "num_ctx": 4096, "safety": False}
NORMAL_CONFIG = {
    "model": "bot-latest",
    "system": "You are a helpful assistant.",
    "options": {"temperature": 0, "seed": 42, "top_p": 0.9, "num_predict": 1024},
    "format": None,
}

# name: (layer, description, provider-state changes, app-config changes)
CHANGES = {
    "none":          ("none",           "nothing changed",                          {}, {}),
    "alias_qwen":    ("model",          "alias bot-latest silently moved Llama -> Qwen", {"alias": "qwen"}, {}),
    "quant_q8":      ("serving",        "served build switched q4 -> q8 quantisation", {"quant": "q8"}, {}),
    "ctx_1024":      ("serving",        "server context window 4096 -> 1024",       {"num_ctx": 1024}, {}),
    "safety_on":     ("safety",         "provider safety filter switched on",       {"safety": True}, {}),
    "temp_09":       ("request_config", "our temperature 0 -> 0.9",                 {}, {"options": {"temperature": 0.9}}),
    "short_answers": ("request_config", "our num_predict 1024 -> 8",                 {}, {"options": {"num_predict": 8}}),
    "new_system":    ("request_config", "our system prompt changed",                {},
                      {"system": "You are a pirate. Always talk like a pirate."}),
}


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


def _log(entry):
    try:
        data = json.loads(config.CHANGE_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = []
    data.append(entry)
    _save(config.CHANGE_LOG, data)


def reset():
    _save(config.PROVIDER_STATE, NORMAL_STATE)
    _save(config.APP_CONFIG, NORMAL_CONFIG)


def apply(name, trial=None):
    if name != "none" and name not in available():
        raise ValueError(f"change '{name}' is not available in {config.PROVIDER_MODE} mode")
    layer, desc, state, cfg = CHANGES[name]
    reset()
    _save(config.PROVIDER_STATE, {**NORMAL_STATE, **state})
    _save(config.APP_CONFIG, _merge(NORMAL_CONFIG, cfg))
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": trial,
             "change": name, "layer": layer, "description": desc}
    _log(entry)
    return entry


def current():
    """For the demo operator panel only (never used by the monitor)."""
    try:
        log = json.loads(config.CHANGE_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        log = []
    try:
        st = json.loads(config.PROVIDER_STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        st = NORMAL_STATE
    try:
        cfg = json.loads(config.APP_CONFIG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        cfg = NORMAL_CONFIG
    active = "none" if (st == NORMAL_STATE and cfg == NORMAL_CONFIG) else (log[-1]["change"] if log else "unknown")
    return {"active": active, "provider_state": st}


def available():
    """Changes that can actually happen in the current provider mode."""
    names = [k for k in CHANGES if k != "none"]
    if config.PROVIDER_MODE == "api":
        if not config.API_MODEL_NEW:
            names.remove("alias_qwen")
        if not config.API_MODEL_SERVING:
            names.remove("quant_q8")
    return names


def list_changes():
    return [{"name": k, "layer": CHANGES[k][0], "description": CHANGES[k][1]} for k in available()]


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for c in list_changes():
            print(f"  {c['name']:14s} {c['layer']:15s} {c['description']}")
    elif cmd == "reset":
        reset()
        print("Everything back to normal.")
    elif cmd == "apply":
        print("Applied:", apply(sys.argv[2]))