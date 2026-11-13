"""
api.py - the SilentShift FastAPI BACKEND + dashboard (port 8001).

    User / dashboard  ->  FastAPI (this file)  ->  evaluation engine  ->  harness.py  ->  LLM

Run (PowerShell):
    python api.py
Then open:
    http://127.0.0.1:8001/        the dashboard
    http://127.0.0.1:8001/docs    API docs (made by FastAPI)

Evaluations take minutes on real models, so long tasks run in the background:
POST starts a job and returns a job id, GET /jobs/{id} tells you when it is done.
All results shown are REAL runs; nothing on the dashboard is simulated.
"""

import glob
import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import diagnose
import evaluator
import harness
app = FastAPI(title="SilentShift API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
DASHBOARD = Path(__file__).parent / "dashboard.html"
JOBS = {}
LOCK = threading.Lock()          # one evaluation at a time (models are slow; avoid mixing runs)


class EvalRequest(BaseModel):
    label: str
    provider: Optional[str] = None
    model: Optional[str] = None
    judge: str = "llm"


class DiagnoseRequest(BaseModel):
    baseline: str
    current: str
    fingerprint: bool = False


def start_job(kind, fn):
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"id": job_id, "kind": kind, "status": "running", "started": time.strftime("%H:%M:%S"),
                    "result": None, "error": None}

    def work():
        with LOCK:
            try:
                JOBS[job_id]["result"] = fn()
                JOBS[job_id]["status"] = "done"
            except Exception as e:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = f"{type(e).__name__}: {e}"
                traceback.print_exc()
        JOBS[job_id]["finished"] = time.strftime("%H:%M:%S")

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": job_id}


# ------------------------------------------------------------------ pages
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(DASHBOARD.read_text(encoding="utf-8"))


@app.get("/health")
def health():
    return {"status": "ok", "busy": LOCK.locked(),
            "judge": f"{os.environ.get('JUDGE_PROVIDER', 'ollama')}/{os.environ.get('JUDGE_MODEL', 'default')}"}


# ------------------------------------------------------------------ data
@app.get("/eval-set")
def eval_set():
    items = evaluator.load("eval_set.json")
    cats = {}
    for i in items:
        cats[i["category"]] = cats.get(i["category"], 0) + 1
    return {"total": len(items), "categories": cats, "questions": items}


@app.get("/runs")
def list_runs():
    out = []
    for p in sorted(glob.glob("runs/*.json"), key=os.path.getmtime, reverse=True):
        r = json.load(open(p))
        s = r["summary"]
        out.append({"label": r["label"], "time": r["time"], "model": f"{r['provider']}/{r['model']}",
                    "pass_rate": s["pass_rate"], "n": s["n"], "judge": r["judge"]})
    return out


@app.get("/runs/{label}")
def get_run(label: str):
    try:
        return evaluator.load_run(label)
    except FileNotFoundError:
        raise HTTPException(404, f"no run called {label}")


@app.get("/agent/reports")
def agent_reports(limit: int = 10):
    try:
        with open("reports.jsonl") as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []
    return lines[-limit:][::-1]


# ------------------------------------------------------------------ actions (background jobs)
@app.post("/eval/run")
def run_eval(req: EvalRequest):
    return start_job("eval", lambda: evaluator.run_eval(
        req.label, req.provider, req.model, req.judge, quiet=True)["summary"])


@app.post("/agent/baseline")
def agent_baseline():
    import agent
    return start_job("agent_baseline", lambda: (agent.make_baseline(), "baseline saved")[1])


@app.post("/agent/check")
def agent_check():
    import agent
    return start_job("agent_check", agent.check)


@app.post("/diagnose")
def run_diagnose(req: DiagnoseRequest):
    def work():
        c = evaluator.compare_runs(evaluator.load_run(req.baseline), evaluator.load_run(req.current))
        fp = None
        if req.fingerprint:
            import agent
            fp = agent.check()
        text, ranking = diagnose.build_report(c, fp)
        os.makedirs("reports", exist_ok=True)
        path = os.path.join("reports", f"diagnostic_{time.strftime('%Y%m%d_%H%M%S')}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return {"report_markdown": text, "ranking": ranking["ranked"], "points": ranking["points"],
                "explanations": ranking["explanations"], "uncertainty": ranking["uncertainty"],
                "comparison": c, "fingerprint": fp, "saved_to": path}
    return start_job("diagnose", work)


@app.get("/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "unknown job")
    return JOBS[job_id]


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
