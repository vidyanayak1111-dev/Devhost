# SilentShift - Judges' Guide (PS-03)

> **SilentShift is a customer-side monitor for the AI behind a model name.** When `bot-latest` silently changes,
> it answers the four questions an engineer asks at 3 a.m.:
> **Did it change? Which layer changed? Who is answering now? Who is hurt?**
> It never sees provider internals and never stores user prompts.

---

## 0. The USP in one sentence

**Other tools stop at "behaviour changed". SilentShift also *names the replacement*:** "bot-latest now behaves like
`llama3.2:1b` (98% match); at baseline it matched `qwen2.5:1.5b`", together with the layer (model / serving / safety /
request config), the user impact and the evidence, all from the customer side.

How: a **fingerprint library**. SilentShift asks the same 15 synthetic canaries directly to every model *you* can
reach (your Ollama models, Groq with your key) and stores each model's answers, tokenizer counts and habits. Every
check is matched against the library. If the best match is a different **build** of the same model (q8 vs q4), that
points at **serving**, not the model. This also gives the attribution engine a new, strong clue.

### What exists already, and what we add (prior art we studied)

| Approach | Examples | Detects change | Which layer | Names the new model | User impact | No prompts stored | Blind evaluation |
|---|---|---|---|---|---|---|---|
| LLM observability | Langfuse, Helicone, LangSmith, Phoenix | logs only | ✗ | ✗ | ✗ | stores prompts | ✗ |
| Drift monitors | DriftWatch, dedrift, Evidently | ✓ | own config only | ✗ | scores | varies | ✗ |
| Research auditors | MMD test (Gao et al.), RUT (ICLR 2026), Cai et al. 2025, IRIS | ✓ yes/no | ✗ | vs. one claimed model | ✗ | ✓ | offline |
| LLM fingerprinting | LLMmap (Pasquini et al.) | ✗ (not a monitor) | ✗ | ✓ | ✗ | ✓ | ✗ |
| **SilentShift** | this project | **✓ noise-aware, 2-stage** | **✓ 4 layers + confidence** | **✓ fingerprint library** | **✓ medical / homework / parser** | **✓ audited** | **✓ randomised, blind** |

Say it like this: *"Research shows you can tell **whether** an API still serves the claimed model (MMD, RUT) and
that you can **identify** a model from a few queries (LLMmap). Monitoring tools show **that** behaviour drifted.
Nobody combines them into one practical monitor that says which layer changed, who is answering now, and which
users are affected. That's SilentShift."*

---

## 1. The problem in one minute

An app sends `bot-latest` to an AI provider and gets answers back. The name hides four layers:

| Layer | Example of a silent change |
|---|---|
| **Model** | the alias `bot-latest` is moved to a newer or different model |
| **Serving** | same model, but new hardware, quantisation or a smaller context window |
| **Safety** | the provider tightens its safety policy |
| **Request config** | *our own* team changes temperature, token limit or system prompt |

Users then quietly get worse answers. Medical summaries drop doses, tutoring bots refuse homework, parsers break on a
new format. The team can't tell **which layer** to fix, or **what** they are now running on.

---

## 2. Demo script (about 5 minutes)

Start: `start.bat` → **2 (Real mode)**, or `docker compose up`. Open **http://localhost:8000/monitor.html**.
The console has tabs on the left: **Overview · Who's behind it? · Reports · Live chatbot · Provider lab · Evaluation ·
Privacy · How it works**.

*Before the demo (once):* **Who's behind it?** → tick your models → **Fingerprint selected** (15 calls per model),
then **Overview → Learn baseline**.

1. **Live chatbot tab.** Ask a question. "Our production support bot. It calls `bot-latest` on an Ollama server."
2. **Overview → Run check** → green **"Behaving normally"**, and the *Who's behind it* card says
   **"Identity confirmed: qwen2.5:1.5b (100%)"**. "Only 15 calls - when nothing changed, we stop there."
3. **Be the provider.** Open a terminal: `python provider_tools/provider.py swap llama3.2:1b`
   (or Provider lab → **model_swap**). "I just changed what is behind the name. SilentShift was not told."
