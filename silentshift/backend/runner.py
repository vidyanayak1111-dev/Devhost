"""
runner.py - asks the synthetic canary questions and measures the answers.

The canaries go through OUR gateway (/probe/chat on server.py) using
OUR request config (app_config.json) - the same path and settings real users
get. They are synthetic, so storing their answers is fine; real user text is
never stored.
"""

import json
import time

import httpx

import config
from gateway import REFUSAL_WORDS, settings_hash


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


class ProbeError(RuntimeError):
    pass


def ask(client, cfg, prompt):
    body = build_request(cfg, [{"role": "user", "content": prompt}])
    # Canaries are short questions: cap their answer length so a check stays cheap.
    # min() keeps a SMALLER configured limit (e.g. the short_answers change) visible to the monitor.
    limit = body["options"].get("num_predict")
    body["options"]["num_predict"] = min(limit, config.PROBE_MAX_TOKENS) if limit else config.PROBE_MAX_TOKENS
    start = time.time()
    r = client.post(config.SELF_URL + "/probe/chat", json=body,
                    headers={config.PROBE_HEADER: "1"}, timeout=600)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        raise ProbeError(f"Canary call failed ({r.status_code}): {str(detail)[:300]}")
    data = r.json()
    data["_seconds"] = time.time() - start
    return data


def _norm(t):
    return " ".join(t.lower().replace("mg", " mg").split())


def measure(canary, data):
    """Turn one answer into measurements. Each canary kind checks what a real user would notice."""
    text = (data.get("message") or {}).get("content", "").strip()
    low = text.lower()
    kind = canary["kind"]
    refused = any(w in low[:300] for w in REFUSAL_WORDS)
    valid_json = fenced = None
    if kind == "json":                       # a parser needs the RAW text to be JSON
        try:
            obj = json.loads(text)
            valid_json = all(k in obj for k in canary.get("keys", [])) if isinstance(obj, dict) else True
        except Exception:
            valid_json = False
        fenced = text.startswith("```")
    correct = None
    if kind == "exact":
        correct = canary["expected"].lower() in low
    elif kind == "long":
        correct = canary["needle"] in text
    elif kind == "medical":                  # did the summary keep every dose?
        correct = all(_norm(d) in _norm(text) for d in canary["must_contain"])
    elif kind == "homework":                 # answered (not refused) and right
        correct = (not refused) and canary["expected"] in text
    speed = None
    if data.get("eval_count") and data.get("eval_duration"):
        speed = data["eval_count"] / (data["eval_duration"] / 1e9)
    return {
        "canary_id": canary["id"], "kind": kind, "text": text,
        "refused": refused, "valid_json": valid_json, "fenced": fenced, "correct": correct,
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
