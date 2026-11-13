"""
proxy.py - a "fake Ollama" that sits between the agent and the server.

Agent  ->  this proxy (port 8000)  ->  server (REAL_OLLAMA)

It forwards every request unchanged. On the way back it records ONLY
metadata (never user text): model, request settings hash, speed, token counts.

Run:
    pip install fastapi uvicorn httpx
    python proxy.py
"""

import hashlib
import json
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

import os
REAL_OLLAMA = os.environ.get("REAL_OLLAMA", "http://127.0.0.1:11434").strip().rstrip("/")
LOG_FILE = "metadata_log.jsonl"

app = FastAPI()
# Allow browser-based chatbots (on another address) to call this server
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
client = httpx.AsyncClient(base_url=REAL_OLLAMA, timeout=None)


def settings_hash(body: dict) -> str:
    """Hash of request settings only (no messages) -> detects config changes."""
    settings = {
        "model": body.get("model"),
        "options": body.get("options", {}),
        "format": body.get("format"),
        "keep_alive": body.get("keep_alive"),
    }
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:12]


def save_metadata(path: str, body: dict, final: dict, started: float):
    """Write one line of metadata. No prompts, no answers are stored."""
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "endpoint": path,
        "model_requested": body.get("model"),
        "model_returned": final.get("model"),
        "settings_hash": settings_hash(body),
        "prompt_tokens": final.get("prompt_eval_count"),
        "output_tokens": final.get("eval_count"),
        "total_seconds": round(time.time() - started, 3),
        "tokens_per_sec": (
            round(final["eval_count"] / (final["eval_duration"] / 1e9), 2)
            if final.get("eval_count") and final.get("eval_duration")
            else None
        ),
        "done_reason": final.get("done_reason"),  # "length" = answer was cut off
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
    print("logged:", record)


def cannot_reach(e):
    msg = (f"proxy cannot reach the server at REAL_OLLAMA={REAL_OLLAMA} ({type(e).__name__}: {e}). "
           "Check that provider.py and cloudflared are running and that REAL_OLLAMA is the right link.")
    print("ERROR:", msg)
    return JSONResponse({"error": msg}, status_code=502)


@app.api_route("/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "HEAD"])
async def forward(path: str, request: Request):
    raw = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}

    # Only chat/generate calls are monitored; everything else passes straight through
    if path not in ("api/chat", "api/generate"):
        try:
            r = await client.request(request.method, "/" + path, content=raw,
                                     headers=headers, params=request.query_params)
        except httpx.HTTPError as e:
            return cannot_reach(e)
        return Response(r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type"))

    body = json.loads(raw or b"{}")
    started = time.time()
    req = client.build_request(request.method, "/" + path, content=raw, headers=headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        return cannot_reach(e)

    async def stream_back():
        last = {}
        buffer = b""
        async for chunk in upstream.aiter_bytes():
            yield chunk                      # send back immediately
            buffer += chunk
            while b"\n" in buffer:           # Ollama streams one JSON per line
                line, buffer = buffer.split(b"\n", 1)
                if line.strip():
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        pass
        if buffer.strip():                   # non-streaming reply (single JSON)
            try:
                last = json.loads(buffer)
            except json.JSONDecodeError:
                pass
        await upstream.aclose()
        save_metadata(path, body, last, started)

    return StreamingResponse(stream_back(), status_code=upstream.status_code,
                             media_type=upstream.headers.get("content-type"))


if __name__ == "__main__":
    print(f"Proxy forwarding to REAL_OLLAMA = {REAL_OLLAMA}")
    uvicorn.run(app, host="0.0.0.0", port=8000)