"""
provider.py - the "AI provider" behind the model name `bot-latest`.

To the monitor this is a BLACK BOX: it only sees answers plus the usual API metadata
(token counts, timing, finish reason). The hidden state lives in data/hidden/ and is
changed by injector.py, playing a provider that changes things silently.

Hidden state (what can silently change):
  route   : "production" | "new" | "q8"   -> which model the alias points to  (MODEL / SERVING layer)
  num_ctx : 4096 | 512                     -> server context window             (SERVING layer)
  slow    : False | True                   -> fewer CPU threads = slower box    (SERVING layer)
  safety  : False | True                   -> provider adds a stricter policy   (SAFETY layer)

REAL mode routes to your real models (data/hidden/routes.json, written by run.py):
  production = your Ollama Qwen,  new = Groq GPT-OSS-20b,  q8 = optional q8 build of Qwen.
DEMO mode uses a simulator with realistic noise so the large evaluation runs in seconds.
"""

import asyncio
import json
import random
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

import backends
import config

app = FastAPI(title="Provider (black box)")

NORMAL_STATE = {"route": "production", "num_ctx": 4096, "slow": False, "safety": False}

SAFETY_POLICY = config.SAFETY_POLICY


def state():
    try:
        return {**NORMAL_STATE, **json.loads(config.PROVIDER_STATE.read_text())}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(NORMAL_STATE)


def routes():
    try:
        return json.loads(config.ROUTES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ===================================================================== REAL
def prepare(body):
    """Apply the hidden provider state to a request. Returns (model spec, messages, serving settings)."""
    s, rt = state(), routes()
    spec = rt.get(s["route"]) or rt.get("production")
    if not spec:
        raise HTTPException(500, "No real model configured - start with: python run.py --real")
    msgs = [dict(m) for m in (body.get("messages") or [])]
    kind, _ = backends.parse(spec)
    serving = {}
    if kind == "ollama":
        # On Ollama, bot-latest is a real model on the server: its instructions and settings
        # (context window, threads, safety policy) live THERE and are changed there - we just forward.
        return spec, msgs, serving
    # Cloud APIs (Groq) have no such knobs, so the wrapper applies the hidden changes itself.
    if s["safety"]:
        msgs.insert(0, {"role": "system", "content": SAFETY_POLICY})
    if s["num_ctx"] < 4096 and msgs:
        # cloud APIs don't expose this knob, so the wrapper truncates like a smaller server would
        budget = int(s["num_ctx"] / 1.3)
        words = msgs[-1]["content"].split()
        if len(words) > budget:
            msgs[-1]["content"] = " ".join(words[-budget:])
    return spec, msgs, serving


async def real_chat(body):
    spec, msgs, serving = prepare(body)
    try:
        r = await backends.chat(spec, msgs, body.get("options") or {}, body.get("format"), serving)
    except backends.BackendError as e:
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))
    return to_api_shape(body.get("model"), r)


def to_api_shape(alias, r):
    """Answer in the Ollama-style shape the gateway expects. The provider echoes the ALIAS, never the real model."""
    return {"model": alias, "message": {"role": "assistant", "content": r["content"]}, "done": True,
            "done_reason": r["done_reason"], "prompt_eval_count": r["prompt_tokens"],
            "eval_count": r["output_tokens"], "eval_duration": int(max(r["seconds"], 0.001) * 1e9)}


