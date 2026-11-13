# SilentShift: "The Model Changed Without Changing Its Name" (PS-03)

A customer-side monitor that **detects** when the AI behind a model name changes behaviour and
**attributes** the change to a layer (**model**, **serving stack**, **safety settings** or **our own
request configuration**). It has no access to provider internals and never stores user prompts.
It comes with a working chatbot (the "production app") and a dashboard, all connected.

## Run it (one command)

```bash
pip install -r requirements.txt
python run.py            # simulated provider: no models or GPU needed
```

* Chatbot: http://localhost:8000
* Monitor dashboard: http://localhost:8000/monitor.html

### With real models (Ollama)
```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:3b
ollama pull llama3.2:3b-instruct-q8_0
python run.py --real
```
The provider wrapper sends the alias `bot-latest` to a real Ollama tag. It also applies the hidden
changes on top: a different tag for alias/quantisation, an injected `num_ctx`, and a guard step for safety.
**No `ollama cp` is needed, and all 7 changes work in real mode.** Real models vary more than the mock,
so learn the baseline after the models are warm (send one chat first).

### With a real cloud AI API (Groq, OpenAI, Gemini, OpenRouter...)
1. Copy `.env.example` to `.env` (next to `run.py`) and fill in your key and model names.
2. `python run.py --api`

`SS_API_MODEL` is what `bot-latest` normally points to. The `alias_qwen` change silently moves it to
`SS_API_MODEL_NEW`. Pick two models from **different families** (e.g. Llama vs Qwen on OpenRouter) for the
clearest demo. Two models of the same family share a tokenizer, so a move between them may be reported
as "serving" (a known limit, documented below). The context and safety changes are applied by the
wrapper. The `quant_q8` change only appears if you set `SS_API_MODEL_SERVING`.
Free tiers have rate limits: run live checks in API mode, and the 40-trial evaluation in mock mode
(or with 10 trials).

## Demo script (about 3 minutes)
1. **Dashboard → Learn baseline** (nothing changed). The monitor asks 15 canaries 3 times, over 3 rounds.
2. **Run check**. Result is "Behaving normally". It took 15 calls (cheap sentinel stage only).
3. **Provider simulator → `alias_qwen`**, then **Run check**. Result: *Change detected · likely MODEL*.
   The left side shows the evidence (tokenizer fingerprint changed, wording very different).
   The right side shows the inference, a ranking of all layers and what cannot be proven.
4. Open the **Chatbot**. The top pill is now red. Type `/status` and the bot explains the change itself.
5. Repeat with `quant_q8` (serving), `ctx_1024` (serving), `safety_on` (safety) and
   `temp_09` / `short_answers` / `new_system` (request config). Let the judges pick the order.
6. **Run privacy audit**. A unique secret goes through the chat path and every stored file is scanned. Result: passed.
7. **Run evaluation (40-60 trials)**. Shows detection rate, attribution accuracy, false-alarm and missed-change rates,
   probe cost and a confusion matrix, all from blind trials scored against a hidden log.

## Architecture
```
Browser ── chat UI (Nova) ──► POST /api/chat ─┐
        └─ dashboard ───────► /monitor/*      │  server.py :8000
Other Ollama clients ───────► /ollama/api/chat┤
Canary runner (probe header) ► /ollama/api/chat┘
                                   │ gateway.forward_chat  → metadata_log.jsonl (NO text)
                                   ▼
                         provider.py :11500  (black box: mock or real-Ollama wrapper)
                         hidden state: data/hidden/provider_state.json
```

| File | Job |
|---|---|
| `run.py` | Starts the provider and the server |
| `backend/server.py` | Chat API, Ollama-compatible proxy, monitor API, demo controls, background jobs, auto-watch |
| `backend/gateway.py` | The one monitored path. Logs metadata only (settings **hash**, tokens, speed, finish reason) |
| `backend/runner.py` | Builds requests from `app_config.json` (same builder as the chatbot) and runs canaries |
| `backend/agent.py` | Baseline, sentinel→confirm check, evidence, layer attribution, report |
| `backend/injector.py` | Controlled changes. Ground truth goes to `data/hidden/change_log.json` (never read by the agent) |
| `backend/evaluation.py` | Randomised blind trials and scoring (`data/results.md`, `results.json`) |
| `backend/provider.py` | Mock models with realistic noise, or a wrapper around real Ollama |
| `web/` | Chatbot (`index.html`) and dashboard (`monitor.html`) |

## How attribution works (evidence → layer)
| Evidence (observed) | Points to |
|---|---|
| Our config hash differs, with a field-level diff | request config (+10, seen directly) |
| The app's latest live request uses a different settings hash | request config (+2) |
| Identical prompts count different input tokens (tokenizer fingerprint) | model (+5) |
| Wording very different from every baseline answer | model (+3) |
| Borderline canaries refused while the harmless control is still answered, same tokenizer | safety (+4…+6) |
| Long prompt truncated / needle forgotten, same tokenizer | serving (+4) |
| Speed outside the noise band, same tokenizer | serving (+3) |
| Wording only slightly different, same tokenizer | serving (+2) |

Normal variation: thresholds come from the baseline. Speed uses max(12%, 4 standard errors). Wording
uses the similarity the baseline had with itself. A clue counts only if both the sentinel and the confirmation round show it.

## Results (simulated provider, 60 blind trials)
Detection 100% · attribution 100% · false alarms 0% · missed 0% ·
quiet check 15 calls / ~2.2k tokens · check with a change 60 calls / ~8.4k tokens (≈ $0.0016 at example prices).
See `docs/results_mock.md`. **Re-run with `--real` before presenting real-model numbers.**

## Known limits and failure cases (be upfront with judges)
* **Fine-tune with the same tokenizer**: a new version of the same family looks like "serving"
  (same tokenizer, small wording change). The report says this in its caveat.
* **Quantisation vs hardware**: both show up as "same tokenizer, different speed". We report "serving", not which one.
* **Safety vs a more cautious model**: the harmless control and the tokenizer help, but a new, more cautious model with the same
  tokenizer would be called "safety".
* **Simultaneous changes**: a config change gets +10 and dominates. The other layers still appear under "also possible".
* **Mock scores are an upper bound**. The mock's changes are clean. Real models have more noise; expect some false alarms
  on speed if the machine is busy (raise the 12% in `agent.py`).
* Only the browser keeps chat history (localStorage). The server never writes prompts or answers.

## What changed from the first version
* Fixed: `proxy.py` forwarded to port 8000, its own port, so it would loop forever. It now goes to the provider.
* Chat UI connected through same-origin `/api/chat` (it used a temporary cloudflare URL and had no CORS). The server adds the
  system prompt and options, so **chat and canaries share one request builder**.
* New dashboard: evidence vs cause, layer ranking, caveats, cost, demo panel, privacy audit, evaluation, history.
* Cheaper checks: a 15-call sentinel stage, with a 45-call confirmation only when something looks off (it was 69 calls every time).
* Noise-aware thresholds, a harmless control canary, a field-level config diff and passive live-traffic metadata.
* The mock now has realistic noise (paraphrase variants, ±8% speed), so 0% false alarms means something.
* Real mode supports all seven changes through the provider wrapper.
* API mode: connect any OpenAI-compatible cloud API, with retry on rate limits.