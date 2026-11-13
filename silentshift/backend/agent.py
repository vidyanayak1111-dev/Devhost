"""
agent.py - the SilentShift monitor.

    python agent.py baseline   learn what "normal" looks like (change nothing while it runs)
    python agent.py check      one check -> report
    python agent.py deep       full check (skip the cheap sentinel stage)

How a check works
  1. SENTINEL: every canary once (cheap). If nothing differs from baseline -> done.
  2. CONFIRM:  only if the sentinel saw something, a full round (3x each).
     A clue is kept only if BOTH rounds show it -> fewer false alarms.
  3. ATTRIBUTE: each clue gives points to the layers it points at.
     Report = observed evidence (facts) kept apart from likely cause (inference).

The agent never reads data/hidden/ (provider state / change log).
"""

import json
import math
import statistics
import sys
import time
import uuid
from difflib import SequenceMatcher

import config
import gateway
from runner import config_hash, load_config, run_round

BASELINE_ROUNDS = 3
LAYERS = ["model", "serving", "safety", "request_config"]

# Example price to express probe cost in money (assumption - edit to your provider's price)
PRICE_PER_1M_INPUT = 0.15
PRICE_PER_1M_OUTPUT = 0.60

LAYER_INFO = {
    "model": "The weights behind the model name probably changed (e.g. the alias was moved to a new version).",
    "serving": "Same model family, but the way it is served changed (quantisation, hardware, context window, server version).",
    "safety": "A provider-side safety filter or policy setting probably changed.",
    "request_config": "Our own request configuration changed (model name, temperature, token limit, system prompt, format).",
}
CAVEATS = {
    "model": "We cannot see provider weights. A tokenizer change is strong evidence of different weights, "
             "but a heavily fine-tuned version of the same family could look the same.",
    "serving": "'Serving' is inferred from 'same tokenizer, similar wording, different speed/context'. "
               "Our probes cannot tell quantisation apart from a hardware change.",
    "safety": "Refusals rose only on borderline canaries while a harmless control was still answered. "
              "A newer model that is simply more cautious can look similar.",
    "request_config": "Seen directly from our own config, so this is the most certain attribution. "
                      "The provider may also have changed something at the same time.",
}


# ---------------------------------------------------------------- helpers
def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def rate(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def config_diff(old, new):
    a, b = flatten(old), flatten(new)
    diff = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            ov, nv = a.get(k), b.get(k)
            if k == "system":   # don't print whole prompts, just say it changed
                ov, nv = f"<{len(ov or '')} chars>", f"<{len(nv or '')} chars>"
            diff.append({"field": k, "before": ov, "after": nv})
    return diff


def summarise(rnd):
    res = rnd["results"]

    def of(kind):
        return [r for r in res if r["kind"] == kind]

    texts, tokens = {}, {}
    for r in res:
        texts.setdefault(r["canary_id"], []).append(r["text"])
        tokens.setdefault(r["canary_id"], set()).add(r["prompt_tokens"])
    speeds = [r["tokens_per_sec"] for r in res if r["tokens_per_sec"]]
    style_out = [r["output_tokens"] or 0 for r in of("style")]
    return {
        "settings_hash": rnd["settings_hash"],
        "config": rnd["config"],
        "refusal_rate": rate([r["refused"] for r in of("safety")]),
        "control_refusal_rate": rate([r["refused"] for r in of("control")]),
        "json_rate": rate([r["valid_json"] for r in of("json")]),
        "correct_rate": rate([r["correct"] for r in of("exact")]),
        "long_memory_rate": rate([r["correct"] for r in of("long")]),
        "cut_off_rate": rate([r["done_reason"] == "length" for r in res]),
        "speed": statistics.mean(speeds) if speeds else None,
        "speeds": speeds,
        "style_output_tokens": statistics.mean(style_out) if style_out else None,
        "long_prompt_tokens": max((r["prompt_tokens"] or 0 for r in of("long")), default=None),
        "model_names": sorted({r["model_returned"] for r in res if r["model_returned"]}),
        "texts": texts,
        "tokens": {k: sorted(v, key=lambda x: x or 0) for k, v in tokens.items()},
    }


def text_similarity(new, base_rounds, ids):
    scores = []
    for cid in ids:
        old = [t for b in base_rounds for t in b["texts"].get(cid, [])]
        for t in new["texts"].get(cid, []):
            if old:
                scores.append(max(similar(t, o) for o in old))
    return statistics.mean(scores) if scores else 1.0


def round_cost(rnd):
    res = rnd["results"]
    p = sum(r["prompt_tokens"] or 0 for r in res)
    o = sum(r["output_tokens"] or 0 for r in res)
    return {"calls": len(res), "prompt_tokens": p, "output_tokens": o,
            "seconds": round(sum(r["seconds"] for r in res), 2)}


def total_cost(rounds):
    c = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "seconds": 0.0}
    for r in rounds:
        for k, v in round_cost(r).items():
            c[k] += v
    c["seconds"] = round(c["seconds"], 2)
    c["tokens"] = c["prompt_tokens"] + c["output_tokens"]
    c["est_usd"] = round(c["prompt_tokens"] / 1e6 * PRICE_PER_1M_INPUT
                         + c["output_tokens"] / 1e6 * PRICE_PER_1M_OUTPUT, 6)
    c["stages"] = [f"{r['repeats']}x" for r in rounds]
    return c


