"""
provider.py - the "AI provider" that sits behind the model name `bot-latest`.

To the monitor this is a BLACK BOX. It only sees answers and the metadata an
Ollama-style API returns. Hidden state lives in data/hidden/provider_state.json
and is changed by injector.py (the "provider" making silent changes).

    alias   : "llama" | "qwen"     -> which weights the alias points to (MODEL layer)
    quant   : "q4" | "q8"          -> quantisation of the served build (SERVING layer)
    num_ctx : 4096 | 1024          -> context window of the server  (SERVING layer)
    safety  : false | true         -> provider-side safety filter   (SAFETY layer)

Modes (config.PROVIDER_MODE):
    mock : simulated models with realistic noise. No GPU needed.
    real : forwards to a real Ollama and applies the same hidden changes:
           alias/quant -> picks a different Ollama tag, num_ctx -> injected
           option, safety -> a guard step in front of the model.
    api  : forwards to any OpenAI-compatible cloud API (Groq, OpenAI, Gemini,
           OpenRouter...) and applies the same hidden changes (see api_chat).

Run alone:  python provider.py
"""

import asyncio
import json
import random
import time

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

import config

app = FastAPI(title="Provider (black box)")

NORMAL_STATE = {"alias": "llama", "quant": "q4", "num_ctx": 4096, "safety": False}
REAL_TAGS = {
    ("llama", "q4"): "llama3.2:3b",
    ("llama", "q8"): "llama3.2:3b-instruct-q8_0",
    ("qwen", "q4"): "qwen2.5:3b",
    ("qwen", "q8"): "qwen2.5:3b-instruct-q8_0",
}
RISKY = ["lock pick", "chemical", "phishing", "dose", "weapon", "hack", "drug"]
REFUSAL = "I'm sorry, but I can't help with that request."


def state():
    try:
        return {**NORMAL_STATE, **json.loads(config.PROVIDER_STATE.read_text())}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(NORMAL_STATE)


# =====================================================================  MOCK
# Each simulated model has its own wording, tokenizer ratio and speed.
# Two wordings per answer = normal variation the monitor must tolerate.
MOCK = {
    "llama": {
        "tok": 1.30, "speed": {"q4": 32.0, "q8": 21.0},
        "identity": ["I am Llama, a large language model made by Meta AI.",
                     "I'm Llama, a large language model made by Meta AI."],
        "sea": ["The sea is a vast, shimmering expanse of blue that covers most of our planet.",
                "The sea is a vast, shimmering expanse of blue covering most of our planet."],
        "focus": ["1. Put your phone away. 2. Study in short blocks with breaks. 3. Keep a clear goal for each session.",
                  "1. Put your phone away. 2. Study in short blocks with breaks. 3. Set a clear goal for each session."],
        "rainbow": ["A rainbow is a big colourful arc in the sky. It happens when sunlight shines through raindrops.",
                    "A rainbow is a big colourful arc in the sky. It appears when sunlight shines through raindrops."],
        "egg": ["Place the egg in boiling water for about 9 minutes, then cool it in cold water."],
        "safe": "Here is a general, safety-focused explanation of how that works and how to stay safe.",
        "chat": "Sure! Here's what I can tell you about that: ",
    },
    "qwen": {
        "tok": 1.12, "speed": {"q4": 36.0, "q8": 24.0},
        "identity": ["Hello! I'm Qwen, an AI assistant created by Alibaba Cloud.",
                     "Hi! I'm Qwen, an AI assistant created by Alibaba Cloud."],
        "sea": ["Endless and restless, the ocean hums with tides that pull at the shore."],
        "focus": ["Sure! Here are some tips: - Remove distractions - Use the Pomodoro method - Reward yourself after each task"],
        "rainbow": ["Rainbows are colourful bands of light! Sunlight bends inside tiny water drops and splits into colours."],
        "egg": ["Gently lower the egg into boiling water, cook 8-10 minutes, then transfer it to ice water."],
        "safe": "Great question! Let me give you an overview, with safety in mind.",
        "chat": "Great question! Let me help. ",
    },
}
Q8_TWEAKS = [("vast", "huge"), ("big", "large"), ("short blocks", "short sessions"),
             ("made by", "developed by"), ("about 9", "around 9"), ("Sure!", "Of course!")]


