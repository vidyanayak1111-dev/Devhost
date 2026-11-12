"""
agent.py - the SilentShift Watcher.

Commands:
    python agent.py baseline     -> learn what "normal" looks like (run while NOTHING is changed)
    python agent.py check        -> run the tests once, compare with normal, write a report
    python agent.py watch        -> run "check" again and again (every 10 minutes)

The agent never reads Ollama's settings or change_log.json. It only uses
the answers to its own fake questions (canaries) and our own settings.
"""

import json
import statistics
import sys
import time
from difflib import SequenceMatcher

from runner import run_round

BASELINE_FILE = "baseline.json"
REPORTS_FILE = "reports.jsonl"
BASELINE_ROUNDS = 3
WATCH_EVERY_MIN = 10


# ---------------------------------------------------------------- helpers
def similar(a, b):
    """0 = totally different text, 1 = exactly the same text."""
    return SequenceMatcher(None, a, b).ratio()


def rate(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarise(rnd):
    """Turn one round of answers into a few simple numbers."""
    res = rnd["results"]

    def of(kind):
        return [r for r in res if r["kind"] == kind]

    texts, tokens = {}, {}
    for r in res:
        texts.setdefault(r["canary_id"], []).append(r["text"])
        tokens.setdefault(r["canary_id"], set()).add(r["prompt_tokens"])

    speeds = [r["tokens_per_sec"] for r in res if r["tokens_per_sec"]]
    return {
        "settings_hash": rnd["settings_hash"],
        "refusal_rate": rate([r["refused"] for r in of("safety")]),
        "json_rate": rate([r["valid_json"] for r in of("json")]),
        "correct_rate": rate([r["correct"] for r in of("exact")]),
        "long_memory_rate": rate([r["correct"] for r in of("long")]),
        "cut_off_rate": rate([r["done_reason"] == "length" for r in res]),
        "speed": statistics.mean(speeds) if speeds else None,
        "long_prompt_tokens": max(r["prompt_tokens"] or 0 for r in of("long")) if of("long") else None,
        "model_names": sorted({r["model_returned"] for r in res if r["model_returned"]}),
        "texts": texts,
        "tokens": {k: sorted(v, key=lambda x: x or 0) for k, v in tokens.items()},
    }


def text_similarity(new, base_rounds, kinds_ids):
    """How close are the new answers to the normal answers? (average, 0..1)"""
    scores = []
    for cid in kinds_ids:
        new_texts = new["texts"].get(cid, [])
        old_texts = [t for b in base_rounds for t in b["texts"].get(cid, [])]
        for t in new_texts:
            if old_texts:
                scores.append(max(similar(t, o) for o in old_texts))
    return statistics.mean(scores) if scores else 1.0


# ---------------------------------------------------------------- baseline
def make_baseline():
    print(f"Learning normal behaviour: {BASELINE_ROUNDS} rounds. Do NOT change anything now.")
    rounds = []
    for i in range(BASELINE_ROUNDS):
        rounds.append(summarise(run_round()))
        print(f"  round {i + 1}/{BASELINE_ROUNDS} done")

    # How similar are normal answers to EACH OTHER? (natural variation)
    style_ids = [cid for cid in rounds[0]["texts"] if cid.startswith(("style", "identity"))]
    self_sims = []
    for i, r in enumerate(rounds):
        others = rounds[:i] + rounds[i + 1:]
        self_sims.append(text_similarity(r, others, style_ids))

    base = {"rounds": rounds, "normal_similarity_min": min(self_sims), "style_ids": style_ids}
    with open(BASELINE_FILE, "w") as f:
        json.dump(base, f, indent=2)
    print(f"Saved {BASELINE_FILE}. Normal answer similarity is at least {min(self_sims):.2f}")


# ---------------------------------------------------------------- compare
def compare(base, new):
    """Return a list of facts (evidence) about what is different from normal."""
    rounds = base["rounds"]
    facts = []

    def normal_values(key):
        return [r[key] for r in rounds if r[key] is not None]

    # 1. Our own settings (directly visible to us)
    if new["settings_hash"] not in {r["settings_hash"] for r in rounds}:
        facts.append({"clue": "our_settings_changed", "text": "Our own request settings are different from normal"})

    # 2. Rates (refusals, JSON, correct answers, long memory, cut-offs)
    checks = [
        ("refusal_rate", "Refusals on safety questions", "refusals_up", "refusals_down"),
        ("json_rate", "Valid JSON answers", "json_up", "json_down"),
        ("correct_rate", "Correct fixed answers", "correct_up", "correct_down"),
        ("long_memory_rate", "Remembers start of long text", "memory_up", "memory_down"),
        ("cut_off_rate", "Answers cut off early", "cutoff_up", "cutoff_down"),
    ]
    for key, label, up, down in checks:
        vals = normal_values(key)
        if not vals or new[key] is None:
            continue
        lo, hi = min(vals), max(vals)
        if new[key] > hi + 0.2:
            facts.append({"clue": up, "text": f"{label}: normal {lo:.0%}-{hi:.0%}, now {new[key]:.0%}"})
        elif new[key] < lo - 0.2:
            facts.append({"clue": down, "text": f"{label}: normal {lo:.0%}-{hi:.0%}, now {new[key]:.0%}"})

    # 3. Speed (allow 15% wobble)
    speeds = normal_values("speed")
    if speeds and new["speed"]:
        avg = statistics.mean(speeds)
        if abs(new["speed"] - avg) > 0.15 * avg:
            direction = "faster" if new["speed"] > avg else "slower"
            facts.append({"clue": "speed_" + direction,
                          "text": f"Speed: normal {avg:.1f} tokens/sec, now {new['speed']:.1f} ({direction})"})

    # 4. Tokenizer fingerprint: same question should always count the same number of tokens
    changed = 0
    short_ids = [cid for cid in new["tokens"] if not cid.startswith("long")]
    for cid in short_ids:
        normal = {t for r in rounds for t in r["tokens"].get(cid, [])}
        if not set(new["tokens"][cid]) <= normal:
            changed += 1
    if short_ids and changed / len(short_ids) > 0.3:
        facts.append({"clue": "token_count_changed",
                      "text": f"Token count of fixed questions changed for {changed}/{len(short_ids)} questions"})

    # 5. Long text got shorter inside the model (= context window cut it)
    longs = normal_values("long_prompt_tokens")
    if longs and new["long_prompt_tokens"] and new["long_prompt_tokens"] < 0.8 * min(longs):
        facts.append({"clue": "long_text_cut",
                      "text": f"Long text was read as {new['long_prompt_tokens']} tokens, normally {min(longs)}"})

    # 6. Writing style / answer text
    sim = text_similarity(new, rounds, base["style_ids"])
    normal_min = base["normal_similarity_min"]
    if sim < normal_min - 0.35:
        facts.append({"clue": "style_very_different",
                      "text": f"Answer style very different: similarity {sim:.2f} (normal at least {normal_min:.2f})"})
    elif sim < normal_min - 0.05:
        facts.append({"clue": "style_slightly_different",
                      "text": f"Answers slightly different: similarity {sim:.2f} (normal at least {normal_min:.2f})"})

    # 7. Model name returned by the server
    if new["model_names"] != rounds[0]["model_names"]:
        facts.append({"clue": "model_name_changed",
                      "text": f"Server reports model {new['model_names']}, normally {rounds[0]['model_names']}"})

    return facts


# ---------------------------------------------------------------- find the cause
def find_cause(facts):
    """Give points to each layer based on the clues. Most points = likely cause."""
    clues = {f["clue"] for f in facts}
    points = {"model": 0, "serving": 0, "safety": 0, "request_config": 0}
    why = {k: [] for k in points}

    def add(layer, n, reason):
        points[layer] += n
        why[layer].append(reason)

    ours = "our_settings_changed" in clues
    if ours:
        add("request_config", 10, "our own settings changed (seen directly)")

    if "model_name_changed" in clues:
        add("model", 5, "server reports a different model name")
    if "token_count_changed" in clues and not ours:
        add("model", 4, "same questions now count different tokens = different tokenizer")
    if "style_very_different" in clues:
        add("model", 3, "writing style is very different")
    if "refusals_up" in clues:
        add("safety", 4, "refuses more borderline questions")
        if "speed_slower" in clues or "token_count_changed" not in clues:
            add("safety", 1, "everything else looks like the same model")
    if "refusals_down" in clues:
        add("safety", 3, "refuses fewer borderline questions")
    if "long_text_cut" in clues or "memory_down" in clues:
        add("serving", 4, "long text is being cut = smaller context window")
    if ("speed_faster" in clues or "speed_slower" in clues) and "token_count_changed" not in clues:
        if "refusals_up" not in clues:
            add("serving", 3, "speed changed but tokenizer is the same = same model, different setup")
    if "style_slightly_different" in clues and "token_count_changed" not in clues and not ours:
        add("serving", 2, "answers only slightly different = same model, different setup")
    if "cutoff_up" in clues:
        add("request_config" if ours else "serving", 2, "answers cut off early")
    if ("json_down" in clues or "correct_down" in clues) and not ours:
        add("model", 1, "fixed answers / JSON format changed")

    total = sum(points.values())
    if total == 0:
        return None, 0.0, points, why
    best = max(points, key=points.get)
    return best, points[best] / total, points, why


# ---------------------------------------------------------------- check + report
def probe_cost(rounds):
    """How much did this check cost? (the PS asks for probe cost)"""
    res = [r for rnd in rounds for r in rnd["results"]]
    return {
        "rounds": len(rounds),
        "calls": len(res),
        "prompt_tokens": sum(r["prompt_tokens"] or 0 for r in res),
        "output_tokens": sum(r["output_tokens"] or 0 for r in res),
        "seconds": round(sum(r["seconds"] for r in res), 2),
    }


def check(trial=None):
    with open(BASELINE_FILE) as f:
        base = json.load(f)

    rounds = [run_round()]
    facts = compare(base, summarise(rounds[0]))
    if facts:
        # Re-check once, to avoid false alarms from one unlucky round
        print("Something looks different, checking again...")
        rounds.append(run_round())
        facts2 = compare(base, summarise(rounds[1]))
        confirmed = {f["clue"] for f in facts} & {f["clue"] for f in facts2}
        facts = [f for f in facts2 if f["clue"] in confirmed]

    layer, confidence, points, why = find_cause(facts)
    report = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trial": trial,
        "changed": bool(facts) and layer is not None,
        "what_i_saw": [f["text"] for f in facts],
        "likely_cause": layer,
        "confidence": round(confidence, 2),
        "reasons": why.get(layer, []) if layer else [],
        "points": points,
        "probe_cost": probe_cost(rounds),
    }
    with open(REPORTS_FILE, "a") as f:
        f.write(json.dumps(report) + "\n")
    print_report(report)
    return report


def print_report(r):
    c = r["probe_cost"]
    cost = (f"Probe cost: {c['calls']} calls, {c['prompt_tokens'] + c['output_tokens']} tokens, "
            f"{c['seconds']} s")
    print("\n" + "=" * 60)
    if not r["changed"]:
        print(f"SilentShift  {r['time']}   No change detected. Everything looks normal.")
        print(cost)
        print("=" * 60)
        return
    print(f"SilentShift  {r['time']}   CHANGE DETECTED")
    print("-" * 60)
    print("WHAT I SAW (facts):")
    for t in r["what_i_saw"]:
        print("  •", t)
    print("\nLIKELY CAUSE (my guess):")
    print(f"  • {r['likely_cause'].upper()}  ({r['confidence']:.0%} confident)")
    for t in r["reasons"]:
        print("    because", t)
    print("\n" + cost)
    print("=" * 60)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "baseline":
        make_baseline()
    elif cmd == "check":
        check()
    elif cmd == "watch":
        while True:
            check()
            time.sleep(WATCH_EVERY_MIN * 60)
    else:
        print(__doc__)
