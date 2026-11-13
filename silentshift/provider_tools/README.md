# Provider tools - be the AI provider

These tools change what is behind the model name `bot-latest` **directly on the Ollama server**,
outside SilentShift. SilentShift is not told - it has to notice by itself.

## Change bot-latest (any computer, any base model)
Double-click **`change_bot_latest.bat`** (Mac/Linux: `./change_bot_latest.sh`) and pick a number, or use commands:

| Command | Layer | What changes on the server |
|---|---|---|
| `python provider.py normal` | - | back to your base model + normal instructions |
| `python provider.py swap llama3.2:1b` | Model | different weights behind the same name |
| `python provider.py quant qwen2.5:1.5b-instruct-q8_0` | Serving | another build of the same model |
| `python provider.py context 512` | Serving | `PARAMETER num_ctx 512` |
| `python provider.py slow` | Serving | `PARAMETER num_thread 1` + `num_gpu 0` |
| `python provider.py safety` | Safety | strict provider policy in `SYSTEM` |
| `python provider.py system "Talk like a robot."` | Safety | any instructions |
| `python provider.py status` | - | shows what `bot-latest` is right now (its Modelfile) |

- The **base model** is read from SilentShift's own setup, so `normal` always restores exactly what
  SilentShift learned as normal (the old .bat hard-coded qwen2.5:1.5b - that is why it looked "broken"
  when your base model was different). Override with `--base qwen3:4b`.
- Missing models are downloaded automatically.
- **Remote server:** add `--url https://xxxx.trycloudflare.com` (or set `SS_OLLAMA_URL` in `.env`).
- The truth goes to `provider_change_log.txt` (SilentShift's monitor never reads it). It also appears
  as a marker on the dashboard's timeline (operator view), so you can see the time-to-detect.
- It is the same as `ollama create bot-latest -f Modelfile` - see `Modelfile.example` to write your own.

The dashboard's **Provider Lab → Server console** does the same from the browser.

## Share Ollama through Cloudflare (optional)
1. On the Ollama computer, double-click **`start_tunnel.bat`**. It prints a link like `https://random-words.trycloudflare.com`.
2. On the SilentShift computer, add to `.env`: `SS_OLLAMA_URL=https://random-words.trycloudflare.com`
3. Start SilentShift (`start.bat`, option 2).

**Warnings:** Ollama has no password - anyone with the link can use your models; close the tunnel after the demo.
The link changes every time. Cloudflare cuts requests after about 100 seconds, so use small models.
