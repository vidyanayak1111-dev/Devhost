"""
server.py - the one app the team runs.

    /                 chat UI (Nova)            -> POST /api/chat
    /monitor.html     monitor dashboard          -> /monitor/*
    /ollama/*         Ollama-compatible proxy (point Open WebUI or any Ollama client here)
    /demo/*           demo-operator controls (plays the provider making silent changes)

Every model call - user chat or canary - goes through gateway.forward_chat,
which records metadata only.
"""

import asyncio
import json
import threading
import time
import traceback
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import agent
import config
import evaluation
import gateway
import injector
from runner import build_request, load_config

app = FastAPI(title="SilentShift")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_provider = httpx.AsyncClient(base_url=config.PROVIDER_URL, timeout=600)

# ======================================================================= jobs
JOB = {"id": None, "kind": None, "state": "idle", "done": 0, "total": 0, "message": "", "result": None,
       "error": None, "started": None, "finished": None}
_job_lock = threading.Lock()
WATCH = {"enabled": False, "minutes": 5, "next_run": None}


def _start_job(kind, fn):
    with _job_lock:
        if JOB["state"] == "running":
            raise HTTPException(409, f"A {JOB['kind']} job is already running")
        JOB.update(id=uuid.uuid4().hex[:8], kind=kind, state="running", done=0, total=0, message="starting",
                   result=None, error=None, started=time.strftime("%H:%M:%S"), finished=None)

    def progress(done, total, msg=None):
        JOB.update(done=done, total=total, message=msg or f"{done}/{total} probe calls")

    def work():
        try:
            JOB["result"] = fn(progress)
            JOB["state"] = "done"
        except Exception as e:
            traceback.print_exc()
            JOB.update(state="error", error=str(e))
        finally:
            JOB["finished"] = time.strftime("%H:%M:%S")

    threading.Thread(target=work, daemon=True).start()
    return dict(JOB)


def _watch_loop():
    while True:
        time.sleep(5)
        if not WATCH["enabled"] or not agent.load_baseline():
            continue
        if WATCH["next_run"] and time.time() < WATCH["next_run"]:
            continue
        WATCH["next_run"] = time.time() + WATCH["minutes"] * 60
        if JOB["state"] != "running":
            try:
                _start_job("check", lambda p: agent.check(progress=p))
            except HTTPException:
                pass


threading.Thread(target=_watch_loop, daemon=True).start()


# ======================================================================= chatbot
def _status_text():
    base = agent.load_baseline()
    reps = agent.read_reports(1)
    if not base:
        return "No baseline yet. Open the Monitor dashboard and click 'Learn baseline' first."
    if not reps:
        return f"Baseline learned at {base['time']}. No checks have run yet."
    r = reps[0]
    if not r["changed"]:
        return (f"✅ Model looks normal (last check {r['time']}). "
                f"Cost of that check: {r['probe_cost']['calls']} probe calls, {r['probe_cost']['tokens']} tokens.")
    lines = [f"⚠️ Behaviour change detected at {r['time']}.", "", "What the monitor observed:"]
    lines += [f"• {t}" for t in r["what_i_saw"]]
    if r["likely_cause"]:
        lines += ["", f"Likely cause: {r['likely_cause'].replace('_', ' ')} "
                      f"({r['confidence']:.0%}, {r['confidence_label']} confidence).", r["likely_cause_meaning"]]
        lines += [f"  because {x}" for x in r["reasons"]]
        lines += ["", f"Caveat: {r['caveat']}"]
    return "\n".join(lines)


HELP = ("I'm the chatbot running on `bot-latest`, watched by SilentShift.\n"
        "Commands:\n• /status – is the model behind me behaving normally?\n"
        "• /help – this message\nAnything else goes to the model.")


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    msgs = [{"role": m.get("role"), "content": str(m.get("content", ""))}
            for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
    if not msgs:
        raise HTTPException(400, "messages required")
    last = msgs[-1]["content"].strip().lower()
    if last in ("/status", "/report", "status?"):
        return {"message": {"role": "assistant", "content": _status_text()}, "local": True}
    if last == "/help":
        return {"message": {"role": "assistant", "content": HELP}, "local": True}

    req = build_request(load_config(), msgs[-20:])     # same builder the canaries use
    try:
        data = await gateway.forward_chat(req, source="live")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Provider unreachable: {e}")
    return {"message": data.get("message"), "model": data.get("model"),
            "meta": {"prompt_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"),
                     "done_reason": data.get("done_reason")}}


