"""
change_injector.py - makes CONTROLLED CHANGES and writes them to change_log.json.

This plays the role of the provider team. The agent NEVER reads
change_log.json; only score.py reads it, to check the agent's answers.

Usage (PowerShell):
    python change_injector.py list
    python change_injector.py apply swap_distilled
    python change_injector.py reset

Mode   ($env:SILENTSHIFT_MODE)  : "mock" -> mock_ollama.py,  "real" -> provider.py + real Ollama
Family ($env:SILENTSHIFT_FAMILY): "large" (qwen2.5:7b family) or "light" (qwen2.5:3b family), see models.json

The idea (mentor feedback): ONE large model sits behind "bot-latest". The provider
silently swaps it for a cheaper version of ITSELF (smaller, distilled, quantised)
or changes serving / safety. The app keeps sending the same name.
"""

import json
import os
import sys
import time

MODE = os.environ.get("SILENTSHIFT_MODE", "mock")
FAMILY_NAME = os.environ.get("SILENTSHIFT_FAMILY", "large")
LOG_FILE = "change_log.json"
STATE_FILE = "mock_state.json" if MODE == "mock" else "provider_state.json"
CONFIG_FILE = "app_config.json"

with open("models.json") as f:
    FAMILY = json.load(f)[FAMILY_NAME]

NORMAL_STATE = {"alias_target": FAMILY["normal"], "num_ctx": None, "safety": False, "cpu_only": False}
NORMAL_CONFIG = {
    "model": "bot-latest",
    "system": "You are a helpful assistant.",
    "options": {"temperature": 0, "seed": 42, "top_p": 0.9, "num_predict": 400},
    "format": None,
}

# name: (layer, description, what to change)
CHANGES = {
    "none":               ("none",           "nothing changed", {}),
    # model layer: the weights behind the alias change
    "swap_smaller":       ("model",          f"alias moved to smaller model {FAMILY['smaller']}",
                           {"state": {"alias_target": FAMILY["smaller"]}}),
    "swap_distilled":     ("model",          f"alias moved to distilled model {FAMILY['distilled']}",
                           {"state": {"alias_target": FAMILY["distilled"]}}),
    "quant_low":          ("model",          f"alias moved to low-precision quantised {FAMILY['quant_low']}",
                           {"state": {"alias_target": FAMILY["quant_low"]}}),
    "quant_high":         ("model",          f"alias moved to high-precision quantised {FAMILY['quant_high']}",
                           {"state": {"alias_target": FAMILY["quant_high"]}}),
    "swap_other_family":  ("model",          f"alias moved to a different family {FAMILY['other_family']}",
                           {"state": {"alias_target": FAMILY["other_family"]}}),
    # serving layer: same weights, served differently
    "ctx_1024":           ("serving",        "context window reduced to 1024 tokens", {"state": {"num_ctx": 1024}}),
    "cpu_only":           ("serving",        "inference moved from GPU to CPU only", {"state": {"cpu_only": True}}),
    # safety layer
    "safety_on":          ("safety",         "safety filter (Llama Guard + strict policy) on", {"state": {"safety": True}}),
    # request configuration (our own app settings)
    "temp_09":            ("request_config", "temperature 0 -> 0.9", {"options": {"temperature": 0.9}}),
    "short_answers":      ("request_config", "num_predict 400 -> 8", {"options": {"num_predict": 8}}),
    "new_system":         ("request_config", "system prompt changed",
                           {"config": {"system": "You are a pirate. Always talk like a pirate."}}),
}


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def log(entry):
    try:
        with open(LOG_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = []
    data.append(entry)
    save(LOG_FILE, data)


def reset():
    save(STATE_FILE, NORMAL_STATE)
    save(CONFIG_FILE, NORMAL_CONFIG)


def apply(name, trial=None):
    layer, desc, what = CHANGES[name]
    reset()
    state = {**NORMAL_STATE, **what.get("state", {})}
    config = json.loads(json.dumps(NORMAL_CONFIG))
    config.update(what.get("config", {}))
    config["options"].update(what.get("options", {}))
    save(STATE_FILE, state)
    save(CONFIG_FILE, config)
    log({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": trial,
         "change": name, "layer": layer, "description": desc})
    print(f"Applied controlled change: {name} ({layer}: {desc})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        print(f"Mode: {MODE}   Family: {FAMILY_NAME}   Normal model behind bot-latest: {FAMILY['normal']}\n")
        for k, (layer, desc, _) in CHANGES.items():
            print(f"  {k:18s} {layer:15s} {desc}")
    elif cmd == "reset":
        reset()
        print("Everything back to normal.")
    elif cmd == "apply":
        apply(sys.argv[2])