# ---------------------------------------------------------------- baseline
def load_baseline():
    try:
        return json.loads(config.BASELINE.read_text())
    except FileNotFoundError:
        return None


def make_baseline(progress=None):
    rounds = []
    for i in range(BASELINE_ROUNDS):
        cb = (lambda d, t, i=i: progress(i * t + d, BASELINE_ROUNDS * t)) if progress else None
        rounds.append(summarise(run_round(repeats=3, progress=cb)))

    style_ids = [cid for cid in rounds[0]["texts"] if cid.startswith("style")]
    self_sims = [text_similarity(r, rounds[:i] + rounds[i + 1:], style_ids) for i, r in enumerate(rounds)]
    all_speeds = [s for r in rounds for s in r["speeds"]]
    cfg = load_config()
    base = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": rounds,
        "style_ids": style_ids,
        "normal_similarity_min": min(self_sims),
        "speed_mean": statistics.mean(all_speeds) if all_speeds else None,
        "speed_sd": statistics.pstdev(all_speeds) if len(all_speeds) > 1 else 0.0,
        "config": cfg,
        "config_hash": config_hash(cfg),
    }
    config.BASELINE.write_text(json.dumps(base, indent=1))
    return base


# ---------------------------------------------------------------- compare
def fact(clue, text, layer_hint=None, baseline=None, now=None):
    return {"clue": clue, "text": text, "baseline": baseline, "now": now}


def compare(base, new):
    """Observed evidence only - no guessing here."""
    rounds = base["rounds"]
    facts = []

    def vals(key):
        return [r[key] for r in rounds if r.get(key) is not None]

    # 1. our own request settings
    if new["settings_hash"] != base["config_hash"]:
        diff = config_diff(base["config"], new["config"])
        fields = ", ".join(f"{d['field']}: {d['before']} -> {d['after']}" for d in diff) or "hash differs"
        facts.append(fact("our_settings_changed", f"Our request config differs from baseline ({fields})",
                          baseline=base["config_hash"], now=new["settings_hash"]))

    # 2. rates (allow a 0.2 margin beyond what baseline ever showed)
    checks = [
        ("refusal_rate", "Refusals on borderline safety canaries", "refusals_up", "refusals_down"),
        ("control_refusal_rate", "Refusals on the harmless control question", "control_refused", None),
        ("json_rate", "Valid JSON answers", None, "json_down"),
        ("correct_rate", "Correct answers to fixed questions", None, "correct_down"),
        ("long_memory_rate", "Remembers the start of a long prompt", None, "memory_down"),
        ("cut_off_rate", "Answers cut off by the token limit", "cutoff_up", None),
    ]
    for key, label, up, down in checks:
        v = vals(key)
        if not v or new.get(key) is None:
            continue
        lo, hi = min(v), max(v)
        rng = f"{lo:.0%}" if lo == hi else f"{lo:.0%}-{hi:.0%}"
        if up and new[key] > hi + 0.2:
            facts.append(fact(up, f"{label}: baseline {rng}, now {new[key]:.0%}", baseline=rng, now=f"{new[key]:.0%}"))
        elif down and new[key] < lo - 0.2:
            facts.append(fact(down, f"{label}: baseline {rng}, now {new[key]:.0%}", baseline=rng, now=f"{new[key]:.0%}"))

    # 3. speed: noise-aware threshold = max(tolerance, 4 standard errors)
    mu, sd = base.get("speed_mean"), base.get("speed_sd") or 0
    if mu and new["speed"]:
        n = max(1, len(new["speeds"]))
        allow = max(config.SPEED_TOLERANCE * mu, 4 * sd / math.sqrt(n))
        if abs(new["speed"] - mu) > allow:
            d = "faster" if new["speed"] > mu else "slower"
            facts.append(fact("speed_" + d,
                              f"Generation speed: baseline {mu:.1f} tok/s (±{allow:.1f} allowed), now {new['speed']:.1f} ({d})",
                              baseline=round(mu, 1), now=round(new["speed"], 1)))

    # 4. tokenizer fingerprint: identical prompts must count identical tokens
    short_ids = [c for c in new["tokens"] if not c.startswith("long")]
    changed = [c for c in short_ids
               if not set(new["tokens"][c]) <= {t for r in rounds for t in r["tokens"].get(c, [])}]
    if short_ids and len(changed) / len(short_ids) > 0.3:
        facts.append(fact("token_count_changed",
                          f"Same prompts now count a different number of input tokens ({len(changed)}/{len(short_ids)} canaries)",
                          baseline="stable", now=f"{len(changed)} changed"))

    # 5. long prompt truncated by the server
    longs = vals("long_prompt_tokens")
    if longs and new["long_prompt_tokens"] and new["long_prompt_tokens"] < 0.8 * min(longs):
        facts.append(fact("long_text_cut",
                          f"Long canary was read as {new['long_prompt_tokens']} tokens, baseline {min(longs)}",
                          baseline=min(longs), now=new["long_prompt_tokens"]))

    # 6. wording of free-text answers
    sim = text_similarity(new, rounds, base["style_ids"])
    nmin = base["normal_similarity_min"]
    if sim < nmin - 0.35:
        facts.append(fact("style_very_different", f"Answer wording very different: similarity {sim:.2f} (normal ≥ {nmin:.2f})",
                          baseline=round(nmin, 2), now=round(sim, 2)))
    elif sim < nmin - 0.05:
        facts.append(fact("style_slightly_different", f"Answer wording slightly different: similarity {sim:.2f} (normal ≥ {nmin:.2f})",
                          baseline=round(nmin, 2), now=round(sim, 2)))

    # 7. answer length
    so = vals("style_output_tokens")
    if so and new["style_output_tokens"] is not None:
        m = statistics.mean(so)
        if m and abs(new["style_output_tokens"] - m) > 0.4 * m:
            facts.append(fact("length_changed",
                              f"Free-text answers are {'longer' if new['style_output_tokens'] > m else 'shorter'}: "
                              f"{m:.0f} -> {new['style_output_tokens']:.0f} output tokens",
                              baseline=round(m), now=round(new["style_output_tokens"])))

    # 8. model name echoed by the server
    if new["model_names"] != rounds[0]["model_names"]:
        facts.append(fact("model_name_changed", f"Server reports model {new['model_names']}, baseline {rounds[0]['model_names']}",
                          baseline=rounds[0]["model_names"], now=new["model_names"]))
    return facts


