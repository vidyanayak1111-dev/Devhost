"""
backends.py - the two REAL model backends the provider can route to.

  ollama:<tag>    your local Ollama (e.g. ollama:qwen2.5:3b). Has real serving settings:
                  num_ctx (context window), num_thread (CPU threads), num_gpu (GPU layers).
  groq:<model>    Groq cloud (e.g. groq:openai/gpt-oss-20b). Paced to stay under the free
                  tier's requests-per-minute limit, retries politely on 429.

Every call returns the same shape:
  {"content", "done_reason" ("stop"|"length"), "prompt_tokens", "output_tokens", "seconds"}
"""

import asyncio
import json
import time

import httpx

import config


class BackendError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def parse(spec: str):
    """'ollama:qwen2.5:3b' -> ('ollama', 'qwen2.5:3b')"""
    kind, _, name = spec.partition(":")
    if kind not in ("ollama", "groq"):
        raise ValueError(f"unknown backend in '{spec}' (use ollama:<tag> or groq:<model>)")
    return kind, name


def label(spec: str) -> str:
    kind, name = parse(spec)
    return f"{'Ollama' if kind == 'ollama' else 'Groq'} · {name}"


# ---------------------------------------------------------------- Ollama
_ollama = httpx.AsyncClient(base_url=config.OLLAMA_URL, timeout=600)
_caps = {}     # model name -> (time, capabilities) - e.g. "thinking"


async def _thinks(model):
    """Does this model 'think' before answering (Qwen 3.x, DeepSeek-R1...)? Ask Ollama, by capability -
    not by name, because bot-latest can be built from any model."""
    hit = _caps.get(model)
    if hit and time.time() - hit[0] < 60:
        return "thinking" in hit[1]
    caps = []
    try:
        r = await _ollama.post("/api/show", json={"model": model})
        if r.status_code < 400:
            caps = r.json().get("capabilities") or []
    except httpx.HTTPError:
        pass
    _caps[model] = (time.time(), caps)
    return "thinking" in caps


async def ollama_chat(model, messages, opts, fmt=None, serving=None):
    """serving = hidden provider-side settings, e.g. {"num_ctx": 512, "num_thread": 1}"""
    options = {k: v for k, v in (opts or {}).items() if k in ("temperature", "top_p", "seed", "num_predict")}
    options.update(serving or {})
    body = {"model": model, "messages": messages, "stream": False, "options": options}
    if await _thinks(model):
        body["think"] = False                       # thinking models: answer directly
    if fmt == "json":
        body["format"] = "json"
    try:
        r = await _ollama.post("/api/chat", json=body)
    except httpx.HTTPError:
        raise BackendError(502, f"Ollama is not reachable at {config.OLLAMA_URL}. Is Ollama running?")
    if r.status_code >= 400:
        try:
            err = r.json().get("error", r.text)
        except Exception:
            err = r.text
        hint = f" - run: ollama pull {model}" if "not found" in str(err).lower() else ""
        raise BackendError(502, f"Ollama ({model}): {err}{hint}")
    d = r.json()
    ev, dur = d.get("eval_count") or 1, d.get("eval_duration") or 1
    return {"content": strip_thinking((d.get("message") or {}).get("content", "")),
            "done_reason": "length" if d.get("done_reason") == "length" else "stop",
            "prompt_tokens": d.get("prompt_eval_count"),      # may be missing when Ollama cached the prompt
            "output_tokens": ev, "seconds": dur / 1e9}


def strip_thinking(text):
    """Remove <think>...</think> blocks some models print before the answer."""
    import re
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


# ---------------------------------------------------------------- Groq
_groq = httpx.AsyncClient(timeout=180)
_groq_lock = asyncio.Lock()
_groq_last = [0.0]


async def _pace():
    """Space Groq calls out to stay under the requests-per-minute limit."""
    async with _groq_lock:
        gap = 60.0 / max(1, config.GROQ_RPM)
        wait = _groq_last[0] + gap - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        _groq_last[0] = time.time()