def mock_answer(prompt, s, opts):
    m, p = MOCK[s["alias"]], prompt.lower()

    def pick(key):
        variants = m[key]
        return variants[0] if random.random() < 0.8 or len(variants) == 1 else variants[1]

    if "17 x 23" in p: text = "391"
    elif "capital of france" in p: text = "Paris"
    elif "backwards" in p: text = "tac"
    elif "who are you" in p: text = pick("identity")
    elif "about the sea" in p: text = pick("sea")
    elif "staying focused" in p: text = pick("focus")
    elif "rainbow" in p: text = pick("rainbow")
    elif "boil an egg" in p: text = pick("egg")
    elif "name and age" in p: text = '{"name": "Asha", "age": 30}'
    elif "fruit names" in p: text = '["apple", "banana", "mango"]'
    elif "secret code" in p:
        text = "7391" if "7391" in prompt else "I don't know any secret code."
    elif any(w in p for w in RISKY): text = m["safe"]
    else:
        topic = " ".join(prompt.split()[:12])
        text = (m["chat"] + f"you asked: \"{topic}\". This is the simulated provider, "
                "so answers are canned. Start the app with --real to chat with a real Ollama model.")

    if s["quant"] == "q8":
        for a, b in Q8_TWEAKS:
            text = text.replace(a, b)
    t = float(opts.get("temperature", 0) or 0)
    if t > 0.5 and not text.startswith(("{", "[")) and len(text) > 5:
        text += " " + random.choice(["Indeed!", "Fascinating, isn't it?", "Hope that helps!", "Truly.", "Cheers!"])
    return text


def mock_chat(body):
    s, opts = state(), body.get("options") or {}
    msgs = body.get("messages") or []
    user = msgs[-1]["content"] if msgs else ""
    full = " ".join(str(x.get("content", "")) for x in msgs)
    m = MOCK[s["alias"]]

    prompt_tokens = int(len(full.split()) * m["tok"])
    if prompt_tokens > s["num_ctx"]:             # server truncates the START of the prompt
        keep = int(s["num_ctx"] / m["tok"])
        user = " ".join(user.split()[-keep:])
        prompt_tokens = s["num_ctx"]

    if s["safety"] and any(w in user.lower() for w in RISKY):
        text = REFUSAL
        time.sleep(0.02)                         # guard step adds latency
    else:
        text = mock_answer(user, s, opts)

    limit = int(opts.get("num_predict", 200) or 200)
    words = text.split()
    done_reason = "stop"
    if int(len(words) * m["tok"]) > limit:
        text = " ".join(words[: max(1, int(limit / m["tok"]))])
        done_reason = "length"
    eval_count = max(1, int(len(text.split()) * m["tok"]))
    speed = m["speed"][s["quant"]] * random.uniform(0.92, 1.08)   # ±8% normal jitter
    return {
        "model": body.get("model"),              # providers echo the ALIAS, not the weights
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": {"role": "assistant", "content": text},
        "done": True, "done_reason": done_reason,
        "prompt_eval_count": prompt_tokens, "eval_count": eval_count,
        "eval_duration": int(eval_count / speed * 1e9),
        "total_duration": int((eval_count / speed + 0.01) * 1e9),
    }


# =====================================================================  REAL
_client = httpx.AsyncClient(base_url=config.REAL_OLLAMA_URL, timeout=600)


async def real_chat(body):
    s = state()
    alias = body.get("model")
    msgs = body.get("messages") or []
    user = msgs[-1]["content"] if msgs else ""
    if s["safety"] and any(w in user.lower() for w in RISKY):   # guard model in front
        return {"model": alias, "message": {"role": "assistant", "content": REFUSAL},
                "done": True, "done_reason": "stop", "prompt_eval_count": len(user.split()),
                "eval_count": 12, "eval_duration": int(0.4e9)}
    fwd = dict(body)
    fwd["model"] = REAL_TAGS[(s["alias"], s["quant"])]
    fwd["stream"] = False
    fwd["options"] = {**(body.get("options") or {}), "num_ctx": s["num_ctx"]}
    r = await _client.post("/api/chat", json=fwd)
    r.raise_for_status()
    data = r.json()
    data["model"] = alias                        # hide the real tag behind the alias
    return data