4. Wait for the auto-check (or click **Run check**). The **Overview** turns red:
   - **Who's behind it:** *"A different model is answering: llama3.2:1b (98% match). At baseline: qwen2.5:1.5b."*
   - **Drift timeline:** the dashed marker is the real change (operator view), the red bar the detection:
     **"time-to-detect N s"**.
   - **Latest report:** likely cause **Model**, with the evidence and **"Users would notice: patients - medical
     summaries now drop doses; integrations - JSON format changed."**
5. **Reports tab:** evidence (facts) vs likely cause (inference) vs what this cannot prove. **Export .md**.
6. **Provider lab → Reset all**, then **serving_context** → **Serving** ("same model - fingerprint still matches
   Qwen - but long input is cut"). Then **safety_policy** → **Safety** (refuses homework, still answers the harmless
   control). Then **config_max_tokens** → **Request config** (our own config hash; exact field shown).
7. **Who's behind it?** tab: the match breakdown (tokenizer / answers / behaviour) and the **similarity matrix** -
   "this is how well the library tells models apart; q4 and q8 builds look alike, so a build match means serving."
8. **Privacy tab → Run privacy audit** → a secret goes through the chatbot, we scan every stored file: not found.
9. **Evaluation tab:** detection, attribution accuracy, false alarms, missed changes, cost **and replacement model
   named** - blind trials scored against a hidden log.

Tip: in the chatbot, type `/status` and the bot explains its own model's health.

---

## 3. Architecture

```
 Chatbot (web)  ──POST /api/chat──┐
                                  │                     ┌───────────────────────────────┐
 SilentShift monitor              │                     │  PROVIDER  (black box)        │
   canary runner ─POST /probe/chat┤                     │  bot-latest = a real model on │
                                  ▼                     │  the Ollama server            │
                         ┌─────────────────┐            │  (FROM, SYSTEM, PARAMETERs)   │
                         │  GATEWAY        │──────────▶ │  changed by: provider.py CLI, │
                         │  metadata only  │            │  lab buttons, server console  │
                         └────────┬────────┘            └───────────────────────────────┘
                                  │
                     metadata_log.jsonl  (no prompts, no answers)

 Fingerprint library: the same canaries sent DIRECTLY to each public model you can reach
 (Ollama tags, Groq models) → data/fingerprints.json → matched on every check
```

| File | Job |
|---|---|
| `backend/provider.py` | The "AI provider": forwards `bot-latest`; `/reference/chat` = direct access to a named public model (for fingerprints) |
| `backend/backends.py` | Real connectors: Ollama and Groq (paced under the free-tier limit) |
| `backend/gateway.py` | The one monitored path. Logs metadata only |
| `backend/runner.py` | Sends canaries and measures each answer |
| `backend/agent.py` | Baseline, comparison (facts), attribution (inference), report |
| `backend/fingerprints.py` | **USP:** fingerprint library, matching, identity verdict, similarity matrix |
| `backend/injector.py` | Controlled changes + server console; hidden change log |
| `backend/evaluation.py` | Randomised blind trials and scoring (incl. identification accuracy) |
| `backend/server.py` | Web app: chatbot API, console API, auto-checks, report export |
| `web/monitor.html` | The tabbed console · `web/index.html` the chatbot |
| `provider_tools/provider.py` | Be the provider from any terminal (works on remote Ollama too) |
| `Dockerfile`, `docker-compose.yml` | One-command setup with Ollama in a container |

---

## 4. The provider is a REAL Ollama server (and can be remote)

In real mode, `bot-latest` is **a real model on the Ollama server**, defined like a provider would with
`ollama create bot-latest` (base model, instructions, settings). The server can be another machine reached through a
**Cloudflare tunnel** (`provider_tools/start_tunnel.bat`, or `docker compose --profile tunnel up`).

Changes happen **on the server**, and SilentShift is not told. Three ways:
- **Terminal:** `python provider_tools/provider.py swap|quant|context|slow|safety|system|normal` (Windows menu:
  `change_bot_latest.bat`). It reads SilentShift's own base model, so `normal` always restores what was learned as
  normal, and downloads missing models itself. `--url https://…trycloudflare.com` works on a remote server.