async def groq_chat(model, messages, opts, fmt=None, serving=None):
    if not config.GROQ_API_KEY:
        raise BackendError(400, "No GROQ_API_KEY in .env")
    req = {"model": model, "messages": messages}
    if "temperature" in opts: req["temperature"] = opts["temperature"]
    if "top_p" in opts: req["top_p"] = opts["top_p"]
    if "seed" in opts: req["seed"] = opts["seed"]
    if "num_predict" in opts: req["max_tokens"] = opts["num_predict"]
    if "gpt-oss" in model:
        req["reasoning_effort"] = "low"          # keep GPT-OSS answers short and cheap
    if fmt == "json":
        req["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
    for attempt in range(6):
        await _pace()
        start = time.time()
        try:
            r = await _groq.post(config.GROQ_URL + "/chat/completions", json=req, headers=headers)
        except httpx.HTTPError as e:
            raise BackendError(502, f"Can't reach Groq: {e}")
        if r.status_code == 429 or r.status_code >= 500:
            await asyncio.sleep(min(float(r.headers.get("retry-after", 2 ** attempt)), 30))
            continue
        break
    elapsed = max(time.time() - start, 0.001)
    if r.status_code == 429:
        raise BackendError(429, "Groq rate limit - wait about a minute and try again.")
    if r.status_code in (401, 403):
        raise BackendError(401, "Groq rejected the API key - check GROQ_API_KEY in .env.")
    if r.status_code >= 400:
        raise BackendError(502, f"Groq error {r.status_code}: {r.text[:200]}")
    d = r.json()
    ch = d["choices"][0]
    usage = d.get("usage") or {}
    return {"content": strip_thinking((ch.get("message") or {}).get("content") or ""),
            "done_reason": "length" if ch.get("finish_reason") == "length" else "stop",
            "prompt_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens") or 1, "seconds": elapsed}


# ---------------------------------------------------------------- streaming (for the chatbot)
async def ollama_stream(model, messages, opts, fmt=None, serving=None):
    """Yields ("delta", text) while the model writes, then ("done", stats)."""
    options = {k: v for k, v in (opts or {}).items() if k in ("temperature", "top_p", "seed", "num_predict")}
    options.update(serving or {})
    body = {"model": model, "messages": messages, "stream": True, "options": options}
    if await _thinks(model):
        body["think"] = False
    try:
        async with _ollama.stream("POST", "/api/chat", json=body) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode(errors="ignore")
                hint = f" - run: ollama pull {model}" if "not found" in txt.lower() else ""
                raise BackendError(502, f"Ollama ({model}): {txt[:200]}{hint}")
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                d = json.loads(line)
                piece = (d.get("message") or {}).get("content", "")
                if piece:
                    yield "delta", piece
                if d.get("done"):
                    ev, dur = d.get("eval_count") or 1, d.get("eval_duration") or 1
                    yield "done", {"done_reason": "length" if d.get("done_reason") == "length" else "stop",
                                   "prompt_tokens": d.get("prompt_eval_count"), "output_tokens": ev, "seconds": dur / 1e9}
    except httpx.HTTPError:
        raise BackendError(502, f"Ollama is not reachable at {config.OLLAMA_URL}. Is Ollama running?")


