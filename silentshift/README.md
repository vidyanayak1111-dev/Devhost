# SilentShift - "The Model Changed Without Changing Its Name" (PS-03)

A customer-side monitor for the AI behind a model name. When `bot-latest` silently changes, SilentShift tells you
**that** it changed, **which layer** changed (model · serving · safety · request config), **who is answering now**
(fingerprint match against a library of known models) and **which users are hurt**. No provider internals,
no user prompts stored, blind evaluation built in.

**Presenting? Read [`docs/JUDGES_GUIDE.md`](docs/JUDGES_GUIDE.md)** - USP, prior-art comparison, demo script, Q&A.

## Run it

**Windows:** install Python (tick "Add python.exe to PATH") and, for real mode, Ollama. Double-click **`start.bat`**
→ **1 = Demo** (simulated, instant) or **2 = Real** (your Ollama models + optional Groq). Paste a `GROQ_API_KEY` in
`.env` if you want Groq (optional).

**Mac/Linux:** `bash start.sh` · **Manual:** `pip install -r requirements.txt` then `python run.py` / `python run.py --real`

**Docker (everything in containers, incl. Ollama + two small models):**
```
docker compose up --build
```
Then open **http://localhost:8000/monitor.html** (console) and **http://localhost:8000** (chatbot).

## First 3 minutes in the console
1. **Who's behind it?** → tick your models → **Fingerprint selected** (15 calls per model, once).
2. **Overview → Learn baseline** (with nothing changed).
3. **Run check** → green, identity confirmed.
4. Be the provider: `python provider_tools/provider.py swap llama3.2:1b` (or **Provider lab → model_swap**).
5. The auto-check (or **Run check**) turns the Overview red: likely cause **model**, "A different model is answering:
   llama3.2:1b", user impact, time-to-detect on the timeline. **Reports → Export .md**.
6. **Provider lab → Reset all** between changes.

Recommended models (fast on a laptop): `qwen2.5:1.5b` (production) and `llama3.2:1b` (swap target) - one click each
in **Provider lab → Models**. Optional: `qwen2.5:1.5b-instruct-q8_0` enables the quantisation change.

## Console tabs
| Tab | What's there |
|---|---|
| Overview | status, KPIs, who's behind the name, drift timeline with time-to-detect, latest report |
| Who's behind it? | fingerprint library, match breakdown, similarity matrix (**the USP**) |
| Reports | full report (evidence / likely cause / what can't be proven), history, export |
| Live chatbot | the production app, embedded |
| Provider lab | one-click silent changes, server console (any FROM/SYSTEM/PARAMETER), models, change log |
| Evaluation | randomised blind trials: detection, attribution, false alarms, missed, cost, identification |
| Privacy | privacy audit + everything that is stored (metadata only) |
| How it works | pipeline, attribution rules, comparison with existing tools |

## Folder map
`backend/` provider, connectors, gateway, monitor (agent), fingerprints, injector, evaluation, server ·
`web/` console + chatbot · `provider_tools/` be the provider (CLI, Cloudflare tunnel) · `docs/` judges' guide ·
`data/` created at runtime (metadata, baseline, fingerprints, reports, results; `data/hidden/` = ground truth).
