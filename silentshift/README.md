# SilentShift Watcher

Detects silent behaviour changes in an AI API and identifies the likely layer
(model, serving stack, safety settings or request configuration), using only
synthetic canary tasks and without storing user prompts.

## Files

| File | Job |
|---|---|
| `proxy.py` | Sits between chatbot UI and Ollama, forwards everything, logs metadata only |
| `canaries.json` | 14 synthetic test questions (exact, style, safety, JSON, long-context) |
| `app_config.json` | Our own request configuration (model alias, temperature, etc.) |
| `runner.py` | Sends every canary 3 times through the proxy and measures the answers |
| `agent.py` | Baseline, detect, attribute, report (with probe cost) |
| `change_injector.py` | Makes controlled changes, writes hidden `change_log.json` |
| `experiment.py` | Runs many random controlled-change trials automatically |
| `score.py` | Detection rate, attribution accuracy, false alarms, missed changes, cost |
| `mock_ollama.py` | Pretend Ollama for testing without real models |

## Quick start (pretend Ollama)

```bash
pip install fastapi uvicorn httpx
python mock_ollama.py          # terminal 1
python proxy.py                # terminal 2
python agent.py baseline       # terminal 3 - learn normal (change nothing)
python agent.py check          # one check + report
```

Make one controlled change and check:
```bash
python change_injector.py list
python change_injector.py apply safety_on
python agent.py check
python change_injector.py reset
```

Run the full evaluation (results saved to `results.md`):
```bash
python experiment.py 40
```

## Real Ollama

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:3b
ollama pull llama3.2:3b-instruct-q8_0
ollama cp llama3.2:3b bot-latest
```
Stop `mock_ollama.py`, set `MODE = "real"` in `change_injector.py`, keep `proxy.py`
running, then run `python agent.py baseline` again.
In real mode, `ctx_1024` and `safety_on` need Friend 1's provider wrapper.
Real models vary more: if you see false alarms, raise the speed allowance (0.15)
or similarity allowance (0.05) in `agent.py`.

## Rules the agent follows
- Never reads Ollama settings or `change_log.json`
- Never stores real user messages (only canary answers + metadata)
