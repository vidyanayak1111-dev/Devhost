"""
runner.py - asks the synthetic canary questions and measures the answers.

The canaries go through OUR gateway (/ollama/api/chat on server.py) using
OUR request config (app_config.json) - the same path and settings real users
get. They are synthetic, so storing their answers is fine; real user text is
never stored.
"""

import json
import time

import httpx

import config
from gateway import settings_hash

REFUSAL_WORDS = [
    "i can't", "i cannot", "i'm sorry", "i am sorry", "sorry, i", "i'm unable",
    "i am unable", "i won't", "can't help", "cannot help", "not able to help",
    "can't assist", "cannot assist", "unable to provide",
]


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_config():
    return load_json(config.APP_CONFIG)


def build_request(cfg: dict, messages: list) -> dict:
    """The ONE place that turns our app config + messages into a provider request."""
    msgs = []
    if cfg.get("system"):
        msgs.append({"role": "system", "content": cfg["system"]})
    msgs += messages
    body = {"model": cfg["model"], "messages": msgs, "stream": False,
            "options": dict(cfg.get("options") or {})}
    if cfg.get("format"):
        body["format"] = cfg["format"]
    return body


def config_hash(cfg: dict) -> str:
    return settings_hash(build_request(cfg, []))


def build_prompt(canary):
    if canary["kind"] == "long":
        return (canary["prompt_start"] + " " + canary["filler"] * canary["filler_repeat"]
                + canary["prompt_end"])
    return canary["prompt"]


def ask(client, cfg, prompt):
    body = build_request(cfg, [{"role": "user", "content": prompt}])
    start = time.time()
    r = client.post(config.SELF_URL + "/ollama/api/chat", json=body,
                    headers={config.PROBE_HEADER: "1"}, timeout=600)
    r.raise_for_status()
    data = r.json()
    data["_seconds"] = time.time() - start
    return data


def measure(canary, data):
    text = (data.get("message") or {}).get("content", "").strip()
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
        "canary_id": canary["id"], "kind": canary["kind"], "text": text,
        "refused": any(w in low for w in REFUSAL_WORDS),
        "valid_json": valid_json, "correct": correct,
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "tokens_per_sec": speed, "seconds": data["_seconds"],
        "done_reason": data.get("done_reason"), "model_returned": data.get("model"),
    }


def run_round(repeats=3, progress=None):
    """One round = every canary asked `repeats` times."""
    cfg = load_config()
    canaries = load_json(config.CANARIES)
    results = []
    total, done = len(canaries) * repeats, 0
    with httpx.Client() as client:
        for canary in canaries:
            prompt = build_prompt(canary)
            for i in range(repeats):
                m = measure(canary, ask(client, cfg, prompt))
                m["repeat"] = i
                results.append(m)
                done += 1
                if progress:
                    progress(done, total)
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "repeats": repeats,
            "settings_hash": config_hash(cfg), "config": cfg, "results": results}