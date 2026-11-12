# SilentShift results

Trials: 40  (with a change: 26, no change: 14)

| Metric | Result |
|---|---|
| Detection rate | 100% (26/26) |
| Attribution accuracy (of detected) | 100% (26/26) |
| False-alarm rate | 0% (0/14) |
| Missed-change rate | 0% (0/26) |
| Probe cost per check (avg) | 69 calls, 15225 tokens, 1.4 s |

## Per controlled change

| Controlled change | Layer | Correct |
|---|---|---|
| alias_qwen | model | 9/9 |
| ctx_1024 | serving | 2/2 |
| new_system | request_config | 1/1 |
| quant_q8 | serving | 5/5 |
| safety_on | safety | 4/4 |
| short_answers | request_config | 2/2 |
| temp_09 | request_config | 3/3 |

## Confusion matrix (rows = true layer, columns = agent's answer)

| True \ Agent | model | serving | safety | request_config | no change |
|---|---|---|---|---|---|
| model | 9 | 0 | 0 | 0 | 0 |
| serving | 0 | 7 | 0 | 0 | 0 |
| safety | 0 | 0 | 4 | 0 | 0 |
| request_config | 0 | 0 | 0 | 6 | 0 |
| none | 0 | 0 | 0 | 0 | 14 |