def live_facts(base):
    """Passive evidence from real user traffic since the baseline (metadata only)."""
    live = gateway.live_summary(since=base["time"])
    facts = []
    # only OUR app's chat traffic, and only its most recent request, counts as evidence
    rows = [r for r in gateway.read_metadata(source="live") if r["time"] >= base["time"]]
    if rows and rows[-1]["settings_hash"] != base["config_hash"]:
        h = rows[-1]["settings_hash"]
        facts.append(fact("live_settings_changed",
                          f"The app's latest live request used settings {h} (baseline {base['config_hash']})",
                          baseline=base["config_hash"], now=h))
    return live, facts


# ---------------------------------------------------------------- attribution
def find_cause(facts):
    clues = {f["clue"] for f in facts}
    points = {k: 0 for k in LAYERS}
    why = {k: [] for k in LAYERS}

    def add(layer, n, reason):
        points[layer] += n
        why[layer].append(reason)

    ours = "our_settings_changed" in clues      # seen directly in our config
    tokenizer = "token_count_changed" in clues

    if "our_settings_changed" in clues:
        add("request_config", 10, "our own request config is different (seen directly)")
    if "live_settings_changed" in clues:
        add("request_config", 2, "the app's live traffic is being sent with different settings")
    if "model_name_changed" in clues:
        add("model", 5, "the server reports a different model name")
    if tokenizer and not ours:
        add("model", 5, "identical prompts count different tokens = a different tokenizer = different weights")
    if "style_very_different" in clues and not ours:
        add("model", 3, "wording is very different from every baseline answer")
    if "refusals_up" in clues:
        add("safety", 4, "refuses borderline questions it used to answer")
        if "control_refused" not in clues:
            add("safety", 1, "still answers the harmless control = a targeted filter, not general breakage")
        if not tokenizer:
            add("safety", 1, "tokenizer unchanged = probably the same model")
    if "refusals_down" in clues:
        add("safety", 3, "refuses fewer borderline questions than before")
    if "control_refused" in clues:
        add("model", 1, "even harmless questions are refused")
    if "long_text_cut" in clues or "memory_down" in clues:
        if tokenizer:
            add("model", 1, "long prompt handled differently, but the tokenizer also changed")
        else:
            add("serving", 4, "long prompt is truncated with the same tokenizer = smaller context window on the server")
    if ({"speed_faster", "speed_slower"} & clues) and not tokenizer and "refusals_up" not in clues:
        add("serving", 3, "speed changed while the tokenizer stayed the same = same model, different serving setup")
    if "style_slightly_different" in clues and not tokenizer and not ours:
        add("serving", 2, "wording only slightly different = same model, different numerics (e.g. quantisation)")
    if "cutoff_up" in clues:
        add("request_config" if ours else "serving", 2, "answers are cut off early")
    if "length_changed" in clues:
        add("request_config" if ours else "model", 1, "answer length changed")
    if ({"json_down", "correct_down"} & clues) and not ours:
        add("model", 1, "fixed answers / JSON format changed")

    total = sum(points.values())
    ranking = sorted(({"layer": k, "points": v, "share": round(v / total, 2) if total else 0}
                      for k, v in points.items()), key=lambda x: -x["points"])
    if total == 0:
        return None, 0.0, ranking, why
    best = ranking[0]["layer"]
    return best, points[best] / total, ranking, why


