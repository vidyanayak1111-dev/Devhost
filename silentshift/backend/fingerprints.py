"""
fingerprints.py - "WHO is behind the name now?"  (SilentShift's USP)

Other drift tools stop at "behaviour changed". Research auditors (MMD / RUT tests) answer
"is this still model X - yes or no". SilentShift also tells you WHICH model it now behaves like.

How:
  1. Build a FINGERPRINT LIBRARY: run the same 15 canaries directly against every model you can
     reach yourself (your Ollama models, Groq models with your key). Each fingerprint stores the
     canary answers, the input-token count of every canary (= the model's tokenizer + chat template)
     and behaviour rates (JSON habits, refusals, ...). No user data - canaries are synthetic.
  2. On every check, the answers coming back from `bot-latest` are compared with every fingerprint:
        tokenizer match  (30%)  - same prompt, same token count?   (different tokenizer = different weights)
        answer match     (55%)  - temperature 0 + fixed seed: the same model gives (almost) the same words
        behaviour match  (15%)  - JSON fences, refusals, dosage kept, homework solved ...
  3. The report then says e.g. "bot-latest now behaves like llama3.2:1b (94% match);
     at baseline it matched qwen2.5:1.5b". If the best match is a different BUILD of the same model
     (q8 vs q4) that points at the SERVING layer, not the model layer.

Fingerprints are built from the customer side only (the same API anyone has), so the
"no provider internals" constraint holds. Stored in data/fingerprints.json.
"""

import json
import re
import time
from difflib import SequenceMatcher

import httpx

import config
from runner import build_prompt, build_request, load_json, measure

LIB_FILE = config.DATA / "fingerprints.json"
W_TOK, W_TEXT, W_BEHAV = 0.30, 0.55, 0.15
BEHAVIOUR = ["json_rate", "fence_rate", "refusal_rate", "homework_rate", "dosage_rate", "correct_rate"]

SIM_MODELS = [   # demo mode: the simulator's "public" models
    {"spec": "sim:production", "label": "Qwen 2.5 1.5B (simulated)", "core": "qwen2.5:1.5b"},
    {"spec": "sim:new", "label": "GPT-OSS 20B (simulated)", "core": "gpt-oss:20b"},
    {"spec": "sim:q8", "label": "Qwen 2.5 1.5B q8 build (simulated)", "core": "qwen2.5:1.5b"},
]


# ---------------------------------------------------------------- naming
def name_of(spec):
    return spec.split(":", 1)[1] if ":" in spec else spec


def family(spec):
    n = name_of(spec).split(":")[0].split("/")[-1].lower()
    return re.sub(r"[-_]?\d+(\.\d+)?b$", "", n)


def core(spec):
    """Same core = same model, maybe another build.  qwen2.5:1.5b-instruct-q8_0 -> qwen2.5:1.5b"""
    for m in SIM_MODELS:
        if m["spec"] == spec:
            return m["core"]
    n = name_of(spec).lower()
    if "/" in n and ":" not in n:                 # groq: openai/gpt-oss-20b
        n = n.split("/")[-1]
    return n.split("-instruct")[0].split("-q")[0].split("-fp")[0]


def label(spec):
    for m in SIM_MODELS:
        if m["spec"] == spec:
            return m["label"]
    kind = spec.split(":", 1)[0]
    return f"{name_of(spec)}" + (" (Groq)" if kind == "groq" else "")


# ---------------------------------------------------------------- library
def load():
    try:
        return json.loads(LIB_FILE.read_text()).get("entries", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(entries):
    LIB_FILE.write_text(json.dumps({"entries": entries}, indent=1))


def candidates():
    """Models the customer can reach directly to fingerprint."""
    if config.MODE != "real":
        return [m["spec"] for m in SIM_MODELS]
    import backends
    import model_setup
    specs = [f"ollama:{m}" for m in model_setup.usable(backends.ollama_models() or [])]
    if config.GROQ_API_KEY:
        specs += ["groq:openai/gpt-oss-20b", "groq:openai/gpt-oss-120b"]
    return specs


def _normal_messages(prompt):
    """Exactly what bot-latest normally receives: our normal request + the provider's normal instructions."""
    import injector
    cfg = injector.normal_config()
    body = build_request(cfg, [{"role": "user", "content": prompt}])
    if not any(m["role"] == "system" for m in body["messages"]):
        body["messages"].insert(0, {"role": "system", "content": config.SERVER_SYSTEM})
    lim = body["options"].get("num_predict")
    body["options"]["num_predict"] = min(lim, config.PROBE_MAX_TOKENS) if lim else config.PROBE_MAX_TOKENS
    return body


def fingerprint(spec, progress=None, base_done=0, total=None):
    """Run every canary once against one model, directly. Returns a profile (same shape as agent.summarise)."""
    import agent
    canaries = load_json(config.CANARIES)
    repeats = 1 if config.MODE == "real" else 2
    results, t0 = [], time.time()
    with httpx.Client(timeout=600) as client:
        for i, c in enumerate(canaries):
            body = _normal_messages(build_prompt(c))
            body["spec"] = spec
            for rep in range(repeats):
                start = time.time()
                r = client.post(config.PROVIDER_URL + "/reference/chat", json=body)
                if r.status_code >= 400:
                    try:
                        msg = r.json().get("detail")
                    except Exception:
                        msg = r.text
                    raise RuntimeError(f"Fingerprinting {spec} failed: {str(msg)[:200]}")
                data = r.json()
                data["_seconds"] = time.time() - start
                m = measure(c, data)
                m["repeat"] = rep
                results.append(m)
            if progress:
                progress(base_done + i + 1, total or len(canaries), f"fingerprinting {label(spec)}: {i + 1}/{len(canaries)}")
    rnd = {"settings_hash": "", "config": {}, "results": results, "repeats": repeats}
    prof = agent.summarise(rnd)
    prof.pop("config", None)
    cost = agent.total_cost([rnd])
    return {"spec": spec, "label": label(spec), "family": family(spec), "core": core(spec),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0, 1),
            "calls": cost["calls"], "tokens": cost["tokens"], "profile": prof}


