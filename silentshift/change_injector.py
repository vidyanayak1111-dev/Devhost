"""
change_injector.py - makes CONTROLLED CHANGES and writes them to change_log.json.

This plays the role of Friend 1 (the provider). The agent NEVER reads
change_log.json; only score.py reads it, to check the agent's answers.

Usage:
    python change_injector.py list
    python change_injector.py apply safety_on
    python change_injector.py reset

MODE = "mock" -> changes mock_ollama.py via mock_state.json (all changes work)
MODE = "real" -> changes real Ollama with `ollama cp` (model, quant, config changes).
                 Context window and safety need Friend 1's provider wrapper.
"""

import json
import subprocess
import sys
import time

MODE = "mock"
LOG_FILE = "change_log.json"
STATE_FILE = "mock_state.json"
CONFIG_FILE = "app_config.json"

NORMAL_STATE = {"model": "llama", "quant": "q4", "safety": False, "num_ctx": 4096}
NORMAL_CONFIG = {
    "model": "bot-latest",
    "system": "You are a helpful assistant.",
    "options": {"temperature": 0, "seed": 42, "top_p": 0.9, "num_predict": 200},
    "format": None,
}
REAL_MODELS = {"llama": "llama3.2:3b", "qwen": "qwen2.5:3b", "llama_q8": "llama3.2:3b-instruct-q8_0"}

# name: (layer, description, what to change)
CHANGES = {
    "none":          ("none",           "nothing changed",                     {}),
    "alias_qwen":    ("model",          "alias bot-latest moved Llama -> Qwen", {"state": {"model": "qwen"}}),
    "quant_q8":      ("serving",        "quantisation q4 -> q8",               {"state": {"quant": "q8"}}),
    "ctx_1024":      ("serving",        "context window 4096 -> 1024",         {"state": {"num_ctx": 1024}}),
    "safety_on":     ("safety",         "safety filter (Llama Guard) on",      {"state": {"safety": True}}),
    "temp_09":       ("request_config", "temperature 0 -> 0.9",                {"options": {"temperature": 0.9}}),
    "short_answers": ("request_config", "num_predict 200 -> 8",                {"options": {"num_predict": 8}}),
    "new_system":    ("request_config", "system prompt changed",               {"config": {"system": "You are a pirate. Always talk like a pirate."}}),
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


def ollama_alias(model_key):
    subprocess.run(["ollama", "cp", REAL_MODELS[model_key], "bot-latest"], check=True)


def reset():
    save(STATE_FILE, NORMAL_STATE)
    save(CONFIG_FILE, NORMAL_CONFIG)
    if MODE == "real":
        ollama_alias("llama")


def apply(name, trial=None):
    layer, desc, what = CHANGES[name]
    reset()
    state = {**NORMAL_STATE, **what.get("state", {})}
    config = json.loads(json.dumps(NORMAL_CONFIG))
    config.update(what.get("config", {}))
    config["options"].update(what.get("options", {}))

    if MODE == "mock":
        save(STATE_FILE, state)
    else:
        if state["model"] == "qwen":
            ollama_alias("qwen")
        elif state["quant"] == "q8":
            ollama_alias("llama_q8")
        elif name in ("ctx_1024", "safety_on"):
            raise SystemExit(f"'{name}' needs the provider wrapper in real mode")
    save(CONFIG_FILE, config)

    log({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": trial,
         "change": name, "layer": layer, "description": desc})
    print(f"Applied controlled change: {name} ({layer}: {desc})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for k, (layer, desc, _) in CHANGES.items():
            print(f"  {k:14s} {layer:15s} {desc}")
    elif cmd == "reset":
        reset()
        print("Everything back to normal.")
    elif cmd == "apply":
        apply(sys.argv[2])
