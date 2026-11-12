"""
score.py - compares the agent's reports with the hidden change_log.json.

Calculates what the problem statement asks for:
    detection rate, attribution accuracy, false-alarm rate,
    missed-change rate, probe cost
Writes results.md (a table you can put in your slides).
"""

import json
from collections import Counter

LAYERS = ["model", "serving", "safety", "request_config"]


def load():
    with open("change_log.json") as f:
        truth = {c["trial"]: c for c in json.load(f)}
    with open("reports.jsonl") as f:
        reports = {r["trial"]: r for r in map(json.loads, f) if r.get("trial") is not None}
    return truth, reports


def main():
    truth, reports = load()
    trials = sorted(set(truth) & set(reports))

    changed = [t for t in trials if truth[t]["layer"] != "none"]
    quiet = [t for t in trials if truth[t]["layer"] == "none"]
    detected = [t for t in changed if reports[t]["changed"]]
    correct = [t for t in detected if reports[t]["likely_cause"] == truth[t]["layer"]]
    false_alarms = [t for t in quiet if reports[t]["changed"]]
    missed = [t for t in changed if not reports[t]["changed"]]

    def pct(a, b):
        return f"{len(a) / len(b):.0%} ({len(a)}/{len(b)})" if b else "n/a"

    costs = [reports[t]["probe_cost"] for t in trials]
    avg_tokens = sum(c["prompt_tokens"] + c["output_tokens"] for c in costs) / len(costs)
    avg_seconds = sum(c["seconds"] for c in costs) / len(costs)
    avg_calls = sum(c["calls"] for c in costs) / len(costs)

    # confusion matrix: true layer (rows) vs agent's answer (columns)
    cols = LAYERS + ["no change"]
    matrix = {row: Counter() for row in LAYERS + ["none"]}
    for t in trials:
        guess = reports[t]["likely_cause"] if reports[t]["changed"] else "no change"
        matrix[truth[t]["layer"]][guess] += 1

    per_change = {}
    for t in changed:
        name = truth[t]["change"]
        ok = reports[t]["changed"] and reports[t]["likely_cause"] == truth[t]["layer"]
        per_change.setdefault(name, [0, 0])
        per_change[name][0] += ok
        per_change[name][1] += 1

    lines = [
        "# SilentShift results", "",
        f"Trials: {len(trials)}  (with a change: {len(changed)}, no change: {len(quiet)})", "",
        "| Metric | Result |", "|---|---|",
        f"| Detection rate | {pct(detected, changed)} |",
        f"| Attribution accuracy (of detected) | {pct(correct, detected)} |",
        f"| False-alarm rate | {pct(false_alarms, quiet)} |",
        f"| Missed-change rate | {pct(missed, changed)} |",
        f"| Probe cost per check (avg) | {avg_calls:.0f} calls, {avg_tokens:.0f} tokens, {avg_seconds:.1f} s |",
        "", "## Per controlled change", "",
        "| Controlled change | Layer | Correct |", "|---|---|---|",
    ]
    for name, (ok, n) in sorted(per_change.items()):
        layer = next(truth[t]["layer"] for t in changed if truth[t]["change"] == name)
        lines.append(f"| {name} | {layer} | {ok}/{n} |")

    lines += ["", "## Confusion matrix (rows = true layer, columns = agent's answer)", "",
              "| True \\ Agent | " + " | ".join(cols) + " |",
              "|---" * (len(cols) + 1) + "|"]
    for row in LAYERS + ["none"]:
        if sum(matrix[row].values()):
            lines.append(f"| {row} | " + " | ".join(str(matrix[row][c]) for c in cols) + " |")

    text = "\n".join(lines)
    with open("results.md", "w") as f:
        f.write(text + "\n")
    print("\n" + text + "\n\nSaved results.md")


if __name__ == "__main__":
    main()