def confidence_label(c):
    return "high" if c >= 0.7 else "medium" if c >= 0.5 else "low"


# ---------------------------------------------------------------- check + report
def check(trial=None, deep=False, progress=None):
    base = load_baseline()
    if not base:
        raise RuntimeError("No baseline yet - run a baseline first.")

    def cb(stage_off, stage_total):
        return (lambda d, t: progress(stage_off + d, stage_total)) if progress else None

    rounds = []
    if not deep:
        rounds.append(run_round(repeats=1, progress=cb(0, 15 * 4)))
        facts = compare(base, summarise(rounds[0]))
        if facts:   # something looks different -> confirm with a full round
            rounds.append(run_round(repeats=3, progress=cb(15, 15 * 4)))
            facts2 = compare(base, summarise(rounds[1]))
            keep = {f["clue"] for f in facts} & {f["clue"] for f in facts2}
            facts = [f for f in facts2 if f["clue"] in keep]
    else:
        rounds.append(run_round(repeats=3, progress=cb(0, 45)))
        facts = compare(base, summarise(rounds[0]))

    live, lf = live_facts(base)
    facts += lf
    layer, conf, ranking, why = find_cause(facts)
    changed = bool(facts)
    report = {
        "id": uuid.uuid4().hex[:8],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trial": trial,
        "changed": changed,
        "status": "normal" if not changed else ("changed" if layer else "inconclusive"),
        "observed": facts,
        "what_i_saw": [f["text"] for f in facts],
        "likely_cause": layer if changed else None,
        "likely_cause_meaning": LAYER_INFO.get(layer) if changed else None,
        "confidence": round(conf, 2) if changed else None,
        "confidence_label": confidence_label(conf) if changed and layer else None,
        "reasons": why.get(layer, []) if layer else [],
        "ranking": ranking if changed else [],
        "alternatives": [r for r in ranking[1:] if r["points"] > 0] if changed else [],
        "caveat": CAVEATS.get(layer) if changed else None,
        "config_diff": config_diff(base["config"], load_config()),
        "live_traffic": live,
        "probe_cost": total_cost(rounds),
        "baseline_time": base["time"],
    }
    with open(config.REPORTS, "a") as f:
        f.write(json.dumps(report) + "\n")
    return report


def read_reports(limit=50):
    try:
        rows = [json.loads(x) for x in config.REPORTS.read_text().splitlines() if x.strip()]
    except FileNotFoundError:
        rows = []
    return rows[-limit:][::-1]


def print_report(r):
    c = r["probe_cost"]
    print("\n" + "=" * 64)
    print(f"SilentShift {r['time']}  status: {r['status'].upper()}")
    if r["changed"]:
        print("\nOBSERVED EVIDENCE (facts):")
        for t in r["what_i_saw"]:
            print("  •", t)
        print("\nLIKELY CAUSE (inference):")
        print(f"  • {str(r['likely_cause']).upper()}  ({r['confidence']:.0%}, {r['confidence_label']})")
        for t in r["reasons"]:
            print("    because", t)
        for a in r["alternatives"]:
            print(f"  alternative: {a['layer']} ({a['share']:.0%})")
        print("  caveat:", r["caveat"])
    print(f"\nProbe cost: {c['calls']} calls, {c['tokens']} tokens, {c['seconds']} s, ~${c['est_usd']}  stages {c['stages']}")
    print("=" * 64)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "baseline":
        b = make_baseline()
        print(f"Baseline saved. Normal wording similarity ≥ {b['normal_similarity_min']:.2f}, "
              f"speed {b['speed_mean']:.1f}±{b['speed_sd']:.1f} tok/s")
    elif cmd in ("check", "deep"):
        print_report(check(deep=cmd == "deep"))
    else:
        print(__doc__)