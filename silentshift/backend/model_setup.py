"""
model_setup.py - decides which real model plays which role, and remembers your choice.

  production : what `bot-latest` normally runs on (default: your smallest Qwen in Ollama)
  new        : where the `model_swap` change moves it - must be a DIFFERENT model family
               (default: Groq GPT-OSS-20b; or another local family like llama3.2:1b)
  q8         : a q8_0 build of the SAME production model (enables `serving_quant`)

Saved to data/hidden/routes.json (the monitor never reads it). Your choices from the
dashboard's Models card are kept in data/hidden/routes_choice.json.
"""

import json
import re

import backends
import config

CHOICE_FILE = config.HIDDEN / "routes_choice.json"

# Small models that run well on a laptop (approximate download sizes)
SUGGESTED = [
    {"name": "qwen2.5:1.5b", "size": "≈1.0 GB", "note": "Recommended production model: fast and follows instructions well"},
    {"name": "llama3.2:1b", "size": "≈1.3 GB", "note": "Recommended model-swap target: a different family, so a swap is clearly a new model (no Groq limits)"},
    {"name": "qwen2.5:0.5b", "size": "≈0.4 GB", "note": "Fastest, but weaker answers"},
    {"name": "gemma3:1b", "size": "≈0.8 GB", "note": "Another small family, also a good swap target"},
    {"name": "qwen2.5:1.5b-instruct-q8_0", "size": "≈1.6 GB", "note": "q8 build of qwen2.5:1.5b: enables the serving_quant change"},
]


def family(tag):
    """'qwen2.5:1.5b-instruct-q8_0' -> 'qwen2.5'"""
    return tag.split(":")[0].split("/")[-1].lower()


def size_of(tag):
    m = re.search(r"(\d+(?:\.\d+)?)b", tag.split(":", 1)[-1].lower())
    return float(m.group(1)) if m else 99.0


def usable(installed):
    alias = config.OLLAMA_ALIAS
    return [m for m in (installed or []) if "embed" not in m.lower() and m not in (alias, alias + ":latest")]


def pick_qwen(installed):
    """Smallest Qwen (fastest on a laptop), or the smallest model of any kind."""
    pool = [m for m in usable(installed) if "qwen" in m.lower() and "q8" not in m.lower()] or \
           [m for m in usable(installed) if "q8" not in m.lower()] or usable(installed)
    return sorted(pool, key=size_of)[0] if pool else None


def find_q8(prod_tag, installed):
    """A q8 build of exactly the same model and size, e.g. qwen2.5:1.5b -> qwen2.5:1.5b-instruct-q8_0."""
    fam, size = family(prod_tag), prod_tag.split(":", 1)[-1].split("-")[0]
    for m in installed or []:
        if m != prod_tag and family(m) == fam and "q8" in m.lower() and m.split(":", 1)[-1].startswith(size):
            return m
    return None


def load_choice():
    try:
        return json.loads(CHOICE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_choice(production=None, new=None):
    choice = load_choice()
    if production is not None:
        choice["production"] = production
    if new is not None:
        choice["new"] = new
    CHOICE_FILE.write_text(json.dumps(choice, indent=2))


def available_specs(installed, groq):
    specs = [f"ollama:{m}" for m in usable(installed)]
    if groq:
        specs += ["groq:openai/gpt-oss-20b", "groq:openai/gpt-oss-120b"]
    return specs


def resolve(installed, groq):
    """Work out the routes from your choice (if still valid), .env settings, or sensible defaults."""
    choice, specs = load_choice(), available_specs(installed, groq)
    prod = choice.get("production") if choice.get("production") in specs else (config.PRODUCTION or "")
    if not prod:
        q = pick_qwen(installed)
        prod = f"ollama:{q}" if q else ("groq:openai/gpt-oss-20b" if groq else "")
    if not prod:
        return None

    new = choice.get("new") if choice.get("new") in specs else (config.MODEL_NEW or "")
    if new and family(new.split(":", 1)[1]) == family(prod.split(":", 1)[1]) and new.startswith(prod.split(":")[0]):
        new = ""          # same family is not a clean model change
    if not new:
        if prod.startswith("ollama:"):
            others = [m for m in usable(installed) if family(m) != family(prod.split(":", 1)[1])]
            # prefer a small local model of another family (fast, no rate limits), else Groq
            local = sorted(others, key=size_of)[0] if others else None
            new = f"ollama:{local}" if local and size_of(local) <= 4 else ("groq:openai/gpt-oss-20b" if groq else
                                                                            (f"ollama:{local}" if local else ""))
        elif groq:
            new = "groq:openai/gpt-oss-120b"
    if new.startswith("groq:") and not groq:
        new = ""

    q8 = None
    if prod.startswith("ollama:"):
        q8 = config.OLLAMA_Q8 or find_q8(prod.split(":", 1)[1], installed)
        q8 = f"ollama:{q8}" if q8 and not q8.startswith("ollama:") else q8
    routes = {"production": prod, "new": new or None, "q8": q8 or None, "base": None, "alias": None}
    if prod.startswith("ollama:"):
        # bot-latest becomes a real model name ON the Ollama server, built from the chosen base model.
        routes.update(base=prod, alias=f"ollama:{config.OLLAMA_ALIAS}", production=f"ollama:{config.OLLAMA_ALIAS}")
    config.ROUTES_FILE.write_text(json.dumps(routes, indent=2))
    return routes


def current():
    try:
        return json.loads(config.ROUTES_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
