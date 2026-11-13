"""
server.py - the one app you run (started by run.py).

    /                  chatbot (the "production app" that uses bot-latest)   -> POST /api/chat
    /monitor.html      SilentShift dashboard                                 -> /monitor/*
    /probe/chat        canary path (localhost only) - same gateway as the chatbot
    /demo/*            demo-operator panel: plays the provider making silent changes

Every model call - user chat or canary - goes through gateway.forward_chat,
which records METADATA ONLY (never prompts or answers).
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
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import agent
import config
import evaluation
import gateway
import injector
from runner import build_request, load_config

app = FastAPI(title="SilentShift")
CHAT_HISTORY = 12      # messages of conversation sent to the model


# ======================================================================= errors + caching
def provider_error(e):
    if isinstance(e, httpx.HTTPStatusError):
        try:
            detail = e.response.json().get("detail") or e.response.text
        except Exception:
            detail = e.response.text
        code = e.response.status_code
        return (429 if code == 429 else 502), str(detail)[:300]
    return 502, f"Can't reach the provider: {e}"


@app.exception_handler(httpx.HTTPError)
async def _provider_errors(request: Request, exc: httpx.HTTPError):
    code, msg = provider_error(exc)
    return JSONResponse({"detail": msg}, status_code=code)


@app.middleware("http")
async def guard(request: Request, call_next):
    # the canary path is for the monitor on this machine only
    if request.url.path.startswith("/probe/") and (request.client is None or request.client.host not in ("127.0.0.1", "::1")):
        return JSONResponse({"detail": "probe path is local only"}, 403)
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/monitor/", "/demo/", "/probe/")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"    # always serve the latest UI
    return response


# ======================================================================= background jobs
JOB = {"id": None, "kind": None, "state": "idle", "done": 0, "total": 0, "message": "",
       "result": None, "error": None, "started": None, "finished": None}
_job_lock = threading.Lock()


def start_job(kind, fn):
    with _job_lock:
        if JOB["state"] == "running":
            raise HTTPException(409, f"A {JOB['kind']} is already running - wait for it to finish")
        JOB.update(id=uuid.uuid4().hex[:8], kind=kind, state="running", done=0, total=0, message="starting",
                   result=None, error=None, started=time.strftime("%H:%M:%S"), finished=None)

    t0 = time.time()

    def progress(done, total, msg=None):
        eta = ""
        if done and total and done < total:
            left = (time.time() - t0) / done * (total - done)
            eta = f" · about {int(left // 60)} min {int(left % 60)} s left" if left >= 60 else f" · about {int(left)} s left"
        JOB.update(done=done, total=total, message=(msg or f"{done}/{total} canary questions") + eta)

    def work():
        try:
            JOB["result"] = fn(progress)
            JOB["state"] = "done"
        except Exception as e:
            print(f"  {kind} failed: {e}")
            if not isinstance(e, RuntimeError):
                traceback.print_exc()
            JOB.update(state="error", error=str(e))
        finally:
            JOB["finished"] = time.strftime("%H:%M:%S")
            JOB["finished_ts"] = time.time()

    threading.Thread(target=work, daemon=True).start()
    return dict(JOB)


def auto_check(reason):
    """Start a check now if we can. Returns a short note for the UI."""
    if not agent.load_baseline():
        return "No baseline yet - learn one first."
    if _changing[0] and not reason.startswith(("right after", "the production")):
        return "A change is being applied."
    if JOB["state"] == "running":
        return "A job is already running - run a check when it finishes."
    try:
        start_job(f"check ({reason})", lambda p: agent.check(progress=p))
        return f"Started a check ({reason})."
    except HTTPException:
        return "Could not start a check."


_last_live_trigger = [0.0]
_changing = [False]      # True while a model switch / change is being applied: no new checks start


async def wait_for_check(what):
    """Changes must not land in the middle of a check (it would mix two states).
    Wait for a running check to finish; refuse only for long jobs."""
    _changing[0] = True
    waited = 0
    while JOB["state"] == "running":
        if not str(JOB["kind"]).startswith(("check", "auto-check")):
            _changing[0] = False
            raise HTTPException(409, f"A {JOB['kind']} is running - {what} when it has finished.")
        if waited > 300:
            _changing[0] = False
            raise HTTPException(409, f"A check is taking very long - try again shortly.")
        await asyncio.sleep(1)
        waited += 1


def live_watch():
    """After each real chat reply: if the live traffic looks unusual compared with the baseline,
    start a check automatically. Uses metadata only (speed, settings hash) - never the text."""
    base = agent.load_baseline()
    if not (WATCH.get("live_trigger") and base) or JOB["state"] == "running":
        return
    if time.time() - _last_live_trigger[0] < 120:
        return
    rows = [r for r in gateway.read_metadata(source="live")[-3:] if r.get("status", 200) < 400]
    if not rows:
        return
    reason = None
    if rows[-1].get("settings_hash") and rows[-1]["settings_hash"] != base["config_hash"]:
        reason = "our request settings changed"
    mu = base.get("speed_mean")
    speeds = [r["tokens_per_sec"] for r in rows if r.get("tokens_per_sec")]
    tol = config.SPEED_TOLERANCE * 1.5
    if mu and len(speeds) >= 2 and sum(abs(v - mu) / mu > tol for v in speeds[-2:]) == 2:
        reason = reason or "chat replies are suddenly much faster or slower"
    if reason:
        _last_live_trigger[0] = time.time()
        auto_check("live chats looked unusual: " + reason)


def _load_watch():
    # real mode: watch by itself every 2 minutes (small local models make this cheap)
    w = {"enabled": config.MODE == "real", "minutes": 2, "after_change": True, "live_trigger": True}
    try:
        w.update(json.loads(config.WATCH_FILE.read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {**w, "next_run": None}


WATCH = _load_watch()


def _watch_loop():
    """Background monitoring: re-check every N minutes while enabled."""
    while True:
        time.sleep(5)
        if not WATCH["enabled"] or not agent.load_baseline() or JOB["state"] == "running" or _changing[0]:
            continue
        # wait a full interval AFTER the last job finished - a slow model must never be checked back to back
        last_end = JOB.get("finished_ts") or 0
        due = max(WATCH["next_run"] or 0, last_end + WATCH["minutes"] * 60)
        if time.time() < due:
            continue
        WATCH["next_run"] = time.time() + WATCH["minutes"] * 60
        try:
            start_job("auto-check", lambda p: agent.check(progress=p))
        except HTTPException:
            pass


threading.Thread(target=_watch_loop, daemon=True).start()


# ======================================================================= chatbot
def status_text():
    base, reps = agent.load_baseline(), [r for r in agent.read_reports(200) if not r.get("trial")][:1]
    if not base:
        return "SilentShift has no baseline yet. Open the Monitor dashboard and click **Learn baseline**."
    if not reps:
        return f"Baseline learned at {base['time']}. No checks have run yet - click **Run check** on the dashboard."
    r = reps[0]
    if not r["changed"]:
        return (f"✅ **The model behind me looks normal** (last check {r['time']}, "
                f"{r['probe_cost']['calls']} canary calls).")
    lines = [f"⚠️ **Behaviour change detected** at {r['time']}.", "", "**What the monitor observed:**"]
    lines += [f"- {t}" for t in r["what_i_saw"]]
    idn = r.get("identity") or {}
    if idn.get("best"):
        lines += ["", f"**Who is behind the name now:** {idn['text']}."]
    if r.get("user_impact"):
        lines += ["", "**What users would notice:**"] + [f"- {t}" for t in r["user_impact"]]
    if r["likely_cause"]:
        lines += ["", f"**Likely cause:** {r['likely_cause'].replace('_', ' ')} "
                      f"({r['confidence']:.0%}, {r['confidence_label']} confidence). {r['likely_cause_meaning']}"]
    return "\n".join(lines)


HELP = ("I'm the support chatbot running on **bot-latest**. SilentShift watches the model behind me.\n\n"
        "- `/status` - is the model behind me behaving normally?\n- `/help` - this message\n\nAnything else goes to the model.")


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    msgs = [{"role": m.get("role"), "content": str(m.get("content", ""))}
            for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
    if not msgs:
        raise HTTPException(400, "messages required")
    last = msgs[-1]["content"].strip().lower()
    if last in ("/status", "/report", "status?"):
        return {"message": {"role": "assistant", "content": status_text()}, "local": True}
    if last == "/help":
        return {"message": {"role": "assistant", "content": HELP}, "local": True}
    req = build_request(load_config(), msgs[-CHAT_HISTORY:])   # the same builder the canaries use
    data = await gateway.forward_chat(req, source="live")
    return {"message": data.get("message"), "model": "bot-latest"}


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """Same as /api/chat, but the reply streams in piece by piece (NDJSON)."""
    body = await request.json()
    msgs = [{"role": m.get("role"), "content": str(m.get("content", ""))}
            for m in body.get("messages", []) if m.get("role") in ("user", "assistant")]
    if not msgs:
        raise HTTPException(400, "messages required")
    last = msgs[-1]["content"].strip().lower()
    local = status_text() if last in ("/status", "/report", "status?") else HELP if last == "/help" else None
    if local is not None:
        async def one():
            yield json.dumps({"delta": local}) + "\n" + json.dumps({"done": True, "local": True}) + "\n"
        return StreamingResponse(one(), media_type="application/x-ndjson")
    # only the recent part of the conversation: faster on a laptop, and stays under API token limits
    req = build_request(load_config(), msgs[-CHAT_HISTORY:])

    async def relay():
        async for chunk in gateway.forward_stream(req, "live"):
            yield chunk
        threading.Thread(target=live_watch, daemon=True).start()

    return StreamingResponse(relay(), media_type="application/x-ndjson")


@app.post("/probe/chat")
async def probe(request: Request):
    """Canary path: identical gateway, marked as probe traffic."""
    body = await request.json()
    return JSONResponse(await gateway.forward_chat(body, source="probe"))


# ======================================================================= monitor API
@app.get("/monitor/status")
def status():
    # evaluation trials are scored separately - the status shows the latest REAL check
    base, reps = agent.load_baseline(), [r for r in agent.read_reports(200) if not r.get("trial")][:1]
    return {
        "mode": config.MODE,
        "baseline": {"id": base["id"], "time": base["time"], "config_hash": base["config_hash"],
                     "canary_set": base["canary_set"], "speed_mean": round(base["speed_mean"] or 0, 1),
                     "speed_sd": round(base["speed_sd"], 2), "rounds": len(base["rounds"])} if base else None,
        "latest": reps[0] if reps else None,
        "job": JOB, "watch": WATCH,
        "live": gateway.live_summary(since=base["time"] if base else None),
    }


@app.post("/monitor/baseline")
def baseline():
    active = injector.current().get("active", "none")
    if active != "none":
        # learning "normal" while a silent change is on would make the CHANGED behaviour the baseline
        raise HTTPException(409, f"The silent change '{active}' is still active. Click Reset all first - "
                                 "otherwise the baseline would learn the changed behaviour as normal.")
    return start_job("baseline", lambda p: {"id": agent.make_baseline(progress=p)["id"]})


@app.post("/monitor/check")
async def run_check(request: Request):
    body = await _json(request)
    if not agent.load_baseline():
        raise HTTPException(400, "Learn a baseline first")
    return start_job("check", lambda p: agent.check(deep=bool(body.get("deep")), progress=p))


@app.post("/monitor/evaluate")
async def run_eval(request: Request):
    body = await _json(request)
    n = max(5, min(int(body.get("trials", 40)), 200))
    return start_job("evaluation", lambda p: evaluation.run(n, seed=body.get("seed"), progress=p))


@app.get("/monitor/job")
def job():
    return JOB


@app.get("/monitor/reports")
def reports(limit: int = 30):
    return agent.read_reports(limit)


@app.get("/monitor/report/{rid}.md", response_class=PlainTextResponse)
def report_md(rid: str):
    for r in agent.read_reports(500):
        if r["id"] == rid:
            return PlainTextResponse(agent.report_markdown(r), headers={
                "Content-Disposition": f'attachment; filename="silentshift-report-{rid}.md"'})
    raise HTTPException(404, "report not found")


@app.get("/monitor/results")
def results():
    out = {}
    for mode in ("demo", "real"):
        try:
            out[mode] = json.loads(Path(str(config.RESULTS_JSON).format(mode=mode)).read_text())
        except FileNotFoundError:
            out[mode] = None
    return out


@app.get("/monitor/metadata")
def metadata(limit: int = 30):
    return gateway.read_metadata(limit=limit)[::-1]


@app.post("/monitor/watch")
async def watch(request: Request):
    body = await _json(request)
    if "enabled" in body:
        WATCH["enabled"] = bool(body["enabled"])
    if "minutes" in body:
        WATCH["minutes"] = max(1, int(body["minutes"]))
    for k in ("after_change", "live_trigger"):
        if k in body:
            WATCH[k] = bool(body[k])
    WATCH["next_run"] = None
    config.WATCH_FILE.write_text(json.dumps({k: WATCH[k] for k in ("enabled", "minutes", "after_change", "live_trigger")}))
    return WATCH


@app.post("/monitor/privacy-audit")
async def privacy_audit():
    """Send a unique secret through the chatbot path, then prove it was not stored anywhere."""
    marker = "PRIVACY-MARKER-" + uuid.uuid4().hex
    req = build_request(load_config(), [{"role": "user", "content": f"My secret note is {marker}. Please repeat it."}])
    note = ""
    try:
        await gateway.forward_chat(req, source="live")
    except httpx.HTTPError:
        note = " (The model returned an error, but the secret still passed through SilentShift's gateway.)"
    await asyncio.sleep(0.2)
    scanned, hits, size = 0, [], 0
    for p in Path(config.DATA).rglob("*"):
        if p.is_file():
            scanned += 1
            content = p.read_bytes()
            size += len(content)
            if marker.encode() in content:
                hits.append(str(p.relative_to(config.ROOT)))
    return {"passed": not hits, "marker": marker[:22] + "…", "files_scanned": scanned, "bytes_scanned": size,
            "found_in": hits, "note": "Only metadata is stored server-side. Your chat history stays in your own browser." + note}


# ======================================================================= fingerprint library (USP)
@app.get("/monitor/fingerprints")
def fp_list():
    import fingerprints
    lib = fingerprints.library_summary()
    try:
        cands = fingerprints.candidates()
    except Exception:
        cands = []
    reps = [r for r in agent.read_reports(200) if not r.get("trial")][:1]
    return {"library": lib, "candidates": [{"spec": c, "label": fingerprints.label(c), "in_library": any(x["spec"] == c for x in lib)}
                                           for c in cands],
            "matrix": fingerprints.matrix() if len(lib) > 1 else None,
            "latest": (reps[0].get("identity") if reps else None), "latest_time": reps[0]["time"] if reps else None,
            "weights": {"tokenizer": fingerprints.W_TOK, "answers": fingerprints.W_TEXT, "behaviour": fingerprints.W_BEHAV}}


@app.post("/monitor/fingerprints/build")
async def fp_build(request: Request):
    import fingerprints
    body = await _json(request)
    specs = body.get("specs") or None
    return start_job("fingerprinting", lambda p: fingerprints.build(specs, progress=p))


@app.post("/monitor/fingerprints/remove")
async def fp_remove(request: Request):
    import fingerprints
    body = await _json(request)
    fingerprints.remove(str(body.get("spec", "")))
    return {"ok": True}


@app.get("/monitor/timeline")
def timeline(limit: int = 120):
    out = []
    for r in agent.read_reports(limit)[::-1]:
        idn = r.get("identity") or {}
        out.append({"id": r["id"], "time": r["time"], "changed": r["changed"], "cause": r.get("likely_cause"),
                    "drift": r.get("drift_score", 100 if r["changed"] else 0), "trial": r.get("trial"),
                    "who": (idn.get("best") or {}).get("label"), "who_score": (idn.get("best") or {}).get("score"),
                    "calls": r["probe_cost"]["calls"], "seconds": r["probe_cost"]["seconds"]})
    return out


# ======================================================================= demo operator (provider side)
@app.get("/demo/events")
def demo_events(limit: int = 60):
    """OPERATOR view (ground truth) - lets the timeline show when a change really happened.
    The monitor itself never reads this."""
    try:
        log = json.loads(config.CHANGE_LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        log = []
    return [e for e in log if not e.get("trial")][-limit:]


@app.get("/demo/server")
def demo_server():
    """OPERATOR view: what bot-latest really is on the Ollama server right now (its Modelfile)."""
    import model_setup
    rt = model_setup.current()
    if config.MODE != "real" or not rt.get("alias"):
        return {"server_mode": False}
    try:
        r = httpx.post(config.OLLAMA_URL + "/api/show", json={"model": config.OLLAMA_ALIAS}, timeout=5)
        d = r.json() if r.status_code < 400 else {}
    except Exception:
        d = {}
    return {"server_mode": True, "ollama_url": config.OLLAMA_URL, "alias": config.OLLAMA_ALIAS,
            "base": rt.get("base", "").split(":", 1)[-1], "server_system": config.SERVER_SYSTEM,
            "modelfile": d.get("modelfile"), "parameters": d.get("parameters"), "system": d.get("system"),
            "installed": [m for m in (__import__("backends").ollama_models() or [])
                          if m not in (config.OLLAMA_ALIAS, config.OLLAMA_ALIAS + ":latest")]}


@app.post("/demo/custom")
async def demo_custom(request: Request):
    """Server console: redefine bot-latest with any base model / SYSTEM / PARAMETERs."""
    body = await _json(request)
    await wait_for_check("change the server")
    try:
        entry = await asyncio.to_thread(injector.custom, body.get("base") or None, body.get("system") or None,
                                        body.get("parameters") or {}, body.get("note") or "")
    except (ValueError, RuntimeError) as e:
        _changing[0] = False
        raise HTTPException(400, str(e))
    entry["note"] = auto_check("right after a change") if WATCH.get("after_change") else "Now run a check."
    _changing[0] = False
    return entry


@app.get("/demo/changes")
def demo_changes():
    return {"changes": injector.list_changes(), **injector.current()}


@app.post("/demo/apply")
async def demo_apply(request: Request):
    body = await _json(request)
    await wait_for_check("apply the change")
    try:
        entry = await asyncio.to_thread(injector.apply, body.get("name", ""))
    except (ValueError, RuntimeError) as e:
        _changing[0] = False
        raise HTTPException(400, str(e))
    entry["note"] = auto_check("right after a change") if WATCH.get("after_change") else \
        "Now click Run check (or wait for the auto-check)."
    _changing[0] = False
    return entry


@app.post("/demo/reset")
async def demo_reset():
    await wait_for_check("reset")
    try:
        was = injector.current().get("active", "none")
        await asyncio.to_thread(injector.reset)
        if was != "none":
            injector.log_change({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trial": None, "change": "reset",
                                 "layer": "none", "description": "back to normal"})
    finally:
        _changing[0] = False
    return {"ok": True}


# ======================================================================= model setup (operator)
@app.get("/setup/models")
def setup_models():
    import backends
    import model_setup
    installed = backends.ollama_models() if config.MODE == "real" else None
    groq = bool(config.GROQ_API_KEY)
    return {"mode": config.MODE, "ollama_url": config.OLLAMA_URL, "alias": config.OLLAMA_ALIAS,
            "ollama_running": installed is not None, "installed": installed or [],
            "groq": groq, "routes": model_setup.current(),
            "choices": model_setup.available_specs(installed, groq),
            "suggested": [dict(x, installed=x["name"] in (installed or [])) for x in model_setup.SUGGESTED]}


@app.post("/setup/routes")
async def setup_routes(request: Request):
    """Choose which model bot-latest runs on, and where model_swap moves it."""
    import backends
    import model_setup
    if config.MODE != "real":
        raise HTTPException(400, "Model choice is for real mode (start.bat option 2)")
    body = await _json(request)
    await wait_for_check("switch models")
    installed = backends.ollama_models()
    specs = model_setup.available_specs(installed, bool(config.GROQ_API_KEY))
    for key in ("production", "new"):
        if body.get(key) and body[key] not in specs:
            _changing[0] = False
            raise HTTPException(400, f"{body[key]} is not available")
    old = model_setup.current()
    model_setup.save_choice(body.get("production"), body.get("new"))
    routes = model_setup.resolve(installed, bool(config.GROQ_API_KEY))
    try:
        await asyncio.to_thread(injector.reset)   # rebuilds bot-latest on the server from the chosen base model
    except RuntimeError as e:
        _changing[0] = False
        raise HTTPException(502, str(e))
    note = ""
    before = old.get("base") or old.get("production")
    if (routes.get("base") or routes["production"]) != before:
        await asyncio.to_thread(_warm_up)
        if config.BASELINE.exists():
            # keep the old baseline: the monitor should NOTICE that the model behind bot-latest changed
            note = (" " + auto_check("the production model was switched") +
                    " When it says the model changed and you meant it, click Learn baseline to accept the new model as normal.")
        else:
            note = " Click Learn baseline to learn what normal looks like for this model."
    else:
        threading.Thread(target=_warm_up, daemon=True).start()
    _changing[0] = False
    return {"routes": routes, "message": "Saved." + note}


def _warm_up():
    try:   # load the model into memory so the next chat isn't extra slow
        httpx.post(config.PROVIDER_URL + "/api/chat", timeout=300, json={
            "model": "bot-latest", "messages": [{"role": "user", "content": "Hi"}], "options": {"num_predict": 1}})
    except Exception:
        pass


@app.post("/setup/pull")
async def setup_pull(request: Request):
    """Download a model into Ollama with a progress bar (same as `ollama pull <name>`)."""
    import re as _re
    import backends
    body = await _json(request)
    name = str(body.get("name", "")).strip()
    if not _re.fullmatch(r"[a-zA-Z0-9._:/-]{2,80}", name):
        raise HTTPException(400, "invalid model name")
    if backends.ollama_models() is None:
        raise HTTPException(400, "Ollama is not running - start the Ollama app first")
    return start_job("download", lambda p: backends.pull_model(name, progress=p))


async def _json(request):
    try:
        return await request.json()
    except Exception:
        return {}


app.mount("/", StaticFiles(directory=config.WEB, html=True), name="web")
