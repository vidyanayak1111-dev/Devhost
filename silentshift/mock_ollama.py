"""
mock_ollama.py - a PRETEND Ollama for testing SilentShift without real models.

Runs on port 11434 like real Ollama. You make secret changes by editing
mock_state.json (this plays the role of Friend 1):

    {"model": "llama", "quant": "q4", "safety": false, "num_ctx": 4096}

    model   : "llama" or "qwen"          -> model change
    quant   : "q4" or "q8"               -> serving change
    num_ctx : 4096 or e.g. 1024          -> serving change (short memory)
    safety  : true / false               -> safety change

Run:  python mock_ollama.py
"""

import json
import random
import time

import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
STATE_FILE = "mock_state.json"

STYLE = {
    "llama": {
        "identity": "I am Llama, a large language model made by Meta AI.",
        "sea": "The sea is a vast, shimmering expanse of blue that covers most of our planet.",
        "focus": "1. Put your phone away. 2. Study in short blocks with breaks. 3. Keep a clear goal for each session.",
        "rainbow": "A rainbow is a big colourful arc in the sky. It happens when sunlight shines through raindrops.",
        "tok": 1.30, "speed": {"q4": 32.0, "q8": 21.0},
    },
    "qwen": {
        "identity": "Hello! I'm Qwen, an AI assistant created by Alibaba Cloud.",
        "sea": "Endless and restless, the ocean hums with tides that pull at the shore.",
        "focus": "Sure! Here are some tips: - Remove distractions - Use the Pomodoro method - Reward yourself after each task",
        "rainbow": "Rainbows are colourful bands of light! Sunlight bends inside tiny water drops and splits into colours.",
        "tok": 1.12, "speed": {"q4": 36.0, "q8": 24.0},
    },
}
Q8_TWEAKS = [("vast", "huge"), ("big", "large"), ("short blocks", "short sessions"), ("made by", "developed by")]
RISKY = ["lock pick", "chemicals", "phishing", "dose"]


def state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"model": "llama", "quant": "q4", "safety": False, "num_ctx": 4096}


def answer(prompt, s, opts):
    st, p = STYLE[s["model"]], prompt.lower()
    if s["safety"] and any(w in p for w in RISKY):
        return "I'm sorry, but I can't help with that request."
    if "17 x 23" in p: return "391"
    if "capital of france" in p: return "Paris"
    if "backwards" in p: return "tac"
    if "who are you" in p: text = st["identity"]
    elif "the sea" in p: text = st["sea"]
    elif "staying focused" in p: text = st["focus"]
    elif "rainbow" in p: text = st["rainbow"]
    elif "name and age" in p: return '{"name": "Asha", "age": 30}'
    elif "fruit" in p: return '["apple", "banana", "mango"]'
    elif "secret code" in p: return "7391" if "7391" in prompt else "I don't know any secret code."
    elif any(w in p for w in RISKY): text = "Here is a general, safe explanation of how that works and how to stay safe."
    else: text = "Okay."
    if s["quant"] == "q8":
        for a, b in Q8_TWEAKS:
            text = text.replace(a, b)
    if opts.get("temperature", 0) > 0.5:          # creative = random extra words
        text += " " + random.choice(["Indeed!", "Fascinating, isn't it?", "Hope that helps!", "Truly."])
    return text


@app.get("/api/tags")
def tags():
    return {"models": [{"name": "bot-latest"}]}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    s, opts = state(), body.get("options", {})
    full = " ".join(m["content"] for m in body["messages"])
    user = body["messages"][-1]["content"]

    # tokenizer: different models count tokens differently
    words = full.split()
    tok = STYLE[s["model"]]["tok"]
    prompt_tokens = int(len(words) * tok)
    if prompt_tokens > s["num_ctx"]:                # short memory: drop the START
        keep = int(s["num_ctx"] / tok)
        user = " ".join(user.split()[-keep:])
        prompt_tokens = s["num_ctx"]

    text = answer(user, s, opts)
    out_words = text.split()
    limit = opts.get("num_predict", 200)
    done_reason = "stop"
    if int(len(out_words) * tok) > limit:
        text = " ".join(out_words[: max(1, int(limit / tok))])
        done_reason = "length"
    eval_count = max(1, int(len(text.split()) * tok))
    speed = STYLE[s["model"]]["speed"][s["quant"]] * random.uniform(0.95, 1.05)
    if s["safety"]:
        time.sleep(0.02)                            # guard model adds a little delay
    return {
        "model": body["model"],                     # like real Ollama: echoes the alias name
        "message": {"role": "assistant", "content": text},
        "done": True, "done_reason": done_reason,
        "prompt_eval_count": prompt_tokens, "eval_count": eval_count,
        "eval_duration": int(eval_count / speed * 1e9),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=11434)
