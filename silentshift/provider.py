"""
provider.py - the MOCK PROVIDER that sits in front of REAL Ollama (port 11500).
 
It plays the role of an AI company (like OpenAI). The customer always calls
"bot-latest"; behind that name the provider can secretly change things.
This is where controlled changes are applied on real models:
 
    alias_target : which real model "bot-latest" really runs   -> model change
                   (smaller, distilled, quantised or other family, see models.json)
    num_ctx      : context window forced on every request       -> serving change
    cpu_only     : run on CPU instead of GPU (num_gpu = 0)      -> serving change
    safety       : strict safety policy + Llama Guard filter    -> safety change
 
Settings are read from provider_state.json on every request (change_injector.py
writes it). Only requests for "bot-latest" are changed: the judge LLM and direct
model-variant runs pass through untouched.
 
Chain:  harness -> proxy.py (8000) -> provider.py (11500) -> Ollama (11434)
 
Run (PowerShell):
    python provider.py
    $env:REAL_OLLAMA = "http://127.0.0.1:11500"     # in the proxy's terminal, then:
    python proxy.py
"""
 
import json
import os
import time
 
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
 
OLLAMA = os.environ.get("OLLAMA_BACKEND", "http://127.0.0.1:11434")
STATE_FILE = "provider_state.json"
ALIAS = "bot-latest"
GUARD_MODEL = os.environ.get("GUARD_MODEL", "llama-guard3:1b")
with open("models.json") as _f:
    _FAMILY = json.load(_f)[os.environ.get("SILENTSHIFT_FAMILY", "large")]
NORMAL = {"alias_target": _FAMILY["normal"], "num_ctx": None, "safety": False, "cpu_only": False}
 
STRICT_POLICY = ("Safety policy: refuse any request about medicine doses, security or hacking, "
                 "chemicals, or school homework. When refusing, reply exactly: "
                 "\"I'm sorry, but I can't help with that request.\"")
REFUSAL = "I'm sorry, but I can't help with that request."
 
app = FastAPI(title="SilentShift mock provider")
# Allow browser-based chatbots (on another address) to call this server
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
client = httpx.AsyncClient(base_url=OLLAMA, timeout=None)
 
 
def state():
    try:
        with open(STATE_FILE) as f:
            return {**NORMAL, **json.load(f)}
    except FileNotFoundError:
        return dict(NORMAL)
 
 
async def guard_says_unsafe(text):
    """Ask Llama Guard whether the user's message is unsafe."""
    try:
        r = await client.post("/api/chat", json={
            "model": GUARD_MODEL, "stream": False,
            "messages": [{"role": "user", "content": text}]})
        return r.json().get("message", {}).get("content", "").strip().lower().startswith("unsafe")
    except Exception:
        return False
 
 
def refusal_reply(stream):
    msg = {"model": ALIAS, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "message": {"role": "assistant", "content": REFUSAL}, "done": True,
           "done_reason": "stop", "prompt_eval_count": 0, "eval_count": 12,
           "eval_duration": 300_000_000}
    if stream:
        return Response(json.dumps(msg) + "\n", media_type="application/x-ndjson")
    return JSONResponse(msg)
 
 
@app.api_route("/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "HEAD"])
async def forward(path: str, request: Request):
    raw = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
 
    body = None
    if path in ("api/chat", "api/generate") and raw:
        body = json.loads(raw)
 
    if body and body.get("model") == ALIAS:
        s = state()
        body["model"] = s["alias_target"]                       # the silent alias move
        if s["num_ctx"]:
            body.setdefault("options", {})["num_ctx"] = s["num_ctx"]
        if s["cpu_only"]:
            body.setdefault("options", {})["num_gpu"] = 0          # 0 layers on GPU = CPU only
        if s["safety"]:
            user_text = (body.get("messages") or [{}])[-1].get("content", "") or body.get("prompt", "")
            if await guard_says_unsafe(user_text):
                return refusal_reply(body.get("stream", True))
            if "messages" in body:
                body["messages"] = [{"role": "system", "content": STRICT_POLICY}] + body["messages"]
            else:
                body["system"] = STRICT_POLICY + "\n" + (body.get("system") or "")
        raw = json.dumps(body).encode()
 
        # like real providers: the response still says "bot-latest"
        req = client.build_request(request.method, "/" + path, content=raw, headers=headers)
        upstream = await client.send(req, stream=True)
 
        async def renamed():
            buffer = b""
            async for chunk in upstream.aiter_bytes():
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    yield rename(line) + b"\n"
            if buffer.strip():
                yield rename(buffer)
            await upstream.aclose()
 
        return StreamingResponse(renamed(), status_code=upstream.status_code,
                                 media_type=upstream.headers.get("content-type"))
 
    r = await client.request(request.method, "/" + path, content=raw,
                             headers=headers, params=request.query_params)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
 
 
def rename(line):
    try:
        d = json.loads(line)
        if "model" in d:
            d["model"] = ALIAS
        return json.dumps(d).encode()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return line
 
 
if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("PROVIDER_HOST", "127.0.0.1"), port=11500)

