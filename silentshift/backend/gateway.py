"""
gateway.py - the monitored path between our app and the provider.

BOTH the chatbot's real traffic and the monitor's canary probes go through
`forward_chat`, so the canaries see exactly the same serving path as users.

Privacy rule: for every call we write ONE metadata line - model name,
a hash of the request SETTINGS (never the messages), token counts, speed,
finish reason. No prompt or answer text is ever written to disk.
"""

import hashlib
import json
import statistics
import threading
import time

import httpx

import config

_client = httpx.AsyncClient(base_url=config.PROVIDER_URL, timeout=600)
_lock = threading.Lock()
MAX_LOG_LINES = 5000


def settings_hash(body: dict) -> str:
    """Fingerprint of request settings only (model, options, format, system prompt hash)."""
    system = next((m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"), "")
    settings = {
        "model": body.get("model"),
        "options": body.get("options") or {},
        "format": body.get("format"),
        # the system prompt is OUR config, not user content; we still only keep a hash
        "system": hashlib.sha256(system.encode()).hexdigest()[:8] if system else None,
    }
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]


def _write_metadata(record: dict):
    with _lock:
        with open(config.METADATA_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
        # simple retention limit so the log stays small
        lines = config.METADATA_LOG.read_text().splitlines()
        if len(lines) > MAX_LOG_LINES:
            config.METADATA_LOG.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n")


async def forward_chat(body: dict, source: str) -> dict:
    """Send one chat request to the provider and log metadata. source = 'live' | 'probe'."""
    body = {**body, "stream": False}
    started = time.time()
    status, data = 200, {}
    try:
        r = await _client.post("/api/chat", json=body)
        status = r.status_code
        r.raise_for_status()
        data = r.json()
        return data
    finally:
        ev, dur = data.get("eval_count"), data.get("eval_duration")
        _write_metadata({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "status": status,
            "model_requested": body.get("model"),
            "model_returned": data.get("model"),
            "settings_hash": settings_hash(body),
            "n_messages": len(body.get("messages", [])),
            "prompt_tokens": data.get("prompt_eval_count"),
            "output_tokens": ev,
            "tokens_per_sec": round(ev / (dur / 1e9), 2) if ev and dur else None,
            "total_seconds": round(time.time() - started, 3),
            "done_reason": data.get("done_reason"),
        })


def read_metadata(limit=None, source=None):
    try:
        rows = [json.loads(x) for x in config.METADATA_LOG.read_text().splitlines() if x.strip()]
    except FileNotFoundError:
        rows = []
    if source:
        rows = [r for r in rows if r.get("source") == source]
    return rows[-limit:] if limit else rows


def live_summary(since: str | None = None):
    """Passive signals from real user traffic - metadata only."""
    rows = [r for r in read_metadata(source="live") if not since or r["time"] >= since]
    speeds = [r["tokens_per_sec"] for r in rows if r.get("tokens_per_sec")]
    return {
        "requests": len(rows),
        "errors": sum(1 for r in rows if r.get("status", 200) >= 400),
        "settings_hashes": sorted({r["settings_hash"] for r in rows}),
        "models_returned": sorted({r["model_returned"] for r in rows if r.get("model_returned")}),
        "median_speed": round(statistics.median(speeds), 2) if speeds else None,
        "cut_off_rate": (sum(r.get("done_reason") == "length" for r in rows) / len(rows)) if rows else None,
    }