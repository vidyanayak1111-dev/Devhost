"""
evaluation.py - randomised BLIND trials, scored against the hidden change log.

    python evaluation.py 40        (demo mode: about 30 seconds)
    real mode: run from the dashboard; each change once + a few no-change trials

Each trial: reset -> apply one controlled change (or none) -> the monitor checks without
knowing what happened -> reset. Scoring compares the monitor's reports with
data/hidden/change_log.json and gives exactly what PS-03 asks for: detection rate,
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


def plan_trials(n, rng):
    names = injector.available()
    if config.MODE == "real":
        # real models are slow: every change once + no-change trials, shuffled
        plan = names + ["none"] * max(3, n - len(names))
        plan = plan[:max(n, len(names) + 3)]
    else:
        plan = names * 2 + ["none"] * max(4, n // 3)
        while len(plan) < n:
            plan.append("none" if rng.random() < 0.33 else rng.choice(names))
        plan = plan[:max(n, len(names) * 2 + 4)]
    rng.shuffle(plan)
    return plan


def run(n=40, seed=None, progress=None):
    rng = random.Random(seed)
    config.CHANGE_LOG.write_text("[]")
    injector.reset()
    plan = plan_trials(n, rng)
    if progress:
        progress(0, len(plan) + 1, "learning baseline")
    import fingerprints
    if config.MODE != "real" and len(fingerprints.load()) < len(fingerprints.SIM_MODELS):
        if progress:
            progress(0, len(plan) + 1, "building the fingerprint library")
        fingerprints.build()
    if progress:
        progress(0, len(plan) + 1, "learning baseline")
    agent.make_baseline()
    pairs = []
    started = time.time()
    for t, name in enumerate(plan, 1):
        if progress:
            progress(t, len(plan) + 1, f"trial {t}/{len(plan)}")
        truth = injector.apply(name, trial=t)
        rep = agent.check(trial=t)
        injector.reset()
        pairs.append((truth, rep))
    result = score(pairs)
    result.update(mode=config.MODE, run_id=time.strftime("%Y%m%d-%H%M%S"),
                  minutes=round((time.time() - started) / 60, 1))
    path = str(config.RESULTS_JSON).format(mode=config.MODE)
    open(path, "w").write(json.dumps(result, indent=1))
    open(str(config.RESULTS_MD).format(mode=config.MODE), "w").write(to_markdown(result))
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
                "seconds": round(statistics.mean(x["seconds"] for x in c), 1),
                "est_usd": round(statistics.mean(x["est_usd"] for x in c), 6)}

    cols = LAYERS + ["unknown", "no change"]
    matrix = {row: Counter() for row in LAYERS + ["none"]}
    for t, r in pairs:
        matrix[t["layer"]][(r["likely_cause"] or "unknown") if r["changed"] else "no change"] += 1

    per_change = {}
    for t, r in changed:
        d = per_change.setdefault(t["change"], {"layer": t["layer"], "description": t["description"],
                                                "correct": 0, "detected": 0, "n": 0, "guesses": []})
        d["n"] += 1
        d["detected"] += int(r["changed"])
        d["correct"] += int(r["changed"] and r["likely_cause"] == t["layer"])
        d["guesses"].append(r["likely_cause"] if r["changed"] else "missed")

    # identification: did the report name the model REALLY behind bot-latest? (when it is in the library)
    ident = []
    for t, r in changed:
        idn = r.get("identity") or {}
        if t.get("target") and idn.get("ranked") and any(x["spec"] == t["target"] for x in idn["ranked"]):
            ident.append((t, idn["best"]["spec"] == t["target"]))
    identified = [x for x in ident if x[1]]

    return {
        "identification_accuracy": pct(identified, ident),
        "trials": len(pairs), "with_change": len(changed), "no_change": len(quiet),
        "detection_rate": pct(detected, changed),
        "attribution_accuracy": pct(correct, detected),
        "false_alarm_rate": pct(false_alarms, quiet),
        "missed_change_rate": pct(missed, changed),
        "cost_quiet": cost(quiet), "cost_changed": cost(changed),
        "per_change": per_change,
        "confusion": {"columns": cols, "rows": {k: [v[c] for c in cols] for k, v in matrix.items() if sum(v.values())}},
    }


def to_markdown(s):
    def p(m):
        return f"{m['value']:.0%} ({m['n']}/{m['of']})" if m["value"] is not None else "n/a"

    def c(x):
        return f"{x['calls']} calls, {x['tokens']} tokens, {x['seconds']} s, ~${x['est_usd']}" if x else "n/a"

    L = [f"# SilentShift evaluation - {s.get('mode', '')} mode", "",
         f"Trials: {s['trials']} (with a change: {s['with_change']}, no change: {s['no_change']})"
         + (f" · took {s['minutes']} min" if s.get("minutes") is not None else ""), "",
         "| Metric | Result |", "|---|---|",
         f"| Detection rate | {p(s['detection_rate'])} |",
         f"| Attribution accuracy (of detected) | {p(s['attribution_accuracy'])} |",
         f"| False-alarm rate | {p(s['false_alarm_rate'])} |",
         f"| Missed-change rate | {p(s['missed_change_rate'])} |",
         f"| Replacement model identified (fingerprint library) | {p(s['identification_accuracy']) if s.get('identification_accuracy') else 'n/a'} |",
         f"| Probe cost, nothing changed | {c(s['cost_quiet'])} |",
         f"| Probe cost, something changed | {c(s['cost_changed'])} |",
         "", "## Per controlled change", "", "| Change | Layer | Detected | Correctly attributed | Monitor said |", "|---|---|---|---|---|"]
    for name, d in sorted(s["per_change"].items()):
        L.append(f"| {name} ({d['description']}) | {d['layer']} | {d['detected']}/{d['n']} | {d['correct']}/{d['n']} | {', '.join(map(str, d['guesses']))} |")
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