# ======================================================================= ollama-compatible proxy
@app.api_route("/ollama/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "HEAD"])
async def ollama_proxy(path: str, request: Request):
    raw = await request.body()
    if path == "api/chat":
        body = json.loads(raw or b"{}")
        wants_stream = body.get("stream", True)        # Ollama clients default to streaming
        # canaries = probe; other Ollama clients (e.g. Open WebUI) = external; our chatbot = live
        source = "probe" if request.headers.get(config.PROBE_HEADER) else "external"
        data = await gateway.forward_chat(body, source=source)
        if wants_stream:   # one final NDJSON line is a valid Ollama stream
            return Response(json.dumps(data) + "\n", media_type="application/x-ndjson")
        return JSONResponse(data)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    r = await _provider.request(request.method, "/" + path, content=raw, headers=headers,
                                params=request.query_params)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


# ======================================================================= monitor API
@app.get("/monitor/status")
def status():
    base = agent.load_baseline()
    reps = agent.read_reports(1)
    return {
        "provider_mode": config.PROVIDER_MODE,
        "baseline": {"time": base["time"], "config_hash": base["config_hash"],
                     "speed_mean": round(base["speed_mean"] or 0, 1), "speed_sd": round(base["speed_sd"], 2),
                     "normal_similarity_min": round(base["normal_similarity_min"], 3)} if base else None,
        "latest": reps[0] if reps else None,
        "job": JOB, "watch": WATCH,
        "live": gateway.live_summary(since=base["time"] if base else None),
    }


@app.post("/monitor/baseline")
def baseline():
    return _start_job("baseline", lambda p: {"time": agent.make_baseline(progress=p)["time"]})


@app.post("/monitor/check")
async def run_check(request: Request):
    body = await _json(request)
    if not agent.load_baseline():
        raise HTTPException(400, "Learn a baseline first")
    return _start_job("check", lambda p: agent.check(deep=bool(body.get("deep")), progress=p))


@app.post("/monitor/evaluate")
async def run_eval(request: Request):
    body = await _json(request)
    n = max(10, min(int(body.get("trials", 30)), 200))
    return _start_job("evaluation", lambda p: evaluation.run(n, seed=body.get("seed"), progress=p))


@app.get("/monitor/job")
def job():
    return JOB


@app.get("/monitor/reports")
def reports(limit: int = 30):
    return agent.read_reports(limit)


@app.get("/monitor/results")
def results():
    try:
        return json.loads(config.RESULTS_JSON.read_text())
    except FileNotFoundError:
        return None


@app.get("/monitor/metadata")
def metadata(limit: int = 40):
    return gateway.read_metadata(limit=limit)[::-1]


@app.post("/monitor/watch")
async def watch(request: Request):
    body = await _json(request)
    WATCH["enabled"] = bool(body.get("enabled"))
    WATCH["minutes"] = max(1, int(body.get("minutes", WATCH["minutes"])))
    WATCH["next_run"] = None
    return WATCH


@app.post("/monitor/privacy-audit")
async def privacy_audit():
    """Send a unique marker as if it were a user message, then prove it was not stored anywhere."""
    marker = "PRIVACY-MARKER-" + uuid.uuid4().hex
    req = build_request(load_config(), [{"role": "user", "content": f"My secret note is {marker}. Please repeat it."}])
    await gateway.forward_chat(req, source="live")
    await asyncio.sleep(0.2)
    scanned, hits, size = 0, [], 0
    for p in Path(config.DATA).rglob("*"):
        if p.is_file():
            scanned += 1
            content = p.read_bytes()
            size += len(content)
            if marker.encode() in content:
                hits.append(str(p.relative_to(config.ROOT)))
    return {"passed": not hits, "marker": marker[:22] + "…", "files_scanned": scanned,
            "bytes_scanned": size, "found_in": hits,
            "note": "Server-side only. The chat UI keeps your own conversation in your browser's localStorage."}


# ======================================================================= demo operator (provider side)
@app.get("/demo/changes")
def demo_changes():
    return {"changes": injector.list_changes(), **injector.current()}


@app.post("/demo/apply")
async def demo_apply(request: Request):
    body = await _json(request)
    try:
        return injector.apply(body.get("name", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/demo/reset")
def demo_reset():
    injector.reset()
    return {"ok": True}


async def _json(request):
    try:
        return await request.json()
    except Exception:
        return {}


# static UI last so API routes win
app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")