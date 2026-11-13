"""
evaluation.py - runs many randomised controlled-change trials and scores them.

    python evaluation.py 40

Each trial: reset -> apply a random change (about 1 in 3 is "no change") -> monitor checks -> reset.
The monitor does not know which change was applied. Scoring compares its reports with
data/hidden/change_log.json and reports what PS-03 asks for: detection rate,
attribution accuracy, false-alarm rate, missed-change rate and probe cost.
"""

import json
import random
import statistics
import sys
import time
from collections import Counter

import agent
import config
import injector

LAYERS = agent.LAYERS


def run(n=40, seed=None, progress=None):
    rng = random.Random(seed)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    config.CHANGE_LOG.write_text("[]")
    injector.reset()
    if progress:
        progress(0, n + 1, "learning baseline")
    agent.make_baseline()

    names = injector.available()
    # make sure every change appears at least twice, then random fill
    plan = names * 2 + ["none"] * max(4, n // 3)
    while len(plan) < n:
        plan.append("none" if rng.random() < 0.33 else rng.choice(names))
    plan = plan[:n]
    rng.shuffle(plan)

    pairs = []
    for t, name in enumerate(plan, 1):
        if progress:
            progress(t, n + 1, f"trial {t}/{n}")
        truth = injector.apply(name, trial=t)
        rep = agent.check(trial=t)
        injector.reset()
        pairs.append((truth, rep))
    result = score(pairs)
    result["run_id"] = run_id
    config.RESULTS_JSON.write_text(json.dumps(result, indent=1))
    config.RESULTS_MD.write_text(to_markdown(result))
    return result


def score(pairs):
    changed = [(t, r) for t, r in pairs if t["layer"] != "none"]
    quiet = [(t, r) for t, r in pairs if t["layer"] == "none"]
    detected = [(t, r) for t, r in changed if r["changed"]]
    correct = [(t, r) for t, r in detected if r["likely_cause"] == t["layer"]]
    false_alarms = [(t, r) for t, r in quiet if r["changed"]]
    missed = [(t, r) for t, r in changed if not r["changed"]]

    def pct(a, b):
        return {"value": round(len(a) / len(b), 3) if b else None, "n": len(a), "of": len(b)}

    def cost(rows):
        if not rows:
            return None
        c = [r["probe_cost"] for _, r in rows]
        return {"calls": round(statistics.mean(x["calls"] for x in c), 1),
                "tokens": round(statistics.mean(x["tokens"] for x in c)),
                "seconds": round(statistics.mean(x["seconds"] for x in c), 2),
                "est_usd": round(statistics.mean(x["est_usd"] for x in c), 6)}

    cols = LAYERS + ["unknown", "no change"]
    matrix = {row: Counter() for row in LAYERS + ["none"]}
    for t, r in pairs:
        matrix[t["layer"]][(r["likely_cause"] or "unknown") if r["changed"] else "no change"] += 1

    per_change = {}
    for t, r in changed:
        d = per_change.setdefault(t["change"], {"layer": t["layer"], "description": t["description"],
                                                "correct": 0, "detected": 0, "n": 0})
        d["n"] += 1
        d["detected"] += int(r["changed"])
        d["correct"] += int(r["changed"] and r["likely_cause"] == t["layer"])

    return {
        "trials": len(pairs), "with_change": len(changed), "no_change": len(quiet),
        "detection_rate": pct(detected, changed),
        "attribution_accuracy": pct(correct, detected),
        "false_alarm_rate": pct(false_alarms, quiet),
        "missed_change_rate": pct(missed, changed),
        "cost_all": cost(pairs), "cost_quiet": cost(quiet), "cost_changed": cost(changed),
        "per_change": per_change,
        "confusion": {"columns": cols,
                      "rows": {k: [v[c] for c in cols] for k, v in matrix.items() if sum(v.values())}},
        "trials_detail": [{"trial": t["trial"], "truth": t["change"], "layer": t["layer"],
                           "detected": r["changed"], "guess": r["likely_cause"],
                           "confidence": r["confidence"], "calls": r["probe_cost"]["calls"]}
                          for t, r in pairs],
    }


def to_markdown(s):
    def p(m):
        return f"{m['value']:.0%} ({m['n']}/{m['of']})" if m["value"] is not None else "n/a"

    def c(x):
        return f"{x['calls']} calls, {x['tokens']} tokens, {x['seconds']} s, ~${x['est_usd']}" if x else "n/a"

    L = ["# SilentShift evaluation results", "",
         f"Trials: {s['trials']} (with a change: {s['with_change']}, no change: {s['no_change']})", "",
         "| Metric | Result |", "|---|---|",
         f"| Detection rate | {p(s['detection_rate'])} |",
         f"| Attribution accuracy (of detected) | {p(s['attribution_accuracy'])} |",
         f"| False-alarm rate | {p(s['false_alarm_rate'])} |",
         f"| Missed-change rate | {p(s['missed_change_rate'])} |",
         f"| Probe cost, quiet check (sentinel only) | {c(s['cost_quiet'])} |",
         f"| Probe cost, check with a change (sentinel + confirm) | {c(s['cost_changed'])} |",
         "", "## Per controlled change", "", "| Change | Layer | Detected | Correctly attributed |", "|---|---|---|---|"]
    for name, d in sorted(s["per_change"].items()):
        L.append(f"| {name} ({d['description']}) | {d['layer']} | {d['detected']}/{d['n']} | {d['correct']}/{d['n']} |")
    cols = s["confusion"]["columns"]
    L += ["", "## Confusion matrix (rows = true layer, columns = monitor's answer)", "",
          "| True \\ Monitor | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for row, vals in s["confusion"]["rows"].items():
        L.append(f"| {row} | " + " | ".join(map(str, vals)) + " |")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    res = run(n, seed=7, progress=lambda i, t, msg: print(f"[{i}/{t}] {msg}"))
    print(to_markdown(res))