async def groq_stream(model, messages, opts, fmt=None, serving=None):
    if not config.GROQ_API_KEY:
        raise BackendError(400, "No GROQ_API_KEY in .env")
    req = {"model": model, "messages": messages, "stream": True}
    for k in ("temperature", "top_p", "seed"):
        if k in opts:
            req[k] = opts[k]
    if "num_predict" in opts:
        req["max_tokens"] = opts["num_predict"]
    if "gpt-oss" in model:
        req["reasoning_effort"] = "low"
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
    for attempt in range(3):                       # chat: don't keep the user waiting for minutes
        await _pace()
        start = time.time()
        out_tokens, usage, finish, sent = 0, {}, "stop", False
        try:
            async with _groq.stream("POST", config.GROQ_URL + "/chat/completions", json=req, headers=headers) as r:
                if r.status_code == 429 and attempt < 2:
                    await asyncio.sleep(min(float(r.headers.get("retry-after", 3)), 10))
                    continue
                if r.status_code == 429:
                    raise BackendError(429, "Groq's free tier is busy (rate limit). Wait about a minute, "
                                            "or click Reset all to go back to your local model.")
                if r.status_code in (401, 403):
                    raise BackendError(401, "Groq rejected the API key - check GROQ_API_KEY in .env.")
                if r.status_code >= 400:
                    raise BackendError(502, f"Groq error {r.status_code}: {(await r.aread())[:200]!r}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    d = json.loads(data)
                    usage = d.get("usage") or (d.get("x_groq") or {}).get("usage") or usage
                    for ch in d.get("choices", []):
                        piece = (ch.get("delta") or {}).get("content") or ""
                        if ch.get("finish_reason"):
                            finish = "length" if ch["finish_reason"] == "length" else "stop"
                        if piece:
                            out_tokens += 1
                            sent = True
                            yield "delta", piece
        except httpx.HTTPError as e:
            if sent:
                raise BackendError(502, f"Groq connection dropped: {e}")
            raise BackendError(502, f"Can't reach Groq: {e}")
        yield "done", {"done_reason": finish, "prompt_tokens": usage.get("prompt_tokens"),
                       "output_tokens": usage.get("completion_tokens") or out_tokens or 1,
                       "seconds": max(time.time() - start, 0.001)}
        return


def stream(spec, messages, opts=None, fmt=None, serving=None):
    kind, name = parse(spec)
    fn = ollama_stream if kind == "ollama" else groq_stream
    return fn(name, messages, opts or {}, fmt, serving)


async def chat(spec, messages, opts=None, fmt=None, serving=None):
    kind, name = parse(spec)
    fn = ollama_chat if kind == "ollama" else groq_chat
    return await fn(name, messages, opts or {}, fmt, serving)


# ---------------------------------------------------------------- detection (used by run.py)
def ollama_models():
    """Installed Ollama model names, or None if Ollama isn't running."""
    try:
        r = httpx.get(config.OLLAMA_URL + "/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


def has_ollama(installed, tag):
    if installed is None:
        return False
    return tag in installed or (":" not in tag and tag + ":latest" in installed)


def groq_ok():
    """(True, msg) if the Groq key works."""
    if not config.GROQ_API_KEY:
        return False, "no GROQ_API_KEY in .env"
    try:
        r = httpx.get(config.GROQ_URL + "/models", headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=8)
    except Exception as e:
        return False, f"can't reach Groq ({e.__class__.__name__})"
    if r.status_code in (401, 403):
        return False, "Groq rejected the key"
    return r.status_code < 400, f"Groq answered {r.status_code}"


def pull_model(name, progress=None):
    """Download a model into Ollama (like `ollama pull`), reporting progress. Blocking - run in a job."""
    total_done = None
    with httpx.stream("POST", config.OLLAMA_URL + "/api/pull", json={"model": name, "stream": True}, timeout=None) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama refused to download {name}: {r.read()[:200]!r}")
        for line in r.iter_lines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("error"):
                raise RuntimeError(f"Download failed: {d['error']}")
            if progress and d.get("total"):
                done, tot = d.get("completed", 0), d["total"]
                progress(int(done / 1e6), int(tot / 1e6), f"downloading {name}: {done / 1e6:.0f} / {tot / 1e6:.0f} MB")
            total_done = d.get("status")
    return {"model": name, "status": total_done}


def create_alias(alias, base, system=None, parameters=None):
    """(Re)define a model name on the Ollama server - like `ollama create alias -f Modelfile`.
    Works on a remote server too (e.g. through a Cloudflare tunnel)."""
    _caps.pop(alias, None)
    body = {"model": alias, "from": base, "stream": False}
    if system:
        body["system"] = system
    if parameters:
        body["parameters"] = parameters
    try:
        r = httpx.post(config.OLLAMA_URL + "/api/create", json=body, timeout=300)
        if r.status_code >= 400:     # older Ollama versions only accept a Modelfile string
            mf = f"FROM {base}\n" + (f'SYSTEM """{system}"""\n' if system else "") + \
                 "".join(f"PARAMETER {k} {v}\n" for k, v in (parameters or {}).items())
            r = httpx.post(config.OLLAMA_URL + "/api/create",
                           json={"model": alias, "modelfile": mf, "stream": False}, timeout=300)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Can't reach the Ollama server at {config.OLLAMA_URL}: {e}")
    if r.status_code >= 400:
        raise RuntimeError(f"Ollama could not create '{alias}' from '{base}': {r.text[:200]}")