- **Provider lab → one-click changes** (the 8 controlled changes).
- **Provider lab → Server console:** any `FROM` / `SYSTEM` / `PARAMETER` - exactly an `ollama create`.

SilentShift notices by itself: **auto-check** every 2 minutes, a **live-traffic trigger** (chats suddenly much
faster/slower, or our settings changed - metadata only), and optionally a check **right after a lab change**.
Then the report updates, a toast appears, the timeline shows time-to-detect, and the **chatbot shows a red banner**.

### Docker
```
docker compose up --build                  # Ollama + qwen2.5:1.5b + llama3.2:1b + SilentShift (real mode)
docker compose --profile demo up --build   # adds a demo-mode copy on :8001
docker compose --profile tunnel up         # shares Ollama through Cloudflare
docker compose exec silentshift python provider_tools/provider.py swap llama3.2:1b
```

---

## 5. The controlled changes

| Change | Layer | How it is done (real mode) |
|---|---|---|
| `model_swap` | Model | `bot-latest` rebuilt `FROM llama3.2:1b` (or rerouted to GPT-OSS on Groq) |
| `serving_quant` | Serving | rebuilt `FROM` the **q8_0 build** of the same model |
| `serving_context` | Serving | `PARAMETER num_ctx 512` |
| `serving_slow` | Serving | `PARAMETER num_thread 1` + `num_gpu 0` (weaker hardware) |
| `safety_policy` | Safety | a stricter `SYSTEM` policy |
| `config_temperature` | Request config | our temperature 0 → 0.9 |
| `config_max_tokens` | Request config | our max tokens 512 → 8 |
| `config_system` | Request config | our own system prompt changed |

**4 layers, 8 changes** (the problem asks for at least 3 layers). Demo mode simulates them so 40+ blind trials run in seconds.

---

## 6. The canaries (15 synthetic questions)

| Canary | What it catches |
|---|---|
| 3 exact questions (17×23, capital, reverse) | correctness; stable token fingerprint |
| 3 style questions (identity, sea, rainbow) | wording drift; self-description; **fingerprint matching** |
| **Medical letter summary** | **does it keep every dose?** (500 mg, 5 mg) |
| **Homework: solve 2x+3=11** | **is homework refused or wrong?** |
| **2 parser tasks (JSON)** | **does the raw output still parse?** Code fences break parsers |
| 3 borderline safety questions | refusals going up or down |
| 1 harmless control (boil an egg) | separates "targeted policy" from "everything broken" |
| 1 long input with a secret code at the start | context-window truncation |

---

## 7. How detection works

1. **Baseline.** Every canary asked several times. SilentShift stores normal answers and their spread (wording
   similarity between rounds, speed mean/spread, rates, token counts).
2. **Sentinel:** every canary once (**15 calls**). All signals in range → *normal*, stop.
3. **Confirm:** only if something looked off, a fuller round. **A clue counts only if both rounds show it.**
4. **Noise-aware thresholds:** rates must move 20 points beyond anything the baseline showed; speed beyond
   max(25%, 4 standard errors); wording versus the baseline's similarity with itself.

## 8. How attribution works (evidence → layer)

Every clue gives points to the layer it points at. The report shows the ranking, alternatives and a
**"what this cannot prove"** caveat.

| Evidence (observed) | Points to | Why |
|---|---|---|
| Our config hash changed (exact field diff) | **Request config** (+10) | seen directly |
| **Answers now match another model's fingerprint** | **Model** (+4) | the library names the replacement |
| **Answers match another build of the same model** | **Serving** (+3) | same model, different numerics |
| Token counts changed **by different amounts** | **Model** (+5) | a different tokenizer = different weights |
| Token counts changed **by the same amount** on every prompt | **Safety** (+2) | the same text was added in front |
| Self-description / wording / format habits changed | **Model** | a different model talks differently |
| Borderline + homework refused, control still answered | **Safety** | a targeted policy |
| Long input cut, **same tokenizer** | **Serving** | smaller context window |
| Speed changed, same tokenizer and answers | **Serving** | other hardware or build |

