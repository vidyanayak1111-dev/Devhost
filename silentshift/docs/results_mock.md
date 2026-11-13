# SilentShift evaluation results

Trials: 60 (with a change: 36, no change: 24)

| Metric | Result |
|---|---|
| Detection rate | 100% (36/36) |
| Attribution accuracy (of detected) | 100% (36/36) |
| False-alarm rate | 0% (0/24) |
| Missed-change rate | 0% (0/36) |
| Probe cost, quiet check (sentinel only) | 15 calls, 2218 tokens, 0.06 s, ~$0.000418 |
| Probe cost, check with a change (sentinel + confirm) | 60 calls, 8386 tokens, 0.29 s, ~$0.001559 |

## Per controlled change

| Change | Layer | Detected | Correctly attributed |
|---|---|---|---|
| alias_qwen (alias bot-latest silently moved Llama -> Qwen) | model | 4/4 | 4/4 |
| ctx_1024 (server context window 4096 -> 1024) | serving | 4/4 | 4/4 |
| new_system (our system prompt changed) | request_config | 6/6 | 6/6 |
| quant_q8 (served build switched q4 -> q8 quantisation) | serving | 5/5 | 5/5 |
| safety_on (provider safety filter switched on) | safety | 8/8 | 8/8 |
| short_answers (our num_predict 200 -> 8) | request_config | 4/4 | 4/4 |
| temp_09 (our temperature 0 -> 0.9) | request_config | 5/5 | 5/5 |

## Confusion matrix (rows = true layer, columns = monitor's answer)

| True \ Monitor | model | serving | safety | request_config | unknown | no change |
|---|---|---|---|---|---|---|
| model | 4 | 0 | 0 | 0 | 0 | 0 |
| serving | 0 | 9 | 0 | 0 | 0 | 0 |
| safety | 0 | 0 | 8 | 0 | 0 | 0 |
| request_config | 0 | 0 | 0 | 15 | 0 | 0 |
| none | 0 | 0 | 0 | 0 | 0 | 24 |
