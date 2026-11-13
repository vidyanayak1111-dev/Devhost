"""
harness.py - the ONE place that talks to an LLM.

The rest of SilentShift (evaluation engine, judge, agent) only calls
    generate(provider, model, prompt, ...)
and gets back the same dictionary, whichever provider answered. So you can
switch between Ollama, Groq and Gemini without rewriting anything else.

Providers
    ollama : local models. Goes through our FastAPI proxy (port 8000) by default.
    groq   : hosted open models (fallback if local is too slow). Needs GROQ_API_KEY.
    gemini : Google Gen AI SDK (pip install google-genai). Needs GEMINI_API_KEY.

Settings come from environment variables (never hard-code API keys):
    OLLAMA_URL        default http://127.0.0.1:8000   (the proxy)
    GROQ_API_KEY      your Groq key
    GROQ_URL          default https://api.groq.com/openai/v1
    GEMINI_API_KEY    your Gemini key
    SILENTSHIFT_FALLBACK  "groq" to fall back to Groq when Ollama fails
"""

import os
import re
import time

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:8000")
GROQ_URL = os.environ.get("GROQ_URL", "https://api.groq.com/openai/v1")
DEFAULT_MODELS = {
    "ollama": "bot-latest",
    "groq": os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b"),
    "gemini": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
}

THINK_TAGS = re.compile(r"<think>.*?</think>", re.DOTALL)


class ProviderError(Exception):
    pass


def _clean(text):
    """Remove <think>...</think> blocks that reasoning/distilled models add."""
    return THINK_TAGS.sub("", text or "").strip()


def _messages(prompt, system):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


# ------------------------------------------------------------------ Ollama
def _ollama(model, prompt, system, options, fmt):
    body = {"model": model, "messages": _messages(prompt, system),
            "stream": False, "options": options or {}}
    if fmt:
        body["format"] = fmt
    start = time.time()
    try:
        r = httpx.post(OLLAMA_URL + "/api/chat", json=body, timeout=600)
        r.raise_for_status()
    except httpx.HTTPError as e:
        detail = ""
        if isinstance(e, httpx.HTTPStatusError):
            detail = " | " + e.response.text[:300]
        raise ProviderError(f"Ollama not reachable at {OLLAMA_URL}: {e}{detail}")
    d = r.json()
    speed = None
    if d.get("eval_count") and d.get("eval_duration"):
        speed = d["eval_count"] / (d["eval_duration"] / 1e9)
    return {
        "text": _clean(d.get("message", {}).get("content", "")),
        "prompt_tokens": d.get("prompt_eval_count"),
        "output_tokens": d.get("eval_count"),
        "tokens_per_sec": speed,
        "seconds": time.time() - start,
        "done_reason": d.get("done_reason"),
        "model_returned": d.get("model"),
    }


# ------------------------------------------------------------------ Groq
def _groq(model, prompt, system, options, fmt):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise ProviderError("GROQ_API_KEY is not set")
    options = options or {}
    body = {"model": model, "messages": _messages(prompt, system),
            "temperature": options.get("temperature", 0),
            "max_tokens": options.get("num_predict", 512)}
    if "top_p" in options:
        body["top_p"] = options["top_p"]
    if "seed" in options:
        body["seed"] = options["seed"]
    if fmt == "json":
        body["response_format"] = {"type": "json_object"}
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"        # keep thinking short so the answer fits
        body["max_tokens"] = max(body["max_tokens"], 1024)
    start = time.time()
    try:
        r = httpx.post(GROQ_URL + "/chat/completions", json=body, timeout=120,
                       headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ProviderError(f"Groq request failed: {e}")
    d = r.json()
    secs = time.time() - start
    usage = d.get("usage", {})
    choice = d["choices"][0]
    out = usage.get("completion_tokens")
    return {
        "text": _clean(choice["message"].get("content", "")),
        "prompt_tokens": usage.get("prompt_tokens"),
        "output_tokens": out,
        "tokens_per_sec": (out / secs) if out and secs else None,
        "seconds": secs,
        "done_reason": "length" if choice.get("finish_reason") == "length" else "stop",
        "model_returned": d.get("model"),
    }


# ------------------------------------------------------------------ Gemini
def _gemini(model, prompt, system, options, fmt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ProviderError("GEMINI_API_KEY is not set")
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ProviderError("Install the Google Gen AI SDK: pip install google-genai")
    options = options or {}
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=options.get("temperature", 0),
        max_output_tokens=options.get("num_predict", 512),
        response_mime_type="application/json" if fmt == "json" else None,
    )
    start = time.time()
    try:
        resp = genai.Client(api_key=key).models.generate_content(
            model=model, contents=prompt, config=config)
    except Exception as e:
        raise ProviderError(f"Gemini request failed: {e}")
    secs = time.time() - start
    usage = getattr(resp, "usage_metadata", None)
    out = getattr(usage, "candidates_token_count", None) if usage else None
    return {
        "text": _clean(resp.text),
        "prompt_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
        "output_tokens": out,
        "tokens_per_sec": (out / secs) if out and secs else None,
        "seconds": secs,
        "done_reason": "stop",
        "model_returned": getattr(resp, "model_version", model),
    }


PROVIDERS = {"ollama": _ollama, "groq": _groq, "gemini": _gemini}


def generate(provider="ollama", model=None, prompt="", system=None, options=None, fmt=None):
    """Ask one question. Returns a dict with text, tokens, speed, etc.
    If Ollama fails and SILENTSHIFT_FALLBACK=groq, Groq answers instead
    (the result then says provider='groq', so reports stay honest)."""
    model = model or DEFAULT_MODELS[provider]
    try:
        result = PROVIDERS[provider](model, prompt, system, options, fmt)
        result["provider"] = provider
        result["model_requested"] = model
        return result
    except ProviderError as e:
        fallback = os.environ.get("SILENTSHIFT_FALLBACK")
        if provider == "ollama" and fallback == "groq":
            print(f"[harness] {e} -> falling back to Groq")
            result = _groq(DEFAULT_MODELS["groq"], prompt, system, options, fmt)
            result["provider"] = "groq"
            result["model_requested"] = DEFAULT_MODELS["groq"]
            result["fallback_used"] = True
            return result
        raise


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    m = sys.argv[2] if len(sys.argv) > 2 else None
    print(generate(p, m, "What is 5 x 6? Reply with only the number.",
                   options={"temperature": 0, "seed": 42}))