# ===================================================================== DEMO (simulator)
SIM = {
    "production": {   # plays "Qwen"
        "tok": 1.30, "speed": 32.0,
        "identity": ["I am Qwen, a large language model created by Alibaba Cloud.",
                     "I'm Qwen, a large language model created by Alibaba Cloud."],
        "sea": ["The sea is a vast, shimmering expanse of blue that covers most of our planet.",
                "The sea is a vast, shimmering expanse of blue covering most of our planet."],
        "rainbow": ["A rainbow is a big colourful arc in the sky. It happens when sunlight shines through raindrops.",
                    "A rainbow is a big colourful arc in the sky. It appears when sunlight shines through raindrops."],
        "medical": "Mr Rao keeps taking metformin 500 mg twice a day, starts amlodipine 5 mg once a day for blood pressure, and is reviewed in 3 months.",
        "homework": "Subtract 3 from both sides: 2x = 8. Divide by 2: x = 4.",
        "json1": '{"name": "Asha Verma", "age": 30}', "json2": '["apple", "banana", "mango"]',
        "egg": "Place the egg in boiling water for about 9 minutes, then cool it in cold water.",
        "safe": "Here is a general, safety-focused explanation of how that works and how to stay safe.",
    },
    "new": {          # plays "GPT-OSS" - different family: other tokenizer, wording and format habits
        "tok": 1.12, "speed": 48.0,
        "identity": ["I'm ChatGPT, an AI assistant developed by OpenAI."],
        "sea": ["Endless and restless, the ocean hums with tides that pull at the shore."],
        "rainbow": ["Rainbows are colourful bands of light! Sunlight bends inside tiny water drops and splits into colours."],
        "medical": "Mr Rao should continue his diabetes tablet and start a new blood-pressure tablet, with a review in 3 months.",
        "homework": "2x + 3 = 11 → 2x = 8 → x = 4.",
        "json1": '```json\n{"name": "Asha Verma", "age": 30}\n```', "json2": '```json\n["apple", "banana", "cherry"]\n```',
        "egg": "Gently boil the egg for 8-10 minutes, then move it to ice water.",
        "safe": "Great question! Here's an overview, with safety in mind.",
    },
}
Q8_TWEAKS = [("vast", "huge"), ("big", "large"), ("created by", "developed by"), ("about 9", "around 9"), ("keeps taking", "continues")]
RISKY = ["lock pick", "chemical", "phishing"]   # matched as substrings
REFUSAL = "I'm sorry, but I can't help with that."


def sim_answer(user, system, s, opts):
    m = SIM["new" if s["route"] == "new" else "production"]
    p = user.lower()

    def pick(key):
        v = m[key]
        return v[0] if random.random() < 0.8 or len(v) == 1 else v[1]

    if s["safety"] and (any(w in p for w in RISKY) or "solve 2x" in p):
        return REFUSAL
    if "17 x 23" in p: t = "391"
    elif "capital of france" in p: t = "Paris"
    elif "backwards" in p: t = "tac"
    elif "who are you" in p: t = pick("identity")
    elif "about the sea" in p: t = pick("sea")
    elif "rainbow" in p: t = pick("rainbow")
    elif "clinic letter" in p: t = m["medical"]
    elif "solve 2x" in p: t = m["homework"]
    elif "keys name and age" in p: t = m["json1"]
    elif "fruit names" in p: t = m["json2"]
    elif "boil an egg" in p: t = m["egg"]
    elif "secret code" in p: t = "7391" if "7391" in user else "I don't know any secret code."
    elif any(w in p for w in RISKY): t = m["safe"]
    else:
        t = ("(Demo mode - simulated provider.) You asked: \"" + " ".join(user.split()[:12]) +
             "\". Start SilentShift in real mode (start.bat option 2) to chat with your Qwen and Groq models.")
    if s["route"] == "q8":
        for a, b in Q8_TWEAKS:
            t = t.replace(a, b)
    if "pirate" in (system or "").lower() and not t.startswith(("{", "[", "`")) and len(t) > 6:
        t = "Arr! " + t
    if float(opts.get("temperature", 0) or 0) > 0.5 and not t.startswith(("{", "[", "`")) and len(t) > 5:
        t += " " + random.choice(["Indeed!", "Fascinating, isn't it?", "Hope that helps!", "Truly.", "Cheers!"])
    return t


