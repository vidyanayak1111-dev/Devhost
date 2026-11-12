"""
runner.py - asks the AI all the fake test questions (canaries) and measures the answers.

It talks to the AI only through the proxy (port 8000), like a real customer.
It never looks inside Ollama.
"""

import hashlib
import json
import time

import httpx

PROXY = "http://localhost:8000"
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
    messages = []
    if cfg.get("system"):
        messages.append({"role": "system", "content": cfg["system"]})
    messages.append({"role": "user", "content": prompt})
    body = {"model": cfg["model"], "messages": messages,
            "stream": False, "options": cfg.get("options", {})}
    if cfg.get("format"):
        body["format"] = cfg["format"]

    start = time.time()
    r = httpx.post(PROXY + "/api/chat", json=body, timeout=600)
    r.raise_for_status()
    data = r.json()
    data["_seconds"] = time.time() - start
    return data


def measure(canary, data):
    """Turn one answer into simple numbers / yes-no checks."""
    text = data.get("message", {}).get("content", "").strip()
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

    speed = None
    if data.get("eval_count") and data.get("eval_duration"):
        speed = data["eval_count"] / (data["eval_duration"] / 1e9)

    return {
        "canary_id": canary["id"],
        "kind": canary["kind"],
        "text": text,   # OK to keep: canaries are fake questions, not user data
        "refused": any(w in low for w in REFUSAL_WORDS),
        "valid_json": valid_json,
        "correct": correct,
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "tokens_per_sec": speed,
        "seconds": data["_seconds"],
        "done_reason": data.get("done_reason"),
        "model_returned": data.get("model"),
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