def build(specs=None, progress=None):
    specs = [s for s in (specs or candidates()) if s]
    if not specs:
        raise RuntimeError("No models to fingerprint - install an Ollama model or add a Groq key.")
    entries, n = load(), len(load_json(config.CANARIES))
    errors = []
    for k, spec in enumerate(specs):
        try:
            entries[spec] = fingerprint(spec, progress, base_done=k * n, total=len(specs) * n)
            save(entries)
        except RuntimeError as e:
            errors.append(str(e))
    if errors and len(errors) == len(specs):
        raise RuntimeError(errors[0])
    return {"built": [s for s in specs if s in entries], "errors": errors}


def remove(spec):
    entries = load()
    entries.pop(spec, None)
    save(entries)


# ---------------------------------------------------------------- matching
def _sim(a, b):
    return SequenceMatcher(None, a, b).ratio()


def compare_profiles(now, fp):
    """How much does `now` (a check round summary) look like fingerprint profile `fp`? 0..1 each."""
    tn, tf = now.get("tokens") or {}, fp.get("tokens") or {}
    ids = [c for c in tn if c in tf and not c.startswith("long")]
    tok = None
    if ids:
        exact = sum(abs(tn[c] - tf[c]) <= 1 for c in ids) / len(ids)
        # same tokenizer with a constant amount of text added in front (e.g. a provider policy prompt)
        d = sorted(tn[c] - tf[c] for c in ids)
        med = d[len(d) // 2]
        shifted = sum(abs(x - med) <= 1 for x in d) / len(d) if abs(med) >= 3 else 0
        tok = max(exact, 0.8 * shifted)

    xn, xf = now.get("texts") or {}, fp.get("texts") or {}
    sims = [max(_sim(a, b) for a in xn[c] for b in xf[c]) for c in xn if c in xf and xn[c] and xf[c]]
    text = sum(sims) / len(sims) if sims else None

    diffs = [abs(now[k] - fp[k]) for k in BEHAVIOUR if now.get(k) is not None and fp.get(k) is not None]
    behav = 1 - sum(diffs) / len(diffs) if diffs else None

    parts = [(W_TOK, tok), (W_TEXT, text), (W_BEHAV, behav)]
    wsum = sum(w for w, v in parts if v is not None)
    score = sum(w * v for w, v in parts if v is not None) / wsum if wsum else 0.0
    r = lambda v: None if v is None else round(v, 3)
    return {"score": round(score, 3), "tokenizer": r(tok), "answers": r(text), "behaviour": r(behav)}


def identify(summary, entries=None):
    """Rank every fingerprint by similarity to this round. [] if the library is empty."""
    entries = load() if entries is None else entries
    out = []
    for spec, e in entries.items():
        out.append({"spec": spec, "label": e["label"], "family": e["family"], "core": e["core"],
                    **compare_profiles(summary, e["profile"])})
    return sorted(out, key=lambda x: -x["score"])


MATCH, CLOSE = 0.75, 0.55


def verdict(ranked):
    if not ranked:
        return None
    b = ranked[0]
    margin = b["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0)
    if b["score"] >= MATCH:
        kind, text = "match", f"Behaves like {b['label']} ({b['score']:.0%} match)"
    elif b["score"] >= CLOSE:
        kind, text = "close", f"Closest to {b['label']} ({b['score']:.0%}) - possibly a variant not in your library"
    else:
        kind, text = "unknown", f"Matches no model in your library (best {b['label']}, {b['score']:.0%}) - an unknown model"
    if len(ranked) > 1 and margin < 0.03 and kind != "unknown":
        if ranked[1]["core"] == b["core"]:
            text += f" · model confirmed; another build ({ranked[1]['label']}) answers the same, so the exact build is judged from speed"
        else:
            text += f" · almost identical to {ranked[1]['label']}, so these two cannot be told apart reliably"
    return {"kind": kind, "text": text, "best": b, "margin": round(margin, 3), "ranked": ranked[:5]}


def matrix():
    """Pairwise similarity of all fingerprints - shows how well the library can tell models apart."""
    entries = load()
    specs = list(entries)
    rows = [[compare_profiles(entries[a]["profile"], entries[b]["profile"])["score"] for b in specs] for a in specs]
    return {"specs": specs, "labels": [entries[s]["label"] for s in specs], "rows": rows}


def library_summary():
    return [{"spec": s, "label": e["label"], "family": e["family"], "core": e["core"], "time": e["time"],
             "calls": e["calls"], "tokens": e["tokens"], "seconds": e["seconds"],
             "identity_answer": (e["profile"]["texts"].get("style_identity") or [""])[0][:140]}
            for s, e in load().items()]
