"""
runner.py - asks the AI all the fake test questions (canaries) and measures the answers.

It talks to the AI only through the harness (Ollama goes via the proxy on
port 8000), like a real customer. It never looks inside Ollama.
"""

import hashlib
import json
import time

from harness import generate

REPEATS = 3          # ask every question 3 times per round

REFUSAL_WORDS = [
    "i can't", "i cannot", "i'm sorry", "i am sorry", "sorry, i", "i'm unable",
    "i am unable", "i won't", "can't help", "cannot help", "not able to help",
    "can't assist", "cannot assist",
]


def load_json(path):
    with open(path) as f:
        return json.load(f)


def settings_hash(cfg):
    """Fingerprint of OUR settings. If this changes, WE changed something."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def build_prompt(canary):
    if canary["kind"] == "long":
        return (canary["prompt_start"] + " "
                + canary["filler"] * canary["filler_repeat"]
                + canary["prompt_end"])
    return canary["prompt"]


def ask(cfg, prompt):
    """Send one question using our own request configuration."""
    return generate(provider=cfg.get("provider", "ollama"), model=cfg["model"],
                    prompt=prompt, system=cfg.get("system"),
                    options=cfg.get("options", {}), fmt=cfg.get("format"))


def measure(canary, data):
    """Turn one answer into simple numbers / yes-no checks."""
    text = data["text"]
    low = text.lower()

    valid_json = None
    if canary["kind"] == "json":
        try:
            json.loads(text)
            valid_json = True
        except Exception:
            valid_json = False

    correct = None
    if canary["kind"] == "exact":
        correct = canary["expected"].lower() in low
    if canary["kind"] == "long":
        correct = canary["needle"] in text

    return {
        "canary_id": canary["id"],
        "kind": canary["kind"],
        "text": text,   # OK to keep: canaries are fake questions, not user data
        "refused": any(w in low for w in REFUSAL_WORDS),
        "valid_json": valid_json,
        "correct": correct,
        "prompt_tokens": data["prompt_tokens"],
        "output_tokens": data["output_tokens"],
        "tokens_per_sec": data["tokens_per_sec"],
        "seconds": data["seconds"],
        "done_reason": data["done_reason"],
        "model_returned": data["model_returned"],
    }


def run_round(canaries_path="canaries.json", config_path="app_config.json"):
    cfg = load_json(config_path)
    canaries = load_json(canaries_path)
    results = []
    for canary in canaries:
        prompt = build_prompt(canary)
        for i in range(REPEATS):
            data = ask(cfg, prompt)
            m = measure(canary, data)
            m["repeat"] = i
            results.append(m)
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings_hash": settings_hash(cfg),
        "results": results,
    }


if __name__ == "__main__":
    rnd = run_round()
    print(json.dumps(rnd, indent=2)[:2000])