# =====================================================================  API (cloud)
# Any OpenAI-compatible API (Groq, OpenAI, Gemini, OpenRouter...). We translate the
# Ollama-style request to /chat/completions and back, and apply the hidden changes:
#   alias -> SS_API_MODEL vs SS_API_MODEL_NEW      (model layer)
#   quant -> SS_API_MODEL_SERVING if configured    (serving layer)
#   num_ctx -> we truncate the start of long prompts like a smaller-context server (serving)
#   safety -> guard step in front of the model     (safety layer)
_api = httpx.AsyncClient(timeout=120)


def api_model_for(s):
    if s["alias"] == "qwen" and config.API_MODEL_NEW:
        return config.API_MODEL_NEW
    if s["quant"] == "q8" and config.API_MODEL_SERVING:
        return config.API_MODEL_SERVING
    return config.API_MODEL


async def api_chat(body):
    s = state()
    alias = body.get("model")
    opts = body.get("options") or {}
    msgs = [dict(m) for m in (body.get("messages") or [])]
    user = msgs[-1]["content"] if msgs else ""

    if s["safety"] and any(w in user.lower() for w in RISKY):
        return {"model": alias, "message": {"role": "assistant", "content": REFUSAL}, "done": True,
                "done_reason": "stop", "prompt_eval_count": int(len(user.split()) * 1.3),
                "eval_count": 12, "eval_duration": int(0.3e9)}

    # smaller context window on the "server": keep only the END of the prompt
    budget_words = int(s["num_ctx"] / 1.3)
    total_words = sum(len(str(m.get("content", "")).split()) for m in msgs)
    if msgs and total_words > budget_words:
        other = total_words - len(user.split())
        keep = max(20, budget_words - other)
        msgs[-1]["content"] = " ".join(user.split()[-keep:])

    req = {"model": api_model_for(s), "messages": msgs, **config.API_EXTRA}
    if "temperature" in opts: req["temperature"] = opts["temperature"]
    if "top_p" in opts: req["top_p"] = opts["top_p"]
    if "seed" in opts: req["seed"] = opts["seed"]
    if "num_predict" in opts: req["max_tokens"] = opts["num_predict"]
    if body.get("format") == "json": req["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {config.API_KEY}"}
    for attempt in range(5):                       # back off on rate limits
        start = time.time()
        r = await _api.post(config.API_BASE + "/chat/completions", json=req, headers=headers)
        if r.status_code == 429 or r.status_code >= 500:
            wait = float(r.headers.get("retry-after", 2 ** attempt))
            await asyncio.sleep(min(wait, 20))
            continue
        break
    elapsed = max(time.time() - start, 0.001)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"API error: {r.text[:300]}")
    data = r.json()
    choice = data["choices"][0]
    usage = data.get("usage") or {}
    out = usage.get("completion_tokens") or 1
    return {
        "model": alias,                            # hide the real model behind the alias
        "message": {"role": "assistant", "content": choice["message"].get("content") or ""},
        "done": True,
        "done_reason": "length" if choice.get("finish_reason") == "length" else "stop",
        "prompt_eval_count": usage.get("prompt_tokens"),
        "eval_count": out,
        "eval_duration": int(elapsed * 1e9),       # wall time incl. network
    }


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    if config.PROVIDER_MODE == "real":
        return await real_chat(body)
    if config.PROVIDER_MODE == "api":
        return await api_chat(body)
    return mock_chat(body)


@app.get("/api/tags")
def tags():
    return {"models": [{"name": "bot-latest", "model": "bot-latest"}]}


@app.get("/api/version")
def version():
    return {"version": "0.0.0-silentshift-provider"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=config.PROVIDER_PORT, log_level="warning")