def sim_chat(body, s=None):
    s, opts = s or state(), body.get("options") or {}
    msgs = [dict(m) for m in (body.get("messages") or [])]
    if s["safety"]:
        msgs.insert(0, {"role": "system", "content": SAFETY_POLICY})
    m = SIM["new" if s["route"] == "new" else "production"]
    user = msgs[-1]["content"] if msgs else ""
    system = " ".join(x["content"] for x in msgs if x["role"] == "system")
    prompt_tokens = int(len(" ".join(x["content"] for x in msgs).split()) * m["tok"])
    if prompt_tokens > s["num_ctx"]:                      # server keeps only the END of the prompt
        user = " ".join(user.split()[-int(s["num_ctx"] / m["tok"]):])
        prompt_tokens = s["num_ctx"]
    text = sim_answer(user, system, s, opts)
    limit = int(opts.get("num_predict", 1024) or 1024)
    done = "stop"
    if int(len(text.split()) * m["tok"]) > limit:
        text = " ".join(text.split()[: max(1, int(limit / m["tok"]))])
        done = "length"
    out = max(1, int(len(text.split()) * m["tok"]))
    speed = m["speed"] * (0.45 if s["slow"] else 0.80 if s["route"] == "q8" else 1.0) * random.uniform(0.92, 1.08)
    return {"model": body.get("model"), "message": {"role": "assistant", "content": text}, "done": True,
            "done_reason": done, "prompt_eval_count": prompt_tokens, "eval_count": out,
            "eval_duration": int(out / speed * 1e9)}


# ===================================================================== API
@app.post("/reference/chat")
async def reference_chat(request: Request):
    """Direct access to ONE named public model, the way any customer can call a model they know
    (their own Ollama, or Groq with their key). Used only to build the fingerprint library -
    never for bot-latest. Demo mode: 'sim:production' | 'sim:new' | 'sim:q8' (simulated public models)."""
    body = await request.json()
    spec = body.get("spec", "")
    if spec.startswith("sim:"):
        if config.MODE == "real":
            raise HTTPException(400, "simulated models exist only in demo mode")
        return sim_chat(body, {**NORMAL_STATE, "route": spec[4:]})
    if config.MODE != "real":
        raise HTTPException(400, "real models need real mode")
    try:
        r = await backends.chat(spec, body.get("messages") or [], body.get("options") or {}, body.get("format"))
    except (backends.BackendError, ValueError) as e:
        raise HTTPException(getattr(e, "status", 400) if 400 <= getattr(e, "status", 400) < 600 else 502, str(e))
    return to_api_shape(spec, r)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    if config.MODE == "real":
        return await real_chat(body)
    return sim_chat(body)


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """Streaming version for the chatbot: NDJSON lines {"delta": ...}, then a final {"done": true, ...stats}."""
    body = await request.json()
    alias = body.get("model")

    async def gen():
        try:
            if config.MODE != "real":
                d = sim_chat(body)
                for i, w in enumerate(d["message"]["content"].split(" ")):
                    yield json.dumps({"delta": (" " if i else "") + w}) + "\n"
                    await asyncio.sleep(0.02)
                yield json.dumps({"done": True, **{k: v for k, v in d.items() if k != "message"}}) + "\n"
                return
            spec, msgs, serving = prepare(body)
            async for kind, val in backends.stream(spec, msgs, body.get("options") or {}, body.get("format"), serving):
                if kind == "delta":
                    yield json.dumps({"delta": val}) + "\n"
                else:
                    shaped = to_api_shape(alias, {"content": "", **val})
                    shaped.pop("message")
                    yield json.dumps({"done": True, **shaped}) + "\n"
        except backends.BackendError as e:
            yield json.dumps({"error": str(e), "status": e.status}) + "\n"
        except HTTPException as e:
            yield json.dumps({"error": e.detail, "status": e.status_code}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=config.PROVIDER_PORT, log_level="warning")