### The fingerprint match (USP)
For each library model: `score = 30% tokenizer + 55% answers + 15% behaviour`
- **tokenizer:** share of canaries with the same input-token count (±1). A constant offset (text added in front)
  still counts as the same tokenizer.
- **answers:** temperature 0 + fixed seed → the same model gives (almost) the same words.
- **behaviour:** JSON fences, refusals, doses kept, homework solved, exact answers.

Verdict: ≥75% *match*, 55-75% *closest (variant?)*, <55% *unknown model*. If the top two are builds of the same model,
the report says so honestly ("model confirmed; exact build judged from speed").

---

## 9. Results

Run them yourself: **Evaluation tab → Run evaluation**.

**Demo mode (simulated provider, 40 randomised blind trials):**

| Metric | Result |
|---|---|
| Detection rate | 100% (23/23) |
| Attribution accuracy | 100% (23/23) |
| False-alarm rate | 0% (0/17) |
| Missed-change rate | 0% (0/23) |
| **Replacement model named** | **100% (5/5)** |
| Probe cost, nothing changed | 15 calls, ~1.3k tokens |
| Probe cost, change found | 60 calls, ~5.4k tokens |

**Real mode:** run it before presenting (20–40 min), then copy `data/results_real.md` here. Demo scores are an upper
bound - real models are noisier, which is why we report both.

Cost: a quiet check is ~1.4k tokens ≈ **$0.0002** at Groq GPT-OSS-20b prices. A fingerprint costs 15 calls per model, once.

---

## 10. How each constraint is met

| Constraint | How |
|---|---|
| No provider internals | Monitor sees only answers, token counts, timing. Fingerprints are built through the same public API any customer has. Ground truth sits in `data/hidden/`, never read by the monitor. |
| Production prompts private | Gateway stores metadata only; **privacy audit** proves it live. |
| Normal variation | Baseline learns spread; noise-aware thresholds; sentinel + confirm; measured false-alarm rate. |
| Probe cost practical | 15 calls when quiet; cost on every report; Groq paced. |
| Evidence vs cause separated | Report: evidence → likely cause + ranking → alternatives → what can't be proven. |
| Randomised controlled evaluation | Blind trials vs hidden log: detection, attribution, false alarms, missed, cost, identification. |

---

## 11. Honest limitations (say these before the judges ask)

* **The library only knows models you fingerprinted.** An unseen model shows as "unknown model" (still detected and
  attributed to *model*), not by name.
* **Builds of the same model** (q4/q8) answer almost identically; we report *serving* and say the exact build is judged
  from speed. Hardware vs quantisation can't be separated from outside.
* **Same-family upgrades** (GPT-OSS-20b → 120b) share a tokenizer and can look alike.
* **A more cautious new model** with the same tokenizer would look like a safety change - the caveat says so.
* **Periodic checks:** a change is caught at the next check (the timeline shows time-to-detect).
* **Demo-mode scores are an upper bound.** Real-mode numbers are the honest ones.

## 12. Questions judges may ask

**"How is this different from LLMmap?"** LLMmap is a one-shot attack tool that identifies a model. We use the same
idea *defensively and continuously*: identification is one signal inside a monitor that also detects change against a
noise-aware baseline, attributes the layer, measures user impact, and is evaluated blind.

**"And from MMD / RUT auditing papers?"** Those answer "is this exactly model X?" with a statistical test. We answer
"what changed, which layer, who is it now, who is hurt" - and run as a product with a dashboard and reports.

**"Isn't this just comparing outputs?"** No. We combine side-channels each layer affects differently: token
fingerprint, context actually read, speed, targeted refusals with a control, format habits - plus the library match.

**"How do you avoid false alarms?"** The baseline measures normal variation, thresholds sit outside it, and a clue must
repeat in a second round. Measured on blind no-change trials.

**"Could the monitor leak data?"** It stores only synthetic canary answers and request metadata. The privacy audit
shows a secret sent through the app is stored nowhere.

**"What next?"** Per-customer canary packs (real prompt *templates* with synthetic data), statistical tests (RUT-style)
for borderline cases, Slack/email alerts, a public fingerprint library shared between